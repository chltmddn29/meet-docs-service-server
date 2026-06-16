import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from database import Base, engine
from models import User, Meeting, Transcript, MeetingAgendaItem, PlatformSave, Template, FormatTemplate
from routers import meetings, stt, audio, ai, markdown, pdf, docx, notion, preview, templates, format_templates

logger = logging.getLogger(__name__)

# 데이터베이스 테이블 생성
Base.metadata.create_all(bind=engine)


def _run_lightweight_migrations():
    """create_all()은 기존 테이블에 새 컬럼을 추가하지 않으므로,
    누락된 컬럼을 시작 시 안전하게 보충한다(idempotent)."""
    # transcripts 테이블에 오디오 영속화 컬럼이 없으면 추가
    is_sqlite = engine.url.get_backend_name() == "sqlite"
    blob_type = "BLOB" if is_sqlite else "BYTEA"
    needed = {
        "audio_data": f"ALTER TABLE transcripts ADD COLUMN audio_data {blob_type}",
        "audio_filename": "ALTER TABLE transcripts ADD COLUMN audio_filename VARCHAR",
    }
    try:
        inspector = inspect(engine)
        existing = {c["name"] for c in inspector.get_columns("transcripts")}
    except Exception as e:
        logger.warning("마이그레이션 검사 건너뜀: %s", e)
        return

    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in existing:
                try:
                    conn.execute(text(ddl))
                    logger.info("컬럼 추가: transcripts.%s", col)
                except Exception as e:
                    logger.warning("컬럼 추가 실패(%s): %s", col, e)


_run_lightweight_migrations()

# FastAPI 앱 생성
app = FastAPI(title="MeetDocs API", version="0.1.0")
app.include_router(meetings.router)
app.include_router(audio.router)
app.include_router(stt.router)
app.include_router(ai.router)
app.include_router(markdown.router)
app.include_router(pdf.router)
app.include_router(docx.router)
app.include_router(notion.router)
app.include_router(preview.router)
app.include_router(templates.router)
app.include_router(format_templates.router)

# CORS 설정
# allow_origins=["*"] 와 allow_credentials=True 는 함께 쓸 수 없음(브라우저가 거부).
# 이 API는 쿠키/인증을 쓰지 않으므로 credentials=False 로 두고 모든 출처 허용.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "MeetDocs backend is running"}

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=8000)

