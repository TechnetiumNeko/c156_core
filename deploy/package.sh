#!/usr/bin/env bash
set -euo pipefail
[[ $# == 5 ]] || { printf 'usage: package.sh OUTPUT SHA BACKEND_DIGEST FRONTEND_DIGEST SEQUENCE\n' >&2; exit 2; }
output=$1; sha=$2; backend=$3; frontend=$4; sequence=$5
[[ $sha =~ ^[0-9a-f]{40}$ && $sequence =~ ^[1-9][0-9]{0,14}$ ]] || exit 2
for image in "$backend" "$frontend"; do [[ $image =~ ^[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}$ ]] || exit 2; done
[[ ! -e $output ]] || { printf 'output already exists\n' >&2; exit 1; }
mkdir -p "$output"
# Every file, including operators' scripts, comes from exactly the image commit.
git archive "$sha" deploy | tar -x -C "$output" --strip-components=1
rm -f "$output/backend.Dockerfile" "$output/frontend.Dockerfile" "$output/frontend-nginx.conf" "$output/smoke.py" "$output/package.sh"
git archive --format=tar.gz --output="$output/source.tar.gz" "$sha"
printf 'BUILD_SHA=%s\nBACKEND_IMAGE=%s\nFRONTEND_IMAGE=%s\nDEPLOY_SEQUENCE=%s\n' "$sha" "$backend" "$frontend" "$sequence" > "$output/release.env"
