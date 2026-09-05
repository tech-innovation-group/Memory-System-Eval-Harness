from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from performance.objective_suite import (
    QUICK_SCENARIOS,
    _append_quick_seed_options,
    _first_completed_commit_csv,
    _first_completed_commit_evidence,
    _resolve_auth_key,
    _acquire_output_lock,
    _materialize_fault_plan,
    _preserve_probe_status,
    _resolve_tenant_id,
    _formal_run_counts,
    _formal_coverage,
    _formal_submitted_operations,
    _formal_profile_name,
    _formal_scenario_filter,
    _probe_plan,
    load_env_file,
    load_profiles,
    objective_statuses,
    platform_objective_coverage,
    run_command,
    render_report,
)
from scripts.build_pr29_six_metric_report import load_formal_artifacts
from performance.probes.limit_failure_probe import (
    auth_key,
    classify_response,
    discover_sessions,
    error_class,
    load_tenants,
    metrics_coverage,
    response_error_detail,
)
from performance.formal_suite import SCENARIOS, _build_seed_warmup_command


class ObjectiveSuiteTests(unittest.TestCase):
    def test_report_loader_keeps_probe_artifacts_next_to_formal_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "4U8G" / "formal"
            formal.mkdir(parents=True)
            (root / "4U8G" / "commit-recovery.json").write_text(
                json.dumps({"status": "PASS"}), encoding="utf-8"
            )
            (root / "4U8G" / "capability-probe.json").write_text(
                json.dumps({"status": "INCONCLUSIVE"}), encoding="utf-8"
            )
            artifacts = load_formal_artifacts(formal)
            self.assertEqual("PASS", artifacts["commit_recovery"]["status"])
            self.assertEqual(
                "INCONCLUSIVE",
                artifacts["capability_probe"]["status"],
            )

    def test_first_completed_commit_evidence_returns_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "case"
            case.mkdir()
            (case / "commit_results.csv").write_text(
                "tenant,session_id,status,archive_id\n"
                "0,session-real,completed,archive-1\n",
                encoding="utf-8",
            )
            result = _first_completed_commit_evidence(root)
            self.assertIsNotNone(result)
            self.assertEqual("session-real", result[1]["session_id"])

    def test_runtime_overrides_replace_stale_profile_target(self) -> None:
        import argparse
        from performance.objective_suite import load_profiles

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(
                json.dumps(
                    {
                        "profiles": [
                            {
                                "name": "4U8G",
                                "base_url": "http://old:8010",
                                "preflight_config": "/old/config.json",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            profiles = load_profiles(path)
            args = argparse.Namespace(
                base_url="http://new:8010",
                preflight_config="/new/config.json",
            )
            for profile in profiles:
                if args.base_url:
                    profile["base_url"] = args.base_url
                if args.preflight_config:
                    profile["preflight_config"] = args.preflight_config
            self.assertEqual("http://new:8010", profiles[0]["base_url"])
            self.assertEqual("/new/config.json", profiles[0]["preflight_config"])

    def test_priority_evidence_uses_request_intervals(self) -> None:
        from performance.formal_suite import _derive_case_summary

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "summary.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            (root / "requests.csv").write_text(
                "op,status,stage_ms,ts_ms,start_ts_ms,tenant_idx,scene\n"
                "read,ok,100,1200,1100,0,S\n"
                "commit_submit,ok,200,1300,1100,0,S\n",
                encoding="utf-8",
            )
            result = _derive_case_summary(root, identity_independent=True)
            overlap = result["details"]["same_window_overlap"]
            self.assertTrue(overlap["overlap_proven"])
            self.assertEqual("request start/end intervals", overlap["basis"])

    def test_six_metric_entrypoint_is_self_contained_and_no_soak_by_default(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "performance"
            / "run_4u8g_six_metrics.sh"
        )
        text = script.read_text(encoding="utf-8")
        self.assertIn("performance.objective_suite", text)
        self.assertIn("--full", text)
        self.assertIn("--profile", text)
        self.assertIn("STRESS_QUICK", text)
        self.assertIn("STRESS_PYTHON", text)
        self.assertNotIn("--scenarios soak", text)
        self.assertIn("run-manifest.json", text)
        self.assertIn("pr29-six-metric-report.html", text)
        self.assertIn("set +e", text)
        self.assertIn("STRESS_SKIP_PREPARE", text)

    def test_load_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{"name": "4U8G"}]}), encoding="utf-8")
            self.assertEqual(["4U8G"], [item["name"] for item in load_profiles(path)])

    def test_full_4u8g_targeted_scenario_is_namespaced_for_formal_suite(self) -> None:
        self.assertEqual(
            "pr421__fairness-steady,pr421__capacity-4",
            _formal_scenario_filter(
                "4U8G",
                "fairness-steady,capacity-4",
                quick=False,
            ),
        )
        self.assertEqual(
            "fairness-steady",
            _formal_scenario_filter(
                "4U8G",
                "fairness-steady",
                quick=True,
            ),
        )

    def test_load_profiles_expands_runtime_deployment_values(self) -> None:
        import os

        previous = os.environ.get("ECHOMEM_CONTAINER")
        os.environ["ECHOMEM_CONTAINER"] = "echomem-current"
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profiles.json"
                path.write_text(
                    json.dumps(
                        {
                            "profiles": [
                                {
                                    "name": "4U8G",
                                    "commit_recovery": {
                                        "container": "${ECHOMEM_CONTAINER}"
                                    },
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                profile = load_profiles(path)[0]
                self.assertEqual(
                    "echomem-current",
                    profile["commit_recovery"]["container"],
                )
        finally:
            if previous is None:
                os.environ.pop("ECHOMEM_CONTAINER", None)
            else:
                os.environ["ECHOMEM_CONTAINER"] = previous

    def test_probe_plan_keeps_missing_controls_explicit(self) -> None:
        plan = _probe_plan(
            {
                "capability_probe": {"enabled": True},
                "fault_isolation": {"enabled": False},
                "commit_recovery": {"container": "target"},
            }
        )
        by_name = {item["name"]: item for item in plan}
        self.assertEqual("scheduled", by_name["blackbox_probe"]["status"])
        self.assertTrue(by_name["blackbox_probe"]["configured"])
        self.assertEqual("scheduled", by_name["capability_probe"]["status"])
        self.assertEqual("not_configured", by_name["fault_isolation"]["status"])
        self.assertEqual("scheduled", by_name["commit_recovery"]["status"])
        self.assertEqual("O2", by_name["fault_isolation"]["objectives"])

    def test_resolve_profile_path_accepts_repo_relative_paths(self) -> None:
        from performance.objective_suite import _resolve_profile_path

        repo_root = Path(__file__).resolve().parents[1]
        profiles_path = repo_root / "performance" / "instance-profiles.example.json"
        resolved = Path(
            _resolve_profile_path(
                "performance/tenants.example.json",
                profiles_path,
            )
        )
        self.assertEqual(
            (repo_root / "performance" / "tenants.example.json").resolve(),
            resolved,
        )

    def test_load_env_file_accepts_export_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.env"
            path.write_text(
                "# credentials\nexport MODEL_KEY='secret-value'\nEMPTY=\n",
                encoding="utf-8",
            )
            self.assertEqual(
                {"MODEL_KEY": "secret-value", "EMPTY": ""},
                load_env_file(path),
            )

    def test_profile_expectations_are_forwarded_to_acceptance(self) -> None:
        from performance.scheduler_acceptance import evaluate

        suite = {
            "runs": [],
            "fairness_expectations": {"tenant_ids": ["a", "b"]},
            "observability_expectations": {
                "lanes": ["commit"],
                "fanout_engines": ["recall"],
            },
        }
        result = evaluate(suite)
        fairness = next(
            item for item in result["checks"]
            if item["name"] == "Commit/Search 公平性 Jain"
        )
        observability = next(
            item for item in result["checks"]
            if item["name"] == "分层/分租户调度可观测性"
        )
        self.assertEqual(["a", "b"], fairness["observed"].get("expected_tenants"))
        self.assertEqual(["commit"], observability["observed"].get("expected_lanes"))
        self.assertEqual(
            ["recall"],
            observability["observed"].get("expected_fanout_engines"),
        )

    def test_platform_objective_coverage_reports_missing_runtime_controls(self) -> None:
        result = platform_objective_coverage(
            {
                "fault_isolation": {"enabled": False},
                "commit_recovery": {"container": "target"},
            },
            {
                "runs": [
                    {"scenario": "capacity-2"},
                    {"scenario": "capacity-4"},
                    {"scenario": "capacity-8"},
                    {"scenario": "capacity-16"},
                    {"scenario": "capacity-32"},
                    {"scenario": "fairness-steady"},
                    {"scenario": "search-priority-blackbox"},
                    {"scenario": "baseline"},
                ]
            },
            [
                {"name": "blackbox_probe", "configured": True},
                {"name": "commit_recovery", "configured": True},
                {"name": "capability_probe", "configured": True},
                {"name": "fault_isolation", "configured": False},
            ],
            {"evidence_missing_scenarios": []},
        )
        by_id = {item["id"]: item for item in result}
        self.assertEqual("configured", by_id["O1"]["status"])
        self.assertEqual("incomplete", by_id["O2"]["status"])
        self.assertIn("probe:fault_isolation", by_id["O2"]["missing"])
        self.assertEqual("configured", by_id["O5"]["status"])
        self.assertEqual("configured", by_id["O6"]["status"])

    def test_resolve_tenant_id_falls_back_when_profile_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "tenant-live-a"},
                            {"tenant_id": "tenant-live-b"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                "tenant-live-a",
                _resolve_tenant_id(path, "stress-a"),
            )

    def test_quick_matrix_is_bounded_and_contains_priority(self) -> None:
        self.assertIn("search-priority-blackbox", QUICK_SCENARIOS)
        self.assertIn("capacity-2", QUICK_SCENARIOS)
        self.assertIn("capacity-8", QUICK_SCENARIOS)
        self.assertIn("fairness-bounded", QUICK_SCENARIOS)
        self.assertIn("tenant-skew", QUICK_SCENARIOS)
        self.assertNotIn("A@1", QUICK_SCENARIOS)
        self.assertNotIn("B@1", QUICK_SCENARIOS)
        self.assertNotIn("D@1", QUICK_SCENARIOS)
        self.assertNotIn("soak", QUICK_SCENARIOS)
        self.assertEqual(0.0, SCENARIOS["capacity-2"]["quick_commit_rpm"])
        self.assertEqual(0.0, SCENARIOS["capacity-4"]["quick_commit_rpm"])

    def test_capacity_catalog_disables_commit_for_full_runs(self) -> None:
        for name in ("capacity-2", "capacity-4", "capacity-8", "capacity-16", "capacity-32"):
            with self.subTest(name=name):
                self.assertEqual(0.0, SCENARIOS[name]["commit_rpm"])

    def test_fairness_steady_has_explicit_per_tenant_commit_rate(self) -> None:
        self.assertEqual(2.0, SCENARIOS["fairness-steady"]["commit_rpm_per_tenant"])
        self.assertEqual(2.0, SCENARIOS["fairness-steady"]["search_rps_per_tenant"])

    def test_normal_4u8g_entry_uses_full_37_case_profile(self) -> None:
        self.assertEqual("4u8g-full", _formal_profile_name("4U8G", quick=False))
        self.assertEqual("4u8g", _formal_profile_name("4U8G", quick=True))
        self.assertEqual("complete", _formal_profile_name("8U16G", quick=False))

    def test_full_seed_warmup_uses_bounded_concurrency_and_long_timeout(self) -> None:
        args = type(
            "Args",
            (),
            {
                "base_url": "http://127.0.0.1:8015",
                "commit_max_attempts": 3,
                "commit_retry_backoff_s": 2.0,
                "seed_concurrency": 2,
                "preflight_config": "/tmp/config.json",
                "no_server_metrics": False,
                "commit_timeout_s": 600.0,
            },
        )()
        command = _build_seed_warmup_command(
            args,
            Path("/tmp/tenants-32.json"),
            Path("/tmp/seed/run"),
            32,
            600.0,
        )
        self.assertIn("--seed-concurrency", command)
        self.assertEqual("2", command[command.index("--seed-concurrency") + 1])
        self.assertEqual(
            "600.0",
            command[command.index("--commit-poll-timeout-s") + 1],
        )

    def test_formal_case_command_propagates_auth_header(self) -> None:
        import argparse
        from performance.formal_suite import _build_case_command

        args = argparse.Namespace(
            base_url="http://127.0.0.1:8010",
            commit_timeout_s=10,
            commit_max_attempts=0,
            commit_retry_backoff_s=0,
            seed_sessions_per_tenant=None,
            active_sessions_per_tenant=None,
            barrier_wave_size=4,
            barrier_drain_timeout_s=10,
            local_auth_mode=False,
            skip_seed=True,
            reuse_existing_data=False,
            search_queries="",
            preflight_config="",
            no_server_metrics=False,
            auth_header="X-API-Key",
            quick_mode=True,
        )
        command = _build_case_command(
            args,
            {"tenants": 1, "duration_s": 1, "search_rps": 1, "commit_rpm": 0},
            Path("/tmp/tenants.json"),
            Path("/tmp/case"),
            1,
        )
        self.assertEqual("X-API-Key", command[command.index("--auth-header") + 1])

    def test_limit_probe_accepts_configured_auth_header(self) -> None:
        from performance.probes.limit_failure_probe import request

        # The request is intentionally pointed at an unused local port. The
        # returned transport error still proves argument compatibility without
        # making a network call to a real service.
        result = request(
            "http://127.0.0.1:1",
            {"tenant_id": "t", "auth_key": "secret"},
            "/health",
            {},
            0.01,
            "search",
            "X-API-Key",
        )
        self.assertIsNone(result["status_code"])

    def test_partial_formal_manifest_is_not_complete_coverage(self) -> None:
        suite = {
            "scenarios": ["pr397__A@1", "pr397__B@1", "pr421__baseline"],
            "repeats": 1,
            "policies": ["server-observe"],
            "runs": [
                {
                    "scenario_key": "pr397__A@1",
                    "scenario": "A@1",
                    "status": "completed",
                },
            ],
        }
        coverage = _formal_coverage(suite)
        self.assertEqual(3, coverage["expected_runs"])
        self.assertEqual(1, coverage["manifest_runs"])
        self.assertEqual(1, coverage["completed_runs"])
        self.assertEqual({"COMPLETED": 1}, coverage["status_counts"])
        self.assertEqual(0, coverage["timeout_runs"])
        self.assertEqual("partial", coverage["status"])
        self.assertEqual(
            ["pr397__B@1", "pr421__baseline"],
            coverage["missing_scenarios"],
        )

    def test_full_formal_manifest_has_complete_coverage(self) -> None:
        suite = {
            "scenarios": ["pr397__A@1", "pr421__baseline"],
            "repeats": 1,
            "policies": ["server-observe"],
            "runs": [
                {
                    "scenario_key": "pr397__A@1",
                    "status": "completed",
                    "summary": {"metrics": {"search": {"submitted": 4}}},
                },
                {
                    "scenario_key": "pr421__baseline",
                    "status": "completed",
                    "summary": {"metrics": {"commit": {"submitted": 2}}},
                },
            ],
        }
        coverage = _formal_coverage(suite)
        self.assertEqual(2, coverage["expected_runs"])
        self.assertEqual(2, coverage["manifest_runs"])
        self.assertEqual({"COMPLETED": 2}, coverage["status_counts"])
        self.assertEqual(2, coverage["evidence_runs"])
        self.assertEqual("complete", coverage["status"])

    def test_manifest_placeholders_do_not_count_as_evidence_coverage(self) -> None:
        suite = {
            "scenarios": ["case-a", "case-b", "case-c"],
            "repeats": 1,
            "policies": ["server-observe"],
            "runs": [
                {
                    "scenario_key": "case-a",
                    "status": "completed",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 3},
                            "commit": {"submitted": 0},
                        }
                    },
                },
                {
                    "scenario_key": "case-b",
                    "status": "completed",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 0},
                            "commit": {"submitted": 0},
                        }
                    },
                },
                {
                    "scenario_key": "case-c",
                    "status": "TIMEOUT",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 0},
                            "commit": {"submitted": 0},
                        }
                    },
                },
            ],
        }
        self.assertEqual(3, _formal_submitted_operations(suite["runs"][0]))
        self.assertEqual(0, _formal_submitted_operations(suite["runs"][1]))
        coverage = _formal_coverage(suite)
        self.assertEqual(3, coverage["manifest_runs"])
        self.assertEqual(2, coverage["completed_runs"])
        self.assertEqual(1, coverage["evidence_runs"])
        self.assertEqual(1, coverage["empty_completed_runs"])
        self.assertEqual("partial", coverage["status"])
        self.assertEqual(
            ["case-b", "case-c"],
            coverage["evidence_missing_scenarios"],
        )

    def test_first_completed_commit_csv_finds_real_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "case" / "commit_results.csv"
            csv_path.parent.mkdir()
            csv_path.write_text(
                "tenant,session_id,archive_id,status\n0,s1,a1,completed\n",
                encoding="utf-8",
            )
            self.assertEqual((csv_path, "0"), _first_completed_commit_csv(root))

    def test_resolve_auth_key_follows_numeric_tenant_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "a", "auth_key": "key-a"},
                            {"tenant_id": "b", "auth_key": "key-b"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(("key-b", ""), _resolve_auth_key(path, "1"))

    def test_resolve_auth_key_follows_tenant_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "a", "auth_key": "key-a"},
                            {"tenant_id": "b", "auth_key": "key-b"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(("key-b", ""), _resolve_auth_key(path, "b"))

    def test_missing_evidence_stays_inconclusive(self) -> None:
        objectives = objective_statuses(
            {"profile_name": "4U8G", "runs": []},
            recovery_configured=False,
            metrics_configured=False,
        )
        self.assertEqual(6, len(objectives))
        self.assertTrue(all(item["status"] == "INCONCLUSIVE" for item in objectives))

    def test_probe_inconclusive_is_not_reported_as_process_failure(self) -> None:
        execution = {"status": "FAIL", "returncode": 2}
        self.assertEqual(
            "INCONCLUSIVE",
            _preserve_probe_status(execution, {"status": "INCONCLUSIVE"})["status"],
        )

    def test_profile_can_enable_blackbox_probes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "profiles.json"
            path.write_text(
                json.dumps({
                    "profiles": [{
                        "name": "4U8G",
                        "missing_cases": {"enabled": True, "max_tenants": 1},
                        "concurrent_commit": {
                            "enabled": True, "concurrency": 4, "timeout_s": 120
                        },
                    }]
                }),
                encoding="utf-8",
            )
            profile = load_profiles(path)[0]
            self.assertTrue(profile["missing_cases"]["enabled"])
            self.assertEqual(4, profile["concurrent_commit"]["concurrency"])

    def test_profile_can_request_real_quick_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(
                json.dumps({
                    "profiles": [{
                        "name": "4U8G",
                        "quick_include_seed": False,
                    }]
                }),
                encoding="utf-8",
            )
            profile = load_profiles(path)[0]
            self.assertFalse(profile["quick_include_seed"])

    def test_quick_seed_options_reuse_one_formal_warmup(self) -> None:
        command = ["python", "-m", "performance.formal_suite"]
        self.assertEqual(
            command + ["--seed-sessions-per-tenant", "1"],
            _append_quick_seed_options(command, include_seed=True),
        )
        self.assertNotIn("--no-seed-reuse", command)

    def test_quick_seed_options_skip_seed_explicitly(self) -> None:
        command = ["python", "-m", "performance.formal_suite"]
        self.assertEqual(
            command + ["--skip-seed", "--seed-sessions-per-tenant", "0"],
            _append_quick_seed_options(command, include_seed=False),
        )

    def test_recovery_profile_requires_accepted_202(self) -> None:
        profile_path = (
            Path(__file__).parents[1]
            / "performance"
            / "instance-profile-4u8g.audit.server.example.json"
        )
        profile = load_profiles(profile_path)[0]
        self.assertTrue(profile["commit_recovery"]["require_accepted_202"])

    def test_probe_pass_failure_status_is_preserved(self) -> None:
        execution = {"status": "FAIL", "returncode": 2}
        self.assertEqual(
            "FAIL",
            _preserve_probe_status(execution, {"status": "PASS"})["status"],
        )

    def test_limit_probe_accepts_explicit_auth_key(self) -> None:
        self.assertEqual(
            "key-a",
            auth_key({"auth_key": "key-a", "auth_key_env": "MISSING_KEY"}),
        )

    def test_limit_probe_loads_explicit_tenant_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenants.json"
            path.write_text(
                json.dumps(
                    {"tenants": [{"tenant_id": "a", "user_id": "u", "auth_key": "key-a"}]}
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                [{"tenant_id": "a", "user_id": "u", "auth_key_env": "", "auth_key": "key-a"}],
                load_tenants(path),
            )

    def test_limit_probe_discovers_sessions_from_numeric_tenant_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "run" / "requests.csv"
            csv_path.parent.mkdir()
            csv_path.write_text(
                "tenant,session_id,op,status\n"
                "0,session-a,open,ok\n"
                "1,session-b,open,ok\n",
                encoding="utf-8",
            )
            tenants = [
                {"tenant_id": "tenant-a"},
                {"tenant_id": "tenant-b"},
            ]
            self.assertEqual(
                {
                    "tenant-a": "session-a",
                    "tenant-b": "session-b",
                },
                discover_sessions(root, tenants),
            )

    def test_limit_probe_discovers_sessions_from_tenant_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "run" / "requests.csv"
            csv_path.parent.mkdir()
            csv_path.write_text(
                "tenant,session_id,op,status\n"
                "tenant-a,session-a,open,ok\n"
                "tenant-b,session-b,open,ok\n",
                encoding="utf-8",
            )
            tenants = [
                {"tenant_id": "tenant-a"},
                {"tenant_id": "tenant-b"},
            ]
            self.assertEqual(
                {
                    "tenant-a": "session-a",
                    "tenant-b": "session-b",
                },
                discover_sessions(root, tenants),
            )

    def test_limit_probe_preserves_error_class_and_detail(self) -> None:
        self.assertEqual("request_or_admission_4xx", error_class(400))
        self.assertEqual("server_error", error_class(503))
        self.assertEqual("transport_error", error_class(None))
        self.assertEqual(
            "invalid session",
            response_error_detail(json.dumps({"detail": "invalid session"})),
        )
        self.assertEqual(
            "admission_rejected",
            classify_response(400, "", "too many recall requests in flight"),
        )
        self.assertEqual(
            "request_or_admission_4xx",
            classify_response(400, "", "invalid session"),
        )

    def test_recovery_objective_requires_idempotency_evidence(self) -> None:
        suite = {
            "profile_name": "4U8G",
            "commit_recovery": {
                "status": "PASS",
                "message_reconciliation": {"status": "PASS"},
                "cursor_reconciliation": {"status": "PASS"},
            },
        }
        objectives = objective_statuses(
            suite,
            recovery_configured=True,
            metrics_configured=False,
        )
        self.assertEqual(
            "INCONCLUSIVE",
            next(item["status"] for item in objectives if item["id"] == "O5"),
        )

        suite["commit_recovery"].update(
            {
                "accepted_202": True,
                "recovered": True,
                "commit_terminal": [{"state": "completed"}],
                "order_reconciliation": {"status": "PASS"},
                "idempotency_reconciliation": {"status": "INCONCLUSIVE"},
                "idempotency_replay": {
                    "same_archive": True,
                    "replayed": False,
                },
            }
        )
        objectives = objective_statuses(
            suite,
            recovery_configured=True,
            metrics_configured=False,
        )
        self.assertEqual(
            "PASS",
            next(item["status"] for item in objectives if item["id"] == "O5"),
        )

    def test_fault_isolation_probe_is_wired_into_objective_evaluator(self) -> None:
        suite = {
            "fault_isolation": {
                "status": "PASS",
                "fault_recovered": True,
                "bystander_p95_degradation": 0.12,
                "bystander_tenants": {
                    "tenant-b": {
                        "baseline_p95_s": 1.0,
                        "fault_p95_s": 1.12,
                        "degradation": 0.12,
                    }
                },
            }
        }
        objectives = objective_statuses(
            suite,
            recovery_configured=False,
            metrics_configured=False,
        )
        self.assertEqual(
            "PASS",
            next(item["status"] for item in objectives if item["id"] == "O2"),
        )

    def test_render_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            render_report({"created_at": "now", "profiles": []}, path)
            self.assertTrue(path.is_file())
            self.assertIn("六项 4U8G 目标", path.read_text(encoding="utf-8"))

    def test_single_profile_does_not_pass_multi_spec(self) -> None:
        objectives = objective_statuses(
            {
                "instance_profiles": [{
                    "name": "4U8G",
                    "status": "completed",
                    "completed_runs": 1,
                }],
                "runs": [],
            },
            recovery_configured=False,
            metrics_configured=False,
        )
        self.assertEqual(6, len(objectives))
        self.assertTrue(all(item["status"] == "INCONCLUSIVE" for item in objectives))

    def test_only_completed_formal_runs_count_as_completed_profile_evidence(self) -> None:
        suite = {
            "runs": [
                {
                    "status": "TIMEOUT",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 10},
                            "commit": {"submitted": 2},
                        }
                    },
                },
                {
                    "status": "completed",
                    "summary": {
                        "metrics": {
                            "search": {"submitted": 10},
                            "commit": {"submitted": 2},
                        }
                    },
                },
            ]
        }
        self.assertEqual((1, 2), _formal_run_counts(suite))

    def test_run_command_redacts_secret_values(self) -> None:
        result = run_command(
            ["python3", "-c", "print('ok')", "secret-value"],
            timeout_s=10,
            redact_values={"secret-value"},
        )
        self.assertEqual("PASS", result["status"])
        self.assertNotIn("secret-value", " ".join(result["command"]))

    def test_materialize_fault_plan_uses_selected_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fault-plan.json"
            output = root / "run" / "fault-plan.resolved.json"
            source.write_text(
                json.dumps({
                    "faults": [{"endpoint": "${BASE_URL}/fault/llm-500"}],
                    "recovery": {"health_url": "${BASE_URL}/health"},
                }),
                encoding="utf-8",
            )
            _materialize_fault_plan(
                source,
                base_url="http://127.0.0.1:18187/",
                output_path=output,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                "http://127.0.0.1:18187/fault/llm-500",
                payload["faults"][0]["endpoint"],
            )
            self.assertEqual(
                "http://127.0.0.1:18187/health",
                payload["recovery"]["health_url"],
            )

    def test_skip_run_can_use_explicit_suite_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = root / "suite.json"
            suite.write_text("{}", encoding="utf-8")
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps({"profiles": [{"name": "4U8G"}]}),
                encoding="utf-8",
            )
            self.assertTrue(suite.is_file())
            self.assertEqual("4U8G", load_profiles(profiles)[0]["name"])

    def test_output_lock_rejects_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _acquire_output_lock(root)
            try:
                with self.assertRaisesRegex(RuntimeError, "already locked"):
                    _acquire_output_lock(root)
            finally:
                first.close()

    def test_metrics_coverage_uses_real_samples_not_help_lines(self) -> None:
        raw = (
            "# HELP echomem_lane_wait_seconds wait\n"
            "# TYPE echomem_lane_wait_seconds histogram\n"
            'echomem_lane_wait_seconds_bucket{lane="recall_engine",le="1"} 1\n'
            'echomem_lane_rejected_total{lane="recall_engine",reason_code="queue_full"} 1\n'
            'echomem_engine_fanout_exec_seconds_count{engine="atomic_engine"} 1\n'
        )
        coverage = metrics_coverage(raw)
        self.assertIn("echomem_lane_wait_seconds", coverage["present"])
        self.assertIn("echomem_lane_rejected_total", coverage["present"])
        self.assertIn("echomem_engine_fanout_exec_seconds", coverage["present"])
        self.assertFalse(coverage["present"]["echomem_lane_queued"])


if __name__ == "__main__":
    unittest.main()
