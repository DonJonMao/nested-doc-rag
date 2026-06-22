#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/deployments/docker-compose.prod.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployments/.env.prod}"

[[ -f "$ENV_FILE" ]] || {
  echo "missing env file: $ENV_FILE" >&2
  exit 1
}

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" push api worker
