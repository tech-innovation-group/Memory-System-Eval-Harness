#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path("/Users/chx/locomo-eval-web/scripts")
ECHO_ROOT = Path("/Users/chx/Code/echomemory/echo_memory_v010")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ECHO_ROOT) not in sys.path:
    sys.path.insert(0, str(ECHO_ROOT))

from echomemory_common import workspace_token_usage_summary  # noqa: E402
from echomem.observability.log_parser import parse_log, summarize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh trigger-windowbudget run summary from log + QA artifacts.")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest_path = None
    for candidate in ("conv30_manifest.json", "subset20_manifest.json"):
        path = run_dir / candidate
        if path.exists():
            manifest_path = path
            break
    if manifest_path is None:
        raise SystemExit(f"manifest not found under {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    qa_dir = run_dir / "echomemory_qa_ruleintent_aligned"
    qa_summary = json.loads((qa_dir / "summary.json").read_text(encoding="utf-8"))
    judge_summary = json.loads((qa_dir / "judge_summary.json").read_text(encoding="utf-8"))
    log_path = None
    for candidate in ("conv30_import.log", "subset20_import.log"):
        path = run_dir / candidate
        if path.exists():
            log_path = path
            break
    if log_path is None:
        raise SystemExit(f"import log not found under {run_dir}")
    log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    qa_marker = log_text.find("[qa]")
    import_log_text = log_text[:qa_marker] if qa_marker >= 0 else log_text
    import_log_summary = summarize(parse_log(import_log_text))

    workspace = str(manifest.get("workspace") or "")
    account = str(manifest.get("account") or "")
    result = {
        "label": str(((manifest.get("notes") or [""])[-1] if (manifest.get("notes") or []) else "")).strip() or run_dir.name,
        "pending_window_turns": int(
            ((manifest.get("env") or {}).get("ECHOMEM_ATOM_WINDOW_SIZE"))
            or ((manifest.get("env") or {}).get("ECHOMEM_EXTRACTION_TRIGGER_WINDOW_TURNS"))
            or 0
        ),
        "pending_token_threshold": int(
            ((manifest.get("env") or {}).get("ECHOMEM_ATOM_MAX_TOKENS"))
            or ((manifest.get("env") or {}).get("ECHOMEM_EXTRACTION_TRIGGER_PENDING_TOKENS"))
            or 0
        ),
        "workspace": workspace,
        "account": account,
        "manifest_path": str(manifest_path),
        "log_path": str(log_path),
        "question_count": int(manifest.get("question_count") or qa_summary.get("count") or judge_summary.get("count") or 0),
        "sample_id": str(manifest.get("sample_id") or "conv-30"),
        "import_token_usage": workspace_token_usage_summary(workspace, account) if workspace and account else {},
        "import_log_usage": {
            "total_calls": import_log_summary.total_calls,
            "total_input_tokens": import_log_summary.total_input_tokens,
            "total_output_tokens": import_log_summary.total_output_tokens,
            "total_tokens": import_log_summary.total_tokens,
            "by_call_site": import_log_summary.by_call_site,
        },
        "qa_summary": qa_summary,
        "judge_summary": judge_summary,
    }
    out = run_dir / "trigger_windowbudget_summary.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
