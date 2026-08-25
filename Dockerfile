# Consultant Experience - production image.
#
# Build:  docker build -t consultant-experience .
# Run:    docker run -p 8000:8000 --env-file .env consultant-experience
#
# Render builds this automatically from render.yaml. See DEPLOY.md.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Dependencies first, so a code change does not reinstall the whole stack.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY seed ./seed
COPY scripts ./scripts

# Run as a non-root user. `data/` only gets written when DATABASE_URL is unset
# or STORAGE_BACKEND=local, but it must be writable either way for startup.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz', timeout=4).status==200 else 1)"

# --proxy-headers so request URLs are correct behind Render's load balancer.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
