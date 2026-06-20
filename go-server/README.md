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
python -m nested_doc_rag.cli ingest-knowledge ...
python -m nested_doc_rag.cli run-step15-agent ...
python -m nested_doc_rag.cli validate-artifacts --run-dir <out_dir>

Go reads:
<out_dir>/run_manifest.json

Go archives:
manifest artifacts into ObjectStorage and run_artifacts table
```

The worker registers both `ingest_knowledge` and `fill_form` with `SubprocessPythonRunner`. Ingestion parses uploaded knowledge documents, embeds real chunks, upserts the selected namespace into Qdrant, and archives the resulting manifest artifacts.

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

`python.ingest_command_enabled` defaults to `true` for the product flow. Set it to `false` only when the deployment intentionally wants to manage knowledge metadata without allowing Python-backed indexing; in that mode creating an ingestion run returns `FEATURE_DISABLED`.

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

## Block 7 Scope

Implemented:

- review_items table
- review item import from Python artifacts
- review queue API
- approve/reject/edit/ignore/reopen
- review audit logs
- review export JSON/CSV
- result center API
- fill_form worker integration with review import

Not implemented in Block 7:

- human-edited re-writeback
- reviewed_filled_form.xlsx generation
- multi-level approval workflow
- front-end UI

List review items:

```bash
curl "http://localhost:8080/api/v1/fill-runs/<run_id>/review-items?status=pending" \
  -H "Authorization: Bearer <token>"
```

Approve:

```bash
curl -X POST http://localhost:8080/api/v1/review-items/<item_id>/approve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"comment":"确认可用"}'
```

Edit:

```bash
curl -X POST http://localhost:8080/api/v1/review-items/<item_id>/edit \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"edited_answer":"人工确认后的答案","comment":"现场确认"}'
```

Export:

```bash
curl -OJ "http://localhost:8080/api/v1/fill-runs/<run_id>/review-items/export?format=csv" \
  -H "Authorization: Bearer <token>"
```

Result center:

```bash
curl "http://localhost:8080/api/v1/fill-runs/<run_id>/result" \
  -H "Authorization: Bearer <token>"
```

Next Block:

Block 8: Observability, security hardening, operations, load testing.

## Block 8 Scope

Implemented:

- expanded Prometheus metrics
- HTTP metrics middleware with route/status/body/response tracking
- security headers
- in-memory per-IP rate limiting
- request body size limit
- optional pprof server bound separately from the main API router
- tracing foundation with no-op provider and HTTP/job/Python span hooks
- log and config secret redaction
- operational docs and runbooks
- k6 load test scripts
- smoke scripts
- CI workflow
- Docker and Makefile operation commands

Not implemented in Block 8:

- new business modules
- Go-native RAG, LLM, Qdrant, or Office parsing
- human-edited re-writeback
- distributed Redis rate limiting
- full OpenTelemetry exporter wiring

## Productized Gongkan Flow

The platform now exposes frontend-oriented APIs described in `gongkan_full_system_design_apple.md`:

- `GET /api/v1/knowledge-bases/options?workspace_id=...` returns selectable knowledge-base cards with `namespace`, `status`, `document_count`, and `last_ingested_at`.
- `POST /api/v1/fill-runs/simple` creates a fill run from `knowledge_base_id`, `form_file_id`, and optional `room_context`; the server derives `target_namespace`, current index version, default rows, retrieval mode, prompt version, and writeback settings.
- `GET /api/v1/fill-runs?...&mine=true` returns only the current user's runs; non-admin users are always restricted to their own fill runs for list/get/cancel/download operations.
- `POST /api/v1/knowledge-bases/{kb_id}/documents?auto_ingest=true` uploads a document and immediately creates an ingestion run.
- `DELETE /api/v1/documents/{doc_id}?reindex=true` soft-deletes a document, marks the knowledge base stale, and queues a namespace rebuild ingestion run.

Knowledge-base write operations require the global `admin` role. Ordinary workspace users can read ready knowledge-base options and create their own fill runs.

Migration `000008_productized_knowledge_fill.sql` adds product fields to `knowledge_bases`, `knowledge_documents`, and `fill_runs`, and seeds the default workspace plus the 9 fixed knowledge bases:

```text
西咸1号楼, 西咸2号楼, 西咸3号楼, 西咸4号楼, 西咸5号楼, 西咸6号楼, 城东浐灞, 西安, 咸阳
```

Default Python resource limits are conservative for production safety:

```yaml
jobs:
  fill_concurrency: 1
  ingestion_concurrency: 1
  max_python_processes: 1
```

## Production Notes

- pprof is disabled by default. If enabled, bind it to localhost or an internal network only.
- Secrets must come from env or a secret manager; do not commit `.env`.
- Authorization headers, passwords, refresh tokens, DB passwords, and provider API keys are redacted from config summaries and logs.
- Prometheus metrics are exposed at `/metrics` and avoid high-cardinality IDs as labels.
- API and worker should run as separate processes.
- On worker startup, stale `running` jobs whose heartbeat has expired are marked `failed`; stale `cancel_requested` jobs are marked `canceled`. The fill-run or ingestion lifecycle is updated through the registered Python job handler recovery hook.
- Python Core remains a separate CLI engine; Go does not call LLMs directly.

## Operational Commands

```bash
make ci
make docker-up
make docker-down
make docker-logs
make run-api CONFIG=configs/config.local.yaml
make run-worker CONFIG=configs/config.local.yaml
make smoke-api
make smoke-auth
make smoke-files
make smoke-jobs
make smoke-product-flow
make loadtest-smoke
```

`make docker-up` starts the local infrastructure declared in
`deployments/docker-compose.yaml`: PostgreSQL, Redis, MinIO, and Qdrant. Use
`COMPOSE_PROFILES=app make docker-up` when you also want the compose-managed API
and worker containers.

Detailed operations docs:

- [Operations](docs/OPERATIONS.md)
- [Security](docs/SECURITY.md)
- [Runbook](docs/RUNBOOK.md)
- [Load testing](docs/LOAD_TESTING.md)
- [Metrics](docs/METRICS.md)

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

Create a product fill run:

```bash
curl -X POST http://localhost:8080/api/v1/fill-runs/simple \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"<workspace_id>",
    "knowledge_base_id":"<ready_knowledge_base_id>",
    "form_file_id":"<form_file_id>",
    "room_context":"西咸4号楼 301机房"
  }'
```

The server derives `target_namespace`, `index_version_id`, `rows`, retrieval mode, prompt version, judge flags, and writeback defaults from the selected ready knowledge base and product configuration. The lower-level `POST /api/v1/fill-runs` endpoint remains for admin/debug use; non-admin callers must still bind it to a ready knowledge base and cannot override product runtime defaults.

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

Run a product smoke flow that uses a real ready knowledge base and creates a real fill run:

```bash
FORM_FILE_PATH="../data/工勘单/基地云机房信息调研表.xlsx" \
TOKEN="<token>" \
WORKSPACE_ID="<workspace_id>" \
make smoke-product-flow
```

If `TOKEN` is omitted, set `USERNAME` and `PASSWORD`. If `WORKSPACE_ID` or `READY_KB_ID` is omitted, the script discovers the first accessible workspace and first ready knowledge base. The script stops when no ready knowledge base exists; it does not simulate ingestion or fill success.

## Block 3 Event Delivery

`run_events` is the source of truth. SSE delivery uses database replay plus Redis-backed realtime fanout:

```text
Worker -> run_events table -> Redis Pub/Sub -> API SSE Broker -> Frontend SSE
```

When an SSE client connects, the API first replays persisted `run_events` after `after_sequence` or the `Last-Event-ID` header, then subscribes to the in-process broker for live events. Worker events are written to PostgreSQL and published to Redis so a separate API process can forward them to its local SSE broker.

This supports separate API and worker processes. Future API replicas can subscribe to the same `jobs.event_channel`.

## Product Permission Notes

- Ordinary users use `GET /api/v1/knowledge-bases/options`; full knowledge-base metadata, index versions, document lists, ingestion runs, and `knowledge_document` files are admin-only.
- Knowledge document upload should go through `POST /api/v1/knowledge-bases/{kb_id}/documents?auto_ingest=true`; generic `/files` rejects `file_category=knowledge_document` for non-admin users.
- Fill-run event streams and artifact downloads are owner-scoped for non-admin users. Ingestion event streams and ingestion artifacts are admin-only.
- `run_events.sequence` is strictly increasing within each `run_id`; clients should reconnect SSE with `after_sequence=<last_seen_sequence>` or the standard `Last-Event-ID` header.

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
  ingest_command_enabled: true
```

Python Core reads Qdrant settings from `config/local.yaml`. Leave `qdrant.url` empty to use the local embedded `paths.qdrant_path`, or set `qdrant.url: "http://localhost:6333"` to use the Docker Compose Qdrant service. `qdrant.api_key_env` names the environment variable for secured Qdrant deployments.

The Go worker does not import Python code or inspect RAG/agent internals. Job payloads carry only lightweight paths and options. Python writes all execution outputs under `out_dir`; Go validates artifacts through the Python CLI, reads `run_manifest.json`, and registers manifest artifacts.

## Tests

```bash
go test ./...
```

## Next Step

This completes the initial industrial V1 backend blocks.

Future work:

- human-edited re-writeback
- advanced review workflow
- distributed Redis rate limit
- OIDC/SSO
- Kubernetes deployment
- HA PostgreSQL/Redis
- model gateway

Detailed design:

- [docs/go_backend_design.md](docs/go_backend_design.md)
