"""할 일 모아보기 — 모든 회의의 할 일(action_items)·한 일(completed_items)을 집계."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import Meeting, MeetingAgendaItem
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/todos", tags=["todos"])


def _load_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


@router.get("")
def get_todos(db: Session = Depends(get_db)):
    """회의별로 할 일/한 일을 묶어 최신순으로 반환.
    할 일 또는 한 일이 하나라도 있는 회의만 포함."""
    meetings = db.query(Meeting).order_by(Meeting.created_at.desc()).all()

    result = []
    total_pending = 0
    total_done = 0
    for m in meetings:
        items = db.query(MeetingAgendaItem).filter(
            MeetingAgendaItem.meeting_id == m.meeting_id
        ).order_by(MeetingAgendaItem.order).all()

        pending = []   # 할 일
        done = []      # 한 일
        for it in items:
            for a in _load_list(it.action_items):
                pending.append({"agenda": it.agenda, "text": a})
            for c in _load_list(getattr(it, "completed_items", None)):
                done.append({"agenda": it.agenda, "text": c})

        if not pending and not done:
            continue

        total_pending += len(pending)
        total_done += len(done)
        result.append({
            "meeting_id": m.meeting_id,
            "meeting_title": m.title,
            "created_at": m.created_at.isoformat(),
            "pending": pending,
            "done": done,
        })

    return {
        "total_pending": total_pending,
        "total_done": total_done,
        "meetings": result,
    }
