from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript
from routers.audio_store import save_audio, ensure_local_file
from pydantic import BaseModel
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["audio"])

# 파일 업로드 크기 제한 (50MB)
MAX_AUDIO_BYTES = 50 * 1024 * 1024


class AudioResponse(BaseModel):
    transcript_id: int
    meeting_id: int
    audio_file_path: str

    class Config:
        from_attributes = True


# 1. 음성 파일 업로드 (DB + 디스크 동시 저장)
@router.post("/meetings/{meeting_id}/upload-audio", response_model=AudioResponse)
async def upload_audio(
    meeting_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """음성 파일 업로드 — 바이트를 DB에 보관해 서버 재시작에도 유지."""
    try:
        data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 읽기 실패: {e}")

    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다 (0 bytes)")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"파일이 너무 큽니다 ({len(data)//(1024*1024)}MB, 최대 50MB)",
        )

    try:
        transcript = Transcript(meeting_id=meeting_id)
        save_audio(transcript, data, file.filename or "audio.webm")
        db.add(transcript)
        db.commit()
        db.refresh(transcript)
        return transcript
    except Exception as e:
        db.rollback()
        logger.exception("오디오 업로드 저장 실패")
        raise HTTPException(status_code=500, detail=f"저장 실패: {e}")


# 2. 음성 파일 목록 조회 — 실제 재생 가능한 것만 반환
@router.get("/audio-files")
def get_audio_files(db: Session = Depends(get_db)):
    """오디오가 실제로 존재하는 기록만 조회.
    DB 바이트가 있거나 디스크 파일이 남아있는 것만 노출 →
    재시작으로 소실돼 복원 불가한 옛 기록은 목록에서 제외.
    BLOB은 length()로 서버측에서만 확인(바이트 전송 안 함)."""
    from sqlalchemy import func

    rows = db.query(
        Transcript.transcript_id,
        Transcript.meeting_id,
        Transcript.audio_file_path,
        func.length(Transcript.audio_data).label("audio_len"),
    ).all()

    result = []
    for r in rows:
        has_db = (r.audio_len or 0) > 0
        has_disk = bool(r.audio_file_path) and os.path.exists(r.audio_file_path)
        if has_db or has_disk:
            result.append({
                "transcript_id": r.transcript_id,
                "meeting_id": r.meeting_id,
                "audio_file_path": r.audio_file_path or "",
            })
    return result


# 3. 음성 스트리밍(재생) / 다운로드 — 디스크에 없으면 DB에서 복원
@router.get("/audio-files/{transcript_id}/download")
def download_audio_file(
    transcript_id: int,
    download: bool = False,
    db: Session = Depends(get_db),
):
    """download=false: inline 재생 / download=true: 파일 다운로드"""
    transcript = db.query(Transcript).filter(
        Transcript.transcript_id == transcript_id
    ).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="음성 기록을 찾을 수 없습니다")

    path = ensure_local_file(transcript)
    if not path:
        raise HTTPException(
            status_code=404,
            detail="음성 파일이 존재하지 않습니다 (업로드 기록은 있으나 데이터 없음)",
        )
    # 복원으로 audio_file_path가 갱신됐을 수 있으니 반영
    db.commit()

    filename = transcript.audio_filename or os.path.basename(path)
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        media_type="audio/webm",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


# 4. 특정 음성 파일 삭제 (DB 레코드 + 디스크 캐시)
@router.delete("/audio-files/{transcript_id}")
def delete_audio_file(transcript_id: int, db: Session = Depends(get_db)):
    """음성 파일 삭제"""
    transcript = db.query(Transcript).filter(
        Transcript.transcript_id == transcript_id
    ).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="음성 기록을 찾을 수 없습니다")

    if transcript.audio_file_path and os.path.exists(transcript.audio_file_path):
        try:
            os.remove(transcript.audio_file_path)
        except OSError:
            pass

    try:
        db.delete(transcript)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"삭제 실패: {e}")

    return {"message": "Audio file deleted"}
