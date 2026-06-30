from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path("/Users/chx/locomo-eval-web")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from web import load_web_package


def test_public_static_contract_tracks_split_frontend_assets() -> None:
    repo_root = REPO_ROOT
    package = load_web_package(repo_root)
    contract = json.loads((repo_root / "web" / "ui_contract.json").read_text(encoding="utf-8"))

    public_files = package.contract_public_static_files(contract)

    expected_web_files = {
        "web/static/index.html",
        "web/static/app-state.js",
        "web/static/app-core.js",
        "web/static/app-format.js",
        "web/static/app.js",
        "web/static/styles.css",
        "web/static/product-roadmap.html",
    }
    expected_legacy_files = {
        "static/" + rel.removeprefix("web/static/")
        for rel in expected_web_files
        if rel.startswith("web/static/")
    }

    assert expected_web_files.issubset(public_files)
    assert expected_legacy_files.issubset(public_files)
    assert public_files == expected_web_files | expected_legacy_files
