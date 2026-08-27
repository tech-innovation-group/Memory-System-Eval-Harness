#!/usr/bin/env bash
set -Eeuo pipefail

# Run one LoCoMo injection and compare QA under three EchoMem engine configs.
# The runner must contain the --qa-only-from option from benchmarks/locomo.

ROOT="${ENGINE_MATRIX_ROOT:?set ENGINE_MATRIX_ROOT}"
RESULTS_ROOT="${ENGINE_MATRIX_RESULTS_ROOT:?set ENGINE_MATRIX_RESULTS_ROOT}"
ECHO_IMAGE="${ECHOMEM_IMAGE:?set ECHOMEM_IMAGE}"
EVAL_IMAGE="${EVAL_IMAGE:?set EVAL_IMAGE}"
BASE_CONFIG="${ECHOMEM_CONFIG_EXAMPLE:?set ECHOMEM_CONFIG_EXAMPLE}"
RUNNER_NETWORK="${ENGINE_MATRIX_RUNNER_NETWORK:-host}"
ECHO_HTTP_PORT="${ENGINE_MATRIX_HTTP_PORT:-18260}"
ECHO_MCP_PORT="${ENGINE_MATRIX_MCP_PORT:-18261}"
SAMPLE="${ENGINE_MATRIX_SAMPLE:-conv-30}"

mkdir -p "$ROOT" "$RESULTS_ROOT"
python3 - "$BASE_CONFIG" "$ROOT" <<'PY'
import copy
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(sys.argv[2])
variants = {
    "full": ["atomic_engine", "episode_engine", "base_engine", "memory_unit_engine"],
    "atomic-only": ["atomic_engine"],
    "atomic-base": ["atomic_engine", "base_engine"],
}
for name, enabled in variants.items():
    config = copy.deepcopy(source)
    config.setdefault("engine", {})["enabled"] = enabled
    config.setdefault("mcp", {}).update({
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8001,
    })
    (root / f"config-{name}.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
PY

getenv() {
  local key="$1"
  docker inspect memory-eval-web --format '{{range .Config.Env}}{{println .}}{{end}}' |
    awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}'
}

: "${LLM_BASE_URL:=$(getenv DEFAULT_LLM_BASE_URL)}"
: "${LLM_MODEL:=$(getenv DEFAULT_LLM_MODEL)}"
: "${LLM_API_KEY:=$(getenv DEFAULT_LLM_API_KEY)}"
: "${EMBEDDING_BASE_URL:=$(getenv DEFAULT_EMBEDDING_BASE_URL)}"
: "${EMBEDDING_MODEL:=$(getenv DEFAULT_EMBEDDING_MODEL)}"
: "${EMBEDDING_API_KEY:=$(getenv DEFAULT_EMBEDDING_API_KEY)}"
: "${ECHOMEM_AUTO_COMMIT_THRESHOLD:=20000}"
: "${ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE:=0.7}"

: "${LLM_API_KEY:?missing LLM_API_KEY}"
: "${EMBEDDING_API_KEY:?missing EMBEDDING_API_KEY}"
: "${ECHOMEM_REGISTRY_MASTER_KEY:=$(openssl rand -base64 32 | tr -d '\n')}"
if [[ -z "${PROVISIONING_KEY:-}" ]]; then
  PROVISIONING_KEY="$(
    MASTER_KEY="$ECHOMEM_REGISTRY_MASTER_KEY" python3 - <<'PY'
import hashlib
import hmac
import os

print(hmac.new(
    os.environ["MASTER_KEY"].encode(),
    b"echomem.registry-provisioning.v1",
    hashlib.sha256,
).hexdigest())
PY
  )"
fi

cat > "$ROOT/echo.env" <<EOF
ECHOMEM_REGISTRY_MASTER_KEY=${ECHOMEM_REGISTRY_MASTER_KEY:?set ECHOMEM_REGISTRY_MASTER_KEY}
ECHOMEM_AUTO_COMMIT_THRESHOLD=$ECHOMEM_AUTO_COMMIT_THRESHOLD
ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE=$ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE
ECHOMEM_LLM_API_KEY=$LLM_API_KEY
ECHOMEM_ATOMIC_ENGINE_LLM_API_KEY=$LLM_API_KEY
ECHOMEM_BASE_ENGINE_LLM_API_KEY=$LLM_API_KEY
ECHOMEM_EPISODE_ENGINE_LLM_API_KEY=$LLM_API_KEY
ECHOMEM_INTENT_LLM_API_KEY=$LLM_API_KEY
ECHOMEM_MEMORY_UNIT_ENGINE_LLM_API_KEY=$LLM_API_KEY
ECHOMEM_EMBEDDING_API_KEY=$EMBEDDING_API_KEY
ECHOMEM_RERANK_API_KEY=$EMBEDDING_API_KEY
EOF

cat > "$ROOT/eval.env" <<EOF
LLM_BASE_URL=$LLM_BASE_URL
LLM_MODEL=$LLM_MODEL
LLM_API_KEY=$LLM_API_KEY
DEFAULT_EMBEDDING_BASE_URL=$EMBEDDING_BASE_URL
DEFAULT_EMBEDDING_MODEL=$EMBEDDING_MODEL
DEFAULT_EMBEDDING_API_KEY=$EMBEDDING_API_KEY
EOF

rm -rf "$ROOT/workspace" "$ROOT/cache"
mkdir -p "$ROOT/workspace" "$ROOT/cache"
docker rm -f echomem-engine-matrix >/dev/null 2>&1 || true

start_echo() {
  local variant="$1"
  docker rm -f echomem-engine-matrix >/dev/null 2>&1 || true
  docker run -d --name echomem-engine-matrix --env-file "$ROOT/echo.env" \
    -p "127.0.0.1:${ECHO_HTTP_PORT}:8010" \
    -p "127.0.0.1:${ECHO_MCP_PORT}:8001" \
    -v "$ROOT/workspace:/workspace" \
    -v "$ROOT/cache:/workspace/cache" \
    -v "$ROOT/config-${variant}.json:/workspace/config.json:ro" \
    "$ECHO_IMAGE" >/dev/null
  for _ in $(seq 1 180); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${ECHO_HTTP_PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  docker inspect echomem-engine-matrix > "$ROOT/docker-inspect-${variant}.json" || true
  docker logs echomem-engine-matrix > "$ROOT/docker-logs-${variant}.txt" 2>&1 || true
  return 1
}

run_eval() {
  local variant="$1"
  local output_name="locomo-engine-matrix-${variant}"
  local args=(
    python /app/benchmarks/locomo/run_eval.py
    --agent-plugin echomem_mcp
    --echomem-url "http://127.0.0.1:${ECHO_HTTP_PORT}"
    --mcp-url "http://127.0.0.1:${ECHO_MCP_PORT}"
    --sample "$SAMPLE"
    --no-tool-calling
    --no-search-in-tools
    --mcp-read-mode disabled
    --concurrency 1
    --judge-concurrency 1
    --top-k 25
    --memory-budget-chars 8000
    --user-memory-budget-chars 4000
    --agent-memory-budget-chars 2000
    --llm-temperature 0.7
    --question-timeout-s 600
    --llm-timeout-s 600
    --llm-retries 3
    --out-dir "/app/results/${output_name}"
  )
  if [[ "$variant" != "full" ]]; then
    args+=(--qa-only-from "/app/results/${FULL_RESULT_REL}")
  fi
  docker run --name "eval-engine-${variant}" --rm \
    --network "$RUNNER_NETWORK" --env-file "$ROOT/eval.env" \
    -e "ECHOMEM_PROVISIONING_AUTH_KEY=$PROVISIONING_KEY" \
    -v "$RESULTS_ROOT:/app/results:rw" \
    "$EVAL_IMAGE" "${args[@]}"
}

start_echo full
run_eval full
FULL_RESULT_DIR="$(find "$RESULTS_ROOT/locomo-engine-matrix-full" \
  -mindepth 2 -maxdepth 2 -name qa_resume_manifest.json -printf '%h\n' | head -1)"
[[ -n "$FULL_RESULT_DIR" ]] || {
  echo "full injection result manifest not found" >&2
  exit 2
}
FULL_RESULT_REL="${FULL_RESULT_DIR#"$RESULTS_ROOT"/}"

for variant in atomic-only atomic-base; do
  start_echo "$variant"
  run_eval "$variant"
done

docker rm -f echomem-engine-matrix >/dev/null 2>&1 || true
python3 "$(dirname "$0")/analyze_locomo_engine_matrix.py" \
  --results-root "$RESULTS_ROOT" \
  --prefix locomo-engine-matrix \
  --output "$ROOT/matrix-report.json"
