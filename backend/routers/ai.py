from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Transcript, MeetingAgendaItem, Meeting
from routers.groq_client import client, ensure_client, TEXT_MODEL
from routers.llm_utils import extract_json_array
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["ai"])

_SYSTEM_PROMPT = (
    "당신은 회의록을 꼼꼼하게 정리하는 전문 서기입니다. "
    "내용을 과도하게 압축하지 말고, 실제로 오간 논의·의견·할 일을 충실히 담으세요. "
    "반드시 순수 JSON 배열만 응답하세요. 마크다운 코드블록이나 다른 텍스트는 절대 포함하지 마세요."
)


def _build_user_prompt(raw_text: str, participants: str = "") -> str:
    participant_line = (
        f"\n참석자 명단: {participants}\n" if participants else "\n참석자 명단: (제공되지 않음)\n"
    )
    return f"""
다음 회의록 텍스트를 안건별로 분석해서 JSON 배열로 반환하세요.
{participant_line}
각 항목의 형식:
{{
  "agenda": "안건명",
  "content": "이 안건에서 논의된 내용을 구체적이고 충실하게 (2~5문장, 요약만 하지 말 것)",
  "discussions": ["참석자들이 낸 주요 의견·관점·근거를 하나씩", "..."],
  "speaker_points": ["김대리: 로그인 버그를 고쳤다고 보고", "이대리: 결제 모듈을 맡기로 함"],
  "decision": "확정된 결정사항 (없으면 빈 문자열)",
  "completed_items": ["이미 완료했다고 언급된 일(한 일)", "..."],
  "action_items": ["앞으로 해야 할 일(할 일)", "..."]
}}

작성 규칙:
- 영어 기술용어·브랜드·제품명이 한글로 음차된 경우 올바른 영어 표기로 바로잡아 쓰세요.
  예) "패스트에이피아이"→"FastAPI", "리버파드"→"Riverpod", "플러터"→"Flutter", "깃허브"→"GitHub"
- content는 핵심만 압축하지 말고 맥락이 드러나게 구체적으로 적으세요.
- discussions에는 찬반·대안·우려 등 회의에서 실제 나온 의견을 빠짐없이 담으세요.
- completed_items(한 일)와 action_items(할 일)를 명확히 구분하세요.
  이미 끝난 일은 completed_items, 앞으로 할 일은 action_items 입니다.
- speaker_points(발언자별 정리): 위 참석자 명단과 문맥(이름 언급, "제가/내가" 등)을
  근거로 "누가 무엇을 말했는지/맡았는지"를 "이름: 내용" 형식으로 정리하세요.
  * 명단의 이름이나 본문 근거로 합리적으로 추정 가능한 경우에만 이름을 붙이세요.
  * 누가 말했는지 불확실하면 그 항목은 넣지 마세요(억지로 배정 금지).
  * 음성만으로는 화자를 100% 알 수 없으니, 확실하지 않으면 비워두세요.
- 회의에 없던 내용을 지어내지 말고, 해당 항목이 없으면 빈 배열/빈 문자열로 두세요.

회의록 텍스트:
{raw_text}
"""


def analyze_and_save(meeting_id: int, raw_text: str, db: Session) -> list:
    """raw_text를 AI로 분석해 안건 항목을 새로 저장. 공용 로직."""
    ensure_client()

    if not raw_text or not raw_text.strip():
        raise HTTPException(status_code=400, detail="분석할 텍스트가 없습니다")

    # 발언자 추정에 쓸 참석자 명단
    meeting = db.query(Meeting).filter(Meeting.meeting_id == meeting_id).first()
    participants = meeting.participants if meeting and meeting.participants else ""

    # 1) LLM 호출
    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(raw_text, participants)},
            ],
            temperature=0.3,
        )
        response_text = response.choices[0].message.content
    except Exception as e:
        logger.exception("AI 분석 호출 실패")
        raise HTTPException(status_code=502, detail=f"AI 분석 실패: {e}")

    # 2) 견고한 JSON 파싱
    try:
        agenda_items = extract_json_array(response_text)
    except ValueError:
        logger.error("AI 응답 파싱 실패: %r", response_text)
        raise HTTPException(status_code=500, detail="AI 응답을 이해하지 못했습니다")

    if not agenda_items:
        raise HTTPException(
            status_code=400,
            detail="분석할 내용이 부족합니다 (녹음이 너무 짧거나 비어있음)",
        )

    # 3) 기존 항목 교체 (트랜잭션 안전)
    try:
        db.query(MeetingAgendaItem).filter(
            MeetingAgendaItem.meeting_id == meeting_id
        ).delete()

        for idx, item in enumerate(agenda_items):
            db.add(MeetingAgendaItem(
                meeting_id=meeting_id,
                agenda=item.get("agenda", ""),
                order=idx + 1,
                content=item.get("content", ""),
                discussions=json.dumps(
                    item.get("discussions", []), ensure_ascii=False
                ),
                speaker_points=json.dumps(
                    item.get("speaker_points", []), ensure_ascii=False
                ),
                decision=item.get("decision", ""),
                completed_items=json.dumps(
                    item.get("completed_items", []), ensure_ascii=False
                ),
                action_items=json.dumps(
                    item.get("action_items", []), ensure_ascii=False
                ),
            ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("안건 저장 실패")
        raise HTTPException(status_code=500, detail=f"저장 실패: {e}")

    return agenda_items


@router.post("/{meeting_id}/analyze")
def analyze_meeting(meeting_id: int, db: Session = Depends(get_db)):
    """STT 텍스트를 AI로 안건별로 정리"""
    transcript = db.query(Transcript).filter(
        Transcript.meeting_id == meeting_id
    ).order_by(Transcript.transcript_id.desc()).first()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not transcript.raw_text:
        raise HTTPException(status_code=400, detail="Raw text not available")

    agenda_items = analyze_and_save(meeting_id, transcript.raw_text, db)

    return {
        "meeting_id": meeting_id,
        "status": "analyzed",
        "agenda_items": agenda_items,
    }
