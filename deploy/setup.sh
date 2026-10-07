#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
root=${1:-/srv/c156}; action=${2:-}
[[ -z $action || $action == --init-db ]] || fail 'usage: setup.sh [ROOT] [--init-db]'
if [[ ! -f $root/config.env ]]; then
  mkdir -p "$root"
  install -m 600 "$(dirname "${BASH_SOURCE[0]}")/config.env.example" "$root/config.env"
  printf 'Edit SITE_DOMAIN in %s/config.env, then run setup again.\n' "$root"
  exit 0
fi
load_config "$root"
[[ $SITE_DOMAIN != docs.example.com ]] || fail 'set your real SITE_DOMAIN first'
check_environment
if [[ ! -L $root/current ]]; then
  command -v ss >/dev/null || fail 'ss is needed for the first-install port check'
  [[ -z $(ss -H -ltn "sport = :$APP_PORT") ]] || fail "port $APP_PORT is occupied; choose another APP_PORT"
fi
mkdir -p "$root"/{data,assets,backups,releases,nginx}
chmod 700 "$root/data" "$root/assets" "$root/backups"
chown "$APP_UID:$APP_GID" "$root/data" "$root/assets" "$root/backups"
if [[ ! -f $root/nginx/proxy.inc ]]; then
  sed "s/@APP_PORT@/$APP_PORT/g" "$(dirname "${BASH_SOURCE[0]}")/nginx-proxy.inc.template" > "$root/nginx/proxy.inc"
  chmod 644 "$root/nginx/proxy.inc"
fi
if [[ $action == --init-db ]]; then
  release=$(readlink -f "$root/prepared")
  [[ -d $release && $release == "$root/releases/"* ]] || fail 'prepare a release first (see README)'
  [[ ! -e $root/data/c156.sqlite ]] || fail 'database already exists; refusing initialization'
  compose_for "$release" run --rm --no-deps backend python -m src.storage init --database /data/c156.sqlite
  compose_for "$release" run --rm --no-deps backend python -m src.identity bootstrap-admin --database /data/c156.sqlite --login-name admin --display-name 管理员
fi
printf 'Directories ready. Data: %s/data; assets: %s/assets; backups: %s/backups\n' "$root" "$root" "$root"
