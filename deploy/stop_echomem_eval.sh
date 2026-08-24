#!/usr/bin/env bash
set -Eeuo pipefail

workspace="${1:?usage: stop_echomem_eval.sh /path/to/job/workspace}"
pid_file="$workspace/echomem.pid"

if [[ ! -f "$pid_file" ]]; then
  echo "no pid file: $pid_file"
  exit 0
fi

pid="$(cat "$pid_file" || true)"
if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  for _ in $(seq 1 25); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
fi

rm -f "$pid_file"
echo "EchoMem stopped: workspace=$workspace"
