from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript, Meeting, MeetingAgendaItem
from routers.groq_client import client, ensure_client, TEXT_MODEL
from routers.audio_store import ensure_local_file
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["stt"])

# Groq Whisper 업로드 허용 한도(무료 25MB). 초과 시 명확히 거부.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


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
def process_audio(meeting_id: int, db: Session = Depends(get_db)):
    """음성 파일을 텍스트로 변환 (Groq Whisper + AI 보정)"""
    ensure_client()  # GROQ_API_KEY 없으면 503

    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).order_by(Transcript.transcript_id.desc()).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="음성 파일이 없습니다")

    # 디스크에 없으면 DB 바이트로 복원 (재시작 후에도 처리 가능)
    audio_path = ensure_local_file(transcript)
    if not audio_path:
        raise HTTPException(
            status_code=404,
            detail="음성 파일이 존재하지 않습니다 (데이터가 저장되지 않았습니다)",
        )
    db.commit()  # 복원으로 audio_file_path가 갱신됐으면 반영

    # 파일 크기 검증 (빈 파일·과대 파일 사전 차단)
    try:
        size = os.path.getsize(audio_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"파일 접근 실패: {e}")

    if size == 0:
        raise HTTPException(status_code=400, detail="음성 파일이 비어있습니다 (0 bytes)")
    if size > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"음성 파일이 너무 큽니다 ({size // (1024*1024)}MB, 최대 25MB)",
        )

    # 회의 맥락(제목·안건·참석자)을 프롬프트로 주입 → 용어·이름 인식 정확도↑
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

    # 1) Whisper STT — 실패 시 원인별로 구분된 메시지
    try:
        with open(audio_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-large-v3",
                language="ko",
                response_format="text",
                temperature=0,
                prompt=whisper_prompt,
            )
        raw_text = result.strip() if isinstance(result, str) else str(result).strip()
    except Exception as e:
        logger.exception("Whisper STT 실패")
        raise HTTPException(status_code=502, detail=f"음성 인식 실패: {e}")

    if not raw_text:
        raise HTTPException(
            status_code=400,
            detail="음성에서 텍스트를 추출하지 못했습니다 (무음이거나 너무 짧음)",
        )

    # 2) LLM 보정 (실패해도 원문 사용 → 데이터 유실 없음)
    agenda_str = ", ".join(a.agenda for a in agenda_items if a.agenda)
    cleaned = correct_transcription(
        raw_text,
        meeting_title=meeting.title if meeting else "",
        agenda_list=agenda_str,
        participants=meeting.participants if meeting else "",
    )

    # 3) 저장
    try:
        transcript.raw_text = cleaned
        db.commit()
        db.refresh(transcript)
    except Exception as e:
        db.rollback()
        logger.exception("transcript 저장 실패")
        raise HTTPException(status_code=500, detail=f"저장 실패: {e}")

    return {
        "meeting_id": meeting_id,
        "status": "completed",
        "raw_text": cleaned,
        "original_text": raw_text,
    }
