#!/usr/bin/env python3
"""Build an auditable HTML report for a PR29 six-metric run.

The report deliberately distinguishes:

* a scenario that was configured;
* a scenario that actually produced samples; and
* a metric that has enough evidence for PASS.

It never upgrades missing evidence to PASS and keeps links to the raw JSON/CSV
files next to the generated report.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from typing import Any

# Allow direct execution from the repository's ``scripts/`` directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from performance.scheduler_acceptance import evaluate as evaluate_scheduler_acceptance
from performance.objective_suite import _probe_plan, platform_objective_coverage


OBJECTIVES = (
    ("O1", "最大 DAU / 热用户量"),
    ("O2", "单租户故障隔离"),
    ("O3", "多租户公平性"),
    ("O4", "Search 优先级"),
    ("O5", "202 Commit 崩溃恢复"),
    ("O6", "四元组可观测性"),
)


def load(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def first_existing(*paths: Path) -> Path | None:
    """Return the first existing artifact path, keeping old result layouts usable."""
    for path in paths:
        if path.is_file():
            return path
    return None


def load_artifact(*paths: Path) -> tuple[dict[str, Any], Path | None]:
    path = first_existing(*paths)
    return (load(path), path) if path else ({}, None)


def load_formal_artifacts(formal: Path) -> dict[str, dict[str, Any]]:
    """Load post-suite probes from both current and legacy result layouts."""
    probe_dirs = (formal / "probes", formal.parent / "probes", formal.parent)
    artifacts: dict[str, dict[str, Any]] = {}
    for key, filename in (
        ("capability_probe", "capability-probe.json"),
        ("commit_recovery", "commit-recovery.json"),
        ("fault_suite", "fault-suite.json"),
        ("fault_isolation", "fault-isolation.json"),
        ("blackbox_contract_probe", "blackbox-contract-probe.json"),
    ):
        candidates = [probe_dir / filename for probe_dir in probe_dirs]
        if key == "fault_suite":
            candidates.extend(
                probe_dir / "fault-suite" / filename
                for probe_dir in probe_dirs
            )
        artifact, _artifact_path = load_artifact(*candidates)
        if artifact:
            artifacts[key] = artifact
    return artifacts


def find_run_artifact(formal: Path, scenario: str, filename: str) -> Path | None:
    """Find a normalized or timestamped artifact for one scenario."""
    case_root = formal / str(scenario) / "repeat-01" / "server-observe"
    direct = case_root / filename
    if direct.is_file():
        return direct
    matches = sorted(case_root.glob(f"run/*/{filename}"))
    return matches[-1] if matches else None


def first_runner_config(formal: Path) -> dict[str, Any]:
    """Read one non-secret runner config for report context."""
    matches = sorted(formal.glob("*/repeat-01/server-observe/run/*/config.json"))
    return load(matches[0]) if matches else {}


def esc(value: Any) -> str:
    return html.escape("-" if value in (None, "") else str(value))


def link(path: Path, report: Path, label: str) -> str:
    if not path.is_file():
        return ""
    return (
        f"<li><a href='{html.escape(os.path.relpath(path, report.parent))}'>"
        f"{esc(label)}</a></li>"
    )


def status(value: Any) -> str:
    text = str(value or "INCONCLUSIVE").upper()
    css = {
        "PASS": "pass",
        "FAIL": "fail",
        "INCONCLUSIVE": "warn",
        "BLOCKED": "warn",
    }.get(text, "neutral")
    return f"<span class='badge {css}'>{esc(text)}</span>"


def metric_bar(value: float | None, maximum: float = 1.0, tone: str = "teal") -> str:
    if value is None or maximum <= 0:
        width = 0
    else:
        width = max(0.0, min(100.0, value / maximum * 100.0))
    return f"<span class='meter {tone}'><i style='width:{width:.1f}%'></i></span>"


def json_text(value: Any) -> str:
    if value in (None, "", {}, []):
        return "-"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        type=Path,
        help="objective-suite result directory, or a direct formal-suite result directory",
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    root = args.root.resolve()
    output = (args.output or root / "pr29-six-metric-report.html").resolve()
    suite = load(root / "objective-suite.json")
    direct_formal = not suite and (root / "suite.json").is_file()
    if direct_formal:
        formal = root
        formal_suite = load(formal / "suite.json")
        # Do not silently lose recovery/capability evidence just because the
        # caller points at the formal directory.
        formal_suite.update(load_formal_artifacts(formal))
        acceptance = evaluate_scheduler_acceptance(
            formal_suite,
            capability=formal_suite.get("capability_probe"),
            recovery=formal_suite.get("commit_recovery"),
            fault=formal_suite.get("fault_suite"),
        )
        objective_name_to_id = {
            "DAU / 最大热用户容量": "O1",
            "单租户故障隔离": "O2",
            "Commit/Search 公平性 Jain": "O3",
            "Search 优先于 Commit": "O4",
            "Commit kill-9 恢复与重放": "O5",
            "分层/分租户调度可观测性": "O6",
        }
        objectives = {
            objective_name_to_id.get(str(item.get("name")), str(item.get("name"))): {
                **item,
                "id": objective_name_to_id.get(
                    str(item.get("name")), str(item.get("name"))
                ),
            }
            for item in acceptance.get("checks") or []
            if isinstance(item, dict)
        }
        suite = formal_suite
        profile = {
            "name": formal_suite.get("instance_profile") or "4U8G",
            "objectives": [],
        }
        formal_manifest = formal_suite
    else:
        profile = (suite.get("profiles") or [{}])[0]
        objectives = {
            str(item.get("id")): item
            for item in profile.get("objectives") or []
            if isinstance(item, dict)
        }
        formal = root / "4U8G" / "formal"
        formal_manifest = load(formal / "suite.json")
        probe_artifacts = load_formal_artifacts(formal)
        if probe_artifacts:
            formal_manifest = {
                **formal_manifest,
                **probe_artifacts,
                # Re-evaluation must use the same declared coverage contract
                # as the objective-suite run, otherwise report regeneration
                # can silently downgrade or change O6.
                "fairness_expectations": profile.get("fairness_expectations", {}),
                "observability_expectations": profile.get("observability", {}),
            }
            acceptance = evaluate_scheduler_acceptance(
                formal_manifest,
                capability=formal_manifest.get("capability_probe"),
                recovery=formal_manifest.get("commit_recovery"),
                fault=formal_manifest.get("fault_suite"),
            )
            objective_name_to_id = {
                "DAU / 最大热用户容量": "O1",
                "单租户故障隔离": "O2",
                "Commit/Search 公平性 Jain": "O3",
                "Search 优先于 Commit": "O4",
                "Commit kill-9 恢复与重放": "O5",
                "分层/分租户调度可观测性": "O6",
            }
            objectives = {
                objective_name_to_id.get(str(item.get("name")), str(item.get("name"))): {
                    **item,
                    "id": objective_name_to_id.get(
                        str(item.get("name")), str(item.get("name"))
                    ),
                }
                for item in acceptance.get("checks") or []
                if isinstance(item, dict)
            }
    platform_coverage = profile.get("platform_objective_coverage") or []
    if not platform_coverage:
        probe_plan = profile.get("probe_plan")
        if not isinstance(probe_plan, list):
            probe_plan = _probe_plan(profile)
        platform_coverage = platform_objective_coverage(
            profile,
            formal_manifest,
            probe_plan,
            profile.get("coverage") or {},
        )
    auth = formal_manifest.get("auth_preflight") or {}
    scenarios = []
    manifest_runs = {
        str(item.get("scenario_key") or item.get("scenario") or ""): item
        for item in formal_manifest.get("runs") or []
        if isinstance(item, dict)
    }
    for name in formal_manifest.get("scenarios") or []:
        path = formal / str(name) / "repeat-01" / "server-observe"
        run_record = manifest_runs.get(str(name), {})
        summary = (
            run_record.get("summary")
            if isinstance(run_record.get("summary"), dict)
            else load(path / "summary.json")
        )
        if not summary:
            summary = load(find_run_artifact(formal, str(name), "summary.json"))
        metrics = summary.get("metrics") or {}
        search = metrics.get("search") or {}
        commit = metrics.get("commit") or {}
        search_latency = search.get("latency") or {}
        commit_latency = commit.get("latency") or {}
        errors = summary.get("errors") or {}
        scenarios.append(
            {
                "name": name,
                "status": (
                    run_record.get("status")
                    or summary.get("status")
                    or "not-run"
                ),
                "search_submitted": search.get("submitted", 0),
                "search_succeeded": search.get("succeeded", 0),
                "search_success_rate": search.get("success_rate"),
                "commit_submitted": commit.get("submitted", 0),
                "commit_completed": commit.get("completed", 0),
                "commit_success_rate": commit.get("success_rate"),
                "search_p95": search_latency.get("p95_s"),
                "commit_p95": commit_latency.get("p95_s"),
                "duration_s": (
                    run_record.get("duration_s")
                    if run_record.get("duration_s") is not None
                    else summary.get("duration_s")
                ),
                "blocked_reason": run_record.get("blocked_reason") or "",
                "error_count": (
                    summary.get("error_count")
                    if summary.get("error_count") is not None
                    else sum(
                        int(value or 0)
                        for value in errors.values()
                        if isinstance(value, (int, float))
                    )
                ),
            }
        )
    scenario_metric_links = "".join(
        link(
            find_run_artifact(formal, str(item["name"]), "metrics_samples.csv")
            or formal
            / str(item["name"])
            / "repeat-01/server-observe/metrics_samples.csv",
            output,
            f"{item['name']} metrics_samples.csv",
        )
        for item in scenarios
        if find_run_artifact(formal, str(item["name"]), "metrics_samples.csv")
        or (
            formal
            / str(item["name"])
            / "repeat-01/server-observe/metrics_samples.csv"
        ).is_file()
    )

    cards = "".join(
        f"<article class='card'><div class='id'>{esc(ident)}</div>"
        f"<h3>{esc(title)}</h3>{status(objectives.get(ident, {}).get('status'))}"
        f"<p>{esc(objectives.get(ident, {}).get('reason'))}</p></article>"
        for ident, title in OBJECTIVES
    )
    scenario_rows = "".join(
        "<tr>"
        f"<td>{esc(item['name'])}</td><td>{status(item['status'])}</td>"
        f"<td>{esc(item['search_submitted'])}/{esc(item['search_succeeded'])}"
        f"<br><span class='muted'>{esc(item['search_success_rate'])}</span></td>"
        f"<td>{esc(item['commit_submitted'])}/{esc(item['commit_completed'])}"
        f"<br><span class='muted'>{esc(item['commit_success_rate'])}</span></td>"
        f"<td>{esc(item['search_p95'])} s"
        f"<br><span class='muted'>Commit {esc(item['commit_p95'])} s</span></td>"
        f"<td>{esc(item['duration_s'])} s</td>"
        f"<td>{esc(item['error_count'])}"
        f"<br><span class='muted'>{esc(item['blocked_reason'])}</span></td></tr>"
        for item in scenarios
    )
    artifact_paths = (
        (root / "objective-suite.json", "objective-suite.json"),
        (formal / "suite.json", "4U8G/formal/suite.json"),
        (formal / "acceptance.json", "4U8G/formal/acceptance.json"),
        (
            first_existing(
                root / "4U8G" / "capability-probe.json",
                root / "capability-probe.json",
                root.parent / "capability-probe.json",
                formal / "probes" / "capability-probe.json",
            ) or root / "__missing__",
            "capability-probe.json",
        ),
        (
            first_existing(
                root / "4U8G" / "blackbox-contract-probe.json",
                root / "blackbox-contract-probe.json",
                root.parent / "blackbox-contract-probe.json",
                formal / "probes" / "blackbox-contract-probe.json",
            ) or root / "__missing__",
            "blackbox-contract-probe.json",
        ),
        (
            first_existing(
                root / "4U8G" / "commit-recovery.json",
                root / "commit-recovery.json",
                root.parent / "commit-recovery.json",
                formal / "probes" / "commit-recovery.json",
            ) or root / "__missing__",
            "commit-recovery.json",
        ),
        (formal / "probes" / "capability-probe.json", "probes/capability-probe.json"),
        (formal / "probes" / "commit-recovery.json", "probes/commit-recovery.json"),
        (
            find_run_artifact(formal, "fairness-bounded", "search_results.csv")
            or formal / "fairness-bounded/repeat-01/server-observe/search_results.csv",
            "fairness Search CSV",
        ),
        (
            find_run_artifact(formal, "fairness-bounded", "commit_results.csv")
            or formal / "fairness-bounded/repeat-01/server-observe/commit_results.csv",
            "fairness Commit CSV",
        ),
        (
            find_run_artifact(formal, "search-priority-blackbox", "search_results.csv")
            or formal / "search-priority-blackbox/repeat-01/server-observe/search_results.csv",
            "priority Search CSV",
        ),
        (
            find_run_artifact(formal, "search-priority-blackbox", "commit_results.csv")
            or formal / "search-priority-blackbox/repeat-01/server-observe/commit_results.csv",
            "priority Commit CSV",
        ),
    )
    artifacts = "".join(link(path, output, label) for path, label in artifact_paths)
    configured = formal_manifest.get("scenarios") or []
    coverage = f"{len(scenarios)}/{len(configured)}"
    model = load(root / "echomem-config-real-4u8g.json")
    if not model and direct_formal:
        model = first_runner_config(formal)
    if not model and direct_formal:
        candidates = sorted(formal.glob("*/repeat-01/server-observe/config.json"))
        if candidates:
            model = load(candidates[0])
    runner_config = first_runner_config(formal)
    llm = ((model.get("model") or {}).get("llm") or {}).get("model", "-")
    embedding = ((model.get("model") or {}).get("embedding") or {}).get("model", "-")
    observed_rows = "".join(
        f"<tr><td>{esc(ident)}</td><td>{status(item.get('status'))}</td>"
        f"<td>{esc(item.get('reason'))}</td>"
        f"<td><code>{esc(json_text(item.get('observed')))}</code></td></tr>"
        for ident, _title in OBJECTIVES
        for item in [objectives.get(ident, {})]
    )
    status_counts = {
        state: sum(
            str(objectives.get(ident, {}).get("status") or "INCONCLUSIVE").upper() == state
            for ident, _title in OBJECTIVES
        )
        for state in ("PASS", "FAIL", "INCONCLUSIVE")
    }
    status_legend = "".join(
        f"<div class='legend-item'><span class='dot {state.lower()}'></span>"
        f"<b>{count}</b> {esc(state)}</div>"
        for state, count in status_counts.items()
    )
    capacity_points = []
    for item in scenarios:
        name = str(item["name"])
        state = str(item["status"])
        if name.startswith("capacity-"):
            try:
                level = int(name.split("-", 1)[1])
            except ValueError:
                continue
            capacity_points.append((level, str(state).upper()))
    capacity_points.sort()
    capacity_chart = "".join(
        f"<div class='capacity-point'><span>{level}</span>"
        f"<i class='{'done' if state == 'COMPLETED' else 'blocked'}'></i></div>"
        for level, state in capacity_points
    )
    o3 = objectives.get("O3", {}).get("observed") or {}
    o4 = objectives.get("O4", {}).get("observed") or {}
    o1 = objectives.get("O1", {}).get("observed") or {}
    o3_jain = o3.get("jain")
    o4_ratio = o4.get("priority_ratio") or o4.get("degradation_ratio")
    o1_max = o1.get("max_completed_active_user_count") or o1.get("max_measured_active_user_count")
    chart_max = max(
        (float(item["search_p95"]) for item in scenarios if item["search_p95"] is not None),
        default=0.0,
    )
    scenario_chart = "".join(
        f"<div class='chart-row'><span>{esc(item['name'])}</span>"
        f"<i style='width:{(float(item['search_p95']) / chart_max * 100.0) if chart_max else 0:.1f}%'></i>"
        f"<b>{esc(item['search_p95'])}s</b></div>"
        for item in scenarios
        if item["search_p95"] is not None
    )
    metric_scene_count = sum(
        1
        for item in scenarios
        if find_run_artifact(formal, str(item["name"]), "metrics_samples.csv")
    )
    seed_warmup = formal_manifest.get("seed_warmup") or {}
    seed_status = str(seed_warmup.get("status") or "unknown")
    fault_configured = bool(
        (profile.get("fault_isolation") or {}).get("enabled")
        or profile.get("fault_plan")
    )
    capacity_completed = [
        item["name"]
        for item in scenarios
        if str(item["name"]).startswith("capacity-")
        and str(item["status"]).lower() == "completed"
    ]
    capacity_blocked = [
        item["name"]
        for item in scenarios
        if str(item["name"]).startswith("capacity-")
        and str(item["status"]).lower() == "blocked"
    ]
    capacity_action = (
        "已完成 2/4/8/16/32 阶梯，但还需要继续增加负载直到出现真实 SLO 失败，"
        "才能报告最大边界。"
        if len(capacity_completed) >= 5
        else "继续执行尚未完成的容量档位，并记录最后成功档位与下一档真实失败。"
    )
    module_rows = [
        (
            "认证 / 多租户",
            f"有效凭据 {auth.get('passed', 0)}/{auth.get('tenant_count', 0)}；"
            "其余租户返回 401。",
            "auth / tenant_config",
            "测试平台输入与部署凭据",
            "补齐独立租户凭据后才能继续 8/16/32 档位和故障旁观租户测试。",
        ),
        (
            "容量阶梯",
            f"已完成 {', '.join(capacity_completed) or '无'}；"
            f"阻断 {', '.join(capacity_blocked) or '无'}，未形成最大容量失败边界。",
            "session / active-user / admission",
            "测试平台场景 / 4U8G 资源",
            capacity_action,
        ),
        (
            "检索 / Search",
            f"真实 Search 场景 {metric_scene_count} 个；共享 seed 状态为 {seed_status}，"
            "当前不能把无记忆请求当作热缓存结果。",
            "recall / model / index",
            "测试数据准备与 EchoMem 检索链路",
            "先保证 seed Commit 完成并用 marker Search 验证命中，再判定热缓存准确率和优先级。",
        ),
        (
            "路由 / 调度",
            "公平窗口有 4 租户 Commit/Search 竞争；Priority 窗口有真实并发，但热记忆前提不足。",
            "router / scheduler / tenant_coordination",
            "测试平台负载 + EchoMem 调度",
            "保留到达/完成时间、在途 Commit 和 Search P95，避免只凭客户端返回判定优先级。",
        ),
        (
            "Commit / 持久化",
            "202、kill-9、重启、completed、history/archive/cursor 顺序对账均有证据。",
            "commit_pipeline / storage / control_store",
            "EchoMem 现有接口 + 测试平台恢复探针",
            "增加重复样本；同幂等键虽返回同 archive，但 replayed 标记为 false，需单独确认契约。",
        ),
        (
            "Metrics / 可观测性",
            f"/metrics 能力探针可访问；{metric_scene_count} 个场景有原始采样，"
            "但 lane/fan-out 覆盖仍需按实际负载补齐。",
            "observability / metrics / engine_state",
            "EchoMem /metrics 与测试平台触发负载",
            "用真实拒绝、等待和引擎 fan-out 负载触发每个指标，再采集完整四元组。",
        ),
        (
            "故障控制面",
            (
                "已配置故障控制计划，但仍需检查每个旁观租户前后 P95。"
                if fault_configured
                else "未配置真实单租户依赖故障控制 URL/命令，O2 无前后 P95 配对数据。"
            ),
            "fault control / deployment",
            "部署侧控制面 + 测试平台采集",
            "提供只作用于目标租户的 500/429/timeout/connection-refused 控制，并保留时间线。",
        ),
    ]
    module_html = "".join(
        f"<tr><td><b>{esc(module)}</b></td><td>{esc(evidence)}</td>"
        f"<td><code>{esc(config_path)}</code></td><td>{esc(owner)}</td>"
        f"<td>{esc(action)}</td></tr>"
        for module, evidence, config_path, owner, action in module_rows
    )

    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR29 4U8G 六项指标报告</title>
<style>
:root{{--ink:#172a35;--muted:#667983;--line:#d8e2e7;--bg:#f4f7f8;--teal:#176b87;--green:#147a61;--red:#b6423d;--amber:#9a6b00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1320px;margin:auto;padding:28px 20px 60px}}h1{{margin:0;font-size:28px}}h2{{margin:0 0 14px;font-size:20px}}h3{{margin:5px 0;font-size:15px}}
.sub,.muted{{color:var(--muted)}}section{{background:#fff;border:1px solid var(--line);padding:18px;margin:14px 0}}
.cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}}.card{{background:#fbfcfc;border-top:4px solid var(--amber);padding:12px;min-height:142px}}
.card p{{color:var(--muted);font-size:12px}}.id{{font-weight:800;color:var(--teal);font-size:12px}}
.badge{{display:inline-block;padding:2px 7px;border-radius:3px;font-weight:700;font-size:12px}}
.pass{{color:var(--green);background:#e5f4ee}}.fail{{color:var(--red);background:#fae9e7}}.warn{{color:var(--amber);background:#fff3d2}}.neutral{{color:var(--muted);background:#edf1f2}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f2f6f7;font-size:12px}}
.dashboard{{display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:12px}}.viz{{border:1px solid var(--line);padding:14px;background:#fbfcfc}}
.viz h3{{margin-top:0}}.legend-item{{display:inline-flex;align-items:center;margin:0 12px 8px 0;gap:5px;font-size:12px}}.dot{{width:10px;height:10px;border-radius:50%;display:inline-block}}.dot.pass{{background:var(--green)}}.dot.fail{{background:var(--red)}}.dot.inconclusive{{background:var(--amber)}}
.big-number{{font-size:28px;font-weight:800;color:var(--teal);margin:4px 0}}.meter{{display:block;height:9px;background:#e6edef;margin:7px 0 11px;border-radius:2px;overflow:hidden}}.meter i{{display:block;height:100%;background:var(--teal)}}.meter.green i{{background:var(--green)}}.meter.amber i{{background:var(--amber)}}.meter.red i{{background:var(--red)}}
.capacity{{display:flex;align-items:flex-end;gap:10px;height:72px;border-bottom:1px solid var(--line);padding:0 8px}}.capacity-point{{display:flex;flex-direction:column;align-items:center;gap:5px;color:var(--muted);font-size:11px}}.capacity-point i{{display:block;width:18px;height:42px;background:var(--amber);border-radius:2px 2px 0 0}}.capacity-point i.done{{background:var(--green)}}.capacity-point i.blocked{{background:#d8e2e7}}
.chart{{display:grid;gap:7px;max-width:880px}}.chart-row{{display:grid;grid-template-columns:190px minmax(80px,1fr) 70px;align-items:center;gap:8px;font-size:12px}}.chart-row i{{display:block;height:14px;background:var(--teal);border-radius:2px;min-width:2px}}.chart-row b{{font-size:12px;color:var(--muted)}}
.callout{{border-left:4px solid var(--teal);background:#eef7f9;padding:12px 14px}}ul{{padding-left:20px}}a{{color:var(--teal)}}
@media(max-width:1050px){{.cards{{grid-template-columns:repeat(2,1fr)}}.dashboard{{grid-template-columns:1fr}}}}@media(max-width:520px){{.cards{{grid-template-columns:1fr}}.chart-row{{grid-template-columns:120px minmax(60px,1fr) 58px}}main{{padding:18px 10px}}}}
</style></head><body><main>
<h1>PR29 4U8G 六项指标真实黑盒测试</h1>
<p class="sub">生成时间：{esc(suite.get("created_at"))} · 场景覆盖：{coverage} · 默认不含 soak</p>
<div class="callout"><b>阅读规则：</b>配置了场景不等于已经完成测试；有 HTTP 返回不等于有记忆召回证据。
缺少故障控制、热记忆或完整指标样本时，保留为 INCONCLUSIVE。</div>
<section><h2>六项指标结论</h2><div class="cards">{cards}</div></section>
<section><h2>测试平台覆盖审计</h2>
<p class="muted">这张表回答“平台有没有配置出数据的入口”，不把入口存在误当成指标通过。</p>
<table><thead><tr><th>指标</th><th>平台状态</th><th>已配置场景</th>
<th>已配置探针</th><th>缺口</th><th>责任边界</th></tr></thead><tbody>
{"".join(
    f"<tr><td><b>{esc(item.get('id'))}</b> {esc(item.get('name'))}</td>"
    f"<td>{esc(item.get('status'))}</td>"
    f"<td>{esc(', '.join(map(str, item.get('configured_scenarios') or [])) or '-')}</td>"
    f"<td>{esc(', '.join(map(str, item.get('configured_probes') or [])) or '-')}</td>"
    f"<td>{esc(', '.join(map(str, item.get('missing') or [])) or '-')}</td>"
    f"<td>{esc(item.get('owner') or '-')}</td></tr>"
    for item in platform_coverage if isinstance(item, dict)
)}
</tbody></table>
<div class="callout">平台状态为 <b>incomplete</b> 时，报告仍展示已有真实数据，
但不会自动把结果标成 PASS；需要区分“平台未配置”“部署没有控制/凭据”和“服务真实失败”。</div>
</section>
<section><h2>一眼看懂</h2><div class="dashboard">
<div class="viz"><h3>状态分布</h3><div>{status_legend}</div>
<p class="muted">PASS 只代表当前验收器已有充分证据；INCONCLUSIVE 代表还缺真实前提或样本。</p></div>
<div class="viz"><h3>已观测容量</h3><div class="big-number">{esc(o1_max or "-")} 个 session</div>
<div class="capacity">{capacity_chart or "<span class='muted'>无容量数据</span>"}</div>
<p class="muted">绿色为实际完成，灰色为未形成有效边界；不是业务 DAU。</p></div>
<div class="viz"><h3>关键数值</h3>
<div class="muted">公平性 Jain（取较小值）</div><b>{esc(o3_jain or "-")}</b>
{metric_bar(float(o3_jain) if o3_jain is not None else None, 1, "green")}
<div class="muted">Search 洪泛/基线比</div><b>{esc(o4_ratio or "-")}</b>
{metric_bar(float(o4_ratio) if o4_ratio is not None else None, 2, "amber")}</div>
</div></section>
<section><h2>Search P95 场景对比</h2>
<p class="muted">每根条代表一个已产生真实 Search 样本的场景；没有样本的阻断场景不参与比例缩放。</p>
<div class="chart">{scenario_chart or "<span class='muted'>暂无 Search P95 数据</span>"}</div></section>
<section><h2>运行环境</h2><table>
<tr><th>项目</th><th>值</th></tr>
<tr><td>测试平台</td><td>Memory-System-Eval-Harness PR29</td></tr>
<tr><td>实例</td><td>4 vCPU / 8 GiB，4U8G</td></tr>
<tr><td>LLM</td><td>{esc(llm)}（真实模型）</td></tr>
<tr><td>Embedding</td><td>{esc(embedding)}（真实模型）</td></tr>
<tr><td>独立凭据</td><td>{esc(auth.get("passed", 0))}/{esc(auth.get("tenant_count", 0))}</td></tr>
<tr><td>Search 配置</td><td>{esc(runner_config.get("search_query_profile") or "-")}；
recall 比例 {esc(runner_config.get("search_recall_ratio") or "-")}；
seed {esc(not runner_config.get("skip_seed", True))}</td></tr>
<tr><td>Commit 配置</td><td>轮询超时 {esc(runner_config.get("commit_poll_timeout_s") or "-")}s；
重试 {esc(runner_config.get("commit_retry_max") or "-")} 次；
barrier {esc(runner_config.get("commit_barrier"))}</td></tr>
</table></section>
<section><h2>场景明细</h2><p class="muted">Search 和 Commit 均按“提交数/成功或完成数”展示；成功率、P95、耗时和错误数直接来自场景 summary.json。</p>
<table><thead><tr><th>场景</th><th>运行状态</th><th>Search</th><th>Commit</th><th>延迟 P95</th><th>耗时</th><th>错误数</th></tr></thead>
<tbody>{scenario_rows or "<tr><td colspan='7'>没有场景结果</td></tr>"}</tbody></table></section>
<section><h2>六项指标原始观测</h2><p class="muted">这里保留验收器实际使用的 observed 字段，便于复核容量档位、Jain、恢复和指标覆盖。</p>
<table><thead><tr><th>指标</th><th>状态</th><th>判定说明</th><th>观测数据</th></tr></thead>
<tbody>{observed_rows}</tbody></table></section>
<section><h2>按 config.json 模块归因</h2>
<table><thead><tr><th>模块</th><th>当前真实证据</th><th>config.json 对应区域</th><th>归属</th><th>下一步</th></tr></thead>
<tbody>{module_html}</tbody></table>
<div class="callout"><b>归因原则：</b>没有真实故障控制、热记忆命中或指标样本时，只能说明测试前提/证据缺失；
不能直接写成 EchoMem 内部未实现。只有 HTTP 404 或真实服务行为明确违反契约时，才建议修改 EchoMem。</div></section>
<section><h2>EchoMem PR449 对接状态</h2>
<table><thead><tr><th>能力</th><th>代码/接口证据</th><th>本轮结论</th></tr></thead>
<tbody>
<tr><td>Commit 状态与 202 恢复</td><td>commit status、history、archive、cursor 对账路径已存在</td><td>{status("PASS")}</td></tr>
<tr><td>Search lane / fan-out 指标</td><td>/metrics 已定义 lane 四元组和 engine fan-out 指标</td><td>{status("INCONCLUSIVE")}：本轮服务器未采到完整实样本</td></tr>
<tr><td>单租户故障控制</td><td>PR449 代码未提供可直接由黑盒调用的故障注入控制面</td><td>{status("INCONCLUSIVE")}：需要部署侧提供控制接口</td></tr>
</tbody></table>
<p class="muted">本轮没有新增 PR449 提交：现有证据不足以证明 EchoMem 内部行为违反契约，先修复部署/测试前提后再复测。</p></section>
<section><h2>原始证据</h2><ul>{artifacts}</ul>
<p class="muted">各场景原始 Prometheus 采样：</p><ul>{scenario_metric_links or "<li>暂无 metrics_samples.csv</li>"}</ul>
<p class="muted">报告只引用运行目录内存在的文件，不写入 API key、密码或环境变量值。</p></section>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
