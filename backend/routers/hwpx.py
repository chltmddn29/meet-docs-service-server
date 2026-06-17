from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem
from routers.hwpx_writer import build_hwpx
from datetime import datetime
import os

router = APIRouter(prefix="/api/meetings", tags=["hwpx"])

HWPX_DIR = "hwpx"


@router.get("/{meeting_id}/download-hwpx")
def download_hwpx(meeting_id: int, db: Session = Depends(get_db)):
    """회의록을 HWPX(한컴) 파일로 다운로드 (항상 최신 내용으로 생성)"""
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()

    if not items:
        raise HTTPException(status_code=400, detail="No agenda items found")

    try:
        data = build_hwpx(meeting, items)
        os.makedirs(HWPX_DIR, exist_ok=True)
        filename = f"meeting_{meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.hwpx"
        file_path = f"{HWPX_DIR}/{filename}"
        with open(file_path, "wb") as f:
            f.write(data)
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type="application/hwp+zip",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HWPX 생성 실패: {e}")
