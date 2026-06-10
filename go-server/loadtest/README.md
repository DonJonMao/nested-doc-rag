# Load Tests

These k6 scripts are manual operational checks. They do not run in default unit tests or CI.

Set environment variables before running:

```bash
export BASE_URL=http://localhost:8080
export USERNAME=admin
export PASSWORD='ChangeMe123!'
export TOKEN='<access_token>'
export WORKSPACE_ID='<workspace_id>'
export RUN_ID='<run_id>'
export ARTIFACT_URL='/api/v1/fill-runs/<run_id>/download/filled-form'
```

Run:

```bash
k6 run loadtest/k6/smoke.js
k6 run loadtest/k6/login.js
k6 run loadtest/k6/sse_events.js
k6 run loadtest/k6/download_artifact.js
```
