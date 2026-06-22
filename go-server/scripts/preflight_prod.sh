#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/deployments/docker-compose.prod.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployments/.env.prod}"
PYTHON_CONFIG="${PYTHON_CONFIG:-$ROOT_DIR/../config/docker.yaml}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

fail() {
  echo "preflight failed: $*" >&2
  exit 1
}

warn() {
  echo "preflight warning: $*" >&2
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

load_env() {
  [[ -f "$ENV_FILE" ]] || fail "missing env file: $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

require_env() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]] || fail "missing required env: $name"
}

reject_placeholder() {
  local name="$1"
  local value="${!name:-}"
  [[ "$value" != change-this* && "$value" != replace-with-* ]] || fail "$name still uses placeholder value"
}

check_image_pin() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]] || fail "missing image env: $name"
  [[ "$value" != *":latest" ]] || fail "$name must not use latest: $value"
  [[ "$value" == *":"* ]] || fail "$name must include an explicit tag: $value"
}

check_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    warn "TCP port $port is already listening; confirm it belongs to the intended deployment"
  fi
}

check_http_reachable() {
  local name="$1"
  local url="$2"
  [[ -n "$url" ]] || return 0
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl missing; skipped $name endpoint check"
    return 0
  fi
  local code
  code="$(curl --connect-timeout 5 --max-time 10 -sS -o /dev/null -w '%{http_code}' "$url" || true)"
  [[ "$code" != "000" ]] || fail "$name endpoint is not reachable: $url"
}

extract_yaml_value() {
  local key="$1"
  awk -F': *' -v key="$key" '$1 ~ "^[[:space:]]*" key "$" {gsub(/^[ \"'\'']+|[ \"'\'']+$/, "", $2); print $2; exit}' "$PYTHON_CONFIG"
}

load_env

require_command docker
docker compose version >/dev/null || fail "docker compose is not available"

for name in \
  COMPOSE_PROJECT_NAME REGISTRY IMAGE_NS IMAGE_TAG \
  POSTGRES_PASSWORD GONGKAN_DATABASE_DSN \
  MINIO_ROOT_USER MINIO_ROOT_PASSWORD GONGKAN_MINIO_ENDPOINT \
  GONGKAN_MINIO_ACCESS_KEY GONGKAN_MINIO_SECRET_KEY GONGKAN_MINIO_BUCKET \
  GONGKAN_JWT_SECRET GONGKAN_BOOTSTRAP_ADMIN_PASSWORD; do
  require_env "$name"
done

for name in POSTGRES_PASSWORD MINIO_ROOT_PASSWORD GONGKAN_MINIO_SECRET_KEY GONGKAN_JWT_SECRET GONGKAN_BOOTSTRAP_ADMIN_PASSWORD DEEPSEEK_API_KEY; do
  reject_placeholder "$name"
done

for image_name in POSTGRES_IMAGE REDIS_IMAGE MINIO_IMAGE MINIO_MC_IMAGE QDRANT_IMAGE; do
  check_image_pin "$image_name"
done

[[ "$IMAGE_TAG" != "latest" ]] || fail "IMAGE_TAG must not be latest"

"${COMPOSE[@]}" config >/dev/null

available_kb="$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')"
min_kb="${MIN_FREE_DISK_KB:-5242880}"
[[ "$available_kb" -ge "$min_kb" ]] || fail "free disk is below ${min_kb}KB"

check_port 8080
check_port 9001
if [[ "${CHECK_EDGE_PORTS:-0}" == "1" ]]; then
  check_port 80
  check_port 443
fi

[[ -f "$PYTHON_CONFIG" ]] || fail "missing Python Docker config: $PYTHON_CONFIG"
check_http_reachable "chat" "$(extract_yaml_value chat_endpoint)"
check_http_reachable "embedding" "$(extract_yaml_value embedding_endpoint)"
check_http_reachable "rerank" "$(extract_yaml_value rerank_endpoint)"

if [[ "${SKIP_PYTHON_SMOKE:-0}" != "1" ]]; then
  "${COMPOSE[@]}" run --rm --no-deps worker python -c "import nested_doc_rag; print('python core ok')" >/dev/null
fi

echo "preflight ok"
