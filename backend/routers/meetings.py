from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, Transcript, PlatformSave
from pydantic import BaseModel
from datetime import datetime
import json
import os
from routers.ai import analyze_and_save

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
                "discussions": json.loads(i.discussions) if i.discussions else [],
                "speaker_points": json.loads(i.speaker_points) if i.speaker_points else [],
                "decision": i.decision,
                "completed_items": json.loads(i.completed_items) if i.completed_items else [],
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

    # 입력 검증 (공백만 있는 경우도 거부)
    raw_text = (body.raw_text or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="수정할 텍스트가 비어있습니다")

    # 1. 텍스트 저장 (없으면 새 transcript 생성)
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).order_by(Transcript.transcript_id.desc()).first()
    try:
        if transcript:
            transcript.raw_text = raw_text
        else:
            transcript = Transcript(meeting_id=meeting_id, raw_text=raw_text)
            db.add(transcript)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"텍스트 저장 실패: {e}")

    # 2. 공용 분석 로직으로 안건 재생성 (견고한 파싱 + 트랜잭션 안전)
    agenda_items = analyze_and_save(meeting_id, raw_text, db)

    # 3. 기존 저장 파일(md/pdf/docx) 삭제 → 다음 다운로드 시 최신본 재생성
    saved_files = db.query(PlatformSave).filter(
        PlatformSave.meeting_id == meeting_id,
        PlatformSave.platform.in_(["markdown", "pdf", "docx"]),
    ).all()
    for saved in saved_files:
        try:
            if saved.platform_doc_id and os.path.exists(saved.platform_doc_id):
                os.remove(saved.platform_doc_id)
        except OSError:
            pass  # 파일 삭제 실패는 무시 (DB 레코드만 정리)
        db.delete(saved)
    try:
        db.commit()
    except Exception:
        db.rollback()  # 파일 레코드 정리 실패는 치명적이지 않음

    return {
        "meeting_id": meeting_id,
        "status": "reanalyzed",
        "raw_text": raw_text,
        "agenda_items": agenda_items,
    }