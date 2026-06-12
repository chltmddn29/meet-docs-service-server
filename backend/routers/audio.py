from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript
from pydantic import BaseModel
from datetime import datetime
import os
import shutil

router = APIRouter(prefix="/api", tags=["audio"])

AUDIO_DIR = "audio"

class AudioResponse(BaseModel):
    transcript_id: int
    meeting_id: int
    audio_file_path: str
    
    class Config:
        from_attributes = True

# 1. 음성 파일 업로드
@router.post("/meetings/{meeting_id}/upload-audio", response_model=AudioResponse)
async def upload_audio(
    meeting_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """음성 파일 업로드"""
    try:
        os.makedirs(AUDIO_DIR, exist_ok=True)
        file_path = f"{AUDIO_DIR}/meeting_{meeting_id}_{file.filename}"
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        transcript = Transcript(
            meeting_id=meeting_id,
            audio_file_path=file_path,
        )
        db.add(transcript)
        db.commit()
        db.refresh(transcript)
        
        return transcript
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# 2. 음성 파일 목록 조회 (경로 변경!)
@router.get("/audio-files")
def get_audio_files(db: Session = Depends(get_db)):
    """모든 음성 파일 조회"""
    files = db.query(Transcript).all()
    return files

# 3. 특정 음성 파일 삭제
@router.delete("/audio-files/{transcript_id}")
def delete_audio_file(transcript_id: int, db: Session = Depends(get_db)):
    """음성 파일 삭제"""
    transcript = db.query(Transcript).filter(
        Transcript.transcript_id == transcript_id
    ).first()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Audio file not found")
    
    if os.path.exists(transcript.audio_file_path):
        os.remove(transcript.audio_file_path)
    
    db.delete(transcript)
    db.commit()
    
    return {"message": "Audio file deleted"}