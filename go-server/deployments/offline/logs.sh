#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
require_tools
load_image_settings
configure_compose
for service in "$@"; do
  case "$service" in api|worker|web|postgres|redis|minio|qdrant) ;; *) fail "unknown service";; esac
done
"${COMPOSE[@]}" logs --tail 200 --follow "$@"
