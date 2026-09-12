"""Render persisted topology/boundary probe evidence without rerunning requests."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from performance.targets.echomem.orchestrator.report import write_objective_suite_html


def build(root: Path, boundary_root: Path | None = None) -> dict:
    profiles = []
    overview = []
    parameter_rows = []
    auto_commit_observed = False
    total_search = quality_ok = total_commit = commit_done = commit_failed = commit_pending = 0
    for kind, level in [("topology", n) for n in (16, 32, 64, 128)] + [("boundary", 32)]:
        folder = (boundary_root if kind == "boundary" and boundary_root else root) / f"{kind}-{level}"
        def load(name):
            path = folder / name
            return json.loads(path.read_text()) if path.exists() else {}
        manifest = load("manifest.json")
        filename = "concurrency-topology.json" if kind == "topology" else "payload-boundary.json"
        payload = load(filename)
        seeds = load("seed.json") or []
        resources = load("resources.json") or []
        parameters = load("deployment-parameters.json")
        events = load("server-event-counts.json")
        auto_commit_observed = auto_commit_observed or bool(events.get("events", {}).get("session_auto_commit_triggered"))
        cpu = max((float(s.get("CPUPerc", "0%").rstrip("%")) for s in resources), default=None)
        memory = max((float(s.get("MemPerc", "0%").rstrip("%")) for s in resources), default=None)
        if kind == "topology" and payload:
            detail = payload.get("checks", [{}])[-1].get("detail") or "{}"
            detail = json.loads(detail) if isinstance(detail, str) else detail
            matrix = detail.get("matrix", [])
            counts = {key: sum(row.get(key, 0) for row in matrix) for key in
                      ("search_offered", "search_quality_ok", "commit_offered", "commit_completed", "commit_failed", "commit_timed_out")}
            total_search += counts["search_offered"]
            quality_ok += counts["search_quality_ok"]
            total_commit += counts["commit_offered"]
            commit_done += counts["commit_completed"]
            commit_failed += counts["commit_failed"]
            commit_pending += counts["commit_timed_out"]
            overview.append([level, len(matrix), max((r.get("peak_active_operations", 0) for r in matrix), default=0),
                             f'{counts["search_quality_ok"]}/{counts["search_offered"]}',
                             f'{counts["commit_completed"]}/{counts["commit_failed"]}/{counts["commit_timed_out"]}',
                             cpu, memory])
        sched = parameters.get("scheduling", {})
        parameter_rows.append([f"{kind}-{level}", sched.get("http", {}).get("max_workers"),
            sched.get("retrieval", {}).get("admission_permits"), parameters.get("recall_max_inflight"),
            sched.get("fanout", {}).get("executor_workers"), sched.get("fanout", {}).get("engine_max_inflight"),
            parameters.get("auto_commit_threshold_config")])
        profile = {"name": f"{kind}-{level}", "objectives": [{
            "id": kind, "name": f"{level} 档", "status": payload.get("status", "NOT_RUN"),
            "reason": "缺少结果，未计为完成" if not payload else "已保留完整请求分母和最终状态；不以 HTTP 2xx 代表语义成功",
            "observed": {"deployment": manifest,
                "parameters": parameters, "server_event_counts": events.get("events", {}),
                "seed_completed": sum(s.get("terminal") == "completed" for s in seeds),
                "seed_strict_quality_ok": sum(bool(s.get("quality_ok")) for s in seeds),
                "seed_total": len(seeds), "resource_samples": len(resources),
                "cpu_peak_percent": max((float(s.get("CPUPerc", "0%").rstrip("%")) for s in resources), default=None),
                "memory_peak_percent": max((float(s.get("MemPerc", "0%").rstrip("%")) for s in resources), default=None)},
            "owner": "实测服务与测试平台证据", "evidence": str(folder / filename),
        }]}
        if payload:
            profile["concurrency_topology" if kind == "topology" else "payload_boundary"] = payload
        profiles.append(profile)
    return {"title": "EchoMem 并发拓扑与超长请求实测",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "16/32/64/128 参数档；四种用户/Session拓扑；1 MiB Commit/add_memory；0–1 MiB 文本/二进制边界。不包含 M1–M6 全套。",
        "summary": f"总体结论：已观测 Search {total_search} 次，命中且未降级 {quality_ok} 次；显式 Commit {total_commit} 次，完成 {commit_done}、失败 {commit_failed}、观察窗口内未完成 {commit_pending}。HTTP 成功不代表记忆能力正常，不能据此认定稳定容量或完成公平性。" +
            ("日志中发现自动 Commit 触发，环境阈值不等于实际生效值，相关数据属于自动/显式混合负载。" if auto_commit_observed else "未获得自动 Commit 触发的日志证据，不代表确定没有触发。"),
        "method": "每档独立实例与租户。先写入会议室 Cedar 的自然语言事实并 Commit，再问会议室名称；128 次操作/拓扑。异构场景混合 Search 和64 KiB Commit；轮询预算45秒，未完成不等于永久失败。边界独立实例测试0/1/1024/65536/262144/524288/1048576字节，另提交1 MiB内容并观察120秒；MCP add_memory单独调用。新旧实验按配置指纹及提交分开保留。",
        "overview": {"headers": ["参数档", "已测拓扑", "峰值在途操作", "Search严格成功/总数", "Commit完成/失败/未完成", "CPU峰值%（4核上限400%）", "内存峰值%（8GiB限额）"], "rows": overview},
        "parameter_table": {"headers": ["实验", "HTTP名额", "Retrieval名额", "Recall入口", "Fanout线程", "引擎并发", "JSON自动Commit阈值"], "rows": parameter_rows},
        "profiles": profiles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--boundary-root", type=Path, help="Separate corrected boundary rerun; source manifest remains distinct")
    parser.add_argument("--isolated-root", type=Path, help="Per-case clean-instance evidence under size/commit/mcp directories")
    args = parser.parse_args()
    result = build(args.root, args.boundary_root)
    if args.isolated_root:
        for case in ("262144", "524288", "1048576", "commit", "mcp"):
            folder = args.isolated_root / case
            if not folder.exists():
                continue
            profile = build(folder)["profiles"][-1]
            profile["name"] = f"独立补测 {case}"
            profile["objectives"][0]["name"] = f"独立补测 {case}"
            result["profiles"].append(profile)
        result["method"] += " 逐长度与超长操作补测分别重建独立容器，单项失败不污染下一项；原始连续场景仍保留，以展示后续请求受阻的事实。"
    boundary_cases = {}
    long_result = mcp_result = {}
    for profile in result["profiles"]:
        payload = profile.get("payload_boundary") or {}
        checks = payload.get("checks") or []
        detail = checks[-1].get("detail") if checks else None
        if not detail:
            continue
        detail = json.loads(detail) if isinstance(detail, str) else detail
        for row in detail.get("cases", []):
            boundary_cases[(row["content_bytes"], row["api"], row["encoding"])] = row
        if detail.get("long_commit", {}).get("status") != "NOT_SELECTED":
            long_result = detail.get("long_commit", {})
        if detail.get("mcp_add_memory", {}).get("status") != "NOT_SELECTED":
            mcp_result = detail.get("mcp_add_memory", {})
    result["boundary_overview"] = {"headers": ["内容字节", "计划用例", "实际发出", "HTTP响应", "超时/传输失败", "未发出"], "rows": []}
    for size in sorted({key[0] for key in boundary_cases}):
        rows = [row for key, row in boundary_cases.items() if key[0] == size]
        sent = sum(row.get("dispatched", True) for row in rows)
        responded = sum(row.get("http_status") is not None for row in rows)
        result["boundary_overview"]["rows"].append([size, len(rows), sent, responded, sent-responded, len(rows)-sent])
    if long_result or mcp_result:
        result["summary"] += (f" 独立超长补测：Commit 实收 {long_result.get('accepted_chars', 0)} 字符，终态 "
            f"{long_result.get('terminal', {}).get('state', '未知')}；MCP add_memory 状态 {mcp_result.get('status', '未知')}，"
            f"耗时 {mcp_result.get('elapsed_ms', '未知')} ms（仅工具接收，不证明抽取完成）。重复字符只测边界，不证明有效记忆质量。")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    write_objective_suite_html(result, args.out)


if __name__ == "__main__":
    main()
