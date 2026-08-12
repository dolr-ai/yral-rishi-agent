FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Modules are copied by name, not with a wildcard, so the image only ever
# carries what we intend to ship. The cost is that forgetting a line here
# is invisible until the container boots and dies on ImportError — tests
# run against the source tree, not the image. test_watchdog_dockerfile.py
# guards that gap; add the COPY line and it passes.
COPY watchdog.py .
COPY replica_drift.py .
COPY patroni_leader.py .
COPY autoheal.py .
COPY heartbeat.py .

USER 1000:1000
ENTRYPOINT ["python", "-u", "watchdog.py"]
