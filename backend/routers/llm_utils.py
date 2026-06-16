"""LLM 응답 파싱 유틸 — AI가 돌려주는 JSON을 안전하게 안건 리스트로 변환."""
import json


def extract_json_array(text: str) -> list:
    """LLM 응답 텍스트에서 안건 JSON 배열을 최대한 견고하게 추출한다.

    대응하는 경우:
    - 순수 JSON 배열
    - ```json ... ``` 코드블록으로 감싼 경우
    - {"items": [...]} 처럼 객체로 감싼 경우
    - 앞뒤에 설명 텍스트가 붙은 경우 (첫 '['~마지막 ']' 추출)

    실패하면 ValueError를 던진다.
    """
    if not text or not text.strip():
        raise ValueError("빈 응답")

    cleaned = text.strip()

    # 1) 코드블록 제거 (```json ... ``` 또는 ``` ... ```)
    if "```" in cleaned:
        # 첫 번째 코드블록 내부만 취함 (split 결과 길이 안전 확인)
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1]
            if cleaned.lstrip().lower().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
            cleaned = cleaned.strip()

    # 2) 1차 파싱 시도
    parsed = _try_load(cleaned)

    # 3) 실패 시: 첫 '[' ~ 마지막 ']' 구간만 잘라 재시도
    if parsed is None:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            parsed = _try_load(cleaned[start:end + 1])

    # 4) 그래도 실패: 객체일 수 있으니 {} 구간 추출 후 안의 배열 탐색
    if parsed is None:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = _try_load(cleaned[start:end + 1])
            parsed = obj

    if parsed is None:
        raise ValueError("JSON 파싱 실패")

    # dict로 감싼 경우 내부 배열 추출
    if isinstance(parsed, dict):
        # 흔한 키 우선, 없으면 첫 번째 list 값
        for key in ("items", "agenda_items", "agendas", "data", "result"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        else:
            list_vals = [v for v in parsed.values() if isinstance(v, list)]
            parsed = list_vals[0] if list_vals else [parsed]

    if not isinstance(parsed, list):
        raise ValueError("배열이 아님")

    # 항목 정규화: dict가 아닌 항목은 건너뛰고, 필드 보정
    normalized = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        actions = item.get("action_items", [])
        if not isinstance(actions, list):
            actions = [str(actions)] if actions else []
        normalized.append({
            "agenda": str(item.get("agenda", "")).strip(),
            "content": str(item.get("content", "")).strip(),
            "decision": str(item.get("decision", "")).strip(),
            "action_items": [str(a).strip() for a in actions if str(a).strip()],
        })

    return normalized


def _try_load(s: str):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
