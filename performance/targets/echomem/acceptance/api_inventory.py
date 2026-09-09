"""Read-only route inventory; partial static discovery is never full coverage."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


def discover_routes(source: Path) -> dict[str, Any]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    routes: dict[tuple[str, str], dict] = {}
    unresolved = []

    def add(method, path, line, origin):
        if isinstance(path, str) and path.startswith("/"):
            routes.setdefault((method, path), {"method": method, "path": path,
                "source_line": line, "source": str(source), "origin": origin})

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_UNSAFE_ROUTE_CONTRACTS":
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    try:
                        method, path = ast.literal_eval(key)
                    except (ValueError, TypeError):
                        continue
                    add(method, path, key.lineno, "declared-route-contract")
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith(("_dispatch_", "_route_")):
            continue
        method = node.name.rsplit("_", 1)[-1].upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
            continue
        has_dynamic = False
        for item in ast.walk(node):
            if isinstance(item, ast.Compare) and len(item.ops) == 1:
                left = item.left
                is_path = (isinstance(left, ast.Name) and left.id == "path") or (
                    isinstance(left, ast.Attribute) and left.attr == "path")
                if is_path and isinstance(item.ops[0], (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                    try:
                        value = ast.literal_eval(item.comparators[0])
                    except (ValueError, TypeError):
                        continue
                    for path in value if isinstance(value, (list, tuple, set)) else [value]:
                        add(method, path, item.lineno, "path-guard-candidate")
            if isinstance(item, ast.Subscript) and isinstance(item.value, ast.Name) and item.value.id.endswith("parts"):
                has_dynamic = True
        if has_dynamic:
            unresolved.append({"function": node.name, "line": node.lineno,
                               "reason": "dynamic segmented routing requires explicit catalog review"})
    return {"status": "PARTIAL", "source": str(source),
            "routes": [routes[key] for key in sorted(routes)],
            "unresolved": unresolved,
            "note": "Static candidates only; existence, reachability and complete product API coverage are not proven."}


def compare_observed(inventory: dict, observed: list[dict]) -> dict:
    rows = []
    for route in inventory["routes"]:
        # Compare path segments without exporting concrete resource identifiers.
        pattern = re.compile("^" + "/".join("[^/]+" if p.startswith("{") and p.endswith("}")
                             else re.escape(p) for p in route["path"].split("/")) + "$")
        matches = []
        for entry in observed:
            endpoint = str(entry.get("endpoint", "")).removeprefix("http/")
            method, separator, path = endpoint.partition(" ")
            if separator and method == route["method"] and pattern.fullmatch(path):
                matches.append(entry)
        counts: dict[str, int] = {}
        for entry in matches:
            for code, count in entry.get("status_counts", {}).items():
                counts[code] = counts.get(code, 0) + count
        rows.append({**route, "observed_completions": sum(e["observed_completions"] for e in matches),
                     "status_counts": counts, "coverage": "OBSERVED_HTTP_ONLY" if matches else "NOT_OBSERVED",
                     "business_contract_verified": False})
    return {**inventory, "routes": rows, "observed_candidates": sum(bool(r["observed_completions"]) for r in rows),
            "candidate_count": len(rows), "all_product_apis_covered": False}
