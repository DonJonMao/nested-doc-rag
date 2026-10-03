#!/usr/bin/env bash
# Shared implementation. Runtime env files are parsed by Compose, never sourced.
set -euo pipefail
OFFLINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail() { printf 'Deployment failed: %s\n' "$*" >&2; exit 1; }
require_tools() {
  command -v docker >/dev/null 2>&1 || fail "Docker is required"
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
}
normalize_arch() {
  case "$1" in amd64|x86_64) printf amd64;; arm64|aarch64) printf arm64;; *) fail "unsupported architecture";; esac
}
read_setting() {
  # Return a scalar without executing dotenv contents.
  awk -v name="$2" 'index($0,name "=")==1 {v=substr($0,length(name)+2); sub(/\r$/, "",v); print v; exit}' "$1"
}
load_image_settings() {
  [[ -f "$OFFLINE_DIR/images.env" ]] || fail "images.env is missing"
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || fail "invalid images.env entry"
    key="${line%%=*}"; value="${line#*=}"
    case "$key" in API_IMAGE|WORKER_IMAGE|WEB_IMAGE|POSTGRES_IMAGE|REDIS_IMAGE|MINIO_IMAGE|QDRANT_IMAGE|BUNDLE_ARCH) ;;
      *) fail "unexpected images.env variable";;
    esac
    [[ "$value" =~ ^[a-zA-Z0-9_./:@-]+$ ]] || fail "invalid image reference or architecture"
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$OFFLINE_DIR/images.env"
  for key in API_IMAGE WORKER_IMAGE WEB_IMAGE POSTGRES_IMAGE REDIS_IMAGE MINIO_IMAGE QDRANT_IMAGE BUNDLE_ARCH; do
    [[ -n "${!key:-}" ]] || fail "missing bundled image setting: $key"
  done
}
configure_compose() {
  [[ -f "$OFFLINE_DIR/.env" ]] || fail "local .env is missing; run ./start.sh first"
  PROJECT="$(read_setting "$OFFLINE_DIR/.env" COMPOSE_PROJECT_NAME)"
  PROJECT="${PROJECT#\"}"; PROJECT="${PROJECT%\"}"; PROJECT="${PROJECT#\'}"; PROJECT="${PROJECT%\'}"
  [[ "$PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail "invalid local Compose project name"
  # Host env must not silently replace the bundle's persisted credentials or ports.
  unset POSTGRES_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD DOMAIN GONGKAN_PUBLIC_HTTP_PORT GONGKAN_PUBLIC_BIND QDRANT_API_KEY
  COMPOSE=(docker compose --project-name "$PROJECT" --env-file "$OFFLINE_DIR/images.env" --env-file "$OFFLINE_DIR/.env" --env-file "$OFFLINE_DIR/models.env" -f "$OFFLINE_DIR/compose.yaml")
}
verify_checksums() {
  [[ -f "$OFFLINE_DIR/SHA256SUMS" ]] || fail "SHA256SUMS is missing"
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$OFFLINE_DIR" && sha256sum --check --status SHA256SUMS) || fail "bundle checksum verification failed"
  elif command -v shasum >/dev/null 2>&1; then
    (cd "$OFFLINE_DIR" && shasum -a 256 --check SHA256SUMS >/dev/null) || fail "bundle checksum verification failed"
  else
    fail "sha256sum or shasum is required"
  fi
}
manifest_value() {
  # The builder emits pretty JSON with one scalar field per line. No eval/source.
  sed -nE 's/^[[:space:]]*"'"$1"'"[[:space:]]*:[[:space:]]*"([^"\\]*)"[[:space:]]*,?[[:space:]]*$/\1/p' "$OFFLINE_DIR/bundle.json" | awk 'NR==1 {print}'
}
manifest_image_value() {
  awk -v key="$1" -v field="$2" '
    /"variable"[[:space:]]*:/ {active=index($0,"\"" key "\"")>0}
    active && index($0,"\"" field "\"")>0 {v=$0;sub(/^[^:]*:[[:space:]]*"/,"",v);sub(/"[[:space:]]*,?[[:space:]]*$/, "",v);print v;exit}
  ' "$OFFLINE_DIR/bundle.json"
}
verify_bundle_architecture() {
  [[ -f "$OFFLINE_DIR/bundle.json" ]] || fail "bundle.json is missing"
  [[ "$(manifest_value schema_version)" == nested-doc-rag-offline-bundle-v1 ]] || fail "unsupported bundle schema"
  [[ "$(manifest_value architecture)" == "$BUNDLE_ARCH" ]] || fail "manifest and image architecture disagree"
  [[ "$(manifest_value platform)" == "linux/$BUNDLE_ARCH" ]] || fail "invalid bundle platform"
  [[ "$(manifest_value source_commit)" =~ ^[a-f0-9]{40}$ ]] || fail "invalid source revision"
  [[ "$(manifest_value path)" == images/all-images.tar ]] || fail "unexpected image archive path"
  local archive_sha actual_sha
  archive_sha="$(manifest_value sha256)"
  [[ "$archive_sha" =~ ^[a-f0-9]{64}$ ]] || fail "invalid image archive checksum"
  if command -v sha256sum >/dev/null 2>&1; then
    actual_sha="$(sha256sum "$OFFLINE_DIR/images/all-images.tar")"
  else
    actual_sha="$(shasum -a 256 "$OFFLINE_DIR/images/all-images.tar")"
  fi
  [[ "${actual_sha%% *}" == "$archive_sha" ]] || fail "image archive differs from bundle manifest"
  [[ "$(normalize_arch "$(uname -m)")" == "$BUNDLE_ARCH" ]] || fail "bundle does not match the host architecture"
  local daemon_arch
  daemon_arch="$(docker info --format '{{.Architecture}}')" || fail "Docker daemon is unavailable"
  [[ "$(normalize_arch "$daemon_arch")" == "$BUNDLE_ARCH" ]] || fail "bundle does not match the Docker daemon architecture"
}
verify_loaded_images() {
  local key expected reference actual local_id image_arch image_os revision config_digest rootfs_expected rootfs_layers rootfs_actual
  : > "$OFFLINE_DIR/runtime-logs/loaded-images.log"
  for key in API_IMAGE WORKER_IMAGE WEB_IMAGE POSTGRES_IMAGE REDIS_IMAGE MINIO_IMAGE QDRANT_IMAGE; do
    expected="$(manifest_image_value "$key" id)"
    reference="$(manifest_image_value "$key" reference)"
    config_digest="$(manifest_image_value "$key" config_digest)"
    [[ "$reference" == "${!key}" && "$expected" =~ ^sha256:[a-f0-9]{64}$ ]] || fail "invalid manifest image identity: $key"
    actual="$(docker image inspect "${!key}" --format '{{.Id}} {{.Architecture}} {{.Os}} {{index .Config.Labels "org.opencontainers.image.revision"}}')" || fail "bundled image is missing: $key"
    read -r local_id image_arch image_os revision <<< "$actual"
    [[ "$image_os" == linux && "$(normalize_arch "$image_arch")" == "$BUNDLE_ARCH" ]] || fail "loaded image platform differs: $key"
    case "$key" in API_IMAGE|WORKER_IMAGE|WEB_IMAGE)
      [[ "$revision" == "$(manifest_value source_commit)" ]] || fail "application image source revision differs: $key";;
    esac
    rootfs_expected="$(manifest_image_value "$key" rootfs_sha256)"
    [[ "$rootfs_expected" =~ ^[a-f0-9]{64}$ ]] || fail "invalid bundled RootFS fingerprint: $key"
    rootfs_layers="$(docker image inspect "${!key}" --format '{{range .RootFS.Layers}}{{println .}}{{end}}')" || fail "cannot read bundled RootFS: $key"
    if command -v sha256sum >/dev/null 2>&1; then
      rootfs_actual="$(printf '%s\n' "$rootfs_layers" | sha256sum)"
    else
      rootfs_actual="$(printf '%s\n' "$rootfs_layers" | shasum -a 256)"
    fi
    [[ "${rootfs_actual%% *}" == "$rootfs_expected" ]] || fail "loaded RootFS differs from bundled image: $key"
    # Docker classic and containerd stores can expose different .Id digests.
    # Archive hash + platform + app revision are the portable checks; keep both IDs.
    printf '%s build_id=%s config_digest=%s local_id=%s platform=%s/%s rootfs_sha256=%s\n' "$key" "$expected" "$config_digest" "$local_id" "$image_os" "$image_arch" "$rootfs_expected" >> "$OFFLINE_DIR/runtime-logs/loaded-images.log"
  done
}
validate_private_settings() {
  local key value
  for key in POSTGRES_PASSWORD GONGKAN_DATABASE_DSN MINIO_ROOT_USER MINIO_ROOT_PASSWORD GONGKAN_MINIO_ACCESS_KEY GONGKAN_MINIO_SECRET_KEY GONGKAN_JWT_SECRET GONGKAN_BOOTSTRAP_ADMIN_PASSWORD; do
    value="$(read_setting "$OFFLINE_DIR/.env" "$key")"
    value="${value#\"}"; value="${value%\"}"; value="${value#\'}"; value="${value%\'}"
    [[ -n "$value" ]] || fail "required local setting is empty: $key"
    case "$value" in generated-on-server|change-this*|replace-with-*) fail "replace local placeholder: $key";; esac
  done
}
stop_project_writers() {
  # Match only this project, including a retained legacy seed container.
  local service ids
  for service in api worker knowledge-seed; do
    ids="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$service")"
    if [[ -n "$ids" ]]; then
      while IFS= read -r container_id; do docker stop "$container_id" >/dev/null; done <<< "$ids"
    fi
  done
}
