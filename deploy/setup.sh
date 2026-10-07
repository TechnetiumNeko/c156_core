#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
root=${1:-$HOME/c156}; action=${2:-}
validate_root "$root"
[[ -z $action || $action == --init-db ]] || fail 'usage: setup.sh [ROOT] [--init-db]'
if [[ ! -f $root/config.env ]]; then
  mkdir -p "$root"
  install -m 600 "$(dirname "${BASH_SOURCE[0]}")/config.env.example" "$root/config.env"
  sed -i "s|^DEPLOY_ROOT=.*$|DEPLOY_ROOT=$root|" "$root/config.env"
  printf 'Edit SITE_DOMAIN in %s/config.env, then run setup again.\n' "$root"
  exit 0
fi
load_config "$root"
if grep -qx 'DEPLOY_ROOT=' "$root/config.env"; then
  sed -i "s|^DEPLOY_ROOT=$|DEPLOY_ROOT=$root|" "$root/config.env"
fi
for key in APP_UID APP_GID; do
  if grep -qx "$key=" "$root/config.env"; then sed -i "s|^$key=$|$key=${!key}|" "$root/config.env"; fi
done
[[ $SITE_DOMAIN != docs.example.com ]] || fail 'set your real SITE_DOMAIN first'
check_environment
if [[ ! -f $root/.deploy-sequence ]]; then
  command -v ss >/dev/null || fail 'ss is needed for the first-install port check'
  [[ -z $(ss -H -ltn "sport = :$APP_PORT") ]] || fail "port $APP_PORT is occupied; choose another APP_PORT"
fi
mkdir -p "$root"/{data,assets,backups,nginx}
for directory in "$root/data" "$root/assets" "$root/backups"; do
  if [[ $(stat -c '%a' "$directory") != 700 ]]; then chmod 700 "$directory"; fi
  if [[ $(stat -c '%u:%g' "$directory") != "$APP_UID:$APP_GID" ]]; then
    chown "$APP_UID:$APP_GID" "$directory" || fail "directory ownership needs an administrator: chown $APP_UID:$APP_GID $directory"
  fi
done
if [[ ! -f $root/nginx/proxy.inc ]]; then
  sed "s/@APP_PORT@/$APP_PORT/g" "$(dirname "${BASH_SOURCE[0]}")/nginx-proxy.inc.template" > "$root/nginx/proxy.inc"
  chmod 644 "$root/nginx/proxy.inc"
fi
if [[ $action == --init-db ]]; then
  load_images "$root"
  [[ ! -e $root/data/c156.sqlite ]] || fail 'database already exists; refusing initialization'
  compose_for "$root" run --rm --no-deps backend python -m src.storage init --database /data/c156.sqlite
  compose_for "$root" run --rm --no-deps backend python -m src.identity bootstrap-admin --database /data/c156.sqlite --login-name admin --display-name 管理员
fi
printf 'Directories ready. Data: %s/data; assets: %s/assets; backups: %s/backups\n' "$root" "$root" "$root"
