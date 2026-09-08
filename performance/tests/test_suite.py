"""通用套件层（suite.py）单元测试：records 汇总与单 case 执行。"""

from __future__ import annotations

import json
import threading
import time

import pytest

from performance.profile import LoadSpec, Profile, TargetSpec
from performance.records import RequestRecord
from performance.suite import (
    _fs_safe_label,
    run_case,
    run_suite,
    summarize_case_records,
)


def _record(**overrides):
    fields = {
        "scene": "scene_generic", "worker_id": 0, "tenant_idx": 0, "op": "read",
        "stage_ms": 0.0, "status": "ok", "error_type": "", "ts_ms": 0.0,
    }
    fields.update(overrides)
    return RequestRecord(**fields)


def _write_scene(tmp_path):
    """一个纯记录型通用场景：不依赖任何 target 协议。"""
    scene = tmp_path / "scene_generic.py"
    scene.write_text(
        "def read(ctx):\n"
        "    ctx.record(op='read', stage_ms=10.0, status='ok', query='q')\n"
        "\n"
        "def write(ctx):\n"
        "    ctx.record(op='commit_submit', stage_ms=5.0, status='ok', session_id='s')\n"
        "    ctx.record(op='commit_done', stage_ms=20.0, status='ok', session_id='s')\n"
        "\n"
        "tasks = {'read': read, 'write': write}\n",
        encoding="utf-8",
    )
    return scene


def _profile():
    return Profile(
        name="generic",
        target=TargetSpec(base_url="http://127.0.0.1:8010"),
        load=LoadSpec(workers=2, duration_s=0.5, mix={"read": 1, "write": 1}),
    )


# -- summarize_case_records ----------------------------------------------


def test_summarize_case_records_metrics_only():
    records = [
        _record(tenant_idx=0, op="read", stage_ms=100.0, query="PERFANCHOR-0-0-0"),
        _record(
            tenant_idx=1, op="read", stage_ms=200.0, status="error",
            error_type="http_4xx", retry_after_s=1.0,
        ),
        _record(tenant_idx=0, op="commit_submit", stage_ms=50.0, session_id="s1"),
        _record(tenant_idx=0, op="commit_done", stage_ms=1000.0, session_id="s1"),
    ]
    summary = summarize_case_records(records)
    # 通用层只产 metrics；details/parameters 由 target 侧扩展。
    assert set(summary) == {"metrics"}
    search = summary["metrics"]["search"]
    assert search["submitted"] == 2
    assert search["succeeded"] == 1
    assert search["errors"] == 1
    assert search["success_rate"] == 0.5
    assert search["rate_limited_count"] == 1
    # is_anchor=None → 无法判定锚点，quality_asserted 记 0。
    assert search["quality_asserted"] == 0
    assert search["quality_failures"] == 0
    assert search["latency"]["mean_s"] == 0.1
    assert search["latency"]["p50_s"] == 0.1
    commit = summary["metrics"]["commit"]
    assert commit["submitted"] == 1
    assert commit["completed"] == 1
    assert commit["failed"] == 0
    assert commit["success_rate"] == 1.0
    assert summary["metrics"]["fairness"]["commit_completed_per_tenant"] == {"0": 1}
    assert summary["metrics"]["per_tenant"]["0"]["commit"]["completed"] == 1


def test_summarize_case_records_custom_ops_and_anchor():
    records = [
        _record(op="query", stage_ms=100.0, query="ANCHOR-0"),
        _record(op="query", stage_ms=200.0, query="普通", quality_ok=False),
        _record(
            op="send", stage_ms=50.0, status="error", error_type="http_4xx",
            reason_code="rate_limit", session_id="s1",
        ),
        _record(op="finish", stage_ms=1000.0, session_id="s1"),
    ]
    summary = summarize_case_records(
        records,
        search_op="query",
        commit_submit_op="send",
        commit_done_op="finish",
        is_anchor=lambda q: q.startswith("ANCHOR"),
    )
    search = summary["metrics"]["search"]
    assert search["submitted"] == 2
    assert search["succeeded"] == 2
    assert search["quality_asserted"] == 1
    assert search["quality_failures"] == 1
    commit = summary["metrics"]["commit"]
    assert commit["submitted"] == 1
    assert commit["completed"] == 1
    assert commit["rate_limited_count"] == 1
    assert summary["metrics"]["per_tenant"]["0"]["commit"]["completed"] == 1


def test_summarize_empty_records():
    summary = summarize_case_records([])
    assert summary["metrics"]["search"]["submitted"] == 0
    assert summary["metrics"]["search"]["success_rate"] is None
    assert summary["metrics"]["search"]["latency"]["mean_s"] is None
    assert summary["metrics"]["commit"]["success_rate"] is None
    assert summary["metrics"]["per_tenant"] == {}


# -- fs-safe label --------------------------------------------------------


def test_fs_safe_label_replaces_windows_illegal_chars():
    assert _fs_safe_label("C8:1@1") == "C8_1@1"
    assert _fs_safe_label("a/b?c*d") == "a_b_c_d"
    assert _fs_safe_label("plain@1") == "plain@1"


# -- run_case ------------------------------------------------------------


def test_run_case_writes_outputs_and_hooks(tmp_path):
    scene = _write_scene(tmp_path)
    case = {"label": "generic-case", "scene": "scene_generic"}
    case_dir = tmp_path / "out"
    evidence_counts: list[int] = []

    def _extended(records):
        summary = summarize_case_records(records)
        summary["details"] = {"custom": True}
        return summary

    run = run_case(
        case,
        _profile(),
        scene_path=scene,
        case_dir=case_dir,
        timeout_s=30.0,
        summarize=_extended,
        write_evidence=lambda out, records: evidence_counts.append(len(records)),
    )
    assert run["status"] == "completed"
    assert run["scenario"] == "generic-case"
    assert run["scene"] == "scene_generic"
    assert run["repetition"] == 1
    assert run["policy"] == "server-observe"
    assert run["runner_timeout"] is False
    assert run["output_dir"] == str(case_dir.resolve())
    for name in ("summary.json", "records.csv"):
        assert (case_dir / name).is_file(), name
    assert evidence_counts, "write_evidence should be invoked"
    summary = json.loads((case_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["details"] == {"custom": True}
    assert summary["metrics"]["search"]["submitted"] > 0
    assert summary["metrics"]["commit"]["completed"] > 0


# -- run_suite resume ----------------------------------------------------


def _suite_cases():
    return [
        {"label": "c1", "scene": "scene_generic", "tenants": 1, "duration_s": 1.0},
        {"label": "c2", "scene": "scene_generic", "tenants": 1, "duration_s": 1.0},
    ]


def _select_cases(profile_name, scenarios):
    cases = _suite_cases()
    if scenarios is None:
        return cases
    return [case for case in cases if case["label"] in scenarios]


def _build_profile(case, base_url, tenant_count, quick):
    return _profile()


def _make_stub_run_case(calls: list[str]):
    """把每次执行记进 calls，并按 run_case 语义写 summary.json。"""

    def _run_case(case, profile, *, case_dir, timeout_s):
        calls.append(case["label"])
        case_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "metrics": {"search": {"submitted": 1}, "commit": {"submitted": 0}},
            "marker": f'{case["label"]}-{len(calls)}',
            "status": "completed",
            "runner_timeout": False,
        }
        (case_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False), encoding="utf-8"
        )
        return {
            "scenario": case["label"],
            "scenario_label": case["label"],
            "scene": case["scene"],
            "repetition": 1,
            "policy": "server-observe",
            "status": "completed",
            "duration_s": 0.0,
            "case_timeout_s": 0.0,
            "runner_timeout": False,
            "output_dir": str(case_dir.resolve()),
            "summary": summary,
        }

    return _run_case


def test_run_suite_resume_skips_completed_cases(tmp_path):
    calls: list[str] = []
    suite_dir = tmp_path / "suite"
    kwargs = {
        "profile": {"name": "test"},
        "suite_dir": suite_dir,
        "profile_name": "test",
        "base_url": "http://127.0.0.1:8010",
        "timeout_s": 30.0,
        "select_cases": _select_cases,
        "build_profile": _build_profile,
        "run_case": _make_stub_run_case(calls),
    }
    # 第一轮只跑完 c1（模拟中断：c2 及之后未执行）。
    manifest = run_suite(**kwargs, scenarios=["c1"])
    assert calls == ["c1"]
    assert [run["scenario"] for run in manifest["runs"]] == ["c1"]

    # 第二轮 resume 跑完整场景列表：c1 跳过（已有 summary.json），c2 执行。
    calls.clear()
    manifest = run_suite(**kwargs, scenarios=["c1", "c2"], resume=True)
    assert calls == ["c2"]
    assert [run["scenario"] for run in manifest["runs"]] == ["c1", "c2"]
    assert all(run["status"] == "completed" for run in manifest["runs"])
    # c1 的历史 run 从盘上重建，保留首次执行的 marker。
    assert manifest["runs"][0]["summary"]["marker"] == "c1-1"
    # suite.json 写盘包含合并后的两条。
    written = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
    assert [run["scenario"] for run in written["runs"]] == ["c1", "c2"]


def test_run_suite_without_resume_reruns_all(tmp_path):
    calls: list[str] = []
    kwargs = {
        "profile": {"name": "test"},
        "suite_dir": tmp_path / "suite",
        "profile_name": "test",
        "base_url": "http://127.0.0.1:8010",
        "timeout_s": 30.0,
        "scenarios": ["c1", "c2"],
        "select_cases": _select_cases,
        "build_profile": _build_profile,
        "run_case": _make_stub_run_case(calls),
    }
    run_suite(**kwargs)
    assert calls == ["c1", "c2"]
    calls.clear()
    run_suite(**kwargs)  # resume 缺省 False → 全部重跑
    assert calls == ["c1", "c2"]


def test_resume_skips_only_parseable_summary(tmp_path):
    calls: list[str] = []
    suite_dir = tmp_path / "suite"
    (suite_dir / "c1").mkdir(parents=True)
    (suite_dir / "c1" / "summary.json").write_text("{not json", encoding="utf-8")
    manifest = run_suite(
        profile={"name": "test"},
        suite_dir=suite_dir,
        profile_name="test",
        base_url="http://127.0.0.1:8010",
        timeout_s=30.0,
        scenarios=["c1", "c2"],
        select_cases=_select_cases,
        build_profile=_build_profile,
        run_case=_make_stub_run_case(calls),
        resume=True,
    )
    # summary.json 损坏的 c1 视为未完成，重新执行。
    assert calls == ["c1", "c2"]
    assert [run["scenario"] for run in manifest["runs"]] == ["c1", "c2"]


# -- case timeout: stop engine + real completion persistence -------------


class _StubbornEngine:
    """run() 阻塞且 stop() 无法中断（模拟不可回收的顽固 worker）。"""

    def __init__(self, profile, scene):
        pass

    def run(self):
        time.sleep(60)

    def stop(self):
        pass


class _BlockingEngine:
    """run() 阻塞直到 stop() 被调用；记录 stop 调用（验证超时回收）。"""

    def __init__(self, profile, scene):
        self._stop_requested = threading.Event()

    def run(self):
        self._stop_requested.wait(10.0)
        from types import SimpleNamespace

        return SimpleNamespace(records=[])

    def stop(self):
        self._stop_requested.set()


def test_run_case_timeout_stops_engine_and_persists_timeout(tmp_path, monkeypatch):
    import performance.suite as suite_mod

    scene = _write_scene(tmp_path)
    case = {"label": "generic-case", "scene": "scene_generic"}
    case_dir = tmp_path / "out"
    monkeypatch.setattr(suite_mod, "Engine", _BlockingEngine)

    run = run_case(
        case,
        _profile(),
        scene_path=scene,
        case_dir=case_dir,
        timeout_s=0.1,
    )

    assert run["status"] == "TIMEOUT"
    assert run["runner_timeout"] is True
    summary = json.loads((case_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "TIMEOUT"
    assert summary["runner_timeout"] is True
    # 超时产物不视为已完成的 run，resume 不得跳过。
    assert suite_mod._load_completed_run(case, case_dir, timeout_s=0.1) is None


def test_run_case_stubborn_worker_aborts_suite(tmp_path, monkeypatch):
    """确认窗口到期 worker 仍存活：先持久化 TIMEOUT 产物，再中止套件。"""
    import performance.suite as suite_mod

    scene = _write_scene(tmp_path)
    case = {"label": "generic-case", "scene": "scene_generic"}
    case_dir = tmp_path / "out"
    monkeypatch.setattr(suite_mod, "Engine", _StubbornEngine)
    monkeypatch.setattr(suite_mod, "_STOP_CONFIRM_S", 0.2)

    with pytest.raises(RuntimeError, match="拒绝推进"):
        run_case(
            case,
            _profile(),
            scene_path=scene,
            case_dir=case_dir,
            timeout_s=0.05,
        )

    # 中止前产物已持久化：resume 将该 case 视为未完成并重跑。
    summary = json.loads((case_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "TIMEOUT"
    assert summary["runner_timeout"] is True
    assert suite_mod._load_completed_run(case, case_dir, timeout_s=0.05) is None


def test_resume_reruns_timed_out_case(tmp_path):
    calls: list[str] = []
    suite_dir = tmp_path / "suite"
    (suite_dir / "c1").mkdir(parents=True)
    (suite_dir / "c1" / "summary.json").write_text(
        json.dumps({
            "metrics": {"search": {"submitted": 0}},
            "status": "TIMEOUT",
            "runner_timeout": True,
        }),
        encoding="utf-8",
    )
    manifest = run_suite(
        profile={"name": "test"},
        suite_dir=suite_dir,
        profile_name="test",
        base_url="http://127.0.0.1:8010",
        timeout_s=30.0,
        scenarios=["c1", "c2"],
        select_cases=_select_cases,
        build_profile=_build_profile,
        run_case=_make_stub_run_case(calls),
        resume=True,
    )
    # 标记 TIMEOUT 的 c1 视为未完成，resume 重新执行。
    assert calls == ["c1", "c2"]
    assert [run["scenario"] for run in manifest["runs"]] == ["c1", "c2"]
