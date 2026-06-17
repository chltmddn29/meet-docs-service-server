"""할 일 체크 토글 — 안건 항목의 action_items 각각의 완료 여부를 저장."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import MeetingAgendaItem
from pydantic import BaseModel
import json

router = APIRouter(prefix="/api/agenda-items", tags=["action-items"])


class ToggleRequest(BaseModel):
    index: int
    checked: bool


def _load_list(raw):
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


@router.put("/{item_id}/action-check")
def toggle_action_check(item_id: int, body: ToggleRequest, db: Session = Depends(get_db)):
    """할 일 체크 상태 토글. action_checked(bool 배열)를 action_items 길이에 맞춰 갱신."""
    item = db.query(MeetingAgendaItem).filter(
        MeetingAgendaItem.item_id == item_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다")

    actions = _load_list(item.action_items)
    if body.index < 0 or body.index >= len(actions):
        raise HTTPException(status_code=400, detail="잘못된 할 일 인덱스입니다")

    checked = _load_list(item.action_checked)
    # action_items 길이에 맞춰 bool 배열 정규화
    checked = [bool(checked[i]) if i < len(checked) else False
               for i in range(len(actions))]
    checked[body.index] = bool(body.checked)

    item.action_checked = json.dumps(checked)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"저장 실패: {e}")

    return {"item_id": item_id, "action_checked": checked}
