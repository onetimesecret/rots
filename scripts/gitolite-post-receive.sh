#!/bin/bash
# gitolite-post-receive.sh
#
# Post-receive hook for triggering OTS deployments after image builds.
# Place in gitolite repo: hooks/post-receive (or use gitolite's hook mechanism)
#
# Environment requirements:
#   RABBITMQ_URL - RabbitMQ connection string (from /etc/default/gitolite-deploy)
#   GL_REPO      - Gitolite repo name (set by gitolite)
#
# Repository requirements:
#   .ots-deploy.yaml - Manifest specifying target hosts and port
#
# Example .ots-deploy.yaml:
#   hosts:
#     - acme-prod-1
#     - acme-prod-2
#   port: 7043

set -euo pipefail

# Load deployment credentials (contains RABBITMQ_URL)
if [[ -f /etc/default/gitolite-deploy ]]; then
    # shellcheck source=/dev/null
    source /etc/default/gitolite-deploy
fi

# Validate RABBITMQ_URL is set
if [[ -z "${RABBITMQ_URL:-}" ]]; then
    echo "ERROR: RABBITMQ_URL not set. Configure /etc/default/gitolite-deploy" >&2
    exit 1
fi

# Determine image tag from git
# Prefer annotated tags, fall back to short commit hash
TAG=$(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)

# Derive image name from repository
# GL_REPO is set by gitolite (e.g., "acme/ots-custom")
REPO_NAME="${GL_REPO:-$(basename "$(pwd)" .git)}"
IMAGE="registry.example.com/${REPO_NAME}"

echo "Building image: ${IMAGE}:${TAG}"

# Build container image
podman build \
    --tag "${IMAGE}:${TAG}" \
    --tag "${IMAGE}:latest" \
    .

# Push to registry
echo "Pushing image: ${IMAGE}:${TAG}"
podman push "${IMAGE}:${TAG}"
podman push "${IMAGE}:latest"

# Trigger deployment via sidecar
# Uses .ots-deploy.yaml in repo root for host targeting
echo "Triggering deployment..."
exec rots workflow trigger \
    --tag "${TAG}" \
    --json
