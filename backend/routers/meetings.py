from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem
from pydantic import BaseModel
from datetime import datetime
import json

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


class MeetingCreate(BaseModel):
    title: str
    agenda: list[str] = []
    participants: list[str] = []
    created_by: int = None


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


# 3. 특정 회의 상세 조회 (안건 + 참석자 포함)
@router.get("/{meeting_id}")
def get_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """특정 회의 조회"""
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()

    return {
        "meeting_id": meeting.meeting_id,
        "title": meeting.title,
        "status": meeting.status,
        "created_at": meeting.created_at.isoformat(),
        "duration": meeting.duration,
        "participants": meeting.participants,
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