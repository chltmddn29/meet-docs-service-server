from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, PlatformSave
from routers.doc_content import build_markdown
from datetime import datetime
import os

router = APIRouter(prefix="/api/meetings", tags=["markdown"])

MARKDOWN_DIR = "markdown"


def _meeting_and_items(meeting_id: int, db: Session):
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()
    if not items:
        raise HTTPException(status_code=400, detail="No agenda items found")
    return meeting, items


def _write_markdown(meeting_id: int, content: str) -> str:
    os.makedirs(MARKDOWN_DIR, exist_ok=True)
    filename = f"meeting_{meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path = f"{MARKDOWN_DIR}/{filename}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


@router.post("/{meeting_id}/save-markdown")
def save_markdown(meeting_id: int, db: Session = Depends(get_db)):
    """회의록을 마크다운 파일로 저장"""
    meeting, items = _meeting_and_items(meeting_id, db)
    try:
        md_content = build_markdown(meeting, items)
        file_path = _write_markdown(meeting_id, md_content)

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
            "preview": md_content,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{meeting_id}/download-markdown")
def download_markdown(meeting_id: int, db: Session = Depends(get_db)):
    """마크다운 파일 다운로드 (항상 최신 내용으로 생성)"""
    meeting, items = _meeting_and_items(meeting_id, db)
    try:
        md_content = build_markdown(meeting, items)
        file_path = _write_markdown(meeting_id, md_content)
        return FileResponse(
            path=file_path,
            filename=os.path.basename(file_path),
            media_type="text/markdown",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
