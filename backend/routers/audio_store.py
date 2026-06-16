"""오디오 영속화 헬퍼.

Render 무료 디스크는 재시작 시 사라지므로, 오디오 바이트를 DB에도 저장한다.
디스크는 빠른 접근용 캐시로만 쓰고, 없으면 DB에서 복원한다.
"""
import os

AUDIO_DIR = "audio"


def _path_for(meeting_id: int, filename: str) -> str:
    safe = (filename or "audio.webm").replace("/", "_").replace("\\", "_")
    return f"{AUDIO_DIR}/meeting_{meeting_id}_{safe}"


def save_audio(transcript, data: bytes, filename: str) -> str:
    """DB(영구) + 디스크(캐시) 양쪽에 저장하고 경로를 반환."""
    transcript.audio_data = data
    transcript.audio_filename = filename
    os.makedirs(AUDIO_DIR, exist_ok=True)
    path = _path_for(transcript.meeting_id, filename)
    with open(path, "wb") as f:
        f.write(data)
    transcript.audio_file_path = path
    return path


def ensure_local_file(transcript) -> str | None:
    """디스크에 파일이 있으면 그 경로, 없으면 DB 바이트로 복원해 경로 반환.
    둘 다 없으면 None."""
    path = transcript.audio_file_path
    if path and os.path.exists(path):
        return path

    data = getattr(transcript, "audio_data", None)
    if not data:
        return None  # DB에도 없으면 복원 불가

    os.makedirs(AUDIO_DIR, exist_ok=True)
    fname = transcript.audio_filename or f"meeting_{transcript.meeting_id}.webm"
    path = _path_for(transcript.meeting_id, fname)
    with open(path, "wb") as f:
        f.write(data)
    transcript.audio_file_path = path
    return path
