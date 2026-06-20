"""오디오 영속화 헬퍼.

Render 무료 디스크는 재시작 시 사라지므로, 오디오 바이트를 DB에도 저장한다.
디스크는 빠른 접근용 캐시로만 쓰고, 없으면 DB에서 복원한다.
DB가 진실 원본이므로 디스크 쓰기 실패는 치명적이지 않다.
"""
import os
import logging

logger = logging.getLogger(__name__)

AUDIO_DIR = "audio"

# DB BLOB로 보관할 최대 크기. 이보다 큰 파일은 디스크에만 두고 DB엔 바이트를 안 넣어
# 커밋 시 메모리 급증(psycopg2 인코딩)으로 서버가 죽는 것을 막는다.
# (디스크가 휘발돼도 재시작 전까진 접근 가능. 영구 보관이 필요하면 객체 스토리지로.)
MAX_DB_BLOB_BYTES = 30 * 1024 * 1024  # 30MB


def path_for(meeting_id: int, filename: str) -> str:
    safe = (filename or "audio.webm").replace("/", "_").replace("\\", "_")
    return f"{AUDIO_DIR}/meeting_{meeting_id}_{safe}"


# 하위호환 별칭(기존 호출부 보존)
_path_for = path_for


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
    path = path_for(transcript.meeting_id, filename)
    # 디스크 캐시는 실패해도 무방(다음 접근 시 DB에서 복원)
    transcript.audio_file_path = path if _try_write(path, data) else None
    return path


def save_audio_streamed(transcript, disk_path: str, filename: str) -> str:
    """이미 디스크에 저장된 파일을 기준으로 transcript 필드를 채운다.
    파일이 작으면(MAX_DB_BLOB_BYTES 이하) DB에도 바이트를 넣어 재시작 후 복원 가능하게,
    크면 메모리 보호를 위해 디스크 경로만 보관한다(DB BLOB 생략)."""
    transcript.audio_filename = filename
    transcript.audio_file_path = disk_path
    try:
        size = os.path.getsize(disk_path)
    except OSError:
        size = None
    # 작은 파일만 DB에 영속화 → 큰 파일 커밋 시 메모리 폭증/서버 다운 방지
    if size is not None and size <= MAX_DB_BLOB_BYTES:
        try:
            with open(disk_path, "rb") as f:
                transcript.audio_data = f.read()
        except OSError as e:
            logger.warning("DB 영속화용 파일 읽기 실패(%s): %s", disk_path, e)
            transcript.audio_data = None
    else:
        # 큰 파일은 DB에 안 넣음(디스크에만 보관)
        transcript.audio_data = None
        if size is not None:
            logger.info(
                "큰 오디오(%dMB)는 DB BLOB 생략, 디스크 보관: %s",
                size // (1024 * 1024), disk_path,
            )
    return disk_path


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
