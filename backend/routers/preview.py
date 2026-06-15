from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, PlatformSave
import json

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

    # 마크다운 텍스트 생성 (미리보기용)
    md = f"# {meeting.title}\n\n"
    md += f"> {meeting.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
    for item in items:
        md += f"## {item.order}. {item.agenda}\n\n"
        if item.decision:
            md += f"**결정:** {item.decision}\n\n"
        if item.action_items:
            actions = json.loads(item.action_items)
            for a in actions:
                md += f"- [ ] {a}\n"
            md += "\n"

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