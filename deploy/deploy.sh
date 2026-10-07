#!/usr/bin/env bash
set -euo pipefail
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 2 || $# == 3 ]] || fail 'usage: deploy.sh ROOT RELEASE_DIR [--prepare|--rollback]'
load_config "$1"
release=$(realpath -e "$2"); action=${3:-}
[[ $release == "$DEPLOY_ROOT/releases/"* && $release != *'/../'* ]] || fail 'release must be inside deployment releases directory'
[[ -z $action || $action == --prepare || $action == --rollback ]] || fail 'invalid deployment action'
load_release "$release"
candidate_sha=$BUILD_SHA; candidate_sequence=$DEPLOY_SEQUENCE
exec 9>"$DEPLOY_ROOT/deploy.lock"
flock -w 300 9 || fail 'another deployment holds the lock'
old=$(readlink -f "$DEPLOY_ROOT/current" || true)
previous=$(readlink -f "$DEPLOY_ROOT/previous" || true)
watermark=0
if [[ -f $DEPLOY_ROOT/sequence ]]; then
  read -r watermark < "$DEPLOY_ROOT/sequence"
  [[ $watermark =~ ^[0-9]{1,15}$ ]] || fail 'invalid sequence state'
fi
if [[ $action != --rollback ]] && (( candidate_sequence <= watermark )); then
  [[ $release == "$old" && $candidate_sequence == "$watermark" ]] && { printf 'Already deployed.\n'; exit 0; }
  fail 'stale deployment rejected'
fi
if [[ $action == --rollback ]]; then [[ -n $previous && $release == "$previous" ]] || fail 'rollback target must be previous successful release'; fi
check_environment
compose_for "$release" config --quiet
compose_for "$release" pull
atomic_link() {
  local target=$1 link=$2
  ln -s "$target" "$link.new.$$"
  mv -Tf "$link.new.$$" "$link"
}
if [[ $action == --prepare ]]; then
  atomic_link "$release" "$DEPLOY_ROOT/prepared"
  printf 'Release prepared. Run setup.sh ROOT --init-db only for a new library.\n'
  exit 0
fi
[[ -f $DEPLOY_ROOT/data/c156.sqlite ]] || fail 'database missing: explicit initialization required'
backup="before-${candidate_sequence}-$(date -u +%Y%m%dT%H%M%S)-$$.sqlite"
# Candidate image must be able to snapshot the existing schema before it starts.
compose_for "$release" run --rm -T --no-deps backend python -m src.storage backup --database /data/c156.sqlite --output "/backups/$backup"
proxy=$DEPLOY_ROOT/nginx/proxy.inc
proxy_saved=$DEPLOY_ROOT/nginx/.proxy.before.$$
proxy_existed=0; switched=0; success=0
if [[ -f $proxy ]]; then cp -p "$proxy" "$proxy_saved"; proxy_existed=1; fi
restore_on_exit() {
  local status=$?
  if (( success == 0 && switched == 1 )); then
    printf 'Deployment failed; restoring prior configuration and containers.\n' >&2
    local recovery_failed=0
    if [[ $NGINX_MANAGED == 1 ]]; then
      if (( proxy_existed )); then mv -f "$proxy_saved" "$proxy"; else rm -f "$proxy"; fi
      "$NGINX_BIN" -t && "$NGINX_BIN" -s reload || recovery_failed=1
    fi
    if [[ -n $old && -d $old ]]; then
      compose_for "$old" up -d --wait --wait-timeout 90 || recovery_failed=1
    else
      printf 'First deployment has no successful release to restore; inspect containers.\n' >&2
    fi
    (( recovery_failed == 0 )) || printf 'ERROR: rollback failed; manual repair required. Data was retained.\n' >&2
  fi
  rm -f "$proxy_saved"
  exit "$status"
}
trap restore_on_exit EXIT
switched=1
compose_for "$release" up -d --wait --wait-timeout 90
probe() {
  local base=$1 path=$2 expected=$3 body
  body=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 10 -H "Host: $SITE_DOMAIN" "$base$path") || return 1
  body=$(printf '%s' "$body" | tr -d '[:space:]')
  [[ $body == "$expected" ]]
}
for attempt in {1..12}; do
  if probe "http://127.0.0.1:$APP_PORT" /build-info.json "{\"build_sha\":\"$candidate_sha\"}" &&
     probe "http://127.0.0.1:$APP_PORT" /api/healthz "{\"status\":\"ok\",\"build_sha\":\"$candidate_sha\"}"; then break; fi
  (( attempt < 12 )) || fail 'local readiness/version check failed'
  sleep 2
done
if [[ $NGINX_MANAGED == 1 ]]; then
  sed "s/@APP_PORT@/$APP_PORT/g" "$release/nginx-proxy.inc.template" > "$proxy.new.$$"
  chmod 644 "$proxy.new.$$"
  mv -f "$proxy.new.$$" "$proxy"
  "$NGINX_BIN" -t
  "$NGINX_BIN" -s reload
fi
probe "https://$SITE_DOMAIN" /build-info.json "{\"build_sha\":\"$candidate_sha\"}" || fail 'public HTTPS frontend/version check failed'
probe "https://$SITE_DOMAIN" /api/healthz "{\"status\":\"ok\",\"build_sha\":\"$candidate_sha\"}" || fail 'public HTTPS API check failed'
# Success pointers advance only after all probes pass. Database is never restored automatically.
if [[ -n $old && $old != "$release" ]]; then atomic_link "$old" "$DEPLOY_ROOT/previous"; fi
atomic_link "$release" "$DEPLOY_ROOT/current"
if (( candidate_sequence > watermark )); then
  printf '%s\n' "$candidate_sequence" > "$DEPLOY_ROOT/sequence.new.$$"
  mv -f "$DEPLOY_ROOT/sequence.new.$$" "$DEPLOY_ROOT/sequence"
fi
success=1
printf 'Deployed %s; backup %s/backups/%s\n' "$candidate_sha" "$DEPLOY_ROOT" "$backup"
