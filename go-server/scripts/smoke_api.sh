#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"

echo "Checking health at ${BASE_URL}"
curl -fsS "${BASE_URL}/healthz" >/dev/null
curl -fsS "${BASE_URL}/api/v1/ping" >/dev/null

echo "Checking readiness"
curl -fsS "${BASE_URL}/readyz" || true

echo "Checking metrics"
curl -fsS "${BASE_URL}/metrics" | head -n 20 >/dev/null

echo "smoke-api ok"
