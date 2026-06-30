from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WebPackageLayout:
    """Frontend package manifest.

    This is the single place that knows where the live frontend assets and UI
    contract live on disk. The server should depend on this package manifest
    rather than hard-coding scattered ``web/static`` paths.
    """

    repo_root: Path
    package_root: Path
    static_root: Path
    legacy_static_root: Path
    ui_contract_file: Path

    @property
    def active_static_root(self) -> Path:
        return self.static_root if self.static_root.exists() else self.legacy_static_root

    def load_ui_contract(self) -> dict[str, Any]:
        try:
            return json.loads(self.ui_contract_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def contract_public_static_files(self, ui_contract: dict[str, Any] | None = None) -> set[str]:
        contract = ui_contract if isinstance(ui_contract, dict) else self.load_ui_contract()
        delivery = contract.get("delivery_boundary") if isinstance(contract.get("delivery_boundary"), dict) else {}
        configured = [
            str(item)
            for item in (delivery.get("public_static_files") or [])
            if str(item).strip()
        ]
        canonical = configured or [
            "web/static/index.html",
            "web/static/app-state.js",
            "web/static/app-core.js",
            "web/static/app-format.js",
            "web/static/app.js",
            "web/static/styles.css",
            "web/static/product-roadmap.html",
        ]
        public_files = set(canonical)
        for rel in list(public_files):
            if rel.startswith("web/static/"):
                public_files.add("static/" + rel.removeprefix("web/static/"))
        return public_files


def load_web_package(repo_root: Path) -> WebPackageLayout:
    root = repo_root.resolve()
    package_root = root / "web"
    return WebPackageLayout(
        repo_root=root,
        package_root=package_root,
        static_root=package_root / "static",
        legacy_static_root=root / "static",
        ui_contract_file=package_root / "ui_contract.json",
    )


__all__ = ["WebPackageLayout", "load_web_package"]
