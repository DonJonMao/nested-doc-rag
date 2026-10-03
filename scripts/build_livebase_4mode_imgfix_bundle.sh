#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${IMAGE_TAG:-writeback-37-livebase-4mode-qdrantfix-nojudge-noasynqtimeout-progressfix-20260710}"
REGISTRY="${REGISTRY:-ghcr.io}"
IMAGE_NS="${IMAGE_NS:-donjonmao/nested-doc-rag}"
BUNDLE_NAME="server_bundle_writeback37_livebase_4mode_qdrantfix_nojudge_arm64"
BUNDLE_DIR="$ROOT/artifacts/$BUNDLE_NAME"
APP_TAR="$BUNDLE_DIR/images/gongkan-writeback37-livebase-4mode-qdrantfix-nojudge-linux-arm64-images.tar"
DEPS_TAR="$BUNDLE_DIR/images/gongkan-dependencies-linux-arm64-images.tar"
OLD_DEPS_TAR="$ROOT/artifacts/server_bundle_writeback37_livebase_4mode_imgfix_arm64/images/gongkan-dependencies-linux-arm64-images.tar"

cd "$ROOT"

docker version >/dev/null

# Image cleanup is an explicit, separately reviewed operation.

(
  cd "$ROOT/web"
  npm run build
)

(
  cd "$ROOT/go-server"
  docker buildx build --platform linux/arm64 \
    -t "$REGISTRY/$IMAGE_NS/gongkan-api:$TAG" \
    -f deployments/Dockerfile.api \
    --load .
)

docker buildx build --platform linux/arm64 \
  -t "$REGISTRY/$IMAGE_NS/gongkan-worker:$TAG" \
  -f go-server/deployments/Dockerfile.worker \
  --load .

docker buildx build --platform linux/arm64 \
  -t "$REGISTRY/$IMAGE_NS/gongkan-knowledge-seed:$TAG" \
  -f go-server/deployments/Dockerfile.knowledge-seed \
  --build-arg REGISTRY="$REGISTRY" \
  --build-arg IMAGE_NS="$IMAGE_NS" \
  --build-arg IMAGE_TAG="$TAG" \
  --load .

rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR/images" "$BUNDLE_DIR/deployments" "$BUNDLE_DIR/scripts" "$BUNDLE_DIR/web"

cp go-server/deployments/docker-compose.prod.yaml "$BUNDLE_DIR/deployments/docker-compose.prod.yaml"
cp go-server/deployments/docker-compose.edge.yaml "$BUNDLE_DIR/deployments/docker-compose.edge.yaml"
cp go-server/deployments/Caddyfile "$BUNDLE_DIR/deployments/Caddyfile"
cp -R web/dist "$BUNDLE_DIR/web/dist"
if [[ -f "$OLD_DEPS_TAR" ]]; then
  cp "$OLD_DEPS_TAR" "$DEPS_TAR"
else
  docker save \
    postgres:16.4 \
    redis:7.4 \
    minio/minio:RELEASE.2024-10-13T13-34-11Z \
    minio/mc:RELEASE.2024-10-08T09-37-26Z \
    qdrant/qdrant:v1.12.4 \
    caddy:2.8.4-alpine \
    -o "$DEPS_TAR"
fi

# Explicit model credentials only: MODELS_ENV_FILE or the runtime environment.
# The dotenv file is parsed as data; it is never sourced as shell code.
BUNDLE_ENV_PATH="$BUNDLE_DIR/deployments/.env.prod" \
BUNDLE_REGISTRY="$REGISTRY" BUNDLE_IMAGE_NS="$IMAGE_NS" BUNDLE_IMAGE_TAG="$TAG" \
BUNDLE_MODELS_ENV_FILE="${MODELS_ENV_FILE:-}" python3 - <<'PY_CREDENTIALS'
import os
import re
import secrets
import shlex
from pathlib import Path

api_key_name = re.compile(r"^[A-Z][A-Z0-9_]*_API_KEY$")
model_values = {
    name: value for name, value in os.environ.items() if api_key_name.fullmatch(name)
}
model_env_path = os.environ["BUNDLE_MODELS_ENV_FILE"]
if model_env_path:
    for number, line in enumerate(Path(model_env_path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not api_key_name.fullmatch(name):
            continue
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            raise SystemExit(f"Invalid model env quoting at line {number}.") from None
        if len(parts) > 1:
            raise SystemExit(f"Invalid model env value at line {number}.")
        model_values[name] = parts[0] if parts else ""
if not model_values.get("DEEPSEEK_API_KEY"):
    raise SystemExit("Set DEEPSEEK_API_KEY in MODELS_ENV_FILE or the runtime environment.")

postgres_password = secrets.token_hex(32)
minio_user = "gongkan-" + secrets.token_hex(8)
minio_password = secrets.token_hex(32)
values = {
    "COMPOSE_PROJECT_NAME": "gongkan",
    "REGISTRY": os.environ["BUNDLE_REGISTRY"],
    "IMAGE_NS": os.environ["BUNDLE_IMAGE_NS"],
    "IMAGE_TAG": os.environ["BUNDLE_IMAGE_TAG"],
    "POSTGRES_IMAGE": "postgres:16.4",
    "REDIS_IMAGE": "redis:7.4",
    "MINIO_IMAGE": "minio/minio:RELEASE.2024-10-13T13-34-11Z",
    "MINIO_MC_IMAGE": "minio/mc:RELEASE.2024-10-08T09-37-26Z",
    "QDRANT_IMAGE": "qdrant/qdrant:v1.12.4",
    "CADDY_IMAGE": "caddy:2.8.4-alpine",
    "DOMAIN": ":80",
    "GONGKAN_PUBLIC_HTTP_PORT": "8003",
    "GONGKAN_WEB_DIST_PATH": "../web/dist",
    "GONGKAN_SERVER_READ_TIMEOUT": "30m",
    "GONGKAN_SERVER_WRITE_TIMEOUT": "30m",
    "GONGKAN_FILES_MAX_UPLOAD_SIZE": "2048MB",
    "GONGKAN_SECURITY_MAX_BODY_SIZE": "2048MB",
    "GONGKAN_KNOWLEDGE_SEED_FORCE": "false",
    "POSTGRES_PASSWORD": postgres_password,
    "GONGKAN_DATABASE_DSN": f"postgres://gongkan:{postgres_password}@postgres:5432/gongkan?sslmode=disable",
    "GONGKAN_REDIS_ADDR": "redis:6379",
    "GONGKAN_REDIS_PASSWORD": "",
    "MINIO_ROOT_USER": minio_user,
    "MINIO_ROOT_PASSWORD": minio_password,
    "GONGKAN_MINIO_ENDPOINT": "minio:9000",
    "GONGKAN_MINIO_ACCESS_KEY": minio_user,
    "GONGKAN_MINIO_SECRET_KEY": minio_password,
    "GONGKAN_MINIO_BUCKET": "gongkan-platform",
    "GONGKAN_JWT_SECRET": secrets.token_hex(32),
    "GONGKAN_BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(32),
    "GONGKAN_BOOTSTRAP_ADMIN_PASSWORD_ENV": "GONGKAN_BOOTSTRAP_ADMIN_PASSWORD",
    "GONGKAN_PYTHON_EXECUTABLE": "/usr/local/bin/python",
    "GONGKAN_PYTHON_PROJECT_DIR": "/app/python-core",
    "GONGKAN_PYTHON_CONFIG_PATH": "config/docker.yaml",
    "GONGKAN_JOBS_WORKER_CONCURRENCY": "1",
    "GONGKAN_JOBS_MAX_PYTHON_PROCESSES": "1",
    "GONGKAN_JOBS_FILL_CONCURRENCY": "1",
    "GONGKAN_JOBS_INGESTION_CONCURRENCY": "1",
    "QDRANT_API_KEY": model_values.get("QDRANT_API_KEY", ""),
    "GONGKAN_MODEL_GATEWAY_ENABLED": "true",
    "NDR_MODEL_GATEWAY_TOKEN": secrets.token_hex(32),
    "GONGKAN_JOBS_DEFAULT_TIMEOUT": "0s",
    "GONGKAN_PYTHON_DEFAULT_TIMEOUT": "0s",
}
values.update(model_values)

def dotenv_literal(value):
    if any(character in value for character in "\r\n\x00"):
        raise SystemExit("Credential/env values must be single-line text.")
    # Single quotes prevent Compose interpolation of opaque API key contents.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

content = "".join(f"{name}={dotenv_literal(value)}\n" for name, value in values.items())
path = Path(os.environ["BUNDLE_ENV_PATH"])
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write(content)
path.chmod(0o600)
PY_CREDENTIALS

cat > "$BUNDLE_DIR/scripts/00-load-images.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail

cd "\$(dirname "\$0")/.."

app_images="images/$(basename "$APP_TAR")"
if [[ ! -f "\$app_images" ]]; then
  echo "missing \$app_images" >&2
  exit 1
fi

docker load -i images/gongkan-dependencies-linux-arm64-images.tar
docker load -i "\$app_images"
docker images | grep -E 'gongkan-(api|worker|knowledge-seed)'
EOF

cat > "$BUNDLE_DIR/scripts/01-start.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  -f deployments/docker-compose.edge.yaml \
  up -d
EOF

cat > "$BUNDLE_DIR/scripts/02-status.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  -f deployments/docker-compose.edge.yaml \
  ps

printf '\nAPI health:\n'
curl -fsS http://127.0.0.1:8080/healthz || true
printf '\n'
curl -fsS http://127.0.0.1:8080/readyz || true
printf '\n'

printf '\nQdrant collection:\n'
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  -f deployments/docker-compose.edge.yaml \
  run --rm --no-deps --entrypoint python knowledge-seed -c 'from qdrant_client import QdrantClient; c=QdrantClient(url="http://qdrant:6333", check_compatibility=False); name="datacenter_chunks_v1"; print(c.get_collections()); print("datacenter_chunks_v1_points", c.count(name, exact=True).count)'

frontend_port="$(grep -E '^GONGKAN_PUBLIC_HTTP_PORT=' deployments/.env.prod | tail -1 | cut -d= -f2 || true)"
frontend_port="${frontend_port:-8003}"
printf '\nFrontend health:\n'
curl -I "http://127.0.0.1:${frontend_port}/" || true
EOF

cat > "$BUNDLE_DIR/scripts/03-logs.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  -f deployments/docker-compose.edge.yaml \
  logs -f api worker knowledge-seed caddy
EOF

cat > "$BUNDLE_DIR/scripts/04-backup-postgres.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

mkdir -p backups
backup_file="backups/postgres-$(date +%Y%m%d_%H%M%S).sql"

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  exec -T postgres pg_dump -U gongkan -d gongkan > "$backup_file"

printf 'Wrote %s\n' "$backup_file"
EOF

cat > "$BUNDLE_DIR/scripts/05-stop.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  -f deployments/docker-compose.edge.yaml \
  down
EOF

cat > "$BUNDLE_DIR/scripts/06-restart.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  -f deployments/docker-compose.edge.yaml \
  restart
EOF


docker save \
  "$REGISTRY/$IMAGE_NS/gongkan-api:$TAG" \
  "$REGISTRY/$IMAGE_NS/gongkan-worker:$TAG" \
  "$REGISTRY/$IMAGE_NS/gongkan-knowledge-seed:$TAG" \
  -o "$APP_TAR"

chmod +x "$BUNDLE_DIR/scripts"/*.sh
(
  cd "$BUNDLE_DIR"
  shasum -a 256 images/*.tar > CHECKSUMS_SHA256.txt
)

tar -czf "$ROOT/artifacts/$BUNDLE_NAME.tar.gz" -C "$ROOT/artifacts" "$BUNDLE_NAME"
chmod 600 "$ROOT/artifacts/$BUNDLE_NAME.tar.gz"
ls -lh "$ROOT/artifacts/$BUNDLE_NAME.tar.gz" "$APP_TAR"
