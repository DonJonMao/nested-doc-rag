# Operations Guide

## Local Startup

```bash
cp deployments/.env.example deployments/.env
make docker-up
export GONGKAN_JWT_SECRET='local-dev-secret-change-me'
export GONGKAN_BOOTSTRAP_ADMIN_PASSWORD='ChangeMe123!'
make run-api CONFIG=configs/config.local.yaml
make run-worker CONFIG=configs/config.local.yaml
```

Run API and worker as separate processes. The API serves HTTP, auth, upload/download, SSE, and metrics. The worker consumes Redis/Asynq jobs and calls Python Core through the configured CLI.

To run the compose-managed API and worker as well as dependencies:

```bash
COMPOSE_PROFILES=app make docker-up
```

## Dependencies

- PostgreSQL stores auth, workspace, file, artifact, job, fill, ingestion, and review metadata.
- Redis backs Asynq queues and run event pub/sub.
- MinIO or local storage stores uploaded files and archived artifacts.
- Python Core remains a separate CLI engine and owns Office parsing, RAG, embedding, Qdrant, and LLM orchestration.

## Configuration

Use YAML for non-secret defaults and environment variables for secrets. Important env vars:

- `GONGKAN_DATABASE_DSN`
- `GONGKAN_JWT_SECRET`
- `GONGKAN_BOOTSTRAP_ADMIN_PASSWORD`
- `GONGKAN_MINIO_SECRET_KEY`
- `GONGKAN_PYTHON_EXECUTABLE`
- `GONGKAN_PYTHON_PROJECT_DIR`
- `DEEPSEEK_API_KEY`

Observability and safety toggles:

- `GONGKAN_OBSERVABILITY_METRICS_ENABLED`
- `GONGKAN_OBSERVABILITY_PPROF_ENABLED`
- `GONGKAN_OBSERVABILITY_PPROF_ADDR`
- `GONGKAN_OBSERVABILITY_TRACING_ENABLED`
- `GONGKAN_SECURITY_RATE_LIMIT_ENABLED`
- `GONGKAN_SECURITY_RATE_LIMIT_RPS`
- `GONGKAN_SECURITY_RATE_LIMIT_BURST`
- `GONGKAN_SECURITY_MAX_BODY_SIZE`
- `GONGKAN_SECURITY_HSTS_ENABLED`
- `GONGKAN_OPERATIONS_GRACEFUL_SHUTDOWN_TIMEOUT`

## Commands

```bash
make ci
make docker-up
make docker-down
make docker-logs
make run-api CONFIG=configs/config.local.yaml
make run-worker CONFIG=configs/config.local.yaml
make smoke-api
make loadtest-smoke
```

## Data Directories

Runtime artifacts, temporary uploads, PostgreSQL data, and MinIO data are deployment state. Do not commit `.env`, `runtime/`, database volumes, or object storage volumes.

## Common Checks

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/metrics
curl -i http://localhost:8080/api/v1/ping
```

`/readyz` checks database, Redis, and storage. `/metrics` exposes Prometheus metrics without high-cardinality IDs.
