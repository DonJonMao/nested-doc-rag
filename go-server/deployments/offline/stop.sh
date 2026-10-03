#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 0 ]] || fail "usage: ./stop.sh (data volumes are retained)"
require_tools
load_image_settings
configure_compose
"${COMPOSE[@]}" stop
stop_project_writers
printf 'Stopped this deployment; all data volumes and local credentials were retained.\n'
