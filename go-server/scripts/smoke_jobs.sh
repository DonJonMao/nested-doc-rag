#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
TOKEN="${TOKEN:-}"
WORKSPACE_ID="${WORKSPACE_ID:-}"

if [[ -z "${TOKEN}" || -z "${WORKSPACE_ID}" ]]; then
  echo "TOKEN and WORKSPACE_ID are required" >&2
  exit 2
fi

curl -fsS "${BASE_URL}/api/v1/jobs?workspace_id=${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${TOKEN}" >/dev/null

echo "smoke-jobs ok"
