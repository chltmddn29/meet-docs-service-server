from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript
from routers.groq_client import client
import os

router = APIRouter(prefix="/api/meetings", tags=["stt"])


def correct_transcription(text: str) -> str:
    """STT 결과 보정 (영어 음차 + 한국어 오류).

    현재 미사용: 과보정으로 멀쩡한 내용이 왜곡되는 문제가 있어 process_audio에서
    호출하지 않음. 필요하면 다시 호출하면 됨.
    """
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": """당신은 STT(음성인식) 결과를 보정하는 AI입니다.
다음 2가지를 수정해주세요:

1. 영어 단어의 한국어 음차 표기를 원래 영어로 변환
예시:
- "리버파드" → "Riverpod"
- "플러터" → "Flutter"
- "깃허브" → "GitHub"
- "자바스크립트" → "JavaScript"
- "리액트" → "React"
- "패스트에이피아이" → "FastAPI"

2. 한국어 STT 오류 교정 (잘못 인식된 조사, 발음 오류 등)
예시:
- "후로" → "으로"
- "데" → "에"
- "이써" → "있어"
- "햇습니다" → "했습니다"

규칙:
1. 한국어 문장 구조는 그대로 유지
2. 명백히 영어인 기술 용어, 브랜드명만 영어로 변환
3. 잘못된 한국어 조사, 발음만 교정
4. 원본 텍스트만 반환 (설명 없이)"""
            },
            {
                "role": "user",
                "content": f"다음 텍스트를 보정해주세요:\n{text}"
            }
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content.strip()


@router.post("/{meeting_id}/process")
def process_audio(meeting_id: int, db: Session = Depends(get_db)):
    """음성 파일을 텍스트로 변환 (Groq Whisper + AI 보정)"""
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="Audio file not found")

    if not os.path.exists(transcript.audio_file_path):
        raise HTTPException(status_code=404, detail="Audio file does not exist")

    try:
        with open(transcript.audio_file_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-large-v3",          # turbo보다 정확 (특히 한국어)
                language="ko",
                response_format="text",
                temperature=0,                      # 결정적 출력 → 환각 감소
                prompt="한국어로 진행된 회의 녹음입니다. 회의 안건, 논의 내용, 결정 사항, 할 일이 포함됩니다.",
            )

        raw_text = result.strip() if isinstance(result, str) else str(result)

        # 환각/과보정 방지를 위해 LLM 보정 단계는 끔. Whisper 결과를 그대로 저장.
        transcript.raw_text = raw_text
        db.commit()
        db.refresh(transcript)

        return {
            "meeting_id": meeting_id,
            "status": "completed",
            "raw_text": raw_text,
            "original_text": raw_text,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
