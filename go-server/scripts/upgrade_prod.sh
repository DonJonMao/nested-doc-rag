#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/deployments/docker-compose.prod.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployments/.env.prod}"

[[ -n "${IMAGE_TAG:-}" ]] || {
  echo "IMAGE_TAG is required, e.g. IMAGE_TAG=v1.2.3 make upgrade-prod" >&2
  exit 1
}
[[ -f "$ENV_FILE" ]] || {
  echo "missing env file: $ENV_FILE" >&2
  exit 1
}

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${COMPOSE[@]}" pull api worker
"${COMPOSE[@]}" up -d --no-deps api worker
echo "upgraded api/worker to IMAGE_TAG=$IMAGE_TAG"
