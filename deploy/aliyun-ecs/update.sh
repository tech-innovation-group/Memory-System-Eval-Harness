#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/locomo/locomo-eval-web}"
SERVICE_NAME="${SERVICE_NAME:-locomo-eval}"

cd "$APP_DIR"

echo "== Update repo =="
git pull --ff-only

echo "== Restart service =="
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
