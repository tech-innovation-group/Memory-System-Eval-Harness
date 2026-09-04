#!/usr/bin/env bash
set -euo pipefail

# One-command entry point for the six real-HTTP 4U8G objectives.
#
# Required deployment inputs:
#   STRESS_PROFILES     objective profile JSON
#   ECHOMEM_CONFIG      the actual EchoMem config.json used by the service
#   tenant_config       path inside STRESS_PROFILES (usually tenants-32.server.json)
#
# Optional:
#   ECHOMEM_BASE_URL    default: http://127.0.0.1:8010
#   STRESS_OUTPUT_DIR   default: results/performance/4u8g-six-metrics-<timestamp>
#   STRESS_ENV_FILE     KEY=VALUE file for the real-model subprocesses
#   STRESS_QUICK=1      bounded diagnostic run; not a full acceptance result
#   STRESS_SKIP_PREPARE=1
#                       skip host-only profile switching inside a runner container
#   STRESS_MAX_WALL_CLOCK_S
#   STRESS_PROBE_BUDGET_S default 900; reserved for post-suite probes
#
# The profile controls the actual tenant credentials, fault/restart controls,
# and metrics endpoint. No API key is written to the result artifacts.

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root_dir"
python_bin="${STRESS_PYTHON:-python3}"

profiles="${STRESS_PROFILES:-$root_dir/performance/instance-profiles.example.json}"
config="${ECHOMEM_CONFIG:?set ECHOMEM_CONFIG to the actual EchoMem config.json}"
base_url="${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}"
profile_name="${STRESS_PROFILE_NAME:-4U8G}"
out_dir="${STRESS_OUTPUT_DIR:-$root_dir/results/performance/4u8g-six-metrics-$(date +%Y%m%d_%H%M%S)}"
max_wall_clock="${STRESS_MAX_WALL_CLOCK_S:-10800}"
probe_budget="${STRESS_PROBE_BUDGET_S:-900}"
env_args=()
if [ -n "${STRESS_ENV_FILE:-}" ]; then
  env_args=(--env-file "$STRESS_ENV_FILE")
fi
prepare_args=()
if [ "${STRESS_SKIP_PREPARE:-0}" = "1" ]; then
  prepare_args=(--skip-prepare)
fi
mkdir -p "$out_dir"

# Persist only non-secret run inputs before starting. This makes a partially
# completed server run diagnosable without copying an environment file into
# the result bundle.
"$python_bin" - "$out_dir" "$profiles" "$config" "$base_url" "$profile_name" "$max_wall_clock" "$probe_budget" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

out, profiles, config, base_url, profile, wall, probe = sys.argv[1:]
config_path = Path(config)
config_digest = ""
model = {}
try:
    raw = config_path.read_bytes()
    config_digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode("utf-8"))
    if isinstance(payload, dict):
        model = payload.get("model") or {}
except (OSError, ValueError, UnicodeDecodeError):
    pass
llm = (model.get("llm") or {}) if isinstance(model, dict) else {}
embedding = (model.get("embedding") or {}) if isinstance(model, dict) else {}
manifest = {
    "test_type": "pr29_six_metric_entrypoint",
    "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    "profile": profile,
    "base_url": base_url,
    "profiles_path": os.path.abspath(profiles),
    "echomem_config_path": os.path.abspath(config),
    "echomem_config_sha256": config_digest,
    "llm_model": llm.get("model", ""),
    "embedding_model": embedding.get("model", ""),
    "real_model_required": True,
    "soak_enabled": False,
    "max_wall_clock_s": float(wall),
    "probe_budget_s": float(probe),
    "status": "started",
}
Path(out, "run-manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

if [ "${STRESS_QUICK:-0}" = "1" ]; then
  set +e
  "$python_bin" -m performance.objective_suite \
    --profiles "$profiles" \
    --profile "$profile_name" \
    --base-url "$base_url" \
    --preflight-config "$config" \
    --out-dir "$out_dir" \
    --quick \
    --timeout-s "$max_wall_clock" \
    --max-wall-clock-s "$max_wall_clock" \
    --probe-budget-s "$probe_budget" \
    "${prepare_args[@]}" \
    "${env_args[@]}"
  suite_rc=$?
else
  set +e
  "$python_bin" -m performance.objective_suite \
    --profiles "$profiles" \
    --profile "$profile_name" \
    --base-url "$base_url" \
    --preflight-config "$config" \
    --out-dir "$out_dir" \
    --full \
    --timeout-s "$max_wall_clock" \
    --max-wall-clock-s "$max_wall_clock" \
    --probe-budget-s "$probe_budget" \
    "${prepare_args[@]}" \
    "${env_args[@]}"
  suite_rc=$?
fi

"$python_bin" - "$out_dir/run-manifest.json" "$suite_rc" <<'PY'
import json
import sys
from pathlib import Path

path, rc = sys.argv[1:]
payload = json.loads(Path(path).read_text(encoding="utf-8"))
payload["status"] = "completed" if rc == "0" else "runner_exit_nonzero"
payload["runner_exit_code"] = int(rc)
Path(path).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

# The objective suite intentionally returns non-zero for a real FAIL. Always
# render the report so FAIL/INCONCLUSIVE runs are useful to the caller.
set +e
"$python_bin" scripts/build_pr29_six_metric_report.py "$out_dir" \
  -o "$out_dir/pr29-six-metric-report.html"
report_rc=$?
set -e
printf '4U8G six-metric report: %s/pr29-six-metric-report.html\n' "$out_dir"
if [ "$suite_rc" -ne 0 ]; then
  exit "$suite_rc"
fi
exit "$report_rc"
