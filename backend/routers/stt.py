from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db, SessionLocal
from models import Transcript, Meeting, MeetingAgendaItem
from routers.groq_client import client, ensure_client, TEXT_MODEL
from routers.audio_store import ensure_local_file
import os
import shutil
import subprocess
import tempfile
import glob
import time
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["stt"])

# Groq Whisper 단일 파일 변환 상한(25MB). 이보다 크면 ffmpeg로 잘라 순차 변환.
GROQ_FILE_LIMIT = 25 * 1024 * 1024
# 안전 여유를 두고 24MB를 넘으면 분할 변환 경로로.
CHUNK_THRESHOLD = 24 * 1024 * 1024
# 분할 시 한 조각의 길이(초). 10분이면 조각당 ~2MB로 작아 변환이 빠르고 타임아웃 안전.
CHUNK_SECONDS = 600
# 청크 동시 변환 수. 같은 모델로 병렬만 하므로 정확도엔 영향 없음.
# 무료 플랜 속도제한과의 균형(너무 키우면 429 다발) — 6이면 1시간(6조각)을 한 번에.
MAX_PARALLEL_CHUNKS = 6


# Groq가 일시적으로 뱉는 오류(과부하/속도제한/타임아웃) — 재시도하면 대개 성공.
_TRANSIENT_HINTS = (
    "502", "503", "429", "500",
    "service_unavailable", "internal_server_error",
    "timed out", "timeout", "connection", "rate limit",
)


def _is_transient(err: Exception) -> bool:
    msg = str(err).lower()
    return any(h in msg for h in _TRANSIENT_HINTS)


def _transcribe_one(path: str, whisper_prompt: str, max_retries: int = 5) -> str:
    """파일 1개를 Groq Whisper로 변환해 텍스트 반환.
    Groq 일시 오류(502/503/429/timeout)는 지수 백오프로 재시도한다."""
    last = None
    for attempt in range(max_retries):
        try:
            with open(path, "rb") as audio_file:
                result = client.audio.transcriptions.create(
                    file=audio_file,
                    model="whisper-large-v3",
                    language="ko",
                    response_format="text",
                    temperature=0,
                    prompt=whisper_prompt,
                )
            return result.strip() if isinstance(result, str) else str(result).strip()
        except Exception as e:
            last = e
            if attempt < max_retries - 1 and _is_transient(e):
                wait = min(2 ** attempt * 2, 30)  # 2,4,8,16,30초
                logger.warning("청크 변환 일시 오류, %d초 후 재시도(%d/%d): %s",
                               wait, attempt + 1, max_retries, e)
                time.sleep(wait)
                continue
            raise
    raise last


def _transcribe_large(audio_path: str, whisper_prompt: str) -> str:
    """25MB 초과 파일: ffmpeg로 시간 분할 → 각 조각을 순차 변환 → 이어붙임.
    ffmpeg가 없으면 RuntimeError."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "큰 파일을 변환하려면 ffmpeg가 필요합니다(서버에 미설치)."
        )
    tmpdir = tempfile.mkdtemp(prefix="stt_chunks_")
    try:
        out_pattern = os.path.join(tmpdir, "chunk_%03d.ogg")
        # 스트리밍 분할(저메모리): opus 16kHz mono로 재인코딩하며 시간 단위로 자름.
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ar", "16000", "-ac", "1",
            "-c:a", "libopus", "-b:a", "24k",
            "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
            out_pattern,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            logger.error("ffmpeg 분할 실패: %s", proc.stderr[-2000:])
            raise RuntimeError("오디오 분할(ffmpeg)에 실패했습니다.")

        chunks = sorted(glob.glob(os.path.join(tmpdir, "chunk_*.ogg")))
        if not chunks:
            raise RuntimeError("분할 결과가 없습니다(빈 오디오일 수 있음).")

        # 청크를 동시에 변환(같은 모델 → 정확도 동일, 시간만 단축).
        # 무료 플랜 속도제한을 감안해 동시 실행 수는 제한(429는 _transcribe_one이 재시도).
        results: list = [None] * len(chunks)
        failed = 0
        workers = min(len(chunks), MAX_PARALLEL_CHUNKS)

        def _work(item):
            idx, ch = item
            try:
                return idx, _transcribe_one(ch, whisper_prompt), True
            except Exception as e:
                logger.warning("청크 %d 최종 실패(건너뜀): %s", idx + 1, e)
                return idx, f"[※ {idx + 1}번째 구간 인식 실패 — 다시 변환 권장]", False

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for idx, text, ok in ex.map(_work, list(enumerate(chunks))):
                results[idx] = text
                if not ok:
                    failed += 1

        if failed == len(chunks):
            raise RuntimeError("모든 구간 변환이 실패했습니다 (Groq 일시 오류). 잠시 후 다시 시도해주세요.")
        parts = [r for r in results if r]
        return "\n".join(parts).strip()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def correct_transcription(text: str, meeting_title: str = "", agenda_list: str = "",
                          participants: str = "") -> str:
    """STT 결과를 '말하려던 것'으로 다듬기: 영어 기술용어 음차 → 올바른 영어 표기
    + 회의 맥락 기반 용어 교정. 실패하면 원문을 그대로 반환(보정은 부가기능).
    """
    context_parts = []
    if meeting_title:
        context_parts.append(f"회의 제목: {meeting_title}")
    if agenda_list:
        context_parts.append(f"안건: {agenda_list}")
    if participants:
        context_parts.append(f"참석자: {participants}")
    context_str = "\n".join(context_parts)

    system_prompt = """당신은 한국어 회의 STT(음성인식) 결과를 다듬는 편집자입니다.
화자가 말하려던 바를 읽기 쉽게 정리하되, 아래 규칙을 엄격히 지키세요.

[교정할 것]
1. 영어 기술용어·브랜드·제품명의 한글 음차를 올바른 영어 표기로 변환
   예) "패스트 API"/"패스트에이피아이" → "FastAPI", "리버파드" → "Riverpod",
       "플러터" → "Flutter", "리액트" → "React", "깃허브" → "GitHub",
       "자바스크립트" → "JavaScript"
2. 명백한 STT 오인식(잘못된 조사·발음)만 자연스럽게 교정
   예) "후로" → "으로", "햇습니다" → "했습니다"
3. 말 더듬기·중복·군더더기는 정리해 읽기 쉽게
4. 회의 맥락에 맞는 도메인 용어를 올바르게 교정 (아래 회의 정보 참고)
5. 문장 경계에 맞춰 마침표·쉼표·물음표 등 문장 부호를 넣어 읽기 쉽게
6. 화제(주제)가 바뀌는 지점에서 문단을 나눠 정리 (빈 줄로 구분)

[금지]
- 내용을 추가/삭제/요약/재구성하지 말 것. 화자가 말한 의미를 그대로 보존.
- 없는 정보를 지어내지 말 것. 애매하면 원문을 유지.
- 교정된 본문만 출력(설명·머리말 없이).

[회의 정보]
""" + (context_str if context_str else "(맥락 정보 없음)")

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"다음 텍스트를 보정해주세요:\n{text}"},
            ],
            temperature=0,
        )
        result = response.choices[0].message.content.strip()
        # 빈 응답이면 원문 유지
        return result if result else text
    except Exception as e:
        # 보정 실패는 치명적이지 않다 → Whisper 원문을 그대로 사용
        logger.warning("LLM 보정 실패, 원문 사용: %s", e)
        return text


@router.post("/{meeting_id}/process")
def process_audio(
    meeting_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """변환을 백그라운드로 시작하고 즉시 응답한다.
    긴 회의(여러 청크)는 시간이 오래 걸려 동기 응답 시 프론트/게이트웨이가 타임아웃되므로,
    여기서는 'processing'만 돌려주고 실제 변환은 _run_transcription에서 수행한다.
    프론트는 GET /process-status 로 완료를 폴링한다."""
    ensure_client()  # GROQ_API_KEY 없으면 503

    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).order_by(Transcript.transcript_id.desc()).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="음성 파일이 없습니다")

    # 이미 변환이 끝나 있으면 다시 돌리지 않고 결과를 그대로 반환
    if transcript.process_status == "completed" and transcript.raw_text:
        return {"meeting_id": meeting_id, "status": "completed",
                "raw_text": transcript.raw_text}
    # 이미 진행 중이면 중복 실행 방지
    if transcript.process_status == "processing":
        return {"meeting_id": meeting_id, "status": "processing"}

    # 디스크에 없으면 DB 바이트로 복원 (재시작 후에도 처리 가능)
    audio_path = ensure_local_file(transcript)
    if not audio_path:
        raise HTTPException(
            status_code=404,
            detail="음성 파일이 존재하지 않습니다 (데이터가 저장되지 않았습니다)",
        )

    try:
        size = os.path.getsize(audio_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"파일 접근 실패: {e}")
    if size == 0:
        raise HTTPException(status_code=400, detail="음성 파일이 비어있습니다 (0 bytes)")

    # 상태를 processing 으로 표시하고 백그라운드 작업 예약
    transcript.process_status = "processing"
    transcript.process_error = None
    db.commit()

    background_tasks.add_task(_run_transcription, meeting_id, transcript.transcript_id)
    return {"meeting_id": meeting_id, "status": "processing"}


@router.get("/{meeting_id}/process-status")
def process_status(meeting_id: int, db: Session = Depends(get_db)):
    """변환 진행 상태 조회 (프론트 폴링용)."""
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).order_by(Transcript.transcript_id.desc()).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="음성 파일이 없습니다")
    return {
        "meeting_id": meeting_id,
        # status가 비어있는데 raw_text가 있으면 과거(동기 시절)에 처리된 것 → completed로 간주
        "status": transcript.process_status
                  or ("completed" if transcript.raw_text else "idle"),
        "raw_text": transcript.raw_text,
        "error": transcript.process_error,
    }


def _run_transcription(meeting_id: int, transcript_id: int):
    """백그라운드에서 실제 변환을 수행한다. 자체 DB 세션을 연다
    (요청 세션은 응답 후 닫히므로 재사용 불가)."""
    db = SessionLocal()
    try:
        transcript = db.query(Transcript).filter(
            Transcript.transcript_id == transcript_id
        ).first()
        if not transcript:
            return

        audio_path = ensure_local_file(transcript)
        if not audio_path:
            transcript.process_status = "failed"
            transcript.process_error = "음성 파일이 존재하지 않습니다."
            db.commit()
            return
        db.commit()  # 복원으로 audio_file_path 갱신됐으면 반영

        try:
            size = os.path.getsize(audio_path)
        except OSError as e:
            transcript.process_status = "failed"
            transcript.process_error = f"파일 접근 실패: {e}"
            db.commit()
            return

        meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
        agenda_items = db.query(MeetingAgendaItem).filter(
            MeetingAgendaItem.meeting_id == meeting_id
        ).order_by(MeetingAgendaItem.order).all()

        prompt_parts = ["한국어로 진행된 회의 녹음입니다."]
        if meeting and meeting.title:
            prompt_parts.append(f"회의 제목: {meeting.title}.")
        agenda_text = ", ".join(a.agenda for a in agenda_items if a.agenda)
        if agenda_text:
            prompt_parts.append(f"주요 안건: {agenda_text}.")
        if meeting and meeting.participants:
            prompt_parts.append(f"참석자: {meeting.participants}.")
        whisper_prompt = " ".join(prompt_parts)

        # Whisper STT — 24MB 이하는 바로, 초과는 ffmpeg로 잘라 순차 변환
        try:
            if size > CHUNK_THRESHOLD:
                logger.info("큰 파일(%dMB) → 분할 변환", size // (1024 * 1024))
                raw_text = _transcribe_large(audio_path, whisper_prompt)
            else:
                raw_text = _transcribe_one(audio_path, whisper_prompt)
        except Exception as e:
            logger.exception("Whisper STT 실패")
            transcript.process_status = "failed"
            transcript.process_error = f"음성 인식 실패: {e}"
            db.commit()
            return

        if not raw_text:
            transcript.process_status = "failed"
            transcript.process_error = "음성에서 텍스트를 추출하지 못했습니다 (무음이거나 너무 짧음)."
            db.commit()
            return

        # LLM 보정 (실패해도 원문 사용 → 데이터 유실 없음)
        agenda_str = ", ".join(a.agenda for a in agenda_items if a.agenda)
        cleaned = correct_transcription(
            raw_text,
            meeting_title=meeting.title if meeting else "",
            agenda_list=agenda_str,
            participants=meeting.participants if meeting else "",
        )

        transcript.raw_text = cleaned
        transcript.process_status = "completed"
        transcript.process_error = None
        db.commit()
        logger.info("변환 완료: meeting %s", meeting_id)
    except Exception as e:
        logger.exception("백그라운드 변환 실패")
        try:
            t = db.query(Transcript).filter(
                Transcript.transcript_id == transcript_id
            ).first()
            if t:
                t.process_status = "failed"
                t.process_error = f"변환 실패: {e}"
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
