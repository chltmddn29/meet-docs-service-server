---
title: MeetDocs API
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# meet-docs
음성을 인식하여 회의록을 적어주는 서비스 (백엔드 API)

Hugging Face Spaces(Docker)로 배포. 필요한 환경변수:
- `GROQ_API_KEY` (필수) — STT/AI
- `DATABASE_URL` (필수) — Supabase/Neon PostgreSQL 연결 주소
- `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` (선택) — 노션 내보내기
