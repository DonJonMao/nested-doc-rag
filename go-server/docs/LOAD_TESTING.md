# Load Testing

Load tests live under `loadtest/k6`. They are manual tools and are not part of default `go test ./...`.

## Install k6

```bash
brew install k6
```

## Environment

Common variables:

- `BASE_URL`, default `http://localhost:8080`
- `USERNAME`
- `PASSWORD`
- `TOKEN`
- `WORKSPACE_ID`
- `RUN_ID`
- `ARTIFACT_URL`

No script contains real credentials. Supply tokens through environment variables.

## Run

```bash
make loadtest-smoke
k6 run loadtest/k6/login.js
k6 run loadtest/k6/sse_events.js
k6 run loadtest/k6/download_artifact.js
```

## Scenarios

- `login.js`: auth login latency and error rate.
- `smoke.js`: ping and optional workspace listing.
- `sse_events.js`: opens an SSE stream for an existing run.
- `download_artifact.js`: downloads a provided artifact URL.

## Key Signals

Watch request failure rate, p95 latency, HTTP 429 rate, worker backlog, Python process saturation, and storage download latency. Compare k6 output with Prometheus metrics at `/metrics`.
