#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/deployments/docker-compose.prod.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployments/.env.prod}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups/$(date +%Y%m%d_%H%M%S)}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

[[ -f "$ENV_FILE" ]] || {
  echo "missing env file: $ENV_FILE" >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PROJECT="${COMPOSE_PROJECT_NAME:-gongkan}"
mkdir -p "$BACKUP_DIR"/{compose,volumes}

cp "$ENV_FILE" "$BACKUP_DIR/compose/.env.prod"
cp "$COMPOSE_FILE" "$BACKUP_DIR/compose/docker-compose.prod.yaml"
if [[ -f "$ROOT_DIR/deployments/docker-compose.edge.yaml" ]]; then
  cp "$ROOT_DIR/deployments/docker-compose.edge.yaml" "$BACKUP_DIR/compose/docker-compose.edge.yaml"
fi
if [[ -f "$ROOT_DIR/deployments/Caddyfile" ]]; then
  cp "$ROOT_DIR/deployments/Caddyfile" "$BACKUP_DIR/compose/Caddyfile"
fi
"${COMPOSE[@]}" config > "$BACKUP_DIR/compose/docker-compose.resolved.yaml"

if "${COMPOSE[@]}" ps --services --status running | grep -qx postgres; then
  "${COMPOSE[@]}" exec -T postgres pg_dump -U gongkan -d gongkan > "$BACKUP_DIR/postgres.sql"
fi

if "${COMPOSE[@]}" ps --services --status running | grep -qx redis; then
  "${COMPOSE[@]}" exec -T redis redis-cli SAVE >/dev/null || true
fi

volumes=(postgres_data redis_data minio_data qdrant_data api_runtime worker_runtime worker_python_artifacts worker_python_tmp)
for volume in "${volumes[@]}"; do
  docker volume inspect "${PROJECT}_${volume}" >/dev/null 2>&1 || continue
  docker run --rm \
    -v "${PROJECT}_${volume}:/volume:ro" \
    -v "$BACKUP_DIR/volumes:/backup" \
    alpine:3.20 sh -c "cd /volume && tar -czf /backup/${volume}.tgz ."
done

cat > "$BACKUP_DIR/backup_manifest.txt" <<EOF
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
compose_project=$PROJECT
image_tag=${IMAGE_TAG:-}
registry=${REGISTRY:-}
image_ns=${IMAGE_NS:-}
EOF

echo "backup written to $BACKUP_DIR"
