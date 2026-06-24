#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path("/Users/chx/locomo-eval-web")
ECHO_TASK_ID = "echomemory_generic_qa_20260622_233948_1a19e0"
OV_TASK_ID = "openviking_generic_qa_20260622_231559_1bd882"
TERMINAL = {"succeeded", "failed", "done", "cancelled", "canceled", "interrupted"}

RENDERERS = [
    ROOT / "scripts/render_hotpotqa_echomemory_live_diagnosis_20260622.py",
    ROOT / "scripts/render_hotpotqa_openviking_vs_echomemory_live_100_20260622.py",
]


def fetch_task(task_id: str) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:19181/api/tasks/{task_id}", timeout=20) as response:
            data = json.load(response)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"id": task_id, "status": "running"}


def render_once() -> None:
    for script in RENDERERS:
        subprocess.run([sys.executable, str(script)], cwd=str(ROOT), check=False)


def main() -> int:
    idle_rounds = 0
    while True:
        echo = fetch_task(ECHO_TASK_ID)
        ov = fetch_task(OV_TASK_ID)
        render_once()
        echo_status = str(echo.get("status") or "").strip().lower()
        ov_status = str(ov.get("status") or "").strip().lower()
        if echo_status in TERMINAL and ov_status in TERMINAL:
            idle_rounds += 1
            if idle_rounds >= 3:
                return 0
        else:
            idle_rounds = 0
        time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
