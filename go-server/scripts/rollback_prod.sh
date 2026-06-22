#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/deployments/docker-compose.prod.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployments/.env.prod}"

[[ -n "${IMAGE_TAG:-}" ]] || {
  echo "IMAGE_TAG is required for rollback, e.g. IMAGE_TAG=v1.2.2 make rollback-prod" >&2
  exit 1
}

if [[ -n "${BACKUP_DIR:-}" ]]; then
  "$SCRIPT_DIR/restore_prod.sh"
fi

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${COMPOSE[@]}" pull api worker
"${COMPOSE[@]}" up -d --no-deps api worker
echo "rolled back api/worker to IMAGE_TAG=$IMAGE_TAG"
