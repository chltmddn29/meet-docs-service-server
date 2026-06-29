from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db, SessionLocal
from models import Transcript, Meeting
from routers.audio_store import ensure_local_file, MAX_DB_BLOB_BYTES
from pydantic import BaseModel
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["audio"])

# 업로드 허용 상한. 25MB를 넘는 파일도 변환 단계에서 자동 분할(청크)되므로 넉넉히 허용.
# 단, 파일 전체를 메모리에 올리지 않고 디스크로 스트리밍 저장하므로 서버가 죽지 않는다.
MAX_AUDIO_BYTES = 500 * 1024 * 1024  # 500MB
# 디스크로 흘려보낼 때의 청크 크기(1MB) — 이 크기 이상은 한 번에 메모리에 올리지 않음.
UPLOAD_CHUNK_BYTES = 1024 * 1024


class AudioResponse(BaseModel):
    transcript_id: int
    meeting_id: int
    audio_file_path: str

    class Config:
        from_attributes = True


# 1. 음성 파일 업로드 (디스크 저장 후 즉시 응답, DB 영속화는 백그라운드)
@router.post("/meetings/{meeting_id}/upload-audio", response_model=AudioResponse)
async def upload_audio(
    meeting_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """음성 파일 업로드.
    파일을 메모리에 통째로 올리지 않고 디스크로 청크 스트리밍 저장한다(서버 OOM 방지).
    상한을 넘기면 받던 파일을 지우고 즉시 거부한다.
    DB 영구 보관(BLOB)은 응답 뒤 백그라운드로 처리해 업로드 체감 시간을 줄인다
    (원격 DB로의 2차 전송을 임계경로에서 제거). 변환은 디스크 파일로 진행되므로 영향 없음.
    """
    filename = file.filename or "audio.webm"
    try:
        path, size = await _stream_to_disk(file, meeting_id, filename)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 읽기 실패: {e}")

    if size == 0:
        try:
            os.remove(path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="빈 파일입니다 (0 bytes)")

    try:
        # 디스크 경로만으로 먼저 레코드 생성(빠름). DB BLOB는 백그라운드에서 채운다.
        transcript = Transcript(
            meeting_id=meeting_id,
            audio_filename=filename,
            audio_file_path=path,
        )
        db.add(transcript)
        db.commit()
        db.refresh(transcript)
    except Exception as e:
        db.rollback()
        logger.exception("오디오 업로드 저장 실패")
        raise HTTPException(status_code=500, detail=f"저장 실패: {e}")

    # 영구 보관용 BLOB 저장은 응답 후 백그라운드로 (재시작 대비)
    background_tasks.add_task(_persist_audio_blob, transcript.transcript_id, path)
    return transcript


def _persist_audio_blob(transcript_id: int, path: str):
    """업로드 응답 후 백그라운드에서 오디오 바이트를 DB에 영속화(재시작 후 복원용).
    너무 크면(>MAX_DB_BLOB_BYTES) 생략하고 디스크에만 둔다."""
    db = SessionLocal()
    try:
        t = db.query(Transcript).filter(
            Transcript.transcript_id == transcript_id
        ).first()
        if not t:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size > MAX_DB_BLOB_BYTES:
            logger.info("큰 오디오(%dMB) DB BLOB 생략, 디스크 보관", size // (1024 * 1024))
            return
        with open(path, "rb") as f:
            t.audio_data = f.read()
        db.commit()
    except Exception as e:
        logger.warning("오디오 BLOB 백그라운드 저장 실패(tid=%s): %s", transcript_id, e)
        db.rollback()
    finally:
        db.close()


async def _stream_to_disk(file: UploadFile, meeting_id: int, filename: str):
    """업로드 스트림을 청크 단위로 디스크에 쓴다. (경로, 총바이트)를 반환.
    상한 초과 시 부분 파일을 지우고 ValueError를 던진다."""
    from routers.audio_store import path_for, AUDIO_DIR
    os.makedirs(AUDIO_DIR, exist_ok=True)
    path = path_for(meeting_id, filename)
    total = 0
    with open(path, "wb") as out:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                out.close()
                try:
                    os.remove(path)
                except OSError:
                    pass
                raise ValueError(
                    f"파일이 너무 큽니다 (최대 {MAX_AUDIO_BYTES//(1024*1024)}MB)"
                )
            out.write(chunk)
    return path, total


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


# 5. 음성으로 새 회의 만들기 — 회의가 삭제돼 고아가 된 음성을 다시 회의록으로 생성할 때 사용
@router.post("/audio-files/{transcript_id}/new-meeting")
def new_meeting_from_audio(transcript_id: int, db: Session = Depends(get_db)):
    """음성에 연결된 회의가 없으면(삭제됨) 새 회의를 만들어 연결하고 meeting_id를 돌려준다.
    이미 연결된 회의가 있으면 그 meeting_id를 그대로 반환한다."""
    transcript = db.query(Transcript).filter(
        Transcript.transcript_id == transcript_id
    ).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="음성 기록을 찾을 수 없습니다")

    # 이미 회의가 있으면 그대로 사용
    if transcript.meeting_id:
        meeting = db.query(Meeting).filter(
            Meeting.meeting_id == transcript.meeting_id
        ).first()
        if meeting:
            return {"meeting_id": meeting.meeting_id, "transcript_id": transcript_id}

    title = "재생성된 회의"
    if transcript.audio_filename:
        base = os.path.splitext(transcript.audio_filename)[0].strip()
        if base:
            title = base

    meeting = Meeting(title=title, status="recording")
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    transcript.meeting_id = meeting.meeting_id
    db.commit()
    return {"meeting_id": meeting.meeting_id, "transcript_id": transcript_id}
