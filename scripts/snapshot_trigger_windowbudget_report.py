#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


DEFAULT_SOURCE = Path("/Users/chx/locomo-eval-web/runs/echomemory_v010_conv30_formal_windowbudget_protocol_20260617_1645.html")
DEFAULT_OUTPUT_DIR = Path("/Users/chx/locomo-eval-web/runs")


def build_snapshot_name(source: Path, ts: datetime) -> str:
    stem = source.stem
    return f"{stem}_hourly_{ts.strftime('%Y%m%d_%H%M%S')}.html"


def inject_snapshot_banner(html: str, snapshot_at: str, source_path: str) -> str:
    banner = (
        "\n    <section style=\"margin:16px 0 24px; padding:14px 16px; "
        "border:1px solid #d9d9d9; background:#fafafa; color:#222;\">"
        f"<strong>Hourly Snapshot</strong><div>生成时间：<code>{snapshot_at}</code></div>"
        f"<div>来源报告：<code>{source_path}</code></div></section>\n"
    )
    marker = "<body>"
    if marker in html:
        return html.replace(marker, marker + banner, 1)
    return banner + html


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a timestamped HTML snapshot from the current trigger-windowbudget report.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"source report not found: {source}")

    ts = datetime.now()
    snapshot_name = build_snapshot_name(source, ts)
    output_path = output_dir / snapshot_name

    html = source.read_text(encoding="utf-8")
    html = inject_snapshot_banner(
        html,
        ts.strftime("%Y-%m-%d %H:%M:%S"),
        str(source),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
