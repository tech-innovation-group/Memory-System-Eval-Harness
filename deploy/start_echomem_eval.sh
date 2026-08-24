#!/usr/bin/env bash
set -Eeuo pipefail

# Start one isolated EchoMem checkout for an evaluation job.
# The script only writes the job workspace; it never edits the checkout.

usage() {
  cat <<'EOF'
Usage:
  start_echomem_eval.sh \
    --source /path/to/EchoMem-checkout \
    --workspace /path/to/job/workspace \
    [--port 8010] [--mcp-port 8001] \
    [--cache-dir /path/to/cache/recall] \
    [--cache /path/to/semantic_embeddings.json]

Required environment:
  DEFAULT_LLM_API_KEY

Optional environment:
  DEFAULT_LLM_BASE_URL=https://api.deepseek.com/v1
  DEFAULT_LLM_MODEL=deepseek-v4-flash
  DEFAULT_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  DEFAULT_EMBEDDING_API_KEY=        # Separate key for the embedding provider
  DEFAULT_EMBEDDING_MODEL=text-embedding-v3
  ECHOMEM_AUTO_COMMIT_THRESHOLD=20000
  ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE=0.7
  ECHOMEM_PYTHON=/path/to/python
  ECHOMEM_HEALTH_TIMEOUT_S=300

Cache warm-up:
  --cache-dir copies semantic_embeddings.json and template_embeddings.json when
  present. --cache remains a compatibility shortcut for semantic_embeddings.json.
EOF
}

source_dir=""
workspace=""
port="8010"
mcp_port="8001"
cache_path=""
cache_dir=""
host="127.0.0.1"
mcp_host="127.0.0.1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_dir="${2:?missing value for --source}"; shift 2 ;;
    --workspace) workspace="${2:?missing value for --workspace}"; shift 2 ;;
    --port) port="${2:?missing value for --port}"; shift 2 ;;
    --mcp-port) mcp_port="${2:?missing value for --mcp-port}"; shift 2 ;;
    --cache) cache_path="${2:?missing value for --cache}"; shift 2 ;;
    --cache-dir) cache_dir="${2:?missing value for --cache-dir}"; shift 2 ;;
    --host) host="${2:?missing value for --host}"; shift 2 ;;
    --mcp-host) mcp_host="${2:?missing value for --mcp-host}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

: "${DEFAULT_LLM_API_KEY:?DEFAULT_LLM_API_KEY is required}"
: "${DEFAULT_LLM_BASE_URL:=https://api.deepseek.com/v1}"
: "${DEFAULT_LLM_MODEL:=deepseek-v4-flash}"
: "${DEFAULT_EMBEDDING_BASE_URL:=https://dashscope.aliyuncs.com/compatible-mode/v1}"
: "${DEFAULT_EMBEDDING_API_KEY:?DEFAULT_EMBEDDING_API_KEY is required; do not reuse a chat-only key}"
: "${DEFAULT_EMBEDDING_MODEL:=text-embedding-v3}"
: "${ECHOMEM_AUTO_COMMIT_THRESHOLD:=20000}"
: "${ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE:=0.7}"
: "${ECHOMEM_HEALTH_TIMEOUT_S:=300}"
: "${ECHOMEM_PYTHON:=$(command -v python3)}"

[[ -n "$source_dir" && -n "$workspace" ]] || { usage >&2; exit 2; }
source_dir="$(cd "$source_dir" && pwd)"
workspace="$(mkdir -p "$workspace" && cd "$workspace" && pwd)"
config_example="$source_dir/configs/config.example.json"
[[ -f "$config_example" ]] || {
  echo "missing checkout config: $config_example" >&2
  exit 1
}
[[ -d "$source_dir/src/echomem" ]] || {
  echo "missing EchoMem source package: $source_dir/src/echomem" >&2
  exit 1
}
if [[ -e "$workspace/config.json" ]]; then
  echo "workspace already contains config.json; use a new job workspace" >&2
  exit 1
fi

mkdir -p "$workspace/cache/recall" "$workspace/log"

# Generate a config from the checked-out EchoMem schema. Only model endpoint
# values and the MCP bind address are overridden; engine and recall switches
# remain owned by the checkout.
"$ECHOMEM_PYTHON" - "$config_example" "$workspace/config.json" \
  "$DEFAULT_LLM_BASE_URL" "$DEFAULT_LLM_MODEL" \
  "$DEFAULT_EMBEDDING_BASE_URL" "$DEFAULT_EMBEDDING_MODEL" \
  "$mcp_host" "$mcp_port" <<'PY'
import json
import sys
from pathlib import Path

(
    src,
    dst,
    llm_base_url,
    llm_model,
    embedding_base_url,
    embedding_model,
    mcp_host,
    mcp_port,
) = sys.argv[1:]
data = json.loads(Path(src).read_text(encoding="utf-8"))

def set_endpoint(endpoint, base_url, model):
    if not isinstance(endpoint, dict):
        return
    if endpoint.get("provider") == "fake":
        return
    endpoint["api_base"] = base_url
    endpoint["model"] = model

model = data.setdefault("model", {})
set_endpoint(model.get("llm"), llm_base_url, llm_model)
set_endpoint(model.get("embedding"), embedding_base_url, embedding_model)

recall_model = data.setdefault("recall", {}).setdefault("model", {})
set_endpoint(recall_model.get("intent_llm"), llm_base_url, llm_model)

for engine in data.setdefault("engine", {}).setdefault("configs", {}).values():
    if not isinstance(engine, dict):
        continue
    engine_model = engine.get("model")
    if isinstance(engine_model, dict):
        set_endpoint(engine_model.get("llm"), llm_base_url, llm_model)

mcp = data.setdefault("mcp", {})
mcp["enabled"] = True
mcp["host"] = mcp_host
mcp["port"] = int(mcp_port)

session = data.setdefault("session", {})
if "auto_commit_threshold" in session:
    session["auto_commit_threshold"] = None

Path(dst).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

if [[ -n "$cache_path" && -n "$cache_dir" ]]; then
  echo "use either --cache or --cache-dir, not both" >&2
  exit 2
fi
if [[ -n "$cache_path" ]]; then
  if [[ -d "$cache_path" ]]; then
    cache_dir="$cache_path"
  else
    [[ -s "$cache_path" ]] || { echo "embedding cache is empty: $cache_path" >&2; exit 1; }
    cache_dir="$(dirname "$cache_path")"
  fi
fi
if [[ -n "$cache_dir" ]]; then
  [[ -d "$cache_dir" ]] || { echo "cache directory does not exist: $cache_dir" >&2; exit 1; }
  semantic_cache="$cache_dir/semantic_embeddings.json"
  [[ -s "$semantic_cache" ]] || {
    echo "semantic embedding cache is empty: $semantic_cache" >&2
    exit 1
  }
  cp "$semantic_cache" "$workspace/cache/recall/semantic_embeddings.json"
  template_cache="$cache_dir/template_embeddings.json"
  if [[ -s "$template_cache" ]]; then
    cp "$template_cache" "$workspace/cache/recall/template_embeddings.json"
  fi
  # The vectors are reusable across checkouts with the same embedding model and
  # dimensions; normalize only the cache metadata expected by this checkout.
  "$ECHOMEM_PYTHON" - "$workspace/config.json" \
    "$workspace/cache/recall/semantic_embeddings.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

config_path, cache_path = map(Path, sys.argv[1:])
config = json.loads(config_path.read_text(encoding="utf-8"))
embedding = config.get("model", {}).get("embedding", {})
identity = {
    "model": embedding.get("model"),
    "dimensions": embedding.get("dimensions", 1024),
}
fingerprint = hashlib.sha256(
    json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
).hexdigest()
payload = json.loads(cache_path.read_text(encoding="utf-8"))
if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
    raise SystemExit("invalid semantic embedding cache")
payload["fingerprint"] = fingerprint
cache_path.write_text(
    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    encoding="utf-8",
)
PY
fi

# Export every endpoint key declared by the checked-out config. Values are
# supplied only through the process environment and are never logged.
while IFS= read -r key_env; do
  [[ -z "$key_env" ]] && continue
  if [[ "$key_env" == *EMBEDDING* || "$key_env" == *RERANK* ]]; then
    export "$key_env=$DEFAULT_EMBEDDING_API_KEY"
  else
    export "$key_env=$DEFAULT_LLM_API_KEY"
  fi
done < <("$ECHOMEM_PYTHON" - "$workspace/config.json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
seen = set()
def walk(value):
    if isinstance(value, dict):
        key = value.get("api_key_env")
        if isinstance(key, str) and key and key not in seen:
            seen.add(key)
            print(key)
        for child in value.values():
            walk(child)
    elif isinstance(value, list):
        for child in value:
            walk(child)
walk(data)
PY
)
export ECHOMEM_AUTO_COMMIT_THRESHOLD
export ECHOMEM_ATOMIC_EXTRACTION_TEMPERATURE

pid_file="$workspace/echomem.pid"
log_file="$workspace/log/runner.log"
if [[ -f "$pid_file" ]]; then
  old_pid="$(cat "$pid_file" || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "workspace already has a running EchoMem process: pid=$old_pid" >&2
    exit 1
  fi
fi

cd "$source_dir"
nohup env PYTHONPATH="$source_dir/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$ECHOMEM_PYTHON" -u -m echomem.entrypoints.cli server \
  --host "$host" --port "$port" --workspace "$workspace" \
  >>"$log_file" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$pid_file"

deadline=$((SECONDS + ECHOMEM_HEALTH_TIMEOUT_S))
healthy=0
while (( SECONDS < deadline )); do
  if curl -fsS --connect-timeout 2 --max-time 5 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "EchoMem exited before health check; see $log_file" >&2
    exit 1
  fi
  sleep 2
done

if (( healthy == 0 )); then
  echo "EchoMem health timeout after ${ECHOMEM_HEALTH_TIMEOUT_S}s; see $log_file" >&2
  exit 1
fi

echo "EchoMem healthy"
echo "pid=$pid"
echo "source=$source_dir"
echo "workspace=$workspace"
echo "http=http://127.0.0.1:$port"
echo "mcp=http://127.0.0.1:$mcp_port"
echo "config=$workspace/config.json"
echo "log=$log_file"
