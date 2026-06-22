#!/bin/zsh
set -euo pipefail

ROOT="/Users/chx/locomo-eval-web"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="${1:-$ROOT/runs/echomemory_v010_subset20_segment_memory_ruleintent_aligned_$STAMP}"
WORKSPACE="${2:-/private/tmp/echomemory_v010_subset20_segment_memory_$STAMP}"
ACCOUNT="${3:-echomemory-v010-subset20-segment-memory-$STAMP}"
WINDOW_TURNS="${4:-12}"
PENDING_TOKENS="${5:-2304}"
LABEL="${6:-segment-memory-subset20}"
MAX_SESSIONS="${7:-0}"
QUESTIONS_OVERRIDE="${8:-}"
SEGMENT_WINDOW="${9:-4}"
SEGMENT_STRIDE="${10:-4}"
SEGMENT_MAX_CHARS="${11:-1400}"

export RUN_DIR
export ECHOMEM_SESSION_SEGMENT_ENABLED="true"
export ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE="$SEGMENT_WINDOW"
export ECHOMEM_SESSION_SEGMENT_STRIDE="$SEGMENT_STRIDE"
export ECHOMEM_SESSION_SEGMENT_MAX_CHARS="$SEGMENT_MAX_CHARS"

/bin/zsh "$ROOT/scripts/run_echomemory_v010_subset20_trigger_windowbudget_ruleintent_aligned.sh" \
  "$RUN_DIR" \
  "$WORKSPACE" \
  "$ACCOUNT" \
  "$WINDOW_TURNS" \
  "$PENDING_TOKENS" \
  "$LABEL" \
  "$MAX_SESSIONS" \
  "$QUESTIONS_OVERRIDE"

python3 - <<'PY'
import json
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
manifest = run_dir / "subset20_manifest.json"
if manifest.exists():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    env = dict(data.get("env") or {})
    env.update({
        "ECHOMEM_SESSION_SEGMENT_ENABLED": os.environ["ECHOMEM_SESSION_SEGMENT_ENABLED"],
        "ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE": os.environ["ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE"],
        "ECHOMEM_SESSION_SEGMENT_STRIDE": os.environ["ECHOMEM_SESSION_SEGMENT_STRIDE"],
        "ECHOMEM_SESSION_SEGMENT_MAX_CHARS": os.environ["ECHOMEM_SESSION_SEGMENT_MAX_CHARS"],
    })
    notes = list(data.get("notes") or [])
    notes.append(
        "Segment-memory experiment wrapper: enable session.segment generation inside SessionService.commit()."
    )
    notes.append(
        f"Segment params: window_size={os.environ['ECHOMEM_SESSION_SEGMENT_WINDOW_SIZE']}, stride={os.environ['ECHOMEM_SESSION_SEGMENT_STRIDE']}, max_chars={os.environ['ECHOMEM_SESSION_SEGMENT_MAX_CHARS']}."
    )
    data["env"] = env
    data["notes"] = notes
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
PY
