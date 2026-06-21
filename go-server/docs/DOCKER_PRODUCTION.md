# Docker Production Deployment

This document describes the single-server Docker Compose production baseline for the Gongkan platform.

## Scope

The production baseline keeps the existing Go API plus Go Worker architecture:

- API container: Go API binary, configs, migrations.
- Worker container: Go worker binary, Python 3.11, installed `nested_doc_rag` wheel, configs, migrations, and Python Docker config.
- Postgres, Redis, MinIO, and Qdrant use persistent Docker volumes.
- Redis uses AOF persistence.
- Model Gateway remains disabled by default.

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

Do not commit `deployments/.env.prod`.

## Validate Compose Config

```bash
make docker-prod-config
```

This expands `deployments/docker-compose.prod.yaml` with `deployments/.env.prod` and catches most YAML or missing variable mistakes before startup.

## Start Production Stack

```bash
make docker-prod-up
```

This builds and starts:

- `api`
- `worker`
- `postgres`
- `redis`
- `minio`
- `minio-init`
- `qdrant`

The API is exposed on `0.0.0.0:8080`. Postgres, Redis, and Qdrant are not published to the host. MinIO console is bound to `127.0.0.1:9001` and should be accessed through an SSH tunnel if needed.

## Health Checks

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/metrics
```

## Worker Smoke Test

After the stack is running:

```bash
make docker-prod-worker-smoke
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
make docker-prod-logs
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
make docker-prod-down
```

Do not run this in production unless you intend to delete data:

```bash
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod down -v
```

`-v` removes Postgres, Redis, MinIO, and Qdrant persistent volumes.

## Backup Recommendations

Back up these Docker volumes before upgrades:

- `postgres_data`
- `redis_data`
- `minio_data`
- `qdrant_data`
- `api_runtime`
- `worker_runtime`
- `worker_python_artifacts`

Recommended approach:

1. Stop write traffic.
2. Run database-native backups for Postgres.
3. Snapshot MinIO and Qdrant volumes.
4. Keep at least one tested restore path before deleting old volumes.

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
