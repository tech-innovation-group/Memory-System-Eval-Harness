from __future__ import annotations

import csv
import json
from pathlib import Path

from performance.targets.echomem.acceptance.capacity_load import arrival_plan
from performance.targets.echomem.acceptance.observation import (
    STATUSES,
    evaluate_observation,
    jain,
    summarize_m5,
)
from performance.targets.echomem.probes.tenant_observability import expected_lanes_from_config


def _run(tmp_path: Path, name: str, rows: list[dict]) -> dict:
    target = tmp_path / name
    target.mkdir()
    fields = sorted(set().union(*(row.keys() for row in rows)))
    with (target / "records.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {"scenario": name, "output_dir": str(target), "duration_s": 60}


def test_arrival_plan_has_four_real_load_shapes() -> None:
    operations = {}
    for mode in ("search", "commit", "mixed", "hotspot"):
        operations[mode] = {event[1] for event in arrival_plan(
            4, 60, 1, load_mode=mode, seed=7, commit_interval_s=10
        )}
    assert operations["search"] == {"read"}
    assert operations["commit"] == {"add", "commit_submit"}
    assert operations["mixed"] == {"read", "add", "commit_submit"}
    assert operations["hotspot"] == {"read", "add", "commit_submit"}
    normal_reads = sum(event[1] == "read" for event in arrival_plan(4, 60, 1, load_mode="mixed", seed=7))
    hot_reads = sum(event[1] == "read" for event in arrival_plan(4, 60, 1, load_mode="hotspot", seed=7))
    assert hot_reads > normal_reads


def test_jain_all_zero_is_undefined_and_zero_tenant_is_retained() -> None:
    assert jain([0, 0, 0, 0]) is None
    assert jain([8, 8, 8, 0]) == 0.75


def test_overlap_uses_search_start_not_interval_intersection(tmp_path: Path) -> None:
    baseline = [{"op": "read", "tenant_idx": 0, "status": "ok", "stage_ms": 10,
                 "ts_ms": 500, "quality_ok": True}]
    flood = [
        {"op": "commit_submit", "tenant_idx": 0, "status": "ok", "http_status": 202,
         "session_id": "s", "archive_id": "a", "accepted_at_ms": 1000, "ts_ms": 1000},
        {"op": "commit_done", "tenant_idx": 0, "status": "ok", "session_id": "s",
         "archive_id": "a", "completed_at_ms": 2000, "ts_ms": 2000},
        # Starts before acceptance and ends during the interval: excluded.
        {"op": "read", "tenant_idx": 0, "status": "ok", "stage_ms": 600,
         "ts_ms": 1200, "quality_ok": True},
        # Starts inside the accepted-to-terminal interval: included.
        {"op": "read", "tenant_idx": 0, "status": "error", "error_type": "timeout",
         "stage_ms": 200, "ts_ms": 1700, "quality_ok": False},
    ]
    suite = {"runs": [_run(tmp_path, "m4-baseline", baseline),
                       _run(tmp_path, "m4-flood-uniform", flood)]}
    result = evaluate_observation(suite, {}, quick=False)["metrics"]["M4"]
    assert result["windows"][0]["overlap"]["planned_or_recorded"] == 1
    assert result["windows"][0]["overlap"]["timeouts"] == 1


def test_m5_preserves_three_sample_denominators() -> None:
    required = ("commit-recovery", "pending-before-kill", "message-reconciliation",
                "cursor-reconciliation", "order-reconciliation", "idempotency-replay")
    samples = []
    for index in range(3):
        checks = [{"name": name, "status": "PASS", "detail": "{}"} for name in required]
        checks[0]["detail"] = json.dumps({"accepted_202": True, "pending_before_kill": True,
                                           "autonomous_recovery_observed": index != 2})
        checks[1]["detail"] = json.dumps({"accepted_202": True, "state": "pending"})
        samples.append({"sample_index": index + 1, "checks": checks})
    result = summarize_m5({"commit_recovery": {"samples": samples}}, quick=False)
    assert result["received_202"] == 3
    assert result["autonomous_completed"] == 2
    assert result["fully_reconciled"] == 3
    assert result["complete_samples"] == 2
    assert result["status"] == "PARTIAL"


def test_lanes_are_derived_from_effective_config(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({"scheduler": {"lanes": ["alpha", "beta"]}}))
    assert expected_lanes_from_config(explicit) == ["alpha", "beta"]
    inferred = tmp_path / "inferred.json"
    inferred.write_text(json.dumps({"recall": {"model": {
        "query_embedding": {"api_base": "https://e", "model": "embed"},
        "rerank": {"enabled": False, "api_base": "https://r", "model": "rank"},
    }}}))
    assert expected_lanes_from_config(inferred) == ["commit", "recall_query_embedding"]


def test_final_observation_uses_only_four_statuses() -> None:
    result = evaluate_observation({}, {}, quick=False)
    assert result["status"] in STATUSES
    assert {metric["status"] for metric in result["metrics"].values()} <= set(STATUSES)
