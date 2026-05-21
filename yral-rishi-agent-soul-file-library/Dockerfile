# ---------------------------------------------------------------------------
# Dockerfile — instructions Docker follows to build the prod container image
# for any v2 service spawned from this template.
#
# ⭐ START HERE: this file has TWO stages.
#   1. `builder` stage:  installs the Python deps into a venv at /opt/venv.
#   2. `runtime` stage:  copies that venv + the app code into a slim image,
#                        switches to a non-root user, and starts uvicorn.
#
# WHAT IS A "MULTI-STAGE" BUILD?
# Each `FROM` line starts a new stage. Only the LAST stage ends up as the
# final image — earlier stages are throwaway scratch space. We use this
# to install Python packages in stage 1 (which leaves behind pip caches,
# build wheels, and other clutter) and then copy ONLY the installed venv
# into a clean stage 2. The final image is smaller and has fewer files
# an attacker could poke at.
#
# HOW IS THIS FILE USED?
# CI (GitHub Actions) runs:
#   docker build -t ghcr.io/dolr-ai/<service-name>:<git-sha> .
# The image gets pushed to GHCR (per CONSTRAINTS F13) and pulled onto
# rishi-4/5/6 during deployment.
#
# WHY IS THIS FILE THE SAME ACROSS ALL 13 SERVICES?
# new-service.sh copies this Dockerfile into every spawned service as-is.
# Identical builds = one mental model + fewer "this one is special"
# surprises. Service-specific bits (env vars, secrets, etc.) live in
# docker-compose.swarm.yml + project.config, not here.
#
# RELATED FILES (footer at the end of this file).
# ---------------------------------------------------------------------------


# ===========================================================================
# STAGE 1 — builder: install Python deps into a virtualenv at /opt/venv.
# ===========================================================================

# Start from the official Python 3.12 image. "slim" = stripped-down Debian
# (~150MB vs ~900MB for the full image). We don't need build tools at
# runtime; if a future dep needs a C compiler we'd add `build-essential`
# here, in the builder stage only.
FROM python:3.12-slim AS builder

# Where we'll build the project. Throwaway path — this whole stage gets
# discarded once we copy the venv out.
WORKDIR /build

# Create an isolated Python environment at /opt/venv. Anything `pip install`
# writes goes inside it. We keep deps in a venv (rather than the system
# Python) because it's a single self-contained directory we can copy
# verbatim into the runtime stage.
RUN python -m venv /opt/venv

# Put the venv's `pip` + `python` at the front of PATH so the rest of this
# stage uses them automatically (no need to type /opt/venv/bin/pip every
# time).
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip itself first. Newer pip = faster installs + better error
# messages. --no-cache-dir means "don't write the download cache to disk"
# (saves ~30MB; we're only running pip once per stage anyway).
RUN pip install --no-cache-dir --upgrade pip

# Copy the dependency manifest + the app source. Hatchling (our build
# backend) needs both to install the project. We could split this into
# "copy pyproject first, install deps, then copy app" to get better
# Docker-layer caching when only app code changes — but for Day 1 we
# keep it simple. The fast-incremental-build optimization can come
# later if build time becomes a real complaint.
COPY pyproject.toml ./
COPY app ./app

# Install the project. `pip install .` reads pyproject.toml, resolves
# the dependency graph, downloads every wheel from PyPI, and installs
# them + the `app` package into /opt/venv/lib/python3.12/site-packages/.
RUN pip install --no-cache-dir .


# ===========================================================================
# STAGE 2 — runtime: minimal image that actually runs in production.
# ===========================================================================

# Same base image so the runtime libc matches the libc the wheels were
# compiled against in stage 1. Mismatched libc = cryptic "GLIBC not
# found" errors at startup.
FROM python:3.12-slim AS runtime

# Metadata that GHCR + tools (Sentry release linker, etc.) display.
# The `source` URL lets viewers click through from the registry to
# the monorepo. new-service.sh overwrites these per service.
LABEL org.opencontainers.image.source="https://github.com/dolr-ai/yral-rishi-agent"
LABEL org.opencontainers.image.description="yral-rishi-agent v2 service (spawned from new-service-template)"

# Create a non-root user (UID/GID 1001) named "appuser". By default
# Docker containers run as root, which means a code-execution bug
# would give an attacker root INSIDE the container, making it easier
# to escape to the host. With a non-root user the attacker is sandboxed
# to /tmp + their own files.
#
#   groupadd --system: create a system group (lower-numbered GIDs)
#   useradd  --system: create a system user in that group
#   --shell /usr/sbin/nologin: prevent the user from getting a shell
#   --create-home: give them a home dir (some libs expect $HOME)
RUN groupadd --system --gid 1001 appuser && \
    useradd  --system --uid 1001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

# Copy the installed venv from stage 1. Includes every dep + the `app`
# package, fully wired and ready to import. We didn't need pip, build
# wheels, or any of stage 1's clutter — just the finished site-packages.
COPY --from=builder /opt/venv /opt/venv

# Put the venv on PATH so `uvicorn` (a venv script) is found by name
# in the CMD line below.
ENV PATH="/opt/venv/bin:$PATH"

# Don't write .pyc files to disk (they'd be owned by appuser and just
# add clutter). And flush stdout/stderr immediately so logs appear in
# real time (no buffering gotchas when tailing logs).
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Where the running service expects its working directory to be. Some
# libs look at $CWD for config files; we set this explicitly so we
# don't inherit /root or whatever the base image had.
WORKDIR /app

# Copy the app source into the runtime image so tracebacks point at
# real files an operator can grep. Owned by appuser so the running
# service can read its own code.
COPY --chown=appuser:appuser app ./app

# Day-7 deploy fix: bundle shared-config.yaml at /app/shared-config.yaml
# so app/composer/four_layer_composer.py can load `LAYER_SEPARATOR` at
# module-import (C7). Without this the soul-file-library import chain
# raises FileNotFoundError before uvicorn can start.
COPY --chown=appuser:appuser shared-config.yaml ./shared-config.yaml

# Switch to the non-root user. Every subsequent CMD / shell command
# (and the running process at startup) runs as appuser, not root.
USER appuser

# Document the port the container listens on. EXPOSE doesn't actually
# open the port — it's a hint for humans + `docker inspect`. The real
# binding happens at `docker run -p` or in docker-compose.yml.
EXPOSE 8000

# What runs when the container starts.
#   uvicorn         — the ASGI server (installed via uvicorn[standard]).
#   app.main:app    — Python path: "in module app.main, the variable `app`".
#   --host 0.0.0.0  — listen on ALL interfaces so Docker can forward
#                     traffic in (127.0.0.1 would only accept localhost).
#   --port 8000     — match the EXPOSE line above.
#
# We use the exec-form (JSON array) so uvicorn becomes PID 1 directly,
# which means SIGTERM from Docker reaches uvicorn and triggers FastAPI's
# graceful shutdown hooks. The shell-form ("uvicorn app.main:app ...")
# would run via /bin/sh, breaking that signal path.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ===========================================================================
# RELATED FILES (read these next to understand the full container flow):
#   pyproject.toml         — what gets installed in stage 1
#   .dockerignore          — what's hidden from COPY (e.g. .venv, .git)
#   docker-compose.yml     — runs this image locally with Postgres + Redis
#   docker-compose.swarm.yml — runs this image as a Swarm service in prod
#   app/main.py            — the FastAPI app uvicorn loads (added Day 2)
# ===========================================================================
