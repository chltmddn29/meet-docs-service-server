from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, PlatformSave
from datetime import datetime
import json
import os

router = APIRouter(prefix="/api/meetings", tags=["markdown"])

MARKDOWN_DIR = "markdown"


@router.post("/{meeting_id}/save-markdown")
def save_markdown(meeting_id: int, db: Session = Depends(get_db)):
    """회의록을 마크다운 파일로 저장"""
    meeting = db.query(Meeting).filter(
        Meeting.meeting_id == meeting_id
    ).first()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    agenda_items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()

    if not agenda_items:
        raise HTTPException(status_code=400, detail="No agenda items found")

    try:
        now = datetime.now()
        md_content = f"# {meeting.title}\n\n"
        md_content += f"> 📅 {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        md_content += "---\n\n"

        for item in agenda_items:
            md_content += f"## {item.order}. {item.agenda}\n\n"

            if item.content:
                md_content += f"**내용**\n{item.content}\n\n"

            if item.decision:
                md_content += f"**결정사항**\n{item.decision}\n\n"

            if item.action_items:
                action_list = json.loads(item.action_items)
                if action_list:
                    md_content += "**할 일**\n"
                    for action in action_list:
                        md_content += f"- [ ] {action}\n"
                    md_content += "\n"

            md_content += "---\n\n"

        os.makedirs(MARKDOWN_DIR, exist_ok=True)
        filename = f"meeting_{meeting_id}_{now.strftime('%Y%m%d_%H%M%S')}.md"
        file_path = f"{MARKDOWN_DIR}/{filename}"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        db.add(PlatformSave(
            meeting_id=meeting_id,
            platform="markdown",
            save_status="success",
            platform_doc_id=file_path,
        ))
        db.commit()

        return {
            "meeting_id": meeting_id,
            "status": "success",
            "file_path": file_path,
            "preview": md_content
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{meeting_id}/download-markdown")
def download_markdown(meeting_id: int, db: Session = Depends(get_db)):
    """마크다운 파일 다운로드"""
    platform_save = db.query(PlatformSave).filter(
        PlatformSave.meeting_id == meeting_id,
        PlatformSave.platform == "markdown",
        PlatformSave.save_status == "success"
    ).order_by(PlatformSave.saved_at.desc()).first()

    if not platform_save:
        raise HTTPException(status_code=404, detail="Markdown file not found")

    if not os.path.exists(platform_save.platform_doc_id):
        raise HTTPException(status_code=404, detail="File does not exist")

    return FileResponse(
        path=platform_save.platform_doc_id,
        filename=os.path.basename(platform_save.platform_doc_id),
        media_type="text/markdown"
    )
