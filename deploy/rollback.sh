#!/usr/bin/env bash
set -euo pipefail
root=${1:-/srv/c156}
previous=$(readlink -f "$root/previous")
[[ -n $previous && -d $previous ]] || { printf 'No previous successful release.\n' >&2; exit 1; }
exec bash "$(dirname "${BASH_SOURCE[0]}")/deploy.sh" "$root" "$previous" --rollback
