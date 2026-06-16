from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript, MeetingAgendaItem
from routers.groq_client import client, ensure_client
from routers.llm_utils import extract_json_array
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["ai"])

_SYSTEM_PROMPT = (
    "당신은 회의록을 분석하는 AI입니다. 반드시 순수 JSON 배열만 응답하세요. "
    "마크다운 코드블록이나 다른 텍스트는 절대 포함하지 마세요."
)


def _build_user_prompt(raw_text: str) -> str:
    return f"""
다음 회의록 텍스트를 분석해서 JSON 배열 형식으로 반환해주세요.

각 항목의 형식:
{{
  "agenda": "안건명",
  "content": "논의된 내용",
  "decision": "결정사항",
  "action_items": ["할 일 1", "할 일 2"]
}}

회의록 텍스트:
{raw_text}
"""


def analyze_and_save(meeting_id: int, raw_text: str, db: Session) -> list:
    """raw_text를 AI로 분석해 안건 항목을 새로 저장. 공용 로직."""
    ensure_client()

    if not raw_text or not raw_text.strip():
        raise HTTPException(status_code=400, detail="분석할 텍스트가 없습니다")

    # 1) LLM 호출
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(raw_text)},
            ],
            temperature=0.3,
        )
        response_text = response.choices[0].message.content
    except Exception as e:
        logger.exception("AI 분석 호출 실패")
        raise HTTPException(status_code=502, detail=f"AI 분석 실패: {e}")

    # 2) 견고한 JSON 파싱
    try:
        agenda_items = extract_json_array(response_text)
    except ValueError:
        logger.error("AI 응답 파싱 실패: %r", response_text)
        raise HTTPException(status_code=500, detail="AI 응답을 이해하지 못했습니다")

    if not agenda_items:
        raise HTTPException(
            status_code=400,
            detail="분석할 내용이 부족합니다 (녹음이 너무 짧거나 비어있음)",
        )

    # 3) 기존 항목 교체 (트랜잭션 안전)
    try:
        db.query(MeetingAgendaItem).filter(
            MeetingAgendaItem.meeting_id == meeting_id
        ).delete()

        for idx, item in enumerate(agenda_items):
            db.add(MeetingAgendaItem(
                meeting_id=meeting_id,
                agenda=item.get("agenda", ""),
                order=idx + 1,
                content=item.get("content", ""),
                decision=item.get("decision", ""),
                action_items=json.dumps(
                    item.get("action_items", []), ensure_ascii=False
                ),
            ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("안건 저장 실패")
        raise HTTPException(status_code=500, detail=f"저장 실패: {e}")

    return agenda_items


@router.post("/{meeting_id}/analyze")
def analyze_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """STT 텍스트를 AI로 안건별로 정리"""
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).order_by(Transcript.transcript_id.desc()).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not transcript.raw_text:
        raise HTTPException(status_code=400, detail="Raw text not available")

    agenda_items = analyze_and_save(meeting_id, transcript.raw_text, db)

    return {
        "meeting_id": meeting_id,
        "status": "analyzed",
        "agenda_items": agenda_items,
    }
