from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, PlatformSave
from datetime import datetime
import json
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

router = APIRouter(prefix="/api/meetings", tags=["pdf"])

PDF_DIR = "pdf"
FONT_PATH = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"

# 한글 폰트 등록 (한 번만)
pdfmetrics.registerFont(TTFont("Korean", FONT_PATH))


@router.post("/{meeting_id}/save-pdf")
def save_pdf(meeting_id: int, db: Session = Depends(get_db)):
    """회의록을 PDF 파일로 저장"""
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()

    if not items:
        raise HTTPException(status_code=400, detail="No agenda items found")

    os.makedirs(PDF_DIR, exist_ok=True)
    filename = f"meeting_{meeting_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    file_path = f"{PDF_DIR}/{filename}"

    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4
    y = height - 30 * mm

    def line(text, size=11, gap=7, bold=False):
        nonlocal y
        if y < 30 * mm:  # 페이지 넘침 처리
            c.showPage()
            y = height - 30 * mm
        c.setFont("Korean", size)
        c.drawString(25 * mm, y, text)
        y -= gap * mm

    # 제목
    line(meeting.title, size=18, gap=10)
    line(datetime.now().strftime("%Y-%m-%d %H:%M"), size=10, gap=10)

    # 안건
    for item in items:
        line(f"{item.order}. {item.agenda}", size=14, gap=8)
        if item.decision:
            line(f"  결정: {item.decision}", size=11, gap=6)
        if item.action_items:
            actions = json.loads(item.action_items)
            for a in actions:
                line(f"  - {a}", size=11, gap=6)
        y -= 4 * mm

    c.save()

    # DB 기록
    db.add(PlatformSave(
        meeting_id=meeting_id,
        platform="pdf",
        save_status="success",
        platform_doc_id=file_path,
    ))
    db.commit()

    return {"meeting_id": meeting_id, "status": "success", "file_path": file_path}


@router.get("/{meeting_id}/download-pdf")
def download_pdf(meeting_id: int, db: Session = Depends(get_db)):
    """PDF 다운로드"""
    save = db.query(PlatformSave).filter(
        PlatformSave.meeting_id == meeting_id,
        PlatformSave.platform == "pdf",
        PlatformSave.save_status == "success",
    ).order_by(PlatformSave.saved_at.desc()).first()

    if not save or not os.path.exists(save.platform_doc_id):
        raise HTTPException(status_code=404, detail="PDF file not found")

    return FileResponse(
        path=save.platform_doc_id,
        filename=os.path.basename(save.platform_doc_id),
        media_type="application/pdf",
    )