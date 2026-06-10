#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
TOKEN="${TOKEN:-}"
WORKSPACE_ID="${WORKSPACE_ID:-}"
TEST_FILE_PATH="${TEST_FILE_PATH:-}"
FILE_CATEGORY="${FILE_CATEGORY:-form_template}"

if [[ -z "${TOKEN}" || -z "${WORKSPACE_ID}" || -z "${TEST_FILE_PATH}" ]]; then
  echo "TOKEN, WORKSPACE_ID, and TEST_FILE_PATH are required" >&2
  exit 2
fi

curl -fsS -X POST "${BASE_URL}/api/v1/files" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "workspace_id=${WORKSPACE_ID}" \
  -F "file_category=${FILE_CATEGORY}" \
  -F "file=@${TEST_FILE_PATH}" >/dev/null

echo "smoke-files ok"
