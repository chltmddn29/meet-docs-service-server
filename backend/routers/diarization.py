"""오디오 기반 화자분리(speaker diarization).

pyannote.audio의 사전학습 파이프라인으로 "누가 언제 말했는지"를 오디오 신호에서
직접 구분한다(텍스트 추측이 아님). 모델 가중치는 HuggingFace에 gated 상태라
무료 계정 + 약관 동의 + 토큰(HF_TOKEN)이 필요하다.

토큰이 없거나 모델 로드/추론이 실패해도 서버가 죽지 않고 None을 반환한다.
호출부는 화자 라벨 없는 기존 방식으로 폴백한다.
"""
import os
import logging

logger = logging.getLogger(__name__)

HF_TOKEN = os.getenv("HF_TOKEN")

if not HF_TOKEN:
    logger.warning(
        "HF_TOKEN 환경변수가 없습니다. 화자분리 기능이 동작하지 않습니다 "
        "(음성 인식 자체는 정상 동작하며, 화자 라벨만 안 붙습니다)."
    )

_pipeline = None
_load_failed = False


def _get_pipeline():
    """프로세스당 1회만 로드되는 lazy singleton. 실패하면 이후 호출도 바로 None."""
    global _pipeline, _load_failed
    if _pipeline is not None:
        return _pipeline
    if _load_failed or not HF_TOKEN:
        return None
    try:
        from pyannote.audio import Pipeline
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=HF_TOKEN
        )
        logger.info("pyannote 화자분리 파이프라인 로드 완료")
    except Exception as e:
        logger.warning("pyannote 파이프라인 로드 실패, 화자분리 비활성: %s", e)
        _load_failed = True
        return None
    return _pipeline


def diarize(wav_path: str) -> list[tuple[float, float, str]] | None:
    """16kHz mono WAV를 화자 구간 리스트로 변환.

    반환: [(start_sec, end_sec, "화자1"), ...] (start 순 정렬).
    화자 라벨은 pyannote의 SPEAKER_00류 내부 ID를 등장 순서 기준
    "화자1", "화자2", ...로 정규화한 것.
    실패(토큰 없음/로드 실패/추론 오류) 시 None.
    """
    pipeline = _get_pipeline()
    if pipeline is None:
        return None
    try:
        diarization = pipeline(wav_path)
    except Exception as e:
        logger.warning("화자분리 추론 실패: %s", e)
        return None

    label_order: dict[str, str] = {}
    segments: list[tuple[float, float, str]] = []
    for turn, _, raw_speaker in diarization.itertracks(yield_label=True):
        if raw_speaker not in label_order:
            label_order[raw_speaker] = f"화자{len(label_order) + 1}"
        segments.append((turn.start, turn.end, label_order[raw_speaker]))

    segments.sort(key=lambda s: s[0])
    return segments
