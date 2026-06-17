from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from database import get_db
from models import FormatTemplate, Transcript, Meeting
from routers.groq_client import client, TEXT_MODEL
from pydantic import BaseModel
from io import BytesIO
import os

router = APIRouter(prefix="/api/format-templates", tags=["format-templates"])


def _extract_text(filename: str, data: bytes) -> str:
    """업로드 파일에서 본문 텍스트 추출 (docx/md/txt 지원)"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md"):
        return data.decode("utf-8", errors="ignore")
    if ext == ".docx":
        from docx import Document
        doc = Document(BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        # 표 안의 텍스트도 포함
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    raise HTTPException(
        status_code=400,
        detail=f"지원하지 않는 형식입니다: {ext or '확장자 없음'} (docx/md/txt만 가능)",
    )


def _serialize(t: FormatTemplate, preview: bool = False) -> dict:
    content = t.content or ""
    return {
        "format_template_id": t.format_template_id,
        "name": t.name,
        "content": content[:200] if preview else content,
        "source_filename": t.source_filename,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# 1. 서식 템플릿 목록 (본문은 미리보기 200자)
@router.get("")
def list_format_templates(db: Session = Depends(get_db)):
    rows = (
        db.query(FormatTemplate)
        .order_by(FormatTemplate.created_at.desc())
        .all()
    )
    return [_serialize(t, preview=True) for t in rows]


# 2. 파일 업로드 → 텍스트 추출 → 서식 템플릿 생성
@router.post("/upload")
async def upload_format_template(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    data = await file.read()
    text = _extract_text(file.filename or "", data)
    if not text.strip():
        raise HTTPException(status_code=400, detail="파일에서 텍스트를 추출하지 못했습니다")

    name = os.path.splitext(file.filename or "서식")[0]
    t = FormatTemplate(name=name, content=text, source_filename=file.filename)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize(t, preview=True)


# 3. 서식 템플릿 삭제
@router.delete("/{format_template_id}")
def delete_format_template(format_template_id: int, db: Session = Depends(get_db)):
    t = (
        db.query(FormatTemplate)
        .filter(FormatTemplate.format_template_id == format_template_id)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Format template not found")
    db.delete(t)
    db.commit()
    return {"message": "Format template deleted"}


class GenerateRequest(BaseModel):
    meeting_id: int
    format_template_id: int


# 4. 회의 원본을 서식 템플릿 형식대로 AI로 생성
@router.post("/generate")
def generate_formatted(req: GenerateRequest, db: Session = Depends(get_db)):
    """회의 transcript를 서식 템플릿 형식에 맞춰 AI 회의록으로 생성 (저장하지 않고 반환)"""
    template = (
        db.query(FormatTemplate)
        .filter(FormatTemplate.format_template_id == req.format_template_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Format template not found")

    meeting = (
        db.query(Meeting).filter(Meeting.meeting_id == req.meeting_id).first()
    )
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    transcript = (
        db.query(Transcript)
        .filter(Transcript.meeting_id == req.meeting_id)
        .first()
    )
    raw_text = transcript.raw_text if transcript else None
    if not raw_text:
        raise HTTPException(
            status_code=400, detail="회의 원본 텍스트가 없습니다 (먼저 음성 처리 필요)"
        )

    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 회의록 작성 AI입니다. 사용자가 제공한 '예시 서식' 문서의 "
                        "구조·제목 체계·섹션 구성·말투를 그대로 본떠서, 이번 회의 내용을 같은 형식의 "
                        "회의록으로 작성하세요. 결과는 마크다운으로만 출력하고 설명이나 코드블록 표시(```)는 넣지 마세요."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""[예시 서식 — 이 형식과 구조를 그대로 따라 작성]
{template.content}

[이번 회의 제목]
{meeting.title}

[이번 회의 원본 내용(STT)]
{raw_text}

위 '예시 서식'과 동일한 구조·스타일로, 이번 회의 내용을 채워 완성된 회의록을 마크다운으로 작성해주세요.""",
                },
            ],
            temperature=0.4,
        )

        content = response.choices[0].message.content.strip()
        # 혹시 코드블록으로 감싸오면 제거
        if content.startswith("```"):
            lines = content.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        return {
            "meeting_id": req.meeting_id,
            "format_template_id": req.format_template_id,
            "formatted": content,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
