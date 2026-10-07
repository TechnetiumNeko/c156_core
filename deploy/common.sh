#!/usr/bin/env bash
set -euo pipefail
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
load_config() {
  local requested=$1 key value line
  [[ $requested =~ ^/[a-zA-Z0-9_./-]+$ && $requested != / && $requested != *'/../'* ]] || fail 'invalid deployment root'
  DEPLOY_ROOT=$requested; APP_PORT=28156; PROXY_NETWORK=172.30.156.0/24
  APP_UID=10001; APP_GID=10001; NGINX_MANAGED=1; NGINX_BIN=nginx; SITE_DOMAIN=
  [[ -f $requested/config.env ]] || fail "missing $requested/config.env"
  while IFS= read -r line || [[ -n $line ]]; do
    [[ -z $line || $line == \#* ]] && continue
    [[ $line == *=* ]] || fail 'config must use KEY=value'
    key=${line%%=*}; value=${line#*=}
    case $key in SITE_DOMAIN|DEPLOY_ROOT|APP_PORT|PROXY_NETWORK|APP_UID|APP_GID|NGINX_MANAGED|NGINX_BIN) printf -v "$key" '%s' "$value" ;; *) fail "unknown config key: $key" ;; esac
  done < "$requested/config.env"
  [[ $DEPLOY_ROOT == "$requested" ]] || fail 'DEPLOY_ROOT differs from requested root'
  [[ $SITE_DOMAIN =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && $SITE_DOMAIN == *.* && $SITE_DOMAIN != *..* ]] || fail 'invalid SITE_DOMAIN'
  [[ $APP_PORT =~ ^[1-9][0-9]{0,4}$ ]] && (( APP_PORT >= 1024 && APP_PORT <= 65535 )) || fail 'invalid APP_PORT'
  [[ $APP_UID =~ ^[1-9][0-9]{0,8}$ && $APP_GID =~ ^[1-9][0-9]{0,8}$ ]] || fail 'invalid application UID/GID'
  [[ $NGINX_MANAGED == 0 || $NGINX_MANAGED == 1 ]] || fail 'NGINX_MANAGED must be 0 or 1'
  [[ $PROXY_NETWORK =~ ^[0-9.]+/[0-9]+$ ]] || fail 'invalid IPv4 proxy subnet'
  [[ $NGINX_BIN =~ ^[a-zA-Z0-9_./-]+$ ]] || fail 'invalid nginx binary'
  export DEPLOY_ROOT SITE_DOMAIN APP_PORT PROXY_NETWORK APP_UID APP_GID
}
load_release() {
  local directory=$1 key value line
  BUILD_SHA=; BACKEND_IMAGE=; FRONTEND_IMAGE=; DEPLOY_SEQUENCE=
  [[ -f $directory/release.env ]] || fail 'release.env missing'
  while IFS= read -r line || [[ -n $line ]]; do
    [[ -z $line || $line == \#* ]] && continue
    key=${line%%=*}; value=${line#*=}
    case $key in BUILD_SHA|BACKEND_IMAGE|FRONTEND_IMAGE|DEPLOY_SEQUENCE) printf -v "$key" '%s' "$value" ;; *) fail 'invalid release key' ;; esac
  done < "$directory/release.env"
  [[ $BUILD_SHA =~ ^[0-9a-f]{40}$ ]] || fail 'invalid build SHA'
  [[ $DEPLOY_SEQUENCE =~ ^[1-9][0-9]{0,14}$ ]] || fail 'invalid deployment sequence'
  for value in "$BACKEND_IMAGE" "$FRONTEND_IMAGE"; do
    [[ $value =~ ^[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}$ ]] || fail 'image must use a registry digest'
  done
  export BUILD_SHA BACKEND_IMAGE FRONTEND_IMAGE DEPLOY_SEQUENCE
}
compose_for() {
  local release=$1; shift
  load_release "$release"
  docker compose -p c156 --project-directory "$release" -f "$release/compose.yaml" "$@"
}
check_environment() {
  local command
  for command in docker curl flock tar; do command -v "$command" >/dev/null || fail "missing command: $command"; done
  docker info >/dev/null
  docker compose version >/dev/null
  if [[ $NGINX_MANAGED == 1 ]]; then command -v "$NGINX_BIN" >/dev/null || fail 'nginx binary not found; set NGINX_BIN'; fi
}
