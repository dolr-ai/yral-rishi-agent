FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY watchdog.py .
COPY replica_drift.py .

USER 1000:1000
ENTRYPOINT ["python", "-u", "watchdog.py"]
