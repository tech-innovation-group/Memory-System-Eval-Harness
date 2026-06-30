#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/locomo/locomo-eval-web}"
SERVICE_NAME="${SERVICE_NAME:-locomo-eval}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$USER}}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env.production}"
APP_PORT="${APP_PORT:-19181}"
DOMAIN="${DOMAIN:-}"
INSTALL_NGINX="${INSTALL_NGINX:-1}"
ENABLE_HTTPS="${ENABLE_HTTPS:-0}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/data/echomem_workspace}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo bash deploy/aliyun-ecs/install.sh"
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "App dir not found: $APP_DIR"
  exit 1
fi

cd "$APP_DIR"

echo "== Install system packages =="
apt update
apt install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx

echo "== Prepare workspace =="
mkdir -p "$WORKSPACE_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$WORKSPACE_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "== Create env file from sample =="
  cp "$APP_DIR/deploy/aliyun-ecs/locomo-eval.env.sample" "$ENV_FILE"
  chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
  echo "Created $ENV_FILE"
  echo "Edit it before real production use."
fi

echo "== Install systemd unit =="
sed \
  -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  -e "s|__ENV_FILE__|$ENV_FILE|g" \
  "$APP_DIR/deploy/aliyun-ecs/locomo-eval.service" > "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

if [[ "$INSTALL_NGINX" == "1" ]]; then
  if [[ -z "$DOMAIN" ]]; then
    DOMAIN="_"
  fi
  echo "== Install nginx config =="
  sed \
    -e "s|__DOMAIN__|$DOMAIN|g" \
    -e "s|__APP_PORT__|$APP_PORT|g" \
    "$APP_DIR/deploy/aliyun-ecs/nginx.locomo-eval.conf" > "/etc/nginx/sites-available/${SERVICE_NAME}"
  ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
  nginx -t
  systemctl reload nginx
fi

if [[ "$ENABLE_HTTPS" == "1" ]]; then
  if [[ -z "$DOMAIN" || "$DOMAIN" == "_" ]]; then
    echo "HTTPS requested but DOMAIN is empty."
    exit 1
  fi
  echo "== Request certificate =="
  certbot --nginx -d "$DOMAIN"
fi

echo
echo "Done."
echo "Service: systemctl status ${SERVICE_NAME} --no-pager"
echo "Health:  curl -s http://127.0.0.1:${APP_PORT}/health"
