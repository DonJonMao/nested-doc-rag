#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/deployments/docker-compose.prod.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployments/.env.prod}"
BACKUP_DIR="${BACKUP_DIR:-}"

[[ -n "$BACKUP_DIR" ]] || {
  echo "BACKUP_DIR is required" >&2
  exit 1
}
[[ -d "$BACKUP_DIR" ]] || {
  echo "backup directory not found: $BACKUP_DIR" >&2
  exit 1
}
[[ -f "$ENV_FILE" ]] || {
  echo "missing env file: $ENV_FILE" >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PROJECT="${COMPOSE_PROJECT_NAME:-gongkan}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

"${COMPOSE[@]}" down

volumes=(postgres_data redis_data minio_data qdrant_data api_runtime worker_runtime worker_python_artifacts worker_python_tmp)
for volume in "${volumes[@]}"; do
  archive="$BACKUP_DIR/volumes/${volume}.tgz"
  [[ -f "$archive" ]] || continue
  docker volume create "${PROJECT}_${volume}" >/dev/null
  docker run --rm \
    -v "${PROJECT}_${volume}:/volume" \
    -v "$BACKUP_DIR/volumes:/backup:ro" \
    alpine:3.20 sh -c "rm -rf /volume/* /volume/.[!.]* /volume/..?* 2>/dev/null || true; tar -xzf /backup/${volume}.tgz -C /volume"
done

"${COMPOSE[@]}" up -d
echo "restore completed from $BACKUP_DIR"
