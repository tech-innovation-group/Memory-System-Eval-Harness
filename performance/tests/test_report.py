"""Report aggregation: per-tenant grouping and the scene ``report`` hook.

``summarize`` groups by tenant (fairness comparison) regardless of the
scene; a scene may additionally export ``report(result, profile)`` whose
return value lands in the summary's ``custom`` section.
"""

from __future__ import annotations

from pathlib import Path

from performance.engine import Engine, load_scene
from performance.profile import Profile, load_profile
from performance.report import summarize
from performance.targets.general.main import _build_summary

SCENES_DIR = Path(__file__).resolve().parent.parent / "targets" / "echomem" / "scenes"


def _profile(base_url: str, *, workers: int, duration_s: float,
             tenants: list[dict] | None = None, **params) -> Profile:
    return load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": workers, "duration_s": duration_s},
            "tenants": tenants or [],
            "params": {"queries": ["q1", "q2"], **params},
        }
    )


# -- per-tenant grouping -------------------------------------------------


def test_summarize_groups_by_tenant(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    profile = _profile(base_url, workers=2, duration_s=2.0,
                       tenants=[{"name": "t1"}, {"name": "t2"}])
    result = Engine(profile, scene).run()
    summary = summarize(result, profile)
    tenants = summary["tenants"]
    assert [t["tenant_idx"] for t in tenants] == [0, 1]
    assert [t["name"] for t in tenants] == ["t1", "t2"]
    for tenant in tenants:
        assert tenant["count"] > 0
        assert tenant["ok"] == tenant["count"], (
            f"tenant={tenant['tenant_idx']} ok={tenant['ok']} count={tenant['count']}"
        )
        assert tenant["latency_ms"]["p50"] is not None


def test_summarize_default_single_tenant(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    profile = _profile(base_url, workers=1, duration_s=1.5)
    result = Engine(profile, scene).run()
    summary = summarize(result, profile)
    assert [t["tenant_idx"] for t in summary["tenants"]] == [0]
    assert summary["tenants"][0]["count"] > 0


# -- scene report hook ---------------------------------------------------


def test_load_scene_extracts_report_hook():
    with_hook = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    without_hook = load_scene(SCENES_DIR / "scene_c_mixed.py")
    assert with_hook.report is not None
    assert without_hook.report is None


def test_scene_report_returns_quality_metrics(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    profile = _profile(base_url, workers=2, duration_s=2.0)
    result = Engine(profile, scene).run()
    custom = scene.report(result, profile)
    assert custom["reads"] > 0
    assert 0 <= custom["quality_ok_ratio"] <= 1
    assert custom["avg_hit_count"] is not None


def test_build_summary_merges_custom(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_a_pure_read.py")
    profile = _profile(base_url, workers=1, duration_s=1.5)
    result = Engine(profile, scene).run()
    summary = _build_summary(scene, result, profile)
    assert isinstance(summary["custom"], dict)
    assert "quality_ok_ratio" in summary["custom"]
    assert "tenants" in summary


def test_build_summary_no_hook_no_custom(server):
    _, _, base_url = server
    scene = load_scene(SCENES_DIR / "scene_c_mixed.py")
    profile = _profile(base_url, workers=1, duration_s=1.5)
    result = Engine(profile, scene).run()
    summary = _build_summary(scene, result, profile)
    assert "custom" not in summary


# -- orchestrator objective-suite 渲染（report.py） -----------------------


def _objective_suite_result() -> dict:
    objectives = [
        {
            "id": f"O{index}",
            "name": f"目标 {index}",
            "status": "INCONCLUSIVE" if index < 7 else "PASS",
            "reason": "证据不足",
            "owner": "测试平台",
            "evidence": "scheduler_acceptance",
        }
        for index in range(1, 8)
    ]
    return {
        "created_at": "2026-01-01T00:00:00+00:00",
        "profiles": [
            {
                "name": "4U8G",
                "objectives": objectives,
                "capability_probe": {
                    "status": "INCONCLUSIVE",
                    "path": "out/4U8G/capability-probe.json",
                    "checks": [
                        {
                            "name": "/health",
                            "status": "PASS",
                            "elapsed_s": 0.01,
                            "reason": "",
                        }
                    ],
                },
            },
            {
                "name": "8U16G",
                "objectives": objectives,
                "commit_recovery": {
                    "status": "NOT_IMPLEMENTED",
                    "path": "out/8U16G/commit-recovery.json",
                    "cases": [{"kind": "kill-9-recovery", "status": "NOT_IMPLEMENTED"}],
                },
            },
        ],
        "objectives": [],
        "instance_profiles": [],
        "multi_spec_completed_count": 0,
    }


def test_render_objective_suite_html():
    from performance.targets.echomem.orchestrator.report import (
        render_objective_suite_html,
    )

    html = render_objective_suite_html(_objective_suite_result())
    assert "EchoMem 七项目标自动化验收" in html
    assert "4U8G" in html
    assert "8U16G" in html
    for objective_id in ("O1", "O2", "O3", "O4", "O5", "O6", "O7"):
        assert objective_id in html
    assert "capability-probe.json" in html
    assert "commit-recovery.json" in html
    assert "能力探针" in html
    assert "Commit 崩溃恢复探针" in html
    assert "未证明真实模型可用或被调用" in html
    assert "mock 模型：否" not in html


def test_objective_report_lists_verified_model_preflight() -> None:
    from performance.targets.echomem.orchestrator.report import render_objective_suite_html

    result = _objective_suite_result()
    result["profiles"][0]["model_preflight"] = {
        "ok": True, "digest": "safe-digest", "probe_attempts": 1,
        "engines": [
            {"kind": "llm", "id": "atomic", "model": "real-llm",
             "api_base": "https://llm.example/v1", "status": "ok",
             "model_supported": True, "code": 200},
            {"kind": "embedding", "id": "embedding", "model": "real-embedding",
             "api_base": "https://embedding.example/v1", "status": "ok",
             "model_supported": True, "code": 200},
        ],
    }
    result["profiles"][1]["model_preflight"] = result["profiles"][0]["model_preflight"]
    rendered = render_objective_suite_html(result)
    assert "真实模型可用性预检已通过" in rendered
    assert "不能据此宣称负载使用了模型" in rendered
    assert "real-llm" in rendered
    assert "real-embedding" in rendered
    assert "safe-digest" in rendered


def test_objective_report_exposes_failed_model_preflight() -> None:
    from performance.targets.echomem.orchestrator.report import render_objective_suite_html

    result = _objective_suite_result()
    result["profiles"][0]["model_preflight"] = {
        "ok": False, "error": "LLM and embedding credentials are missing",
        "engines_checked": 0, "probe_attempts": 0, "engines": [],
    }
    rendered = render_objective_suite_html(result)
    assert "未证明真实模型可用或被调用" in rendered
    assert "LLM and embedding credentials are missing" in rendered
    assert "没有真实模型调用明细" in rendered


def test_write_objective_suite_html(tmp_path):
    from performance.targets.echomem.orchestrator.report import (
        render_objective_suite_html,
        write_objective_suite_html,
    )

    path = tmp_path / "objective-suite.html"
    write_objective_suite_html(_objective_suite_result(), path)
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert content == render_objective_suite_html(_objective_suite_result())
