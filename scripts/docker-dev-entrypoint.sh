#!/usr/bin/env sh
# Dev-compose entrypoint (#234): mint a per-install bootstrap token (never commit one),
# print it once for host-browser create-admin, then start EMS.
set -eu

TOKEN_FILE="${EMS_BOOTSTRAP_TOKEN_FILE:-/app/ems/data/bootstrap_web_token}"
mkdir -p "$(dirname "$TOKEN_FILE")"

if [ -n "${EMS_WEB_TOKEN:-}" ]; then
  :
elif [ -f "$TOKEN_FILE" ]; then
  EMS_WEB_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
else
  EMS_WEB_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi

if [ -z "$EMS_WEB_TOKEN" ]; then
  echo "docker-dev-entrypoint: empty bootstrap token" >&2
  exit 1
fi

umask 077
printf '%s\n' "$EMS_WEB_TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE" 2>/dev/null || true
export EMS_WEB_TOKEN

echo "EMS bootstrap token (create-admin → Existing access token): $EMS_WEB_TOKEN"
echo "(also at $TOKEN_FILE — host port is 127.0.0.1 only; do not commit this value)"

exec python -m ems.main
