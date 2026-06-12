from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from models import User, Meeting, Transcript, MeetingAgendaItem, PlatformSave
from routers import meetings, stt, audio, ai, markdown


# 데이터베이스 테이블 생성
Base.metadata.create_all(bind=engine)

# FastAPI 앱 생성
app = FastAPI(title="MeetDocs API", version="0.1.0")
app.include_router(meetings.router)
app.include_router(audio.router)
app.include_router(stt.router)
app.include_router(ai.router)
app.include_router(markdown.router)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "MeetDocs backend is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
