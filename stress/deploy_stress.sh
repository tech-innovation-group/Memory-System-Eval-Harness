#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUN_ID=${RUN_ID:-$(date +%Y%m%d_%H%M%S)}
export RUN_ID
export STRESS_OUTPUT_DIR=${STRESS_OUTPUT_DIR:-"$ROOT_DIR/results/stress"}

mkdir -p "$STRESS_OUTPUT_DIR"
cd "$ROOT_DIR"

if [ -n "${ECHOMEM_CONTAINER:-}" ]; then
  ECHOMEM_PID=${ECHOMEM_PID:-$(docker inspect --format '{{.State.Pid}}' "$ECHOMEM_CONTAINER")}
  export ECHOMEM_PID
fi

docker compose -f stress/compose.yaml build --pull
docker compose -f stress/compose.yaml run --rm \
  --name "echomem-stress-${RUN_ID}" \
  echomem-stress
