"""Build a developer-facing anomaly dossier and safe archive for M1/M2/M3 runs.

The script is intentionally post-processing only: it never changes measurements
or denominators.  It reads the canonical observation outputs, computes bounded
diagnostic facts, optionally asks a configured LLM for an evidence-constrained
summary, and augments the existing report.html in place.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import tarfile
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


SCENARIOS = ("m2-fairness-4t", "m2-fairness-8t", "m3-baseline",
             "m3-flood-uniform", "m3-flood-single-tenant", "m3-heterogeneous-tenants")
SAFE_FILES = (
    "summary.json", "execution-manifest.json", "suite.json", "stress-profile.json",
    "report.html", "container.log", "structured-stage-events.jsonl", "records.csv",
    "metrics_samples.csv", "progress.json", "echomem.config.json", "anomaly-dossier.json",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _percent(n: int, d: int) -> float:
    return round(100.0 * n / d, 3) if d else 0.0


def _search_facts(root: Path, scenario: str) -> dict[str, Any]:
    rows = _rows(root / scenario / "search_results.csv")
    statuses = Counter(row.get("status_code") or "transport_error" for row in rows)
    empty = sum(1 for row in rows if _int(row.get("hit_count")) <= 0)
    degraded = sum(1 for row in rows if str(row.get("degraded", "")).lower() == "true")
    return {
        "scenario": scenario,
        "planned_or_recorded": len(rows),
        "completed": sum(1 for row in rows if row.get("status_code")),
        "empty_recall": empty,
        "empty_recall_rate_pct": _percent(empty, len(rows)),
        "degraded": degraded,
        "http_status": dict(statuses),
        "transport_or_http_errors": sum(n for code, n in statuses.items() if code == "transport_error" or not str(code).startswith("2")),
    }


def _commit_facts(root: Path, scenario: str) -> dict[str, Any]:
    rows = _rows(root / scenario / "commit_results.csv")
    statuses = Counter(row.get("status") or "missing" for row in rows)
    return {
        "scenario": scenario,
        "planned_or_recorded": len(rows),
        "completed": statuses.get("completed", 0),
        "rejected": sum(n for key, n in statuses.items() if key in {"rejected", "failed"}),
        "unresolved_or_timeout": sum(n for key, n in statuses.items() if key in {"unresolved", "timeout", "pending"}),
        "status_counts": dict(statuses),
    }


def collect_facts(root: Path) -> dict[str, Any]:
    summary = _read_json(root / "summary.json")
    manifest = _read_json(root / "execution-manifest.json")
    searches = [_search_facts(root, name) for name in SCENARIOS if (root / name).is_dir()]
    commits = [_commit_facts(root, name) for name in SCENARIOS if (root / name).is_dir()]
    duration_s = None
    try:
        from datetime import datetime
        left = datetime.fromisoformat(str(manifest["started_at"]).replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(manifest["finished_at"]).replace("Z", "+00:00"))
        duration_s = round((right - left).total_seconds(), 3)
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "run_status": summary.get("status") or manifest.get("execution_status"),
        "duration_s": duration_s,
        "selected_metrics": manifest.get("selected_metrics") or summary.get("selected_metrics"),
        "search": searches,
        "commit": commits,
        "quality_evidence": {
            "semantic_quality_fields_present": any(
                any("quality_ok" in row for row in _rows(root / name / "search_results.csv"))
                for name in SCENARIOS if (root / name / "search_results.csv").is_file()
            ),
            "note": "quality_ok/事实命中必须有真实 QA 证据；非空 hit_count 不等于事实命中。",
        },
    }


def derive_anomalies(facts: dict[str, Any]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    searches = {row["scenario"]: row for row in facts["search"]}
    commits = {row["scenario"]: row for row in facts["commit"]}
    baseline = searches.get("m3-baseline")
    multi = searches.get("m2-fairness-8t")
    if baseline and multi and multi["empty_recall_rate_pct"] > baseline["empty_recall_rate_pct"]:
        anomalies.append({
            "id": "multi_tenant_empty_recall_regression",
            "severity": "high",
            "title": "多租户空召回高于单租户基线",
            "evidence": {
                "baseline_empty": baseline["empty_recall"],
                "baseline_total": baseline["planned_or_recorded"],
                "multi_tenant_empty": multi["empty_recall"],
                "multi_tenant_total": multi["planned_or_recorded"],
                "rate_delta_pct": round(multi["empty_recall_rate_pct"] - baseline["empty_recall_rate_pct"], 3),
            },
            "next_step": "按 tenant、trace_id、recall stage 对齐空召回请求，区分索引可见性、召回降级和模型服务错误。",
        })
    for row in facts["search"]:
        if row["transport_or_http_errors"]:
            anomalies.append({"id": "search_transport_or_http_error", "severity": "high", "title": f"{row['scenario']} 存在 Search HTTP/传输异常", "evidence": row, "next_step": "按状态码和 reason_code 关联服务端日志。"})
    for row in facts["commit"]:
        if row["rejected"]:
            anomalies.append({"id": "commit_rejected", "severity": "high", "title": f"{row['scenario']} 存在 Commit 拒绝", "evidence": row, "next_step": "检查 admission、租户配额和服务端拒绝原因。"})
        if row["unresolved_or_timeout"]:
            anomalies.append({"id": "commit_unresolved", "severity": "high", "title": f"{row['scenario']} 存在未终态 Commit", "evidence": row, "next_step": "用 request_id/trace_id 对齐队列、执行器和 provider 阶段日志。"})
    if not facts["quality_evidence"]["semantic_quality_fields_present"]:
        anomalies.append({"id": "quality_evidence_missing", "severity": "medium", "title": "缺少事实命中质量证据", "evidence": facts["quality_evidence"], "next_step": "为每道题保存 expected fact、召回文本和 judge 结果，避免把非空召回当作命中。"})
    return anomalies


def _llm_analysis(facts: dict[str, Any], anomalies: list[dict[str, Any]]) -> dict[str, Any]:
    base = os.getenv("ANOMALY_LLM_BASE_URL") or os.getenv("LLM_BASE_URL")
    model = os.getenv("ANOMALY_LLM_MODEL") or os.getenv("LLM_MODEL")
    key = os.getenv("ANOMALY_LLM_API_KEY") or os.getenv("LLM_API_KEY")
    if not (base and model and key):
        return {"status": "unavailable", "reason": "未配置 ANOMALY_LLM_BASE_URL/ANOMALY_LLM_MODEL/ANOMALY_LLM_API_KEY", "text": ""}
    prompt = {
        "instruction": "只根据证据输出中文开发者诊断。不要虚构根因。对每个异常给出：现象、最可能的两个候选环节、需要补采的日志字段、建议下一步。明确哪些结论仍不能由黑盒证据证明。",
        "facts": facts,
        "anomalies": anomalies,
    }
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "temperature": 0, "max_tokens": 900, "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode())
        text = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return {"status": "completed", "model": model, "text": str(text)[:6000]}
    except Exception as exc:
        return {"status": "failed", "model": model, "reason": f"LLM 调用失败：{type(exc).__name__}", "text": ""}


def _inject_report(report: Path, dossier: dict[str, Any]) -> None:
    if not report.is_file():
        return
    raw = report.read_text(encoding="utf-8")
    cards = []
    for item in dossier["anomalies"]:
        ev = item.get("evidence") or {}
        cards.append("<article class='stress-anomaly-card'><div><b>" + html.escape(str(item.get("title"))) + "</b><span class='stress-severity " + html.escape(str(item.get("severity"))) + "'>" + html.escape(str(item.get("severity"))) + "</span></div><p>" + html.escape(str(item.get("next_step"))) + "</p><pre>" + html.escape(json.dumps(ev, ensure_ascii=False, indent=2)) + "</pre></article>")
    llm = dossier.get("llm") or {}
    llm_html = "<p class='stress-muted'>" + html.escape(str(llm.get("reason") or "模型分析已完成")) + "</p>"
    if llm.get("text"):
        llm_html = "<pre class='stress-llm'>" + html.escape(str(llm["text"])) + "</pre>"
    job_root = html.escape(os.getenv("STRESS_JOB_ID") or report.parent.name)
    section = "<section class='stress-dossier'><h2>异常诊断与开发者分析</h2><p>本节只展示由本次结果计算出的异常，不修改原始分母；HTTP 成功、非空召回和事实命中分别统计。</p><div class='stress-anomaly-grid'>" + "".join(cards or ["<p>本次没有检测到规则异常。</p>"]) + "</div><h3>大模型分析</h3>" + llm_html + f"<p><a href='/jobs/{job_root}/files/anomaly-dossier.json'>结构化诊断</a> · <a href='/jobs/{job_root}/files/developer-bundle.tar.gz'>开发者资料包</a></p></section>"
    css = "<style>.stress-dossier{border:2px solid #b84a3b!important;background:#fffaf8}.stress-anomaly-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.stress-anomaly-card{background:#fff;border:1px solid #ead5d0;border-radius:10px;padding:14px}.stress-severity{float:right;border-radius:99px;padding:2px 8px;font-size:12px;background:#f1e4df}.stress-severity.high{color:#a3281b;background:#f9d9d2}.stress-severity.medium{color:#8b6200;background:#fff0c2}.stress-dossier pre{max-height:260px;overflow:auto;white-space:pre-wrap}.stress-llm{background:#f4f7f8;border-left:3px solid #17746a}.stress-muted{color:#66757d}</style>"
    if "class='stress-dossier'" in raw:
        raw = re.sub(r"<section class='stress-dossier'>.*?</section>", section, raw, flags=re.S)
    else:
        raw = raw.replace("</main>", css + section + "</main>")
    report.write_text(raw, encoding="utf-8")


def _archive(root: Path, out: Path) -> None:
    def safe_bytes(path: Path) -> bytes:
        data = path.read_bytes()
        if path.suffix.lower() not in {".json", ".jsonl", ".log", ".csv", ".html"}:
            return data
        text = data.decode("utf-8", errors="replace")
        text = re.sub(r"(?i)(authorization|api[_-]?key|password|secret|token)(\s*[=:]\s*)[^,\\s}]+", r"\1\2[REDACTED]", text)
        return text.encode("utf-8")

    def add_safe(archive: tarfile.TarFile, path: Path, arcname: str) -> None:
        payload = safe_bytes(path)
        info = tarfile.TarInfo(arcname)
        info.size = len(payload)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(payload))

    with tarfile.open(out, "w:gz") as archive:
        for name in SAFE_FILES:
            path = root / name
            if path.is_file():
                add_safe(archive, path, name)
        for scenario in SCENARIOS:
            directory = root / scenario
            if not directory.is_dir():
                continue
            for path in directory.glob("*.csv"):
                add_safe(archive, path, f"{scenario}/{path.name}")


def build(root: Path) -> Path:
    facts = collect_facts(root)
    dossier = {"schema_version": 1, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "facts": facts, "anomalies": derive_anomalies(facts)}
    dossier["llm"] = _llm_analysis(facts, dossier["anomalies"])
    (root / "anomaly-dossier.json").write_text(json.dumps(dossier, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _inject_report(root / "report.html", dossier)
    _archive(root, root / "developer-bundle.tar.gz")
    return root / "anomaly-dossier.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
