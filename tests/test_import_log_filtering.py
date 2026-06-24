from __future__ import annotations

from pathlib import Path


def test_import_log_filter_promotes_atom_and_summary_lines() -> None:
    app_js = Path("/Users/chx/locomo-eval-web/static/app.js").read_text(encoding="utf-8")

    assert r"/\[(atom|atom-extract|atom-extract-call|summary)\]/" in app_js
