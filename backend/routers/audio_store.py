"""오디오 영속화 헬퍼.

Render 무료 디스크는 재시작 시 사라지므로, 오디오 바이트를 DB에도 저장한다.
디스크는 빠른 접근용 캐시로만 쓰고, 없으면 DB에서 복원한다.
DB가 진실 원본이므로 디스크 쓰기 실패는 치명적이지 않다.
"""
import os
import logging

logger = logging.getLogger(__name__)

AUDIO_DIR = "audio"


def _path_for(meeting_id: int, filename: str) -> str:
    safe = (filename or "audio.webm").replace("/", "_").replace("\\", "_")
    return f"{AUDIO_DIR}/meeting_{meeting_id}_{safe}"


def _try_write(path: str, data: bytes) -> bool:
    """디스크에 best-effort 쓰기. 실패해도 예외를 던지지 않는다."""
    try:
        os.makedirs(AUDIO_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return True
    except OSError as e:
        logger.warning("오디오 디스크 캐시 쓰기 실패(%s): %s", path, e)
        return False


def save_audio(transcript, data: bytes, filename: str) -> str:
    """DB(영구)에 저장하고 디스크(캐시)에도 best-effort로 쓴다."""
    transcript.audio_data = data
    transcript.audio_filename = filename
    path = _path_for(transcript.meeting_id, filename)
    # 디스크 캐시는 실패해도 무방(다음 접근 시 DB에서 복원)
    transcript.audio_file_path = path if _try_write(path, data) else None
    return path


def ensure_local_file(transcript) -> str | None:
    """디스크에 파일이 있으면 그 경로, 없으면 DB 바이트로 복원해 경로 반환.
    둘 다 없거나 복원 실패 시 None."""
    path = transcript.audio_file_path
    if path and os.path.exists(path):
        return path

    data = getattr(transcript, "audio_data", None)
    if not data:
        return None  # DB에도 없으면 복원 불가

    fname = transcript.audio_filename or f"meeting_{transcript.meeting_id}.webm"
    restored = _path_for(transcript.meeting_id, fname)
    if not _try_write(restored, data):
        return None  # 디스크 복원 실패
    transcript.audio_file_path = restored
    return restored
