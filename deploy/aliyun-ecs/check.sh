#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-locomo-eval}"
APP_PORT="${APP_PORT:-19181}"

echo "== systemd =="
systemctl status "$SERVICE_NAME" --no-pager || true

echo
echo "== health =="
curl -s "http://127.0.0.1:${APP_PORT}/health" || true
echo

echo
echo "== readiness =="
curl -s "http://127.0.0.1:${APP_PORT}/api/readiness" || true
echo

echo
echo "== nginx =="
nginx -t || true
