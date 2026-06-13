from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript, MeetingAgendaItem
from routers.groq_client import client
import json

router = APIRouter(prefix="/api/meetings", tags=["ai"])


@router.post("/{meeting_id}/analyze")
def analyze_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """STT 텍스트를 AI로 안건별로 정리"""
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    if not transcript.raw_text:
        raise HTTPException(status_code=400, detail="Raw text not available")

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "당신은 회의록을 분석하는 AI입니다. 반드시 순수 JSON 배열만 응답하세요. 마크다운 코드블록이나 다른 텍스트는 절대 포함하지 마세요."
                },
                {
                    "role": "user",
                    "content": f"""
다음 회의록 텍스트를 분석해서 JSON 배열 형식으로 반환해주세요.

각 항목의 형식:
{{
  "agenda": "안건명",
  "content": "논의된 내용",
  "decision": "결정사항",
  "action_items": ["할 일 1", "할 일 2"]
}}

회의록 텍스트:
{transcript.raw_text}
"""
                }
            ],
            temperature=0.3,
        )

        response_text = response.choices[0].message.content.strip()

        if "```" in response_text:
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()

        agenda_items = json.loads(response_text)

        if not agenda_items:
            raise HTTPException(
                status_code=400,
                detail="분석할 내용이 부족합니다 (녹음이 너무 짧거나 비어있음)"
            )

        db.query(MeetingAgendaItem).filter(
            MeetingAgendaItem.meeting_id == meeting_id
        ).delete()
        db.commit()

        for idx, item in enumerate(agenda_items):
            db.add(MeetingAgendaItem(
                meeting_id=meeting_id,
                agenda=item.get("agenda", ""),
                order=idx + 1,
                content=item.get("content", ""),
                decision=item.get("decision", ""),
                action_items=json.dumps(item.get("action_items", []), ensure_ascii=False),
            ))

        db.commit()

        return {
            "meeting_id": meeting_id,
            "status": "analyzed",
            "agenda_items": agenda_items
        }

    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI 응답 파싱 실패")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
