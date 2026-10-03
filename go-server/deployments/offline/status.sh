#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $# -eq 0 ]] || fail "usage: ./status.sh"
require_tools
load_image_settings
configure_compose
"${COMPOSE[@]}" ps
