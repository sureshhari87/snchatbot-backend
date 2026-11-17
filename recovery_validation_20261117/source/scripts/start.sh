#!/usr/bin/env sh
set -eu

APP_MODULE="${APP_MODULE:-main:app}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-7860}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"

if [ "${RUN_MIGRATIONS_BEFORE_START:-0}" = "1" ]; then
  alembic upgrade head
fi

set -- uvicorn "$APP_MODULE" \
  --host "$HOST" \
  --port "$PORT" \
  --workers "$WEB_CONCURRENCY" \
  --log-level "$UVICORN_LOG_LEVEL"

case "${PROXY_HEADERS:-1}" in
  1|true|TRUE|yes|YES|on|ON)
    set -- "$@" --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
    ;;
esac

exec "$@"
