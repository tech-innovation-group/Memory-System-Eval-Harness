#!/usr/bin/env bash
set -euo pipefail

# Single-instance 4U8G entry point for the PR397 + PR421 suite.
# The soak case is deliberately opt-in; this command is the bounded
# acceptance run used before a longer stability experiment.
root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root_dir"
python_bin="${STRESS_PYTHON:-python3}"
python_major="$("$python_bin" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)"
python_minor="$("$python_bin" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)"
if [ "$python_major" -lt 3 ] || { [ "$python_major" -eq 3 ] && [ "$python_minor" -lt 9 ]; }; then
  echo "ERROR: Harness requires Python >= 3.9; detected $("$python_bin" --version 2>&1 || true)." >&2
  echo "Run this script inside the echomem-stress-runner image; see README.md section 6." >&2
  exit 78
fi
base_url="${ECHOMEM_BASE_URL:-http://127.0.0.1:8010}"
tenant_config="${STRESS_TENANT_CONFIG:?set STRESS_TENANT_CONFIG to an independent-tenant JSON file}"
preflight_config="${ECHOMEM_CONFIG:?set ECHOMEM_CONFIG to the actual EchoMem config.json}"
out_dir="${STRESS_OUTPUT_DIR:-$root_dir/results/performance/4u8g-complete-$(date +%Y%m%d_%H%M%S)}"
repeats="${STRESS_REPEATS:-1}"
commit_timeout_s="${STRESS_COMMIT_TIMEOUT_S:-600}"
case_timeout_s="${STRESS_CASE_TIMEOUT_S:-0}"
barrier_wave_size="${STRESS_BARRIER_WAVE_SIZE:-32}"
barrier_drain_timeout_s="${STRESS_BARRIER_DRAIN_TIMEOUT_S:-600}"
max_wall_clock_s="${STRESS_MAX_WALL_CLOCK_S:-21600}"
case_retries="${STRESS_CASE_RETRIES:-2}"
case_retry_backoff_s="${STRESS_CASE_RETRY_BACKOFF_S:-5}"

# The full 4U8G profile runs 12 PR397/report(6) cases plus the 25-case
# PR421 catalog. The long soak case remains excluded from the routine run.
profile="${STRESS_PROFILE:-4u8g-full}"
scenarios="${STRESS_SCENARIOS:-}"
scenario_args=()
if [ -n "$scenarios" ]; then
  scenario_args=(--scenarios "$scenarios")
fi

mkdir -p "$out_dir"

"$python_bin" -m performance.formal_suite \
  --base-url "$base_url" \
  --tenant-config "$tenant_config" \
  --preflight-config "$preflight_config" \
  --profile "$profile" \
  "${scenario_args[@]}" \
  --repeats "$repeats" \
  --commit-timeout-s "$commit_timeout_s" \
  --case-timeout-s "$case_timeout_s" \
  --barrier-wave-size "$barrier_wave_size" \
  --barrier-drain-timeout-s "$barrier_drain_timeout_s" \
  --max-wall-clock-s "$max_wall_clock_s" \
  --case-retries "$case_retries" \
  --case-retry-backoff-s "$case_retry_backoff_s" \
  --out-dir "$out_dir"

printf '4U8G complete suite: %s\n' "$out_dir"
