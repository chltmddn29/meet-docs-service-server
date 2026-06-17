from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, PlatformSave
from routers.doc_content import build_markdown

router = APIRouter(prefix="/api/meetings", tags=["preview"])


@router.get("/{meeting_id}/preview")
def preview_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """회의록 내용 미리보기 (마크다운 텍스트 + 저장된 플랫폼 목록)"""
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()

    # 모든 필드를 포함한 마크다운 (공용 빌더)
    md = build_markdown(meeting, items)

    # 저장된 플랫폼 목록
    saves = db.query(PlatformSave).filter(
        PlatformSave.meeting_id == meeting_id,
        PlatformSave.save_status == "success",
    ).all()
    saved_platforms = {}
    for s in saves:
        saved_platforms[s.platform] = s.platform_doc_id  # 노션은 url, 나머지는 파일경로

    return {
        "meeting_id": meeting_id,
        "title": meeting.title,
        "markdown": md,
        "saved_platforms": saved_platforms,
    }