FROM python:3.11-slim

WORKDIR /app

# 한글 폰트(나눔) — PDF 생성용 / ffmpeg — 25MB 초과 오디오 분할 변환용
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# torch는 CPU 전용 휠을 먼저 깔아둔다 (화자분리용 pyannote.audio 의존성).
# 기본 PyPI 인덱스로 깔면 GPU(CUDA) 런타임이 딸려와 이미지가 수 GB 커지는데,
# 이 서버는 GPU가 없어 무의미하다 → CPU 인덱스로 고정.
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

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
