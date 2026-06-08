# Gongkan Platform Go Backend

This is the industrial platform service layer for `nested-doc-rag`.

Python Core handles knowledge ingestion and form filling. Go handles API, auth, storage, jobs, orchestration, review, download, and observability.

## Block 0 Scope

Implemented in this block:

- config
- logging
- router
- unified response
- unified error
- PostgreSQL connection
- Redis connection
- object storage abstraction
- health/ready
- metrics
- Docker Compose

Business modules are intentionally not implemented in Block 0.

## Run Locally

```bash
cp configs/config.example.yaml configs/config.local.yaml
make docker-up
make run-api CONFIG=configs/config.local.yaml
```

## Health Check

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/api/v1/ping
curl http://localhost:8080/metrics
```

## Tests

```bash
go test ./...
```

## Next Blocks

- Block 1: Auth/RBAC/Workspace
- Block 2: File Storage and Artifact Management
- Block 3: Task Queue, Worker, State Machine, SSE
- Block 4: Python Core integration
- Block 5: Gongkan form filling business
- Block 6: Knowledge document management and ingestion
- Block 7: Review queue and result download
- Block 8: Observability, security hardening, operations, load testing

Detailed design:

- [docs/go_backend_design.md](docs/go_backend_design.md)
