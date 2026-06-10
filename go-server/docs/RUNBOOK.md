# Runbook

## Python Job Failed

Symptom: fill or ingestion job enters `failed`.

Check:

```bash
curl "http://localhost:8080/api/v1/jobs/<job_id>" -H "Authorization: Bearer <token>"
curl -N "http://localhost:8080/api/v1/runs/<run_id>/events?workspace_id=<workspace_id>" -H "Authorization: Bearer <token>"
```

Metrics: `jobs_failed_total`, `python_runs_total`, `python_process_exit_total`.

Likely cause: Python config path, missing dependency, invalid input artifact, timeout, or provider API failure.

Mitigation: inspect job error, stdout/stderr tail, Python config, and retry after fixing the dependency.

## Artifact Validation Failed

Symptom: run completed in Python but Go marks post-processing failed.

Metrics: `python_artifact_validation_total`.

Likely cause: missing `run_manifest.json`, manifest references a missing artifact, or storage registration failed.

Mitigation: verify `out_dir`, run Python `validate-artifacts`, then retry the job.

## Redis Unavailable

Symptom: API cannot enqueue jobs, worker idle, `/readyz` fails Redis.

Metrics: `app_ready_checks_total{component="redis"}`.

Mitigation: restart Redis, verify `GONGKAN_REDIS_ADDR`, check network/DNS, then restart API and worker if connections do not recover.

## MinIO Unavailable

Symptom: upload/download/archive fails, `/readyz` storage check fails.

Metrics: `file_upload_total{status="failed"}`, `artifact_register_total{status="failed"}`.

Mitigation: verify endpoint, bucket, credentials, and MinIO health. Re-run failed jobs after storage is restored.

## DB Migration Failed

Symptom: API startup fails while applying migrations.

Check migration logs and PostgreSQL connectivity. Confirm the target database is not partially modified by an interrupted manual migration.

Mitigation: restore from backup if schema is inconsistent, then re-run migrations.

## SSE No Events

Symptom: client connects but receives no live updates.

Metrics: `sse_connections_current`, `sse_events_sent_total`.

Likely cause: wrong `workspace_id`, auth failure, worker not publishing, Redis pub/sub disabled, or job not started.

Mitigation: check persisted `run_events`, worker logs, `jobs.event_bus_enabled`, and `jobs.event_channel`.

## Worker Stuck or Queue Backlog

Metrics: `worker_running_jobs`, `worker_limiter_in_use`, `worker_limiter_capacity`, `jobs_started_total`.

Likely cause: Python process exhaustion, long jobs, Redis issue, or concurrency too low.

Mitigation: inspect running jobs, increase worker concurrency conservatively, and ensure Python process limits match host capacity.

## Rate Limit Too Aggressive

Symptom: clients receive HTTP 429.

Metrics: `http_requests_total{status="429"}`.

Mitigation: increase `GONGKAN_SECURITY_RATE_LIMIT_RPS` and `GONGKAN_SECURITY_RATE_LIMIT_BURST`, or disable only in trusted local environments.

## Cancel Job Not Effective

Symptom: cancel requested but Python process continues.

Likely cause: process is in non-interruptible work or worker lost heartbeat.

Mitigation: inspect job status and worker logs. The worker uses configured Python kill grace period and retry semantics.
