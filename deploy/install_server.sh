#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/memory-eval-harness}"
WEB_ROOT="${WEB_ROOT:-/opt/memory-eval-web}"
SOURCE_ROOT="${SOURCE_ROOT:-/opt/memory-eval-sources}"
ENV_FILE="${ENV_FILE:-$WEB_ROOT/server.env}"
WEB_IMAGE="${WEB_IMAGE:-memory-eval-web:local}"
RUNNER_IMAGE="${RUNNER_IMAGE:-memory-eval-runner:local}"
SKILL_SOURCE="$INSTALL_ROOT/deploy/skills/echomem-eval-startup"
SKILL_TARGET="$WEB_ROOT/data/skills/echomem-eval-startup"

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
if [[ "$SESSION_SECRET" == "change-this-secret" ]]; then
  echo "SESSION_SECRET 仍是示例值，请在 $ENV_FILE 改为随机长字符串" >&2
  exit 1
fi
if [[ -n "${FEISHU_APP_ID:-}" || -n "${FEISHU_APP_SECRET:-}" ]]; then
  : "${FEISHU_APP_ID:?启用飞书时必须配置 FEISHU_APP_ID}"
  : "${FEISHU_APP_SECRET:?启用飞书时必须配置 FEISHU_APP_SECRET}"
  : "${PUBLIC_BASE_URL:?启用飞书时必须配置 PUBLIC_BASE_URL}"
fi
if [[ -n "${FEISHU_BITABLE_APP_TOKEN:-}" || -n "${FEISHU_BITABLE_TABLE_ID:-}" ]]; then
  : "${FEISHU_BITABLE_APP_TOKEN:?上传多维表格时必须配置 FEISHU_BITABLE_APP_TOKEN}"
  : "${FEISHU_BITABLE_TABLE_ID:?上传多维表格时必须配置 FEISHU_BITABLE_TABLE_ID}"
fi

docker build -f "$INSTALL_ROOT/deploy/Dockerfile.runner" \
  -t "$RUNNER_IMAGE" "$INSTALL_ROOT"
docker build -f "$INSTALL_ROOT/deploy/Dockerfile.web" \
  -t "$WEB_IMAGE" "$INSTALL_ROOT"

mkdir -p "$SKILL_TARGET"
install -m 0644 "$SKILL_SOURCE/SKILL.md" "$SKILL_TARGET/SKILL.md"
install -m 0644 "$SKILL_SOURCE/references/failure-categories.md" \
  "$SKILL_TARGET/failure-categories.md"
install -m 0755 "$SKILL_SOURCE/scripts/preflight.sh" \
  "$SKILL_TARGET/preflight.sh"
touch "$SKILL_TARGET/incidents.jsonl"
chmod 0644 "$SKILL_TARGET/incidents.jsonl"

sed -i.bak \
  -e "/^EVAL_IMAGE=/d" \
  -e "/^RESULTS_DIR=/d" \
  -e "/^HOST_RESULTS_DIR=/d" \
  -e "/^SOURCE_ROOT=/d" \
  -e "/^SOURCE_CACHE_ROOT=/d" \
  -e "/^WEB_DATA_DIR=/d" \
  -e "/^RESULT_ARCHIVE_DIR=/d" \
  -e "/^ECHOMEM_WORKSPACE_CACHE=/d" \
  "$ENV_FILE"
cat >>"$ENV_FILE" <<EOF
EVAL_IMAGE=$RUNNER_IMAGE
WEB_DATA_DIR=/data
RESULTS_DIR=$INSTALL_ROOT/results
HOST_RESULTS_DIR=$INSTALL_ROOT/results
RESULT_ARCHIVE_DIR=$INSTALL_ROOT/results/_archives
SOURCE_ROOT=$SOURCE_ROOT
SOURCE_CACHE_ROOT=$SOURCE_ROOT/_cache
ECHOMEM_WORKSPACE_CACHE=$WEB_ROOT/cache
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
    ENV_FILE="$ENV_FILE" WEB_PORT="${WEB_PORT:-8081}" \
      "$SKILL_TARGET/preflight.sh" --strict
    echo "部署成功：http://$(hostname -I | awk '{print $1}'):${WEB_PORT:-8081}"
    exit 0
  fi
  sleep 2
done
docker logs --tail 100 memory-eval-web >&2 || true
exit 1
