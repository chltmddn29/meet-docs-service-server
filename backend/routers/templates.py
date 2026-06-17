from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Template
from pydantic import BaseModel
import json

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    description: str = None
    agenda_items: list[str] = []
    participants: list[str] = []


class TemplateUpdate(BaseModel):
    name: str = None
    description: str = None
    agenda_items: list[str] = None
    participants: list[str] = None


def _safe_list(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _serialize(t: Template) -> dict:
    """Template ORM 객체를 JSON 응답용 dict로 변환"""
    return {
        "template_id": t.template_id,
        "name": t.name,
        "description": t.description,
        "agenda_items": _safe_list(t.agenda_items),
        "participants": t.participants,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# 1. 템플릿 목록 조회
@router.get("")
def get_templates(db: Session = Depends(get_db)):
    """모든 템플릿 조회 (최신순)"""
    templates = db.query(Template).order_by(Template.created_at.desc()).all()
    return [_serialize(t) for t in templates]


# 2. 특정 템플릿 조회
@router.get("/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)):
    """특정 템플릿 조회"""
    t = db.query(Template).filter(Template.template_id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return _serialize(t)


# 3. 템플릿 생성
@router.post("")
def create_template(template: TemplateCreate, db: Session = Depends(get_db)):
    """새 템플릿 생성"""
    if not template.name or not template.name.strip():
        raise HTTPException(status_code=400, detail="템플릿 이름을 입력해주세요")

    agendas = [a.strip() for a in template.agenda_items if a.strip()]
    db_template = Template(
        name=template.name.strip(),
        description=template.description,
        agenda_items=json.dumps(agendas, ensure_ascii=False),
        participants=", ".join(template.participants) if template.participants else None,
    )
    try:
        db.add(db_template)
        db.commit()
        db.refresh(db_template)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"템플릿 저장 실패: {e}")
    return _serialize(db_template)


# 4. 템플릿 수정
@router.put("/{template_id}")
def update_template(template_id: int, template: TemplateUpdate, db: Session = Depends(get_db)):
    """템플릿 수정 (전달된 필드만 갱신)"""
    t = db.query(Template).filter(Template.template_id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.name is not None:
        t.name = template.name
    if template.description is not None:
        t.description = template.description
    if template.agenda_items is not None:
        agendas = [a.strip() for a in template.agenda_items if a.strip()]
        t.agenda_items = json.dumps(agendas, ensure_ascii=False)
    if template.participants is not None:
        t.participants = ", ".join(template.participants) if template.participants else None

    try:
        db.commit()
        db.refresh(t)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"템플릿 수정 실패: {e}")
    return _serialize(t)


# 5. 템플릿 삭제
@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """템플릿 삭제"""
    t = db.query(Template).filter(Template.template_id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    try:
        db.delete(t)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"템플릿 삭제 실패: {e}")
    return {"message": "Template deleted"}
