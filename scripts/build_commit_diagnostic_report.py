"""Render the bounded Commit rerun from persisted secret-free evidence."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from performance.targets.echomem.orchestrator.report import write_objective_suite_html


def build(root: Path):
    folder = root / "topology-16"
    def load(name):
        path = folder / name
        return json.loads(path.read_text()) if path.exists() else {}
    diagnostic, preflight = load("diagnostic.json"), load("model-preflight.json")
    topology, service = load("concurrency-topology.json"), load("service-diagnostics.json")
    parameters = load("deployment-parameters.json")
    rows = [{**row, "phase": phase} for phase, field in (("小内容串行", "smoke"), ("64 KiB串行", "serial_large"))
            for row in diagnostic.get(field, [])]
    matrix = []
    if topology:
        detail = topology.get("checks", [{}])[-1].get("detail") or "{}"
        matrix = (json.loads(detail) if isinstance(detail, str) else detail).get("matrix", [])
    for scene in matrix:
        rows.extend({**row, "phase": "16并发"} for row in scene.get("samples", []) if row.get("operation") == "commit")
    categories = Counter(code for row in rows for code in row.get("terminal_evidence", {}).get("categories", []))
    provider_codes = Counter(code for row in rows for code in row.get("terminal_evidence", {}).get("provider_codes", []))
    seed_reasons = Counter(reason for seed in diagnostic.get("seeds", []) for reason in seed.get("quality", {}).get("degraded_reasons", []))
    burst = [row for row in rows if row["phase"] == "16并发"]
    failed = [row for row in burst if row.get("terminal_state") in ("failed", "error")]
    failed_refs = {row.get("archive_ref") for row in failed if row.get("archive_ref")}
    errors = Counter(row.get("evidence", {}).get("error_type", "unknown")
                     for row in service.get("samples", []) if row.get("event") == "commit_failed")
    quota_events = sum("PROVIDER_QUOTA" in row.get("evidence", {}).get("categories", [])
                       for row in service.get("samples", []))
    completed = sum(row.get("terminal_state") == "completed" for row in burst)
    service_failed = {row.get("archive_ref"): row.get("evidence", {})
                      for row in service.get("samples", [])
                      if row.get("event") == "commit_failed" and row.get("archive_ref")}
    for row in rows:
        row["service_failure_evidence"] = service_failed.get(row.get("archive_ref"), {})
    business_status = "FAIL" if failed else "INCONCLUSIVE"
    summary = (f"模型预检 {'通过' if preflight.get('ok') else '未通过/未完成'}；小内容 Commit "
        f"{sum(r.get('terminal_state')=='completed' for r in diagnostic.get('smoke', []))}/{len(diagnostic.get('smoke', []))} 完成；"
        f"串行64 KiB Commit {sum(r.get('terminal_state')=='completed' for r in diagnostic.get('serial_large', []))}/{len(diagnostic.get('serial_large', []))} 完成。"
        f"16并发{'已启动' if diagnostic.get('burst_started') else '未启动'}，记录{len(burst)}次 Commit 调用，"
        f"完成观察{completed}次；失败观察{len(failed)}次，涉及{len(failed_refs)}个不同任务。"
        f" 服务日志Commit失败类型：{dict(errors)}；模型额度不足相关日志{quota_events}条（事件数，非任务数）。"
        "诊断程序PASS只表示采集完成，本轮业务未通过。终态接口未返回错误详情；不能将全部抽取异常直接归因于额度不足。")
    if diagnostic.get("reason"):
        summary += " 停止原因：" + diagnostic["reason"]
    profile = {"name": "4U8G · 固定高限额 · 16并发诊断", "model_preflight": preflight,
        "objectives": [{"id": "Commit", "name": "失败归因复测", "status": business_status,
            "reason": summary, "owner": "按逐任务证据归因，不推断历史失败", "evidence": str(folder),
            "observed": {"manifest": load("manifest.json"), "container": load("container-evidence.json"),
                "parameters": parameters, "failure_categories": dict(categories), "provider_codes": dict(provider_codes),
                "seed_degraded_reasons": dict(seed_reasons), "service_events": service.get("events", {}),
                "commit_log_error_types": dict(errors), "model_quota_log_events": quota_events,
                "failure_evidence_by_archive": service_failed,
                "matched_failure_observations": sum(bool(row["service_failure_evidence"]) for row in burst),
                "findings": [
                    "当前导出日志与客户端的脱敏任务标识未匹配，无法把47/1两类日志逐一归属到48个失败任务；两份计数分别呈现，不作强关联。",
                    "模型独立预检通过，但负载中出现insufficient_quota；预检不能代替持续调用证据。",
                    f"Commit日志类型计数：{dict(errors)}；各错误不能仅凭时间接近认定同因。",
                    "引擎事件异常与Commit终态分别统计；EngineStateAdoptionRequiredError不等同于独立失败任务数。",
                    "Search种子命中但存在engine_not_enabled:resource_engine降级，本轮不是健康Search性能基线。",
                    "manifest的BrokenPipeError属于控制器输出通道异常；负载已完成，诊断日志采集成功，不是Commit失败根因。",
                    "后续应核查Provider额度、保留抽取异常底层cause及引擎标识，并排查引擎状态接管；不建议继续盲目放大并发。"]}}],
        "commit_diagnostic": {"status": diagnostic.get("status", "RUNNING"), "checks": [{"detail": {"rows": rows}}]}}
    if topology:
        profile["concurrency_topology"] = topology
    note = ("LLM、Embedding、意图和 Rerank 的独立真实请求预检已通过；业务错误、任务终态与脱敏日志另行保留，预检成功不代表负载下全部成功。"
            if preflight.get("ok") else "真实模型预检未通过或未完成，不应启动并发性能测量。")
    return {"title": "16并发 Commit 复测与失败原因", "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "固定 HTTP256 / Fanout256、单引擎128 / Recall128；LLM与Embedding总并发256；JSON自动Commit阈值4 MiB。仅诊断16并发，不扩展其他档。",
        "summary": summary, "model_evidence_note": note,
        "method": "顺序执行真实模型预检、4租户小内容Commit、2个串行64 KiB Commit，再运行4用户各1 Session、每Session最多4并发的混合负载。2用户做Search，2用户各提交32次64 KiB Commit。Commit最多观察90秒。按脱敏archive标识区分调用次数与不同任务数；终态接口及日志只导出白名单错误分类，不含API Key或原始请求内容。所有降级保留，事实命中不等于健康Search基线。本轮结果不能倒推上一轮56次失败的具体原因。",
        "profiles": [profile]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = build(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    write_objective_suite_html(result, args.out)
