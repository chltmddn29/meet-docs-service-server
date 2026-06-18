from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, PlatformSave
from routers.doc_content import item_sections
from datetime import datetime, timezone, timedelta
import os
import textwrap

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

router = APIRouter(prefix="/api/meetings", tags=["pdf"])

PDF_DIR = "pdf"
KST = timezone(timedelta(hours=9))

# 페이지 폭을 넘지 않도록 긴 한글 줄을 적당히 끊는 기준(대략)
WRAP_WIDTH = 38

# 폰트 경로 후보: 맥(AppleGothic) → 동봉 폰트 → 리눅스 시스템 나눔(도커 설치)
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    os.path.join(os.path.dirname(__file__), '..', 'fonts', 'NanumGothic.ttf'),
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]
FONT_PATH = next(
    (p for p in _FONT_CANDIDATES if os.path.exists(p)),
    _FONT_CANDIDATES[-1],
)

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

    created = meeting.created_at.replace(tzinfo=timezone.utc).astimezone(KST)
    date_str = created.strftime("%Y-%m-%d %H:%M")

    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4
    y = height - 30 * mm

    def line(text, size=11, gap=7, bold=False):
        nonlocal y
        if y < 30 * mm:
            c.showPage()
            y = height - 30 * mm
        c.setFont("Korean", size)
        c.drawString(25 * mm, y, text)
        y -= gap * mm

    # 제목 + 날짜 + 참석자
    line(meeting.title, size=18, gap=10)
    line(date_str, size=10, gap=6)
    if meeting.participants:
        line(f"참석자: {meeting.participants}", size=10, gap=10)
    else:
        y -= 4 * mm

    # 안건 (내용·주요 의견·결정·한 일·할 일 모두 출력)
    for item in items:
        line(f"{item.order}. {item.agenda}", size=14, gap=8)
        for label, body in item_sections(item):
            if isinstance(body, list):
                line(f"  [{label}]", size=11, gap=6)
                for b in body:
                    for seg in textwrap.wrap(f"- {b}", WRAP_WIDTH) or ["-"]:
                        line(f"   {seg}", size=11, gap=6)
            else:
                line(f"  [{label}]", size=11, gap=6)
                for seg in textwrap.wrap(str(body), WRAP_WIDTH) or [""]:
                    line(f"   {seg}", size=11, gap=6)
        y -= 4 * mm

    c.save()

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
    """PDF 다운로드 (없으면 자동 생성)"""
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

    created = meeting.created_at.replace(tzinfo=timezone.utc).astimezone(KST)
    date_str = created.strftime("%Y-%m-%d %H:%M")

    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4
    y = height - 30 * mm

    def line(text, size=11, gap=7, bold=False):
        nonlocal y
        if y < 30 * mm:
            c.showPage()
            y = height - 30 * mm
        c.setFont("Korean", size)
        c.drawString(25 * mm, y, text)
        y -= gap * mm

    # 제목 + 날짜 + 참석자
    line(meeting.title, size=18, gap=10)
    line(date_str, size=10, gap=6)
    if meeting.participants:
        line(f"참석자: {meeting.participants}", size=10, gap=10)
    else:
        y -= 4 * mm

    # 안건 (내용·주요 의견·결정·한 일·할 일 모두 출력)
    for item in items:
        line(f"{item.order}. {item.agenda}", size=14, gap=8)
        for label, body in item_sections(item):
            if isinstance(body, list):
                line(f"  [{label}]", size=11, gap=6)
                for b in body:
                    for seg in textwrap.wrap(f"- {b}", WRAP_WIDTH) or ["-"]:
                        line(f"   {seg}", size=11, gap=6)
            else:
                line(f"  [{label}]", size=11, gap=6)
                for seg in textwrap.wrap(str(body), WRAP_WIDTH) or [""]:
                    line(f"   {seg}", size=11, gap=6)
        y -= 4 * mm

    c.save()

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/pdf",
    )