FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/dolr-ai/yral-rishi-agent"
LABEL org.opencontainers.image.description="Yral Agent API — AI chat service"

RUN groupadd --system --gid 1001 appuser && \
    useradd  --system --uid 1001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser app/ .
COPY --chown=appuser:appuser infra/ ./infra/

USER appuser

EXPOSE 8000

# Zero-downtime deploy anchor. Without this, Swarm's `--update-order
# start-first` considers the new task "ready" as soon as the container
# process is up — but FastAPI isn't listening on 8000 yet, so Swarm
# kills the old task before the new one can serve. Ingress VIP briefly
# has zero healthy backends → Kuma probe fails → alert fires. We saw
# this pattern reproduce on every merge-triggered deploy on
# 2026-07-07/08 (~5-15 s down per deploy).
#
# HEALTHCHECK hits /health/live (no DB check, so a transient DB blip
# during Patroni failover doesn't cascade into "kill all replicas").
# start-period=45s covers the FastAPI boot budget (uvicorn + Pillow +
# background loop init + DB pool warm-up); retries=3 filters
# transient blips. urllib is stdlib, so we don't add curl to the
# slim image.
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live', timeout=3).read()" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
