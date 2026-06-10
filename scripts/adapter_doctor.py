#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.adapters.doctor import build_report, markdown_report, text_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check memory backend adapter contracts.")
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero for warn as well as fail.")
    args = parser.parse_args()
    report = build_report()
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.format == "markdown":
        print(markdown_report(report))
    else:
        print(text_report(report))
    if report["status"] == "fail" or (args.strict and report["status"] != "ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
