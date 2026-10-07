#!/usr/bin/env bash
# Actions streams this script over SSH; it updates the cloned checkout and Compose.
set -euo pipefail
umask 077
abort() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# == 5 || $# == 6 ]] || abort 'usage: deploy.sh ROOT SHA BACKEND_DIGEST FRONTEND_DIGEST SEQUENCE [--prepare]'
root=$1; candidate_sha=$2; backend=$3; frontend=$4; sequence=$5; action=${6:-}
[[ $root =~ ^/[a-zA-Z0-9_./-]+$ && $root != / && $root != *'//'* && $root != *'/./'* && $root != *'/../'* && $root != */. && $root != */.. && $root != */ ]] || abort 'invalid deployment root'
[[ $candidate_sha =~ ^[0-9a-f]{40}$ && $sequence =~ ^[1-9][0-9]{0,14}$ ]] || abort 'invalid deployment SHA or sequence'
[[ -z $action || $action == --prepare ]] || abort 'invalid deployment action'
for image in "$backend" "$frontend"; do
  [[ $image =~ ^[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}$ ]] || abort 'image must use a registry digest'
done
[[ -d $root/.git ]] || abort 'clone the repository into the deployment root first'
[[ -f $root/config.env ]] || abort 'create config.env and run setup.sh first'
command -v flock >/dev/null || abort 'missing command: flock'
exec 9>"$root/deploy.lock"
flock -w 300 9 || abort 'another deployment holds the lock'
watermark=0
if [[ -f $root/.deploy-sequence ]]; then
  read -r watermark < "$root/.deploy-sequence"
  [[ $watermark =~ ^[0-9]{1,15}$ ]] || abort 'invalid deployment sequence state'
fi
(( sequence >= watermark )) || abort 'stale deployment rejected'
cd "$root"
[[ -z $(git status --porcelain --untracked-files=no) ]] || abort 'tracked files have local changes; commit or resolve them before deployment'
git fetch origin main < /dev/null
# Deploy exactly the tested commit, never the moving tip of main.
git merge-base --is-ancestor "$candidate_sha" FETCH_HEAD || abort 'deployment commit is not on origin/main'
git checkout --detach "$candidate_sha" < /dev/null
# shellcheck source=common.sh
source "$root/deploy/common.sh"
load_config "$root"
[[ $SITE_DOMAIN != docs.example.com ]] || fail 'set your real SITE_DOMAIN first'
check_environment
for directory in data assets backups nginx; do
  [[ -d $root/$directory ]] || fail 'run setup.sh first: runtime directory missing'
done
printf 'BUILD_SHA=%s\nBACKEND_IMAGE=%s\nFRONTEND_IMAGE=%s\nDEPLOY_SEQUENCE=%s\n' "$candidate_sha" "$backend" "$frontend" "$sequence" > "$root/images.env.new.$$"
mv -f "$root/images.env.new.$$" "$root/images.env"
compose_for "$root" config --quiet < /dev/null
compose_for "$root" pull < /dev/null
if [[ $action == --prepare ]]; then
  printf 'Images prepared. For a new library run: bash deploy/setup.sh %s --init-db\n' "$root"
  exit 0
fi
[[ -f $root/data/c156.sqlite ]] || fail 'database missing: explicit initialization required'
backup="before-${sequence}-$(date -u +%Y%m%dT%H%M%S)-$$.sqlite"
compose_for "$root" run --rm --interactive=false -T --no-deps backend python -m src.storage backup --database /data/c156.sqlite --output "/backups/$backup" < /dev/null
compose_for "$root" up -d --wait --wait-timeout 90 < /dev/null
probe() {
  local base=$1 path=$2 expected=$3 body
  body=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 10 -H "Host: $SITE_DOMAIN" "$base$path") || return 1
  body=$(printf '%s' "$body" | tr -d '[:space:]')
  [[ $body == "$expected" ]]
}
probe "http://127.0.0.1:$APP_PORT" /build-info.json "{\"build_sha\":\"$candidate_sha\"}" || fail 'local frontend/version check failed'
probe "http://127.0.0.1:$APP_PORT" /api/healthz "{\"status\":\"ok\",\"build_sha\":\"$candidate_sha\"}" || fail 'local API check failed'
if [[ $NGINX_MANAGED == 1 ]]; then
  sed "s/@APP_PORT@/$APP_PORT/g" "$root/deploy/nginx-proxy.inc.template" > "$root/nginx/proxy.inc.new.$$"
  chmod 644 "$root/nginx/proxy.inc.new.$$"
  mv -f "$root/nginx/proxy.inc.new.$$" "$root/nginx/proxy.inc"
  "$NGINX_BIN" -t
  "$NGINX_BIN" -s reload
fi
probe "https://$SITE_DOMAIN" /build-info.json "{\"build_sha\":\"$candidate_sha\"}" || fail 'public HTTPS frontend/version check failed'
probe "https://$SITE_DOMAIN" /api/healthz "{\"status\":\"ok\",\"build_sha\":\"$candidate_sha\"}" || fail 'public HTTPS API check failed'
printf '%s\n' "$sequence" > "$root/.deploy-sequence.new.$$"
mv -f "$root/.deploy-sequence.new.$$" "$root/.deploy-sequence"
printf 'Deployed %s; backup %s/backups/%s\n' "$candidate_sha" "$root" "$backup"
