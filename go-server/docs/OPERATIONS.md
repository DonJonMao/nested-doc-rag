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

`make docker-up` starts PostgreSQL, Redis, MinIO, and Qdrant. The API and
worker are still intended to run as separate local processes unless you enable
the compose `app` profile.

Run API and worker as separate processes. The API serves HTTP, auth, upload/download, SSE, and metrics. The worker consumes Redis/Asynq jobs and calls Python Core through the configured CLI.

To run the compose-managed API and worker as well as dependencies:

```bash
COMPOSE_PROFILES=app make docker-up
```

## Dependencies

- PostgreSQL stores auth, workspace, file, artifact, job, fill, ingestion, and review metadata.
- Redis backs Asynq queues and run event pub/sub.
- MinIO or local storage stores uploaded files and archived artifacts.
- Qdrant stores Python Core vector indexes for knowledge retrieval.
- Python Core remains a separate CLI engine and owns Office parsing, RAG, embedding, Qdrant, and LLM orchestration.

Python Core defaults to an embedded local Qdrant path from `paths.qdrant_path`. To use the Compose-managed Qdrant service, set this in `config/local.yaml`:

```yaml
qdrant:
  url: "http://localhost:6333"
  collection_name: datacenter_chunks_v1
```

## Worker Recovery

The worker writes heartbeat timestamps while jobs are running. On startup it scans for `running` and `cancel_requested` jobs whose latest heartbeat is older than three heartbeat intervals:

- stale `running` jobs are marked `failed`
- stale `cancel_requested` jobs are marked `canceled`
- fill-run and ingestion lifecycle tables are updated through the registered Python job handlers
- fresh running jobs with recent heartbeats are left alone, which allows multiple worker processes as long as they share the same database and Redis queue

Keep these defaults for the product flow unless you intentionally want more parallel Python processes:

```yaml
jobs:
  fill_concurrency: 1
  ingestion_concurrency: 1
  max_python_processes: 1
```

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

`/readyz` checks the Go API's direct dependencies: database, Redis, and storage.
Python Core owns Qdrant access during ingestion and fill execution. `/metrics`
exposes Prometheus metrics without high-cardinality IDs.
