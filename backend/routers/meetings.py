from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

# Pydantic 모델 (요청/응답 데이터 형식)
class MeetingCreate(BaseModel):
    title: str
    agenda: list[str] = []
    created_by: int = None

class MeetingResponse(BaseModel):
    meeting_id: int
    title: str
    status: str
    created_at: datetime
    
    class Config:
        from_attributes = True

# 1. 회의 생성
@router.post("", response_model=MeetingResponse)
def create_meeting(meeting: MeetingCreate, db: Session = Depends(get_db)):
    """새 회의 생성"""
    db_meeting = Meeting(
        title=meeting.title,
        status="recording",
        created_by=meeting.created_by,
    )
    db.add(db_meeting)
    db.commit()
    db.refresh(db_meeting)
    return db_meeting

# 2. 회의 목록 조회
@router.get("")
def get_meetings(db: Session = Depends(get_db)):
    """모든 회의 조회"""
    meetings = db.query(Meeting).all()
    return meetings

# 3. 특정 회의 상세 조회
@router.get("/{meeting_id}", response_model=MeetingResponse)
def get_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """특정 회의 조회"""
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting