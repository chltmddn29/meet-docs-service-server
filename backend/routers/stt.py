from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db, SessionLocal
from models import Transcript, Meeting, MeetingAgendaItem
from routers.groq_client import client, ensure_client
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
# 무료 플랜은 시간당 오디오 7,200초 한도라 6개 동시(=한 번에 3,600초)면 재시도 한두 번에
# 시간당 쿼터를 넘겨 429가 다발한다 → 보수적으로 3으로 낮춰 버스트를 줄인다.
MAX_PARALLEL_CHUNKS = 3


# Groq가 일시적으로 뱉는 오류(과부하/속도제한/타임아웃) — 재시도하면 대개 성공.
_TRANSIENT_HINTS = (
    "502", "503", "429", "500",
    "service_unavailable", "internal_server_error",
    "timed out", "timeout", "connection", "rate limit",
)


def _is_transient(err: Exception) -> bool:
    msg = str(err).lower()
    return any(h in msg for h in _TRANSIENT_HINTS)


def _retry_after_seconds(err: Exception) -> float | None:
    """429 응답의 Retry-After 헤더(초)를 읽어온다. 없으면 None.
    Groq가 '시간당 오디오 한도 초과'를 알릴 때 정확한 대기시간을 여기로 준다."""
    resp = getattr(err, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    val = headers.get("retry-after") or headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _transcribe_one(path: str, whisper_prompt: str, max_retries: int = 6) -> str:
    """파일 1개를 Groq Whisper로 변환해 텍스트 반환.
    Groq 일시 오류(502/503/429/timeout)는 재시도한다. 429면 응답의 Retry-After를
    존중해 그만큼 기다린다(시간당 오디오 쿼터 창은 30초 백오프로는 안 풀리므로)."""
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
                # 서버가 알려준 대기시간이 있으면 우선 사용(429 쿼터), 없으면 지수 백오프.
                ra = _retry_after_seconds(e)
                if ra is not None:
                    wait = min(ra + 1, 90)  # 헤더값 +여유, 최대 90초
                else:
                    wait = min(2 ** attempt * 2, 60)  # 2,4,8,16,32,60초
                logger.warning("청크 변환 일시 오류, %.0f초 후 재시도(%d/%d): %s",
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
        # compression_level 0 = 가장 빠른 인코딩(음성인식엔 품질 영향 없음).
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ar", "16000", "-ac", "1",
            "-c:a", "libopus", "-b:a", "24k", "-compression_level", "0",
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
        workers = min(len(chunks), MAX_PARALLEL_CHUNKS)

        def _work(item):
            idx, ch = item
            try:
                return idx, _transcribe_one(ch, whisper_prompt), True
            except Exception as e:
                logger.warning("청크 %d 1차 실패: %s", idx + 1, e)
                return idx, None, False

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for idx, text, ok in ex.map(_work, list(enumerate(chunks))):
                results[idx] = text if ok else None

        # 2차 패스: 실패한 구간만 잠시 쉬었다가 순차로(동시성 0) 다시 시도.
        # 1차 실패가 시간당 쿼터 소진(429)이면 버스트를 멈추고 천천히 재시도해야 풀린다.
        failed_idx = [i for i, r in enumerate(results) if r is None]
        if failed_idx:
            logger.info("실패 구간 %d개 → 30초 후 순차 재시도", len(failed_idx))
            time.sleep(30)
            for i in failed_idx:
                try:
                    results[i] = _transcribe_one(chunks[i], whisper_prompt)
                    logger.info("청크 %d 2차 재시도 성공", i + 1)
                except Exception as e:
                    logger.warning("청크 %d 최종 실패(건너뜀): %s", i + 1, e)
                    results[i] = f"[※ {i + 1}번째 구간 인식 실패 — 다시 변환 권장]"

        if all(r is None or r.startswith("[※ ") for r in results):
            raise RuntimeError("모든 구간 변환이 실패했습니다 (Groq 일시 오류). 잠시 후 다시 시도해주세요.")
        parts = [r for r in results if r]
        return "\n".join(parts).strip()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _compact_audio(audio_path: str) -> str | None:
    """전체 오디오를 음성인식용 저용량 opus(16kHz mono)로 한 번에 재인코딩한 임시 파일 경로 반환.
    이렇게 줄이면 긴 회의도 Groq 25MB 한도 안에 들어와 '분할 없이 한 번에' 변환할 수 있다.
    ffmpeg가 없거나 실패하면 None(→ 호출부가 기존 분할 경로로 폴백)."""
    if not shutil.which("ffmpeg"):
        return None
    fd, out = tempfile.mkstemp(prefix="stt_compact_", suffix=".ogg")
    os.close(fd)
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-ar", "16000", "-ac", "1",
        "-c:a", "libopus", "-b:a", "24k", "-compression_level", "0",
        out,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        logger.error("ffmpeg 컴팩트 재인코딩 실패: %s", proc.stderr[-1000:])
        try:
            os.remove(out)
        except OSError:
            pass
        return None
    return out


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

        # Whisper STT
        #  - 24MB 이하: 원본을 바로 변환
        #  - 초과: 먼저 저용량 opus로 재인코딩 → 25MB 한도에 들어오면 '한 번에' 변환,
        #          그래도 크면(아주 긴 회의) 그때만 분할. → 대부분의 회의가 단일 호출로 빨라짐.
        t0 = time.monotonic()
        try:
            if size > CHUNK_THRESHOLD:
                compact = _compact_audio(audio_path)
                if compact:
                    try:
                        csize = os.path.getsize(compact)
                        logger.info("재인코딩 %dMB → %dMB (%.1fs)",
                                    size // (1024 * 1024), csize // (1024 * 1024),
                                    time.monotonic() - t0)
                        if csize <= CHUNK_THRESHOLD:
                            raw_text = _transcribe_one(compact, whisper_prompt)
                        else:
                            logger.info("재인코딩 후도 큼 → 분할 변환")
                            raw_text = _transcribe_large(compact, whisper_prompt)
                    finally:
                        try:
                            os.remove(compact)
                        except OSError:
                            pass
                else:
                    # ffmpeg 미설치/실패 → 기존 분할 경로로 폴백
                    logger.info("컴팩트 재인코딩 불가 → 분할 변환")
                    raw_text = _transcribe_large(audio_path, whisper_prompt)
            else:
                raw_text = _transcribe_one(audio_path, whisper_prompt)
            logger.info("STT 완료 (%.1fs, meeting %s)", time.monotonic() - t0, meeting_id)
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

        # STT 원문을 그대로 저장한다.
        # (예전엔 여기서 LLM 보정 패스를 한 번 더 돌렸지만, 바로 뒤 /analyze 가
        #  어차피 전체 텍스트를 다시 읽으므로 중복이었다. 영어 기술용어 표기 교정은
        #  분석 프롬프트에서 함께 처리하도록 옮겨 LLM 패스를 1회로 줄였다 — 변환 속도 개선)
        transcript.raw_text = raw_text
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
