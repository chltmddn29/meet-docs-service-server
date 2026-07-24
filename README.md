---
title: MeetDocs API
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# MeetDocs

**회의 음성을 업로드하면 자동으로 회의록을 만들어주는 백엔드 API 서비스**

녹음 파일을 올리면 STT(음성 인식)로 텍스트를 뽑고, 화자분리로 "누가 말했는지"를 구분한 뒤, LLM이 안건별로 논의 내용·결정사항·할 일을 정리해줍니다. 완성된 회의록은 Markdown / PDF / DOCX / HWPX 파일로 내려받거나 Notion 페이지로 바로 내보낼 수 있습니다.

FastAPI 기반 REST API 서버이며, Docker로 패키징되어 Hugging Face Spaces에 배포됩니다.

## 주요 기능

- **음성 업로드 & STT**: 오디오 파일 업로드 후 Groq Whisper로 텍스트 변환. 25MB를 넘는 긴 회의는 ffmpeg로 자동 분할해 병렬 변환하고, 진행 상태를 폴링으로 확인 가능
- **화자분리(Speaker Diarization)**: pyannote.audio로 오디오 신호 자체에서 화자를 구분(텍스트 추측이 아닌 음성 기반). `HF_TOKEN`이 없거나 실패해도 화자 라벨 없이 정상 동작하도록 폴백
- **AI 회의록 분석**: LLM(Groq `openai/gpt-oss-120b`)이 원문 텍스트를 안건별로 나눠 논의 내용, 참석자별 발언, 결정사항, 한 일/할 일 목록을 JSON으로 구조화
- **다양한 포맷으로 내보내기**: Markdown, PDF(한글 폰트 포함), DOCX, HWPX(한컴오피스) 생성 및 다운로드
- **Notion 연동**: 완성된 회의록을 Notion 페이지로 바로 저장
- **서식 템플릿**: 샘플 문서를 업로드해두면 AI가 그 형식에 맞춰 회의록을 생성
- **회의 템플릿**: 자주 쓰는 안건·참석자 구성을 템플릿으로 저장/재사용
- **할 일 모아보기**: 모든 회의에서 나온 action item을 한곳에 모아 체크/관리

## 폴더 구조

```
meet-docs-service/
├── Dockerfile              # HF Spaces 배포용 이미지 정의 (Python 3.11 + ffmpeg + CPU torch)
├── README.md
└── backend/
    ├── main.py              # FastAPI 앱 엔트리포인트, 라우터 등록, 경량 마이그레이션
    ├── database.py          # SQLAlchemy 엔진/세션 (SQLite 로컬 / Postgres 배포)
    ├── models.py            # ORM 모델 (User, Meeting, Transcript, MeetingAgendaItem 등)
    ├── requirements.txt
    ├── create_test_audio.py # 테스트용 오디오 생성 스크립트
    ├── fonts/                # PDF 생성용 한글 폰트(나눔고딕)
    ├── audio/ markdown/ pdf/ docx/  # 생성/업로드 파일 캐시 (DB가 원본, 디스크는 휘발성 가정)
    └── routers/
        ├── meetings.py       # 회의 CRUD
        ├── audio.py          # 오디오 업로드/다운로드/삭제
        ├── audio_store.py    # 오디오 파일 로컬 저장 헬퍼
        ├── stt.py            # STT 처리 (Groq Whisper, 청크 분할, 상태 폴링)
        ├── diarization.py    # 화자분리 (pyannote.audio)
        ├── ai.py             # LLM 기반 회의록 분석(안건 정리)
        ├── llm_utils.py      # LLM 응답 파싱 유틸
        ├── groq_client.py    # Groq API 클라이언트 초기화
        ├── markdown.py       # Markdown 생성/저장/다운로드
        ├── pdf.py            # PDF 생성/저장/다운로드
        ├── docx.py           # DOCX 생성/저장/다운로드
        ├── hwpx.py / hwpx_writer.py  # HWPX(한컴) 생성/다운로드
        ├── doc_content.py    # 문서 내보내기 공통 콘텐츠 조립
        ├── preview.py        # 회의록 미리보기
        ├── notion.py         # Notion 페이지로 내보내기
        ├── format_templates.py  # 서식(샘플 문서) 업로드 및 AI 생성 활용
        ├── templates.py      # 회의 템플릿(안건/참석자) CRUD
        ├── action_items.py   # 할 일 체크 상태 업데이트
        └── todos.py          # 전체 회의의 할 일/한 일 모아보기
```

## 사용 기술 스택

**프레임워크 / 서버**
- [FastAPI](https://fastapi.tiangolo.com/) — REST API 프레임워크
- [Uvicorn](https://www.uvicorn.org/) — ASGI 서버
- [SQLAlchemy](https://www.sqlalchemy.org/) — ORM (SQLite 로컬 / PostgreSQL 배포, Supabase·Neon·Render 호환)
- [Pydantic](https://docs.pydantic.dev/) — 요청/응답 스키마 검증

**음성 인식 / 화자분리 / AI**
- [Groq](https://groq.com/) (`groq` SDK) — Whisper 기반 STT, `openai/gpt-oss-120b` 텍스트 모델로 회의록 분석
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) + PyTorch(CPU) — 오디오 기반 화자분리
- `ffmpeg` — 대용량 오디오 분할/재인코딩

**문서 생성**
- [python-docx](https://python-docx.readthedocs.io/) — DOCX 생성
- [ReportLab](https://www.reportlab.com/) — PDF 생성(한글 폰트 포함)
- [olefile](https://github.com/decalage2/olefile) — HWPX(한컴오피스) 생성 지원
- [notion-client](https://github.com/ramnes/notion-sdk-py) — Notion API 연동

**배포**
- Docker (Hugging Face Spaces, `sdk: docker`)
- 환경에 따라 SQLite(로컬) 또는 PostgreSQL(배포)로 자동 전환

## 환경변수

| 변수 | 필수 여부 | 설명 |
|---|---|---|
| `GROQ_API_KEY` | 필수 | STT/AI 분석용 Groq API 키 |
| `DATABASE_URL` | 배포 시 필수 | PostgreSQL 연결 주소 (없으면 로컬 SQLite 사용) |
| `HF_TOKEN` | 선택 | pyannote.audio 화자분리 모델(gated) 접근용 HuggingFace 토큰. 없으면 화자 라벨 없이 동작 |
| `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` | 선택 | Notion 내보내기 기능에 필요 |

## 로컬 실행

```bash
cd backend
pip install -r requirements.txt
python main.py
```

기본 포트는 `7860`(환경변수 `PORT`로 변경 가능)이며, `http://localhost:7860/api/health`로 상태를 확인할 수 있습니다.

## Docker 실행

```bash
docker build -t meetdocs-api .
docker run -p 7860:7860 --env-file backend/.env meetdocs-api
```

Hugging Face Spaces(Docker SDK)로 배포되며, 위 환경변수를 Space secrets에 설정해야 합니다.
