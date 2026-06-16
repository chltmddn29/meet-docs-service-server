from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript, Meeting, MeetingAgendaItem
from routers.groq_client import client
import os

router = APIRouter(prefix="/api/meetings", tags=["stt"])


def correct_transcription(text: str, meeting_title: str = "", agenda_list: str = "",
                          participants: str = "") -> str:
    """STT 결과를 '말하려던 것'으로 다듬기: 영어 기술용어 음차 → 올바른 영어 표기
    + 회의 맥락 기반 용어 교정. 화자가 말한 의미는 보존하고 새 내용은 지어내지 않는다.

    Args:
        text: STT 원본 텍스트
        meeting_title: 회의 제목 (컨텍스트용)
        agenda_list: 안건 목록 (쉼표 구분, 컨텍스트용)
        participants: 참석자 (쉼표 구분, 컨텍스트용)
    """
    # 회의 맥락을 프롬프트에 포함
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

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": f"다음 텍스트를 보정해주세요:\n{text}"
            }
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


@router.post("/{meeting_id}/process")
def process_audio(meeting_id: int, db: Session = Depends(get_db)):
    """음성 파일을 텍스트로 변환 (Groq Whisper + AI 보정)"""
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="Audio file not found")

    if not os.path.exists(transcript.audio_file_path):
        raise HTTPException(status_code=404, detail="Audio file does not exist")

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

    try:
        with open(transcript.audio_file_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-large-v3",          # turbo보다 정확 (특히 한국어)
                language="ko",
                response_format="text",
                temperature=0,                      # 결정적 출력 → 환각 감소
                prompt=whisper_prompt,              # 회의 맥락 주입 → 용어/이름 정확도↑
            )

        raw_text = result.strip() if isinstance(result, str) else str(result)

        # 들린 그대로가 아니라 '말하려던 것'으로 다듬기
        # 회의 맥락(제목·안건·참석자)을 LLM에 넘겨 도메인 용어를 제대로 교정
        agenda_str = ", ".join(a.agenda for a in agenda_items if a.agenda)
        cleaned = (
            correct_transcription(
                raw_text,
                meeting_title=meeting.title if meeting else "",
                agenda_list=agenda_str,
                participants=meeting.participants if meeting else "",
            )
            if raw_text
            else raw_text
        )

        transcript.raw_text = cleaned
        db.commit()
        db.refresh(transcript)

        return {
            "meeting_id": meeting_id,
            "status": "completed",
            "raw_text": cleaned,
            "original_text": raw_text,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
