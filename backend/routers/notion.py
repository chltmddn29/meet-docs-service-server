from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem, PlatformSave
from notion_client import Client
from datetime import timezone, timedelta
import json
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

router = APIRouter(prefix="/api/meetings", tags=["notion"])

_NOTION_TOKEN = os.getenv("NOTION_TOKEN")
PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID")
notion = Client(auth=_NOTION_TOKEN) if _NOTION_TOKEN else None
KST = timezone(timedelta(hours=9))  # 한국 시간


def _safe_actions(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


@router.post("/{meeting_id}/save-notion")
def save_notion(meeting_id: int, db: Session = Depends(get_db)):
    """회의록을 노션 페이지로 저장"""
    # 노션 연동 설정 확인 → 미설정이면 명확히 안내
    if notion is None or not PARENT_PAGE_ID:
        raise HTTPException(
            status_code=503,
            detail="노션 연동이 설정되지 않았어요 (NOTION_TOKEN / NOTION_PARENT_PAGE_ID 필요).",
        )

    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    items = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.meeting_id == meeting_id
    ).order_by(MeetingAgendaItem.order).all()

    if not items:
        raise HTTPException(status_code=400, detail="No agenda items found")

    # 회의 생성 시각(UTC 저장) → 한국 시간 변환
    created = meeting.created_at.replace(tzinfo=timezone.utc).astimezone(KST)
    date_str = created.strftime("%Y-%m-%d %H:%M")

    # 노션 블록 구성
    children = []

    # 날짜 + 참석자 헤더
    children.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": f"📅 {date_str}"}}]
        },
    })
    if meeting.participants:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": f"👥 참석자: {meeting.participants}"}}]
            },
        })
    children.append({"object": "block", "type": "divider", "divider": {}})

    for item in items:
        # 안건 제목
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": f"{item.order}. {item.agenda}"}}]
            },
        })
        # 결정사항
        if item.decision:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"결정: {item.decision}"}}]
                },
            })
        # 할 일 (체크박스)
        actions = _safe_actions(item.action_items)
        if actions:
            for a in actions:
                children.append({
                    "object": "block",
                    "type": "to_do",
                    "to_do": {
                        "rich_text": [{"type": "text", "text": {"content": a}}],
                        "checked": False,
                    },
                })

    try:
        new_page = notion.pages.create(
            parent={"page_id": PARENT_PAGE_ID},
            properties={
                "title": {
                    "title": [{"type": "text", "text": {"content": meeting.title}}]
                }
            },
            children=children,
        )

        page_url = new_page.get("url", "")

        db.add(PlatformSave(
            meeting_id=meeting_id,
            platform="notion",
            save_status="success",
            platform_doc_id=page_url,
        ))
        db.commit()

        return {
            "meeting_id": meeting_id,
            "status": "success",
            "notion_url": page_url,
        }

    except Exception as e:
        db.add(PlatformSave(
            meeting_id=meeting_id,
            platform="notion",
            save_status="failed",
            error_message=str(e),
        ))
        db.commit()
        raise HTTPException(status_code=400, detail=str(e))