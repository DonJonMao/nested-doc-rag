# Docker Production Deployment

This document describes the single-server Docker Compose production baseline for the Gongkan platform.
For the current writeback-37 final profile and a Chinese step-by-step deployment checklist, see
`docs/DOCKER_WRITEBACK37_DEPLOYMENT_ZH.md`.

## Scope

The production baseline keeps the existing Go API plus Go Worker architecture:

- API container: Go API binary, configs, migrations.
- Worker container: Go worker binary, Python 3.11, installed `nested_doc_rag` wheel, configs, migrations, Python Docker config, and experiment profile copies.
- Postgres, Redis, MinIO, and Qdrant use persistent Docker volumes.
- Redis uses AOF persistence.
- Model Gateway remains disabled by default.
- `config/docker.yaml` is the production default used by the Go Worker. It currently follows the writeback-37 final profile:
  a single AnswerArbitration LLM agent plus rule-based grounding/writeback gate.

The Worker image is intentionally self-contained because the worker calls Python Core through subprocess:

```bash
python -m nested_doc_rag.cli ingest-knowledge
python -m nested_doc_rag.cli run-step15-agent
python -m nested_doc_rag.cli validate-artifacts
```

## Prepare Environment

From the Go server directory:

```bash
cd go-server
cp deployments/.env.prod.example deployments/.env.prod
```

Edit `deployments/.env.prod` before starting production:

```bash
vim deployments/.env.prod
```

At minimum, replace:

- `POSTGRES_PASSWORD`
- `GONGKAN_DATABASE_DSN`
- `MINIO_ROOT_PASSWORD`
- `GONGKAN_MINIO_SECRET_KEY`
- `GONGKAN_JWT_SECRET`
- `GONGKAN_BOOTSTRAP_ADMIN_PASSWORD`
- `DEEPSEEK_API_KEY`
- `REGISTRY`
- `IMAGE_NS`
- `IMAGE_TAG`

Do not commit `deployments/.env.prod`.

Dependency image versions are pinned in `deployments/.env.prod.example`:

- `POSTGRES_IMAGE`
- `REDIS_IMAGE`
- `MINIO_IMAGE`
- `MINIO_MC_IMAGE`
- `QDRANT_IMAGE`
- `CADDY_IMAGE`

Do not set these to `latest` in production.

## Preflight

Run preflight before first startup and before upgrades:

```bash
make preflight-prod
```

The preflight checks Docker and Compose availability, required `.env.prod` values, image tags, compose config, free disk, key ports, Python Core in the Worker image, and model endpoint reachability.

If the Worker image has not been built or pulled yet, build or pull it first. For configuration-only checks:

```bash
SKIP_PYTHON_SMOKE=1 make preflight-prod
```

## Validate Compose Config

```bash
make docker-prod-config
```

This expands `deployments/docker-compose.prod.yaml` with `deployments/.env.prod` and catches most YAML or missing variable mistakes before startup.

## Local Build

```bash
make docker-build IMAGE_TAG=local
```

This builds the API and Worker images from local source using the tags configured by:

```text
${REGISTRY}/${IMAGE_NS}/gongkan-api:${IMAGE_TAG}
${REGISTRY}/${IMAGE_NS}/gongkan-worker:${IMAGE_TAG}
```

Push images to the configured registry:

```bash
make docker-push IMAGE_TAG=v0.1.0
```

## Remote Registry Deployment

On a remote server, the source tree is not required. Copy only:

- `deployments/docker-compose.prod.yaml`
- `deployments/.env.prod`
- optionally `deployments/docker-compose.edge.yaml`
- optionally `deployments/Caddyfile`

Then pull and start:

```bash
docker compose --env-file deployments/.env.prod -f deployments/docker-compose.prod.yaml pull api worker
docker compose --env-file deployments/.env.prod -f deployments/docker-compose.prod.yaml up -d
```

This starts:

- `api`
- `worker`
- `postgres`
- `redis`
- `minio`
- `minio-init`
- `qdrant`

The API is exposed on `0.0.0.0:8080`. Postgres, Redis, and Qdrant are not published to the host. MinIO console is bound to `127.0.0.1:9001` and should be accessed through an SSH tunnel if needed.

## HTTPS Edge And Static Web

Build the frontend and place it at `web/dist`, then run the optional Caddy overlay:

```bash
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  -f deployments/docker-compose.edge.yaml \
  up -d
```

Set `DOMAIN` in `.env.prod`. Caddy terminates HTTPS, serves static frontend files from `/srv/web`, proxies `/api/*`, `/healthz`, `/readyz`, and `/metrics` to `api:8080`, and keeps SSE proxy flushing disabled for long-running event streams.

## Health Checks

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/metrics
```

## Worker Smoke Test

After the stack is running:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod exec worker python --version
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod exec worker python -c "import nested_doc_rag; print('python core ok')"
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod exec worker sh -lc "cd /app/python-core && python -m nested_doc_rag.cli --help >/tmp/nested_doc_rag_help.txt && head -n 5 /tmp/nested_doc_rag_help.txt"
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod exec worker sh -lc "cd /app/python-core && python -m nested_doc_rag.cli show-config --config config/docker.yaml --json >/tmp/nested_doc_rag_config.json && head -n 20 /tmp/nested_doc_rag_config.json"
```

The smoke test verifies:

```bash
python --version
python -c "import nested_doc_rag; print('python core ok')"
cd /app/python-core && python -m nested_doc_rag.cli --help
cd /app/python-core && python -m nested_doc_rag.cli show-config --config config/docker.yaml --json
```

## Logs

Follow API and Worker logs:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod logs -f api worker
```

For one service:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod logs -f worker
```

## Restart And Stop

Restart services while retaining volumes:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod restart
```

Stop services while retaining volumes:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod down
```

Do not run this in production unless you intend to delete data:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod down -v
```

`-v` removes Postgres, Redis, MinIO, and Qdrant persistent volumes.

## Backup

Create a backup:

```bash
make backup-prod BACKUP_DIR=/var/backups/gongkan/$(date +%Y%m%d_%H%M%S)
```

The backup includes:

- Postgres logical dump, when the database container is running.
- Docker volume archives for Postgres, Redis, MinIO, Qdrant, API runtime, Worker runtime, Python artifacts, and Python tmp data.
- `.env.prod`, compose files, Caddyfile, and resolved compose config snapshot.

## Restore

Restore from a backup directory:

```bash
make restore-prod BACKUP_DIR=/var/backups/gongkan/20260622_120000
```

The restore script stops the stack, restores available volume archives, and starts the stack again. Test restore on a staging host before relying on a backup plan.

## Upgrade

Push the new API and Worker images first, then upgrade the running server:

```bash
make backup-prod BACKUP_DIR=/var/backups/gongkan/pre-upgrade-$(date +%Y%m%d_%H%M%S)
IMAGE_TAG=v0.2.0 make upgrade-prod
```

`upgrade-prod` pulls the configured API/Worker image tag and recreates only those services.

## Rollback

Rollback to a previous image tag:

```bash
IMAGE_TAG=v0.1.9 make rollback-prod
```

If data also needs to be restored, pass a backup directory:

```bash
IMAGE_TAG=v0.1.9 BACKUP_DIR=/var/backups/gongkan/pre-upgrade-20260622_120000 make rollback-prod
```

## Common Failures

### `exec: "python": executable file not found`

The Worker image is not the Python runtime image or `GONGKAN_PYTHON_EXECUTABLE` is wrong.

Check:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod exec worker which python
```

Expected:

```text
/usr/local/bin/python
```

### `ModuleNotFoundError: No module named 'nested_doc_rag'`

The Python wheel was not installed or the Worker build context did not include `src/`.

Check:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod exec worker \
  python -c "import nested_doc_rag; print(nested_doc_rag.__file__)"
```

### `config/docker.yaml not found`

The Worker image did not copy the Python Docker config or Go config points to the wrong path.

Check:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod exec worker \
  ls -l /app/python-core/config
```

Expected:

```text
/app/python-core/config/docker.yaml
```

### Python cannot reach Qdrant

Inside Docker, Python must use the Compose service name:

```yaml
qdrant:
  url: "http://qdrant:6333"
```

Do not use `localhost:6333` from the Worker container.

### Postgres, Redis, or MinIO connection failure

Go Docker config should use Compose service names:

```text
postgres:5432
redis:6379
minio:9000
qdrant:6333
```

Verify `deployments/.env.prod` does not override these with host-local addresses.
