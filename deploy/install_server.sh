#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/memory-eval-harness}"
WEB_ROOT="${WEB_ROOT:-/opt/memory-eval-web}"
SOURCE_ROOT="${SOURCE_ROOT:-/opt/memory-eval-sources}"
ENV_FILE="${ENV_FILE:-$WEB_ROOT/server.env}"
WEB_IMAGE="${WEB_IMAGE:-memory-eval-web:local}"
RUNNER_IMAGE="${RUNNER_IMAGE:-memory-eval-runner:local}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 运行：sudo $0" >&2
  exit 1
fi
command -v docker >/dev/null || { echo "未找到 Docker"; exit 1; }
docker info >/dev/null || { echo "Docker daemon 不可用"; exit 1; }

mkdir -p "$INSTALL_ROOT" "$WEB_ROOT/data" "$WEB_ROOT/cache/recall" "$SOURCE_ROOT" \
  "$INSTALL_ROOT/results/_archives"
if [[ "$ROOT" != "$INSTALL_ROOT" ]]; then
  cp -a "$ROOT/." "$INSTALL_ROOT/"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/deploy/server.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "已生成配置：$ENV_FILE，请填写 API Key 后重新执行。"
  exit 2
fi
set -a
# The env file is operator-owned and is never copied into the repository.
source "$ENV_FILE"
set +a
: "${DEFAULT_LLM_API_KEY:?请在 $ENV_FILE 配置 DEFAULT_LLM_API_KEY}"
: "${DEFAULT_EMBEDDING_API_KEY:?请在 $ENV_FILE 配置 DEFAULT_EMBEDDING_API_KEY}"
: "${SESSION_SECRET:?请在 $ENV_FILE 配置 SESSION_SECRET}"

docker build -f "$INSTALL_ROOT/deploy/Dockerfile.runner" \
  -t "$RUNNER_IMAGE" "$INSTALL_ROOT"
docker build -f "$INSTALL_ROOT/deploy/Dockerfile.web" \
  -t "$WEB_IMAGE" "$INSTALL_ROOT"

sed -i.bak \
  -e "/^EVAL_IMAGE=/d" \
  -e "/^RESULTS_DIR=/d" \
  -e "/^HOST_RESULTS_DIR=/d" \
  -e "/^SOURCE_ROOT=/d" \
  -e "/^SOURCE_CACHE_ROOT=/d" \
  -e "/^WEB_DATA_DIR=/d" \
  "$ENV_FILE"
cat >>"$ENV_FILE" <<EOF
EVAL_IMAGE=$RUNNER_IMAGE
WEB_DATA_DIR=/data
RESULTS_DIR=/opt/memory-eval-harness/results
HOST_RESULTS_DIR=/opt/memory-eval-harness/results
SOURCE_ROOT=/opt/memory-eval-sources
SOURCE_CACHE_ROOT=/opt/memory-eval-sources/_cache
EOF

docker rm -f memory-eval-web >/dev/null 2>&1 || true
docker run -d --name memory-eval-web --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -p "${WEB_PORT:-8081}:8081" \
  -v "$WEB_ROOT/data:/data" \
  -v "$SOURCE_ROOT:$SOURCE_ROOT" \
  -v "$WEB_ROOT/cache:$WEB_ROOT/cache" \
  -v "$INSTALL_ROOT/results:$INSTALL_ROOT/results" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  "$WEB_IMAGE" >/dev/null

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${WEB_PORT:-8081}/" >/dev/null; then
    echo "部署成功：http://$(hostname -I | awk '{print $1}'):${WEB_PORT:-8081}"
    exit 0
  fi
  sleep 2
done
docker logs --tail 100 memory-eval-web >&2 || true
exit 1
