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

## Block 1 Scope

Implemented in this block:

- users
- roles
- JWT access token
- refresh token with rotation
- RBAC middleware
- workspace
- workspace members
- audit logs
- bootstrap admin

Not implemented in Block 1:

- file upload APIs
- knowledge base management
- form filling APIs
- task queues
- PythonRunner
- review queue
- artifact management APIs

## Block 2 Scope

Implemented in this block:

- workspace-scoped file upload
- file metadata table
- file download with auth
- file soft delete
- upload validation
- SHA256 checksum
- object storage integration
- artifact metadata model
- artifact download
- audit logs for file/artifact operations

Not implemented in Block 2:

- knowledge base management
- form filling business
- job queue
- PythonRunner
- review queue

## Block 3 Scope

Implemented in this block:

- jobs table
- run_events table
- JobService
- Redis/Asynq queue
- worker process
- job state machine
- cancellation
- retry foundation
- heartbeat
- ResourceLimiter
- SSE event broker
- Redis Pub/Sub event bridge
- persisted run events
- job query/cancel APIs

Not implemented in Block 3:

- fill_runs business
- knowledge_bases business
- review queue business

## Block 4 Scope

Implemented in this block:

- PythonRunner interface
- SubprocessPythonRunner
- Step15Agent command builder
- validate-artifacts command runner
- run_manifest parser
- artifact validator
- artifact archiver
- fill_form Python job handler
- ingest_knowledge Python job handler interface
- process timeout/cancel
- stdout/stderr tail capture
- worker integration with Python handlers

Not implemented in Block 4:

- form_files
- fill_runs
- knowledge_bases
- ingestion_jobs
- review_items
- public business APIs for form filling or knowledge ingestion

Go/Python boundary:

```text
Go calls:
python -m nested_doc_rag.cli run-step15-agent ...
python -m nested_doc_rag.cli validate-artifacts --run-dir <out_dir>

Go reads:
<out_dir>/run_manifest.json

Go archives:
manifest artifacts into ObjectStorage and run_artifacts table
```

The worker registers the `fill_form` handler with `SubprocessPythonRunner` and `ArtifactArchiver`. `ingest_knowledge` keeps a full runner interface, but the command remains disabled unless `python.ingest_command_enabled=true`.

## Block 5 Scope

Implemented in this block:

- gongkan form upload
- form_files table
- fill_runs table
- create fill run
- enqueue fill_form job
- materialize uploaded form into Python out_dir
- run Python Step15AgentRunner through Worker
- sync fill_runs from run_manifest
- artifact download shortcuts
- cancel fill run

Not implemented in Block 5:

- knowledge base management
- review approve/reject/edit
- ingestion jobs API
- human-edited re-writeback

## Block 6 Scope

Implemented in this block:

- knowledge_bases table
- knowledge_documents table
- knowledge_index_versions table
- ingestion_jobs table
- create/list/get knowledge base
- upload/list/delete knowledge documents
- create/list/cancel ingestion run
- index version management
- ingest_knowledge job payload
- worker integration with Python RunKnowledgeIngestion
- document materialization into ingestion out_dir
- ingestion lifecycle sync

Not implemented in Block 6:

- review approve/reject/edit
- human-edited writeback
- front-end
- Go-native document parsing
- Go-native embedding/Qdrant indexing

If `python.ingest_command_enabled=false`, knowledge bases and documents can still be managed, but creating an ingestion run returns `FEATURE_DISABLED`.

Create a knowledge base:

```bash
curl -X POST http://localhost:8080/api/v1/knowledge-bases \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"<workspace_id>",
    "name":"西咸4号楼知识库",
    "description":"测试知识库",
    "qdrant_collection":"datacenter_chunks_v1"
  }'
```

Upload a knowledge document:

```bash
curl -X POST http://localhost:8080/api/v1/knowledge-bases/<kb_id>/documents \
  -H "Authorization: Bearer <access_token>" \
  -F "document_role=knowledge_base" \
  -F "namespace=xixian_4" \
  -F "file=@./能力清单.xlsx"
```

Create an ingestion run:

```bash
curl -X POST http://localhost:8080/api/v1/knowledge-bases/<kb_id>/ingestion-runs \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "namespace":"xixian_4",
    "qdrant_collection":"datacenter_chunks_v1",
    "qdrant_namespace":"xixian_4",
    "resume":true
  }'
```

Watch ingestion events:

```bash
curl -N "http://localhost:8080/api/v1/runs/<ingestion_job_id>/events?workspace_id=<workspace_id>" \
  -H "Authorization: Bearer <access_token>"
```

Next Block:

Block 7: Review queue and result download.

## Run Locally

```bash
cp configs/config.example.yaml configs/config.local.yaml
make docker-up
export GONGKAN_JWT_SECRET='local-dev-secret-change-me'
export GONGKAN_BOOTSTRAP_ADMIN_PASSWORD='ChangeMe123!'
make run-api CONFIG=configs/config.local.yaml
```

Run the worker in a separate shell:

```bash
make run-worker CONFIG=configs/config.local.yaml
```

## Health Check

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/api/v1/ping
curl http://localhost:8080/metrics
```

## Bootstrap Admin

Bootstrap admin is controlled by config:

```yaml
auth:
  bootstrap_admin:
    enabled: true
    username: "admin"
    password_env: "GONGKAN_BOOTSTRAP_ADMIN_PASSWORD"
```

The password is read from `GONGKAN_BOOTSTRAP_ADMIN_PASSWORD`. It is never stored in config or logs.

## Auth Example

```bash
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"ChangeMe123!"}'
```

Use the returned access token:

```bash
curl http://localhost:8080/api/v1/auth/me \
  -H "Authorization: Bearer <access_token>"
```

Create a workspace:

```bash
curl -X POST http://localhost:8080/api/v1/workspaces \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"西咸数据中心","description":"测试工作区"}'
```

## File Example

Upload a file:

```bash
curl -X POST http://localhost:8080/api/v1/files \
  -H "Authorization: Bearer <access_token>" \
  -F "workspace_id=<workspace_id>" \
  -F "file_category=form_template" \
  -F "file=@./example.xlsx"
```

Download a file:

```bash
curl -OJ http://localhost:8080/api/v1/files/<file_id>/download \
  -H "Authorization: Bearer <access_token>"
```

## Jobs and Events

Subscribe to run events:

```bash
curl -N "http://localhost:8080/api/v1/runs/<run_id>/events?workspace_id=<workspace_id>" \
  -H "Authorization: Bearer <access_token>"
```

Create an infrastructure-only noop job when `jobs.enable_noop_job=true`:

```bash
curl -X POST http://localhost:8080/api/v1/admin/noop-jobs \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"<workspace_id>","sleep_ms":1000}'
```

The noop endpoint is for queue/worker/SSE verification only. It is not a business job creation API.

## Gongkan Form Filling

Upload a form:

```bash
curl -X POST http://localhost:8080/api/v1/forms \
  -H "Authorization: Bearer <token>" \
  -F "workspace_id=<workspace_id>" \
  -F "file=@./基地云机房信息调研表.xlsx"
```

Create a fill run:

```bash
curl -X POST http://localhost:8080/api/v1/fill-runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"<workspace_id>",
    "form_file_id":"<form_file_id>",
    "target_namespace":"xixian_4",
    "global_namespace":"global",
    "room_context":"西咸4号楼 301机房",
    "rows":"4-144",
    "judge":false,
    "writeback":true
  }'
```

Watch events:

```bash
curl -N "http://localhost:8080/api/v1/runs/<fill_run_id>/events?workspace_id=<workspace_id>" \
  -H "Authorization: Bearer <token>"
```

Download the filled form:

```bash
curl -OJ http://localhost:8080/api/v1/fill-runs/<run_id>/download/filled-form \
  -H "Authorization: Bearer <token>"
```

## Block 3 Event Delivery

`run_events` is the source of truth. SSE delivery uses database replay plus Redis-backed realtime fanout:

```text
Worker -> run_events table -> Redis Pub/Sub -> API SSE Broker -> Frontend SSE
```

When an SSE client connects, the API first replays persisted `run_events` after `after_sequence`, then subscribes to the in-process broker for live events. Worker events are written to PostgreSQL and published to Redis so a separate API process can forward them to its local SSE broker.

This supports separate API and worker processes. Future API replicas can subscribe to the same `jobs.event_channel`.

## Python Core Integration

Python execution is configured under `python`:

```yaml
python:
  executable: "python"
  project_dir: "../"
  config_path: "config/local.yaml"
  default_timeout: "2h"
  artifact_validation_enabled: true
  kill_grace_period: "10s"
  stdout_log_max_bytes: 1048576
  stderr_log_max_bytes: 1048576
  step15_default_retrieval_mode: "layered"
  step15_default_prompt_version: "step15_compat"
  step15_default_rows: "4-144"
  ingest_command_enabled: false
```

The Go worker does not import Python code or inspect RAG/agent internals. Job payloads carry only lightweight paths and options. Python writes all execution outputs under `out_dir`; Go validates artifacts through the Python CLI, reads `run_manifest.json`, and registers manifest artifacts.

## Tests

```bash
go test ./...
```

## Next Blocks

- Block 6: Knowledge document management and ingestion
- Block 7: Review queue and result download
- Block 8: Observability, security hardening, operations, load testing

Detailed design:

- [docs/go_backend_design.md](docs/go_backend_design.md)
