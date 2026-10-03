#!/usr/bin/env bash
set -euo pipefail
# This library is shipped code, not a runtime env file.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 0 ]] || fail "usage: ./start.sh"
umask 077
require_tools
verify_checksums
load_image_settings
verify_bundle_architecture
[[ -f "$OFFLINE_DIR/images/all-images.tar" ]] || fail "bundled image archive is missing"
mkdir -p "$OFFLINE_DIR/runtime-logs"
docker load --input "$OFFLINE_DIR/images/all-images.tar" > "$OFFLINE_DIR/runtime-logs/image-load.log" 2>&1 || fail "image load failed; see runtime-logs/image-load.log"
verify_loaded_images
random_secret() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
if [[ ! -e "$OFFLINE_DIR/.env" ]]; then
  docker volume inspect datacenter-vnext_postgres_data >/dev/null 2>&1 && fail "existing default database volume has no local .env; restore its original credentials"
  db_password="$(random_secret)"; minio_password="$(random_secret)"
  jwt_secret="$(random_secret)"; admin_password="$(random_secret)"
  (set -o noclobber; cat > "$OFFLINE_DIR/.env" <<EOF
COMPOSE_PROJECT_NAME=datacenter-vnext
DOMAIN=:80
GONGKAN_PUBLIC_BIND=0.0.0.0
GONGKAN_PUBLIC_HTTP_PORT=8003
POSTGRES_PASSWORD=$db_password
GONGKAN_DATABASE_DSN=postgres://gongkan:$db_password@postgres:5432/gongkan?sslmode=disable
GONGKAN_REDIS_ADDR=redis:6379
GONGKAN_REDIS_PASSWORD=
MINIO_ROOT_USER=vnext-local
MINIO_ROOT_PASSWORD=$minio_password
GONGKAN_MINIO_ENDPOINT=minio:9000
GONGKAN_MINIO_ACCESS_KEY=vnext-local
GONGKAN_MINIO_SECRET_KEY=$minio_password
GONGKAN_MINIO_BUCKET=gongkan-platform
GONGKAN_MINIO_USE_SSL=false
GONGKAN_JWT_SECRET=$jwt_secret
GONGKAN_BOOTSTRAP_ADMIN_ENABLED=true
GONGKAN_BOOTSTRAP_ADMIN_USERNAME=admin
GONGKAN_BOOTSTRAP_ADMIN_PASSWORD=$admin_password
GONGKAN_BOOTSTRAP_ADMIN_PASSWORD_ENV=GONGKAN_BOOTSTRAP_ADMIN_PASSWORD
GONGKAN_MODEL_GATEWAY_ENABLED=false
GONGKAN_PYTHON_CONFIG_PATH=config/docker.yaml
GONGKAN_PYTHON_INGEST_COMMAND_ENABLED=true
EOF
  ) || fail "could not create local private .env"
  unset db_password minio_password jwt_secret admin_password
fi
[[ -f "$OFFLINE_DIR/.env" && ! -L "$OFFLINE_DIR/.env" ]] || fail "local .env must be a regular non-symlink file"
chmod 600 "$OFFLINE_DIR/.env"
if [[ ! -e "$OFFLINE_DIR/models.env" ]]; then (set -o noclobber; : > "$OFFLINE_DIR/models.env"); fi
[[ -f "$OFFLINE_DIR/models.env" && ! -L "$OFFLINE_DIR/models.env" ]] || fail "models.env must be a regular non-symlink file"
chmod 600 "$OFFLINE_DIR/models.env"
# Preserve dotenv quoting verbatim; never execute or print the private value.
# Leave the variable absent when no key is configured, rather than setting an empty API key.
qdrant_key="$(read_setting "$OFFLINE_DIR/models.env" QDRANT_API_KEY)"
: > "$OFFLINE_DIR/.qdrant.env"
if [[ -n "$qdrant_key" && "$qdrant_key" != "''" && "$qdrant_key" != '""' ]]; then
  printf 'QDRANT__SERVICE__API_KEY=%s\n' "$qdrant_key" > "$OFFLINE_DIR/.qdrant.env"
fi
unset qdrant_key
chmod 600 "$OFFLINE_DIR/.qdrant.env"
configure_compose
validate_private_settings
"${COMPOSE[@]}" config --quiet > "$OFFLINE_DIR/runtime-logs/config-check.log" 2>&1 || fail "invalid Compose or runtime configuration; see private runtime-logs/config-check.log"
if [[ -e "$OFFLINE_DIR/.initialized" ]]; then
  [[ "$(cat "$OFFLINE_DIR/.initialized")" == "$PROJECT" ]] || fail "initialized marker belongs to another project"
fi
if [[ -e "$OFFLINE_DIR/.fresh-install-pending" ]]; then
  [[ "$(cat "$OFFLINE_DIR/.fresh-install-pending")" == "$PROJECT" ]] || fail "fresh-install marker belongs to another project"
elif [[ ! -e "$OFFLINE_DIR/.initialized" ]]; then
  if ! docker volume inspect "${PROJECT}_postgres_data" >/dev/null 2>&1; then
    (set -o noclobber; printf '%s\n' "$PROJECT" > "$OFFLINE_DIR/.fresh-install-pending")
  else
    printf 'Existing database volume retained; fresh-install metadata cleanup is disabled.\n'
  fi
fi
stop_project_writers
"${COMPOSE[@]}" stop web
"${COMPOSE[@]}" up -d --pull never --no-build --wait postgres redis minio qdrant api worker
if [[ -f "$OFFLINE_DIR/.fresh-install-pending" ]]; then
  "${COMPOSE[@]}" exec -T postgres psql -U gongkan -d gongkan --no-psqlrc --set ON_ERROR_STOP=1 < "$OFFLINE_DIR/fresh-cleanup.sql" > "$OFFLINE_DIR/runtime-logs/fresh-cleanup.log" 2>&1 || fail "fresh-install cleanup failed; marker retained and web not started; see runtime-logs/fresh-cleanup.log"
fi
printf '%s\n' "$PROJECT" > "$OFFLINE_DIR/.initialized"
rm -f "$OFFLINE_DIR/.fresh-install-pending"
"${COMPOSE[@]}" up -d --pull never --no-build --wait web
port="$(read_setting "$OFFLINE_DIR/.env" GONGKAN_PUBLIC_HTTP_PORT)"
username="$(read_setting "$OFFLINE_DIR/.env" GONGKAN_BOOTSTRAP_ADMIN_USERNAME)"
printf 'Deployment ready on HTTP port %s. Bootstrap username: %s. Credentials are stored only in the local .env (mode 600).\n' "${port:-8003}" "${username:-admin}"
