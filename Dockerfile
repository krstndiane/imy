# imy in one container: the API + the built website + Tesseract for reading screenshots.
#   docker build -t imy .
#   docker run -p 8000:8000 -v imy-data:/data --env-file .env imy
# Works on any host that runs Docker (Render, Railway, Fly.io, a VPS...). Keep /data on a persistent
# disk/volume: it holds the database and uploaded screenshots.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements-deploy.txt backend/requirements-deploy.txt
RUN pip install --no-cache-dir -r backend/requirements-deploy.txt

COPY backend backend
COPY frontend/dist frontend/dist

ENV IMY_DATA_DIR=/data \
    IMY_EMBEDDER=tfidf \
    TZ=Asia/Manila \
    PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')"

# One worker: imy keeps each person's model in memory and uses SQLite. It handles many chats at once with threads.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30"]
