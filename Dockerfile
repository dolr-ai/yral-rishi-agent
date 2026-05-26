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

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
