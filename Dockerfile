# imy in one container: the API + the built website. Screenshots are read by Groq (no OCR here),
# the database can be on Turso and pictures in Supabase (see .env.example), so the container keeps nothing.
#   docker build -t imy .
#   docker run -p 8000:8000 -v imy-data:/data --env-file .env imy
# Works on any host that runs Docker (Render, Railway, Fly.io, a VPS...). Keep /data on a persistent
# disk/volume: without Turso and Supabase it holds the database and uploaded pictures. Before launch: python -m backend.preflight
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements-deploy.txt backend/requirements-deploy.txt
RUN pip install --no-cache-dir -r backend/requirements-deploy.txt

COPY backend backend
COPY frontend/dist frontend/dist

# Backups on by default in the container: any copy real people use should have them.
ENV IMY_DATA_DIR=/data \
    IMY_EMBEDDER=tfidf \
    IMY_BACKUP_DAILY=1 \
    TZ=Asia/Manila \
    PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health')"

# One worker on purpose - don't add --workers. imy keeps each person's model in memory, and it
# orders database writes into a fair queue inside the process (see backend/db.py). A second worker
# has its own queue and its own copy of every model, so writes would go back to fighting each other
# and memory would double. Measured: one worker is comfortable with ~150 people chatting at once.
# To grow past that, move the database to Postgres first, then add workers.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30"]
