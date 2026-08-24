#!/usr/bin/env bash
set -Eeuo pipefail

strict=0
if [[ "${1:-}" == "--strict" ]]; then
  strict=1
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--strict]" >&2
  exit 2
fi

ENV_FILE="${ENV_FILE:-/opt/memory-eval-web/server.env}"
WEB_CONTAINER="${WEB_CONTAINER:-memory-eval-web}"
WEB_PORT="${WEB_PORT:-8081}"
failures=0

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; failures=$((failures + 1)); }

command -v docker >/dev/null 2>&1 && ok "docker command" || fail "docker command missing"
command -v curl >/dev/null 2>&1 && ok "curl command" || fail "curl command missing"
if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null 2>&1 && ok "Docker daemon" || fail "Docker daemon unavailable"
fi

if [[ -f "$ENV_FILE" ]]; then
  ok "environment file: $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  fail "environment file missing: $ENV_FILE"
fi

for name in DEFAULT_LLM_API_KEY DEFAULT_EMBEDDING_API_KEY SESSION_SECRET; do
  [[ -n "${!name:-}" ]] && ok "$name configured" || fail "$name missing"
done
[[ "${SESSION_SECRET:-}" != "change-this-secret" ]] \
  && ok "SESSION_SECRET is not the example value" \
  || fail "SESSION_SECRET still uses the example value"

if [[ -n "${FEISHU_APP_ID:-}" || -n "${FEISHU_APP_SECRET:-}" ]]; then
  [[ -n "${FEISHU_APP_ID:-}" && -n "${FEISHU_APP_SECRET:-}" ]] \
    && ok "Feishu app credentials configured" \
    || fail "FEISHU_APP_ID and FEISHU_APP_SECRET must be configured together"
  [[ -n "${PUBLIC_BASE_URL:-}" ]] \
    && ok "PUBLIC_BASE_URL=${PUBLIC_BASE_URL}" \
    || fail "PUBLIC_BASE_URL missing for Feishu"
fi

printf '[INFO] LLM model=%s\n' "${DEFAULT_LLM_MODEL:-missing}"
printf '[INFO] embedding model=%s\n' "${DEFAULT_EMBEDDING_MODEL:-missing}"
printf '[INFO] auto commit threshold=%s\n' "${ECHOMEM_AUTO_COMMIT_THRESHOLD:-missing}"
printf '[INFO] extraction temperature=%s\n' "${ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE:-missing}"

available_kb="$(df -Pk /opt 2>/dev/null | awk 'NR==2 {print $4}')"
if [[ "$available_kb" =~ ^[0-9]+$ ]] && (( available_kb >= 10 * 1024 * 1024 )); then
  ok "disk space >= 10 GiB"
else
  warn "available disk under /opt is below 10 GiB or unknown"
  (( strict == 0 )) || failures=$((failures + 1))
fi

cache="${ECHOMEM_WORKSPACE_CACHE:-/opt/memory-eval-web/cache}/recall/semantic_embeddings.json"
if [[ -s "$cache" ]]; then
  ok "semantic embedding warm-up cache"
else
  warn "semantic embedding cache missing; first task can be slower: $cache"
fi

if docker inspect "$WEB_CONTAINER" >/dev/null 2>&1; then
  state="$(docker inspect "$WEB_CONTAINER" --format '{{.State.Status}}')"
  [[ "$state" == "running" ]] && ok "$WEB_CONTAINER running" || fail "$WEB_CONTAINER state=$state"
else
  (( strict == 0 )) && warn "$WEB_CONTAINER not created yet" || fail "$WEB_CONTAINER missing"
fi

if curl -fsS "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1; then
  ok "Web health http://127.0.0.1:${WEB_PORT}/"
else
  (( strict == 0 )) && warn "Web endpoint not ready" || fail "Web endpoint unavailable"
fi

if [[ -n "${FEISHU_APP_ID:-}" ]]; then
  token="${FEISHU_VERIFICATION_TOKEN:-probe}"
  body="$(printf '{\"type\":\"url_verification\",\"challenge\":\"probe\",\"token\":\"%s\"}' "$token")"
  if curl -fsS -X POST "http://127.0.0.1:${WEB_PORT}/feishu/events" \
      -H 'Content-Type: application/json' --data "$body" | grep -q 'probe'; then
    ok "Feishu callback URL verification"
  else
    fail "Feishu callback verification failed"
  fi
fi

if (( failures > 0 )); then
  printf '[SUMMARY] %d blocking check(s) failed\n' "$failures" >&2
  exit 1
fi
echo "[SUMMARY] server is ready for an evaluation task"
