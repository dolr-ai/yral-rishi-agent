#!/bin/bash
# deploy-app.sh — deploy v2 app container to a single server with health checking.
# Adapted from chat-ai's deploy script for the v2 cluster (rishi-4/5).
#
# Usage (called by CI on each APP_SERVER):
#   APP_DIR=/path/to/repo IMAGE_TAG=abc123 bash scripts/ci/deploy-app.sh

set -e

if [ ! -d "${APP_DIR}" ]; then
    echo "FATAL: APP_DIR does not exist: ${APP_DIR}"
    exit 1
fi
cd "${APP_DIR}"

set -a
source ./project.config
[ -f ./servers.config ] && source ./servers.config
set +a

echo "[deploy] Deploying ${PROJECT_REPO} image tag: ${IMAGE_TAG}"

# Record current image for rollback
PREVIOUS_TAG=""
if docker inspect "${PROJECT_REPO}" >/dev/null 2>&1; then
    PREVIOUS_TAG=$(docker inspect "${PROJECT_REPO}" --format '{{.Config.Image}}' | sed 's/.*://')
    echo "${PREVIOUS_TAG}" > .previous_image_tag
fi

# Write secrets
mkdir -p secrets
echo "${DATABASE_URL}" > secrets/database_url

# GHCR login
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u "${GITHUB_ACTOR}" --password-stdin

# Run migrations (only on the first app server)
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    bash scripts/ci/run-migrations.sh
fi

# Pull new image
docker pull "${IMAGE_REPO}:${IMAGE_TAG}"

# Start container
export IMAGE_TAG
export SERVER_NAME=$(hostname)
docker compose up -d --remove-orphans

# Health check (90 seconds max)
echo "[deploy] Waiting for health check..."
HEALTHY=false
for i in $(seq 1 18); do
    sleep 5
    STATUS=$(docker inspect "${PROJECT_REPO}" --format '{{.State.Health.Status}}' 2>/dev/null || echo "unknown")
    echo "[deploy]   check $i: ${STATUS}"
    if [ "${STATUS}" = "healthy" ]; then
        HEALTHY=true
        break
    fi
done

if [ "${HEALTHY}" = "true" ]; then
    echo "${IMAGE_TAG}" > .last_good_image_tag
    echo "[deploy] SUCCESS: ${PROJECT_REPO} is healthy"
    exit 0
else
    echo "[deploy] FAILURE: health check failed after 90s"
    # Rollback
    ROLLBACK_TAG=""
    [ -f .last_good_image_tag ] && ROLLBACK_TAG=$(cat .last_good_image_tag)
    [ -z "${ROLLBACK_TAG}" ] && [ -n "${PREVIOUS_TAG}" ] && ROLLBACK_TAG="${PREVIOUS_TAG}"

    if [ -n "${ROLLBACK_TAG}" ]; then
        echo "[deploy] Rolling back to: ${ROLLBACK_TAG}"
        export IMAGE_TAG="${ROLLBACK_TAG}"
        docker compose up -d --remove-orphans
        sleep 10
    fi
    exit 1
fi
