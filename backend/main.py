from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from models import User, Meeting, Transcript, MeetingAgendaItem, PlatformSave, Template
from routers import meetings, stt, audio, ai, markdown, pdf, docx, notion, preview, templates


# 데이터베이스 테이블 생성
Base.metadata.create_all(bind=engine)

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

