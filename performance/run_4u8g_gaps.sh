#!/usr/bin/env bash
set -euo pipefail

# Run only black-box gap probes against an existing formal suite.
# This does not resend the 37-case real-model workload.
root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root_dir"

profiles="${STRESS_PROFILES:-$root_dir/performance/instance-profile-4u8g.audit.server.example.json}"
profile="${STRESS_PROFILE:-4U8G}"
suite_path="${STRESS_SUITE_PATH:?set STRESS_SUITE_PATH to an existing formal suite.json}"
out_dir="${STRESS_OUTPUT_DIR:-$root_dir/results/performance/4u8g-gaps-$(date +%Y%m%d_%H%M%S)}"
env_file="${STRESS_ENV_FILE:-}"

args=(
  -m performance.objective_suite
  --profiles "$profiles"
  --profile "$profile"
  --gaps-only
  --suite-path "$suite_path"
  --out-dir "$out_dir"
  --timeout-s "${STRESS_TIMEOUT_S:-7200}"
  --max-wall-clock-s "${STRESS_MAX_WALL_CLOCK_S:-7200}"
)
if [ -n "$env_file" ]; then
  args+=(--env-file "$env_file")
fi

python3 "${args[@]}"
printf '4U8G gap suite: %s\n' "$out_dir"
