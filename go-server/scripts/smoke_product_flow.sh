#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
TOKEN="${TOKEN:-}"
USERNAME="${USERNAME:-admin}"
PASSWORD="${PASSWORD:-}"
WORKSPACE_ID="${WORKSPACE_ID:-}"
READY_KB_ID="${READY_KB_ID:-}"
FORM_FILE_PATH="${FORM_FILE_PATH:-}"
ROOM_CONTEXT="${ROOM_CONTEXT:-}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-600}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-3}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-}"

if [[ -z "${FORM_FILE_PATH}" ]]; then
  echo "FORM_FILE_PATH is required" >&2
  exit 2
fi

if [[ ! -f "${FORM_FILE_PATH}" ]]; then
  echo "FORM_FILE_PATH does not exist: ${FORM_FILE_PATH}" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required for JSON parsing" >&2
  exit 2
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

auth_header=()
with_auth() {
  auth_header=(-H "Authorization: Bearer ${TOKEN}")
}

json_value() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    cursor = json.load(fh)

for part in sys.argv[2].split("."):
    if not part:
        continue
    if isinstance(cursor, dict):
        cursor = cursor[part]
    elif isinstance(cursor, list):
        cursor = cursor[int(part)]
    else:
        raise SystemExit(f"cannot descend into {type(cursor).__name__}")

if cursor is None:
    print("")
elif isinstance(cursor, (dict, list)):
    print(json.dumps(cursor, ensure_ascii=False))
else:
    print(cursor)
PY
}

if [[ -z "${TOKEN}" ]]; then
  if [[ -z "${PASSWORD}" ]]; then
    echo "TOKEN or PASSWORD is required" >&2
    exit 2
  fi
  echo "Logging in as ${USERNAME}"
  login_payload="$(
    USERNAME="${USERNAME}" PASSWORD="${PASSWORD}" python3 - <<'PY'
import json
import os

print(json.dumps({
    "username": os.environ["USERNAME"],
    "password": os.environ["PASSWORD"],
}))
PY
  )"
  login_json="${tmpdir}/login.json"
  curl -fsS -X POST "${BASE_URL}/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "${login_payload}" >"${login_json}"
  TOKEN="$(json_value "${login_json}" "data.access_token")"
fi

with_auth

if [[ -z "${WORKSPACE_ID}" ]]; then
  echo "Discovering workspace"
  workspaces_json="${tmpdir}/workspaces.json"
  curl -fsS "${BASE_URL}/api/v1/workspaces" "${auth_header[@]}" >"${workspaces_json}"
  WORKSPACE_ID="$(
    python3 - "${workspaces_json}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

data = payload.get("data")
items = data.get("workspaces", []) if isinstance(data, dict) else data
if not items:
    raise SystemExit("no workspace found; set WORKSPACE_ID or create a workspace first")
print(items[0]["id"])
PY
  )"
fi

echo "Using workspace ${WORKSPACE_ID}"

options_json="${tmpdir}/knowledge_options.json"
curl -fsS "${BASE_URL}/api/v1/knowledge-bases/options?workspace_id=${WORKSPACE_ID}" \
  "${auth_header[@]}" >"${options_json}"

selected_kb_json="${tmpdir}/selected_kb.json"
READY_KB_ID="${READY_KB_ID}" python3 - "${options_json}" >"${selected_kb_json}" <<'PY'
import json
import os
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

data = payload.get("data", {})
items = data.get("knowledge_bases", []) if isinstance(data, dict) else []
requested = os.environ.get("READY_KB_ID", "").strip()

selected = None
if requested:
    selected = next((item for item in items if item.get("id") == requested), None)
    if selected is None:
        raise SystemExit(f"READY_KB_ID not found in options: {requested}")
    if selected.get("status") != "ready":
        raise SystemExit(f"READY_KB_ID is not ready: {selected.get('status')}")
else:
    selected = next(
        (
            item for item in items
            if item.get("status") == "ready" and item.get("current_index_version_id")
        ),
        None,
    )

if selected is None:
    statuses = ", ".join(f"{item.get('name')}={item.get('status')}" for item in items) or "none"
    raise SystemExit(
        "no ready knowledge base found; ingest knowledge documents first. "
        f"Current options: {statuses}"
    )

print(json.dumps(selected, ensure_ascii=False))
PY

READY_KB_ID="$(json_value "${selected_kb_json}" "id")"
READY_KB_NAME="$(json_value "${selected_kb_json}" "name")"
READY_KB_NAMESPACE="$(json_value "${selected_kb_json}" "namespace")"
echo "Using ready knowledge base ${READY_KB_NAME} (${READY_KB_NAMESPACE})"

form_json="${tmpdir}/form.json"
curl -fsS -X POST "${BASE_URL}/api/v1/forms" \
  "${auth_header[@]}" \
  -F "workspace_id=${WORKSPACE_ID}" \
  -F "file=@${FORM_FILE_PATH}" >"${form_json}"

FORM_FILE_ID="$(json_value "${form_json}" "data.id")"
FORM_FILENAME="$(json_value "${form_json}" "data.filename")"
echo "Uploaded form ${FORM_FILENAME} (${FORM_FILE_ID})"

fill_payload="$(
  WORKSPACE_ID="${WORKSPACE_ID}" \
  READY_KB_ID="${READY_KB_ID}" \
  FORM_FILE_ID="${FORM_FILE_ID}" \
  ROOM_CONTEXT="${ROOM_CONTEXT}" \
  python3 - <<'PY'
import json
import os

payload = {
    "workspace_id": os.environ["WORKSPACE_ID"],
    "knowledge_base_id": os.environ["READY_KB_ID"],
    "form_file_id": os.environ["FORM_FILE_ID"],
}
room_context = os.environ.get("ROOM_CONTEXT", "").strip()
if room_context:
    payload["room_context"] = room_context
print(json.dumps(payload))
PY
)"

run_json="${tmpdir}/fill_run.json"
curl -fsS -X POST "${BASE_URL}/api/v1/fill-runs/simple" \
  "${auth_header[@]}" \
  -H "Content-Type: application/json" \
  -d "${fill_payload}" >"${run_json}"

RUN_ID="$(json_value "${run_json}" "data.id")"
RUN_STATUS="$(json_value "${run_json}" "data.status")"
echo "Created fill run ${RUN_ID} with status ${RUN_STATUS}"

mine_json="${tmpdir}/my_runs.json"
curl -fsS "${BASE_URL}/api/v1/fill-runs?workspace_id=${WORKSPACE_ID}&mine=true&limit=20&offset=0" \
  "${auth_header[@]}" >"${mine_json}"

RUN_ID="${RUN_ID}" python3 - "${mine_json}" <<'PY'
import json
import os
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

items = payload.get("data", {}).get("fill_runs", [])
run_id = os.environ["RUN_ID"]
if not any(item.get("id") == run_id for item in items):
    raise SystemExit(f"created run was not returned by mine=true list: {run_id}")
PY

echo "Verified run appears in mine=true list"

run_detail_json="${tmpdir}/run_detail.json"
deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
terminal_status=""
echo "Waiting for fill run to finish"
while (( SECONDS <= deadline )); do
  curl -fsS "${BASE_URL}/api/v1/fill-runs/${RUN_ID}" "${auth_header[@]}" >"${run_detail_json}"
  terminal_status="$(json_value "${run_detail_json}" "data.status")"
  progress_done="$(json_value "${run_detail_json}" "data.progress_done")"
  progress_total="$(json_value "${run_detail_json}" "data.progress_total")"
  echo "  status=${terminal_status} progress=${progress_done}/${progress_total}"
  case "${terminal_status}" in
    succeeded|completed_with_failures|failed|canceled)
      break
      ;;
  esac
  sleep "${POLL_INTERVAL_SECONDS}"
done

if [[ "${terminal_status}" != "succeeded" ]]; then
  echo "fill run did not succeed: ${terminal_status}" >&2
  cat "${run_detail_json}" >&2
  exit 1
fi

events_sse="${tmpdir}/events.sse"
curl -fsS --max-time 5 "${BASE_URL}/api/v1/runs/${RUN_ID}/events?workspace_id=${WORKSPACE_ID}&after_sequence=0" \
  "${auth_header[@]}" >"${events_sse}" 2>/dev/null || true

if ! grep -q "event: succeeded" "${events_sse}"; then
  echo "SSE replay did not include succeeded event" >&2
  cat "${events_sse}" >&2
  exit 1
fi

last_event_id="$(
  awk '/^id: / { value=$2 } END { print value }' "${events_sse}"
)"
if [[ -z "${last_event_id}" ]]; then
  echo "SSE replay did not include event ids" >&2
  cat "${events_sse}" >&2
  exit 1
fi

events_replay_empty="${tmpdir}/events_replay_empty.sse"
curl -fsS --max-time 5 "${BASE_URL}/api/v1/runs/${RUN_ID}/events?workspace_id=${WORKSPACE_ID}" \
  "${auth_header[@]}" \
  -H "Last-Event-ID: ${last_event_id}" >"${events_replay_empty}" 2>/dev/null || true

if grep -q "^event: " "${events_replay_empty}"; then
  echo "Last-Event-ID replay unexpectedly returned old events" >&2
  cat "${events_replay_empty}" >&2
  exit 1
fi

if [[ -z "${DOWNLOAD_DIR}" ]]; then
  DOWNLOAD_DIR="${tmpdir}/downloads"
fi
mkdir -p "${DOWNLOAD_DIR}"
filled_form_path="${DOWNLOAD_DIR}/filled_form_${RUN_ID}.xlsx"
curl -fsS "${BASE_URL}/api/v1/fill-runs/${RUN_ID}/download/filled-form" \
  "${auth_header[@]}" \
  -o "${filled_form_path}"

if [[ ! -s "${filled_form_path}" ]]; then
  echo "downloaded filled form is empty: ${filled_form_path}" >&2
  exit 1
fi

echo "Verified SSE replay and filled form download"
echo "smoke-product-flow ok: run_id=${RUN_ID}"
