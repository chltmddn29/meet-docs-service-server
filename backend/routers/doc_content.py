"""회의록 문서 본문 생성 공용 헬퍼.

안건 항목의 모든 필드(내용·주요 의견·결정·한 일·할 일)를 일관되게 렌더링.
markdown/pdf/docx/preview가 공유한다.
"""
import json
from datetime import timezone, timedelta

KST = timezone(timedelta(hours=9))


def _load_list(value) -> list:
    """JSON 문자열/리스트/None → 문자열 리스트."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return [str(value)]


def item_sections(item):
    """안건 항목 1개를 (제목, [(라벨, 본문 or 리스트)]) 구조로 반환.
    포맷터(md/pdf/docx)가 공통으로 사용."""
    sections = []
    if item.content:
        sections.append(("내용", item.content))
    discussions = _load_list(getattr(item, "discussions", None))
    if discussions:
        sections.append(("주요 의견", discussions))
    speakers = _load_list(getattr(item, "speaker_points", None))
    if speakers:
        sections.append(("발언자별 정리", speakers))
    if item.decision:
        sections.append(("결정사항", item.decision))
    completed = _load_list(getattr(item, "completed_items", None))
    if completed:
        sections.append(("한 일", completed))
    actions = _load_list(item.action_items)
    if actions:
        sections.append(("할 일", actions))
    return sections


def build_markdown(meeting, items) -> str:
    md = f"# {meeting.title or '제목 없음'}\n\n"
    if meeting.created_at:
        created = meeting.created_at.replace(tzinfo=timezone.utc).astimezone(KST)
        md += f"> 📅 {created.strftime('%Y-%m-%d %H:%M')}\n>\n"
    if meeting.participants:
        md += f"> 👥 참석자: {meeting.participants}\n"
    md += "\n---\n\n"

    for item in items:
        md += f"## {item.order}. {item.agenda}\n\n"
        for label, body in item_sections(item):
            if isinstance(body, list):
                # 할 일은 체크박스, 나머지는 불릿
                bullet = "- [ ] " if label == "할 일" else "- "
                md += f"**{label}**\n"
                for line in body:
                    md += f"{bullet}{line}\n"
                md += "\n"
            else:
                md += f"**{label}**\n{body}\n\n"
        md += "---\n\n"
    return md
