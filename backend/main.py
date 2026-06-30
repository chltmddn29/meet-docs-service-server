import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from database import Base, engine
from models import User, Meeting, Transcript, MeetingAgendaItem, PlatformSave, Template, FormatTemplate
from routers import meetings, stt, audio, ai, markdown, pdf, docx, notion, preview, templates, format_templates, todos, hwpx, action_items

# 앱 모듈(routers.*)의 INFO 로그가 HF Space 로그에 보이도록 설정.
# uvicorn이 먼저 로깅을 구성하면 basicConfig가 no-op이 되므로 force=True로 강제 적용,
# 추가로 root/routers 로거 레벨을 명시적으로 INFO로 내린다(전부 묻히는 것 방지).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("routers").setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.info("INFO 로깅 활성화됨 — 단계별 타이밍 로그가 출력됩니다")

# 데이터베이스 테이블 생성
Base.metadata.create_all(bind=engine)


def _run_lightweight_migrations():
    """create_all()은 기존 테이블에 새 컬럼을 추가하지 않으므로,
    누락된 컬럼을 시작 시 안전하게 보충한다(idempotent)."""
    is_sqlite = engine.url.get_backend_name() == "sqlite"
    blob_type = "BLOB" if is_sqlite else "BYTEA"

    # 테이블별 추가해야 할 컬럼 (없을 때만 ADD)
    migrations = {
        "transcripts": {
            "audio_data": f"ALTER TABLE transcripts ADD COLUMN audio_data {blob_type}",
            "audio_filename": "ALTER TABLE transcripts ADD COLUMN audio_filename VARCHAR",
            "process_status": "ALTER TABLE transcripts ADD COLUMN process_status VARCHAR",
            "process_error": "ALTER TABLE transcripts ADD COLUMN process_error TEXT",
        },
        "meeting_agenda_items": {
            "discussions": "ALTER TABLE meeting_agenda_items ADD COLUMN discussions TEXT",
            "completed_items": "ALTER TABLE meeting_agenda_items ADD COLUMN completed_items TEXT",
            "speaker_points": "ALTER TABLE meeting_agenda_items ADD COLUMN speaker_points TEXT",
            "action_checked": "ALTER TABLE meeting_agenda_items ADD COLUMN action_checked TEXT",
        },
    }

    try:
        inspector = inspect(engine)
    except Exception as e:
        logger.warning("마이그레이션 검사 건너뜀: %s", e)
        return

    with engine.begin() as conn:
        for table, cols in migrations.items():
            try:
                existing = {c["name"] for c in inspector.get_columns(table)}
            except Exception as e:
                logger.warning("테이블 검사 건너뜀(%s): %s", table, e)
                continue
            for col, ddl in cols.items():
                if col not in existing:
                    try:
                        conn.execute(text(ddl))
                        logger.info("컬럼 추가: %s.%s", table, col)
                    except Exception as e:
                        logger.warning("컬럼 추가 실패(%s.%s): %s", table, col, e)


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
app.include_router(todos.router)
app.include_router(hwpx.router)
app.include_router(action_items.router)

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

@app.get("/")
def root():
    return {"status": "ok", "service": "MeetDocs API"}


@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "MeetDocs backend is running"}

if __name__ == "__main__":
    import uvicorn
    import os
    # HF Spaces는 기본 7860, Render 등은 PORT 환경변수를 줌
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)

