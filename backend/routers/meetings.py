from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, Transcript
from pydantic import BaseModel
from datetime import datetime
import json
from routers.groq_client import client as groq_client

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


class MeetingCreate(BaseModel):
    title: str
    agenda: list[str] = []
    participants: list[str] = []
    created_by: int = None


class RawTextUpdate(BaseModel):
    raw_text: str


class MeetingResponse(BaseModel):
    meeting_id: int
    title: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# 1. 회의 생성 (안건 + 참석자 함께 저장)
@router.post("", response_model=MeetingResponse)
def create_meeting(meeting: MeetingCreate, db: Session = Depends(get_db)):
    """새 회의 생성"""
    db_meeting = Meeting(
        title=meeting.title,
        status="recording",
        created_by=meeting.created_by,
        participants=", ".join(meeting.participants) if meeting.participants else None,
    )
    db.add(db_meeting)
    db.commit()
    db.refresh(db_meeting)

    # 입력한 안건 저장 (녹음 화면 참고용)
    for idx, agenda_text in enumerate(meeting.agenda):
        if agenda_text.strip():
            db.add(MeetingAgendaItem(
                meeting_id=db_meeting.meeting_id,
                agenda=agenda_text,
                order=idx + 1,
            ))
    db.commit()

    return db_meeting


# 2. 회의 목록 조회
@router.get("")
def get_meetings(db: Session = Depends(get_db)):
    """모든 회의 조회"""
    return db.query(Meeting).all()


# 3. 특정 회의 상세 조회 (안건 + 참석자 + 원본 텍스트 포함)
@router.get("/{meeting_id}")
def get_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """특정 회의 조회"""
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()

    # 원본 STT 텍스트
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).first()
    raw_text = transcript.raw_text if transcript else None

    return {
        "meeting_id": meeting.meeting_id,
        "title": meeting.title,
        "status": meeting.status,
        "created_at": meeting.created_at.isoformat(),
        "duration": meeting.duration,
        "participants": meeting.participants,
        "raw_text": raw_text,
        "agenda_items": [
            {
                "item_id": i.item_id,
                "agenda": i.agenda,
                "order": i.order,
                "content": i.content,
                "decision": i.decision,
                "action_items": json.loads(i.action_items) if i.action_items else [],
            }
            for i in items
        ],
    }


# 4. 원본 텍스트 수정 + 다시 정리
@router.put("/{meeting_id}/raw-text")
def update_and_reanalyze(meeting_id: int, body: RawTextUpdate, db: Session = Depends(get_db)):
    """원본 STT 텍스트를 사용자가 수정하고 저장 → AI가 다시 정리"""
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # 1. 텍스트 저장
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).first()
    if transcript:
        transcript.raw_text = body.raw_text
        db.commit()

    # 2. 다시 정리: 수정된 텍스트로 AI 분석
    if not body.raw_text:
        raise HTTPException(status_code=400, detail="Raw text is empty")

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "당신은 회의록을 분석하는 AI입니다. 반드시 순수 JSON 배열만 응답하세요. 마크다운 코드블록이나 다른 텍스트는 절대 포함하지 마세요."
                },
                {
                    "role": "user",
                    "content": f"""다음 회의 내용을 분석해서 JSON 배열 형식으로 반환해주세요.

각 항목의 형식:
{{
  "agenda": "안건명",
  "content": "논의된 내용",
  "decision": "결정사항",
  "action_items": ["할 일 1", "할 일 2"]
}}

회의 내용:
{body.raw_text}
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
                detail="분석할 내용이 부족합니다"
            )

        # 3. 기존 agenda items 삭제 후 새로 저장
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

        # 기존 저장된 파일들 삭제 (마크다운, PDF, Word) → 다음 다운로드 시 새로 생성되도록
        import os
        from pathlib import Path
        from models import PlatformSave

        saved_files = db.query(PlatformSave).filter(
            PlatformSave.meeting_id == meeting_id,
            PlatformSave.platform.in_(["markdown", "pdf", "docx"])
        ).all()

        for saved in saved_files:
            try:
                if os.path.exists(saved.platform_doc_id):
                    os.remove(saved.platform_doc_id)
            except Exception:
                pass
            db.delete(saved)
        db.commit()

        return {
            "meeting_id": meeting_id,
            "status": "reanalyzed",
            "raw_text": body.raw_text,
            "agenda_items": agenda_items
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI 응답 파싱 실패")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))