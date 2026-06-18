FROM python:3.11-slim

WORKDIR /app

# 한글 폰트(나눔) 설치 — PDF 생성에 사용. (폰트를 git에 안 올리는 대신 시스템 설치)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 복사 (backend/ 내용을 /app 루트로)
COPY backend/ ./

# Hugging Face Spaces 기본 포트
EXPOSE 7860

# 생성 파일 캐시 디렉터리 미리 생성 (DB가 진실원본이라 휘발돼도 무방)
RUN mkdir -p audio markdown pdf docx hwpx

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
