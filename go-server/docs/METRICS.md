# Metrics Reference

Prometheus metrics are exposed at `/metrics` when metrics are enabled. Labels intentionally avoid `run_id`, `user_id`, `workspace_id`, and file IDs.

## HTTP

- `http_requests_total{method,path,status,code}`: request count.
- `http_request_duration_seconds{method,path,status,code}`: request latency.
- `http_requests_in_flight{method,path}`: current in-flight requests.
- `http_request_body_bytes{method,path}`: request body bytes.
- `http_response_body_bytes{method,path,status}`: response body bytes.

`path` is the route pattern when available, otherwise a normalized path fallback.

## Jobs and Worker

- `jobs_created_total{job_type}`
- `jobs_queued_total{job_type}`
- `jobs_started_total{job_type}`
- `jobs_finished_total{job_type,status}`
- `jobs_failed_total{job_type,error_class}`
- `job_duration_seconds{job_type,status}`
- `job_attempts_total{job_type}`
- `job_cancel_requested_total{job_type}`
- `worker_running_jobs{job_type}`
- `worker_limiter_in_use{resource}`
- `worker_limiter_capacity{resource}`

## Python

- `python_runs_total{command,status}`
- `python_run_duration_seconds{command,status}`
- `python_process_running{command}`
- `python_process_exit_total{command,exit_code}`
- `python_artifact_validation_total{status}`
- `python_artifacts_registered_total{artifact_type}`

## Files and Artifacts

- `file_upload_total{category,status}`
- `file_upload_bytes_total{category}`
- `file_download_total{category,status}`
- `artifact_download_total{artifact_type,status}`
- `artifact_register_total{artifact_type,status}`

## Business

- `fill_runs_total{status}`
- `ingestion_runs_total{status}`
- `review_items_total{status,risk_level}`
- `review_actions_total{action}`

## SSE and Readiness

- `sse_connections_current`
- `sse_connections_total`
- `sse_events_sent_total{event_type}`
- `sse_client_disconnect_total`
- `app_ready_checks_total{component,status}`

Keep labels low-cardinality. Use logs and database queries for per-run investigation.
