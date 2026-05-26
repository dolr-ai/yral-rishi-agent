# ---------------------------------------------------------------------------
# Dockerfile — two-stage build producing the prod container image for
# the user-memory-service.
#
# ⭐ START HERE: this file has TWO stages.
#   1. `builder` stage:  installs Python deps into a venv at /opt/venv.
#   2. `runtime` stage:  copies that venv + the app code into a slim image,
#                        switches to a non-root user, and starts uvicorn.
#
# WHAT IS A "MULTI-STAGE" BUILD?
# Each `FROM` line starts a new stage. Only the LAST stage ends up as the
# final image — earlier stages are throwaway scratch space. We install deps
# in stage 1 (which leaves pip caches, build wheels, other clutter) and
# then copy ONLY the installed venv into a clean stage 2. The final image
# is smaller and has fewer files an attacker could poke at.
#
# HOW IS THIS FILE USED?
# CI (GitHub Actions) runs:
#   docker build -t ghcr.io/dolr-ai/yral-rishi-agent-user-memory-service:<git-sha> .
# The image is pushed to GHCR (per CONSTRAINTS F13) and pulled onto
# rishi-4/5/6 during deployment.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# STAGE 1 — builder: install Python deps into a virtualenv at /opt/venv.
# ===========================================================================

FROM python:3.12-slim AS builder

WORKDIR /build

# Create an isolated Python environment at /opt/venv.
RUN python -m venv /opt/venv

# Put the venv's pip + python at the front of PATH.
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip first — newer pip = faster installs + better error messages.
RUN pip install --no-cache-dir --upgrade pip

# Copy the dependency manifest + the app source. Hatchling needs both
# to install the project.
COPY pyproject.toml ./
COPY app ./app

# Install the project — reads pyproject.toml, resolves deps, installs.
RUN pip install --no-cache-dir .


# ===========================================================================
# STAGE 2 — runtime: minimal image that runs in production.
# ===========================================================================

FROM python:3.12-slim AS runtime

# Metadata for GHCR + Sentry release linker.
LABEL org.opencontainers.image.source="https://github.com/dolr-ai/yral-rishi-agent"
LABEL org.opencontainers.image.description="yral-rishi-agent-user-memory-service v2 — Phase 1 conversation history persistence"

# Create a non-root user (UID/GID 1001) so a code-execution bug can't
# give an attacker root inside the container.
RUN groupadd --system --gid 1001 appuser && \
    useradd  --system --uid 1001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

# Copy the installed venv from stage 1. Includes every dep + the `app`
# package — fully wired and ready to import.
COPY --from=builder /opt/venv /opt/venv

# Put the venv on PATH so `uvicorn` is found by name in CMD.
ENV PATH="/opt/venv/bin:$PATH"

# Don't write .pyc files + flush stdout/stderr immediately for real-time logs.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the app source into the runtime image for readable tracebacks.
COPY --chown=appuser:appuser app ./app

# Bundle alembic.ini at /app/alembic.ini so the operator's one-off
# `alembic upgrade head` task container (per RUNBOOK "Schema migration")
# finds the Alembic config. Without it `alembic upgrade head` fails with
# "No config file 'alembic.ini' found". The file is config-only —
# no secrets (DB URL is read from env by app/migrations/env.py per D1+D8).
COPY --chown=appuser:appuser alembic.ini ./alembic.ini

# Switch to the non-root user.
USER appuser

# Document the port the container listens on (hint only — actual binding
# is at docker run -p or in docker-compose.yml).
EXPOSE 8000

# Start uvicorn as PID 1 (exec-form JSON array) so SIGTERM reaches it
# directly and FastAPI's graceful-shutdown lifespan hook fires on stop.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ===========================================================================
# RELATED FILES:
#   pyproject.toml           — what gets installed in stage 1
#   .dockerignore            — what's hidden from COPY (e.g. .venv, .git)
#   docker-compose.yml       — runs this image locally with Postgres
#   docker-compose.swarm.yml — runs this image as a Swarm service in prod
#   app/main.py              — the FastAPI app uvicorn loads
# ===========================================================================
