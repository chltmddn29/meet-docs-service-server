import os
import logging
from dotenv import load_dotenv
from groq import Groq

logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_api_key = os.getenv("GROQ_API_KEY")

# 키가 없어도 import 시점에 죽지 않고 경고만 남긴다.
# 실제 호출 시 ensure_client()로 명확한 메시지를 준다.
if not _api_key:
    logger.warning(
        "GROQ_API_KEY 환경변수가 없습니다. STT/AI 기능이 동작하지 않습니다."
    )

client = Groq(api_key=_api_key) if _api_key else None

# 회의 보정·분석에 쓰는 텍스트 모델.
# Groq 무료 호스팅 중 가장 크고 강력한 모델(OpenAI 오픈 웨이트 120B).
# 모델 교체는 이 한 줄만 바꾸면 됨.
TEXT_MODEL = "openai/gpt-oss-120b"


def ensure_client():
    """Groq client가 준비됐는지 확인. 없으면 503으로 명확히 알린다."""
    if client is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="AI 서비스가 설정되지 않았습니다 (GROQ_API_KEY 누락).",
        )
    return client
