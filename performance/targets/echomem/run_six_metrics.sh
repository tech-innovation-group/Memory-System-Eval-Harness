#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 {quick|full|m6} PROFILE_JSON OUTPUT_DIR [ENV_FILE]" >&2
  exit 2
}

[[ $# -ge 3 && $# -le 4 ]] || usage

mode=$1
profile_json=$2
output_dir=$3
env_file=${4:-}

[[ -f "$profile_json" ]] || { echo "Profile not found: $profile_json" >&2; exit 2; }
[[ -x .venv/bin/python ]] || { echo "Missing .venv/bin/python; install requirements first" >&2; exit 2; }
if [[ -n "$env_file" && ! -f "$env_file" ]]; then
  echo "Env file not found: $env_file" >&2
  exit 2
fi

command=(
  .venv/bin/python -m performance.targets.echomem.observation_run
  --profiles "$profile_json"
  --out-dir "$output_dir"
)
if [[ -n "${ECHOMEM_STRESS_PROFILE:-}" ]]; then
  command+=(--profile "$ECHOMEM_STRESS_PROFILE")
fi
if [[ -n "$env_file" ]]; then
  command+=(--env-file "$env_file")
fi

case "$mode" in
  quick)
    command+=(--quick)
    ;;
  full)
    command+=(--metrics M1,M2,M3,M4,M5,M6)
    ;;
  m6)
    command+=(--metrics M6)
    ;;
  *)
    usage
    ;;
esac

exec "${command[@]}"
