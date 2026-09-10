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
    targeted = load("provider-targeted-diagnosis.json")
    direct_runs = [data for data in (load("provider-concurrency-diagnosis.json"),
                  load("provider-concurrency-max8192.json")) if data]
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
    raw_refs = {}
    for row in rows:
        if row.get("archive_id_ref"):
            raw_refs.setdefault(row["archive_id_ref"], set()).add(row.get("archive_ref"))
    log_by_id = {row["archive_id_ref"]: row.get("evidence", {})
                 for row in service.get("samples", [])
                 if row.get("event") == "commit_failed" and row.get("archive_id_ref")
                 and len(raw_refs.get(row["archive_id_ref"], set())) == 1}
    log_by_trace = {row["evidence"]["trace_ref"]: row["evidence"]
                    for row in service.get("samples", []) if row.get("event") == "commit_failed"
                    and row.get("evidence", {}).get("trace_ref")}
    for row in rows:
        trace = row.get("terminal_evidence", {}).get("trace_ref")
        row["service_failure_evidence"] = log_by_trace.get(trace, {}) or service_failed.get(row.get("archive_ref"), {}) or log_by_id.get(row.get("archive_id_ref"), {})
    matched = sum(bool(row["service_failure_evidence"]) for row in failed)
    sources = Counter((row.get("message_class", "") or row.get("engine_id", "") or "unclassified")
                      for row in service.get("samples", [])
                      if "PROVIDER_QUOTA" in row.get("evidence", {}).get("categories", []))
    business_status = "FAIL" if failed else "INCONCLUSIVE"
    summary = (f"模型预检 {'通过' if preflight.get('ok') else '未通过/未完成'}；小内容 Commit "
        f"{sum(r.get('terminal_state')=='completed' for r in diagnostic.get('smoke', []))}/{len(diagnostic.get('smoke', []))} 完成；"
        f"串行64 KiB Commit {sum(r.get('terminal_state')=='completed' for r in diagnostic.get('serial_large', []))}/{len(diagnostic.get('serial_large', []))} 完成。"
        f"16并发{'已启动' if diagnostic.get('burst_started') else '未启动'}，记录{len(burst)}次 Commit 调用，"
        f"完成观察{completed}次；失败观察{len(failed)}次，涉及{len(failed_refs)}个不同任务。"
        f" 服务日志Commit失败类型：{dict(errors)}；模型额度不足相关日志{quota_events}条（事件数，非任务数）。"
        "诊断程序PASS只表示采集完成，不代表业务通过。不能将全部抽取异常直接归因于额度不足。")
    if diagnostic.get("reason"):
        summary += " 停止原因：" + diagnostic["reason"]
    probes = targeted.get("probes", [])
    if probes:
        summary += (f" 后续定向模型复核{sum(p.get('http_status') == 200 for p in probes)}/{len(probes)}成功；"
                    "当前单请求成功不否定此前压测错误，也不证明并发窗口没有限额。不能据insufficient_quota断言账户欠费。")
    for direct in direct_runs:
        for level in direct.get("levels", []):
            summary += (f" 模型直压{level['concurrency']}并发、输出上限{direct['max_tokens']}："
                        f"HTTP200 {level['http_counts'].get('200', 0)}/{level['requests']}，"
                        f"非空正文{level['valid_responses']}/{level['requests']}，P95 {level['p95_s']}秒。")
    profile = {"name": "4U8G · 固定高限额 · 16并发诊断", "model_preflight": preflight,
        "objectives": [{"id": "Commit", "name": "失败归因复测", "status": business_status,
            "reason": summary, "owner": "按逐任务证据归因，不推断历史失败", "evidence": str(folder),
            "observed": {"manifest": load("manifest.json"), "container": load("container-evidence.json"),
                "parameters": parameters, "failure_categories": dict(categories), "provider_codes": dict(provider_codes),
                "targeted_model_recheck": targeted,
                "direct_model_concurrency_rechecks": direct_runs,
                "seed_degraded_reasons": dict(seed_reasons), "service_events": service.get("events", {}),
                "commit_log_error_types": dict(errors), "model_quota_log_events": quota_events,
                "failure_evidence_by_archive": service_failed,
                "matched_failure_observations": matched,
                "quota_log_sources": dict(sources),
                "findings": [
                    "已确认采集器历史缺陷：未展开commit_status返回的status对象，漏读error/error_type/trace_id；不是EchoMem接口没有返回详情。修复不能补回已丢弃的历史字段。",
                    f"失败调用观察{len(failed)}次，其中{matched}次匹配到服务端Commit失败日志；未匹配的不可强行归因。",
                    f"配额错误日志来源：{dict(sources)}。预检不能代替持续调用证据。",
                    f"Commit日志类型计数：{dict(errors)}；各错误不能仅凭时间接近认定同因。",
                    "引擎事件异常与Commit终态分别统计；EngineStateAdoptionRequiredError不等同于独立失败任务数。",
                    f"Search种子降级记录：{dict(seed_reasons)}。事实命中不等于健康Search基线。",
                    "控制器退出状态、业务终态和证据采集状态分别记录；不可互相替代。",
                    "后续应核查Provider额度、保留抽取异常底层cause及引擎标识，并排查引擎状态接管；不建议继续盲目放大并发。"]}}],
        "commit_diagnostic": {"status": diagnostic.get("status", "RUNNING"), "checks": [{"detail": {"rows": rows}}]}}
    if topology:
        profile["concurrency_topology"] = topology
    note = ("LLM、Embedding、意图和 Rerank 的独立真实请求预检已通过；业务错误、任务终态与脱敏日志另行保留，预检成功不代表负载下全部成功。"
            if preflight.get("ok") else "真实模型预检未通过或未完成，不应启动并发性能测量。")
    return {"title": "16并发 Commit 复测与失败原因", "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "固定 HTTP256 / Fanout256、单引擎128 / Recall128；LLM与Embedding总并发256；JSON自动Commit阈值4 MiB。仅诊断16并发，不扩展其他档。",
        "summary": summary, "model_evidence_note": note,
        "method": f"顺序执行真实模型预检、4租户小内容Commit、2个串行64 KiB Commit，再运行4用户各1 Session、每Session最多4并发的混合负载。2用户做Search，2用户共提交{len(burst)}次64 KiB Commit。Commit最多观察90秒。按脱敏archive标识区分调用次数与不同任务数；终态接口及日志只导出白名单错误分类，不含API Key或原始请求内容。所有降级保留，事实命中不等于健康Search基线。本轮结果不能倒推历史失败的具体原因。",
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
