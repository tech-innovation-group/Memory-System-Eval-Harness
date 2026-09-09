"""Report scope must not confuse unselected metrics with failed tests."""
from performance.targets.echomem.acceptance.observation import METRIC_NAMES, write_observation_report


def result(selected):
    return {"selected_metrics": selected, "status": "MEASURED", "sampling_mode": "full",
            "metrics": {code: {"status": "MEASURED" if code in selected else "BLOCKED",
                               "reason": "measured" if code in selected else "本次命令未选择该指标"}
                        for code in METRIC_NAMES}}


def test_single_metric_report_has_scoped_title_and_no_unselected_failure_cards(tmp_path):
    data = result(["M3"])
    path = tmp_path / "report.html"
    write_observation_report(data, path)
    page = path.read_text()
    assert "<h1>EchoMem 4U8G M3黑盒观测</h1>" in page
    assert "本报告不包含：M1、M4、M2、M5、M6" in page
    assert "<article><b>M3</b>" in page
    assert "<article><b>M1</b>" not in page
    assert "本次命令未选择该指标" not in page
    assert data["metrics"]["M1"]["status"] == "BLOCKED"


def test_selected_blocked_metric_is_still_displayed(tmp_path):
    data = result(["M3", "M4"])
    data["metrics"]["M4"] = {"status": "BLOCKED", "reason": "seed failed"}
    path = tmp_path / "report.html"
    write_observation_report(data, path)
    page = path.read_text()
    assert "<article><b>M4</b>" in page
    assert "seed failed" in page
    assert "<span class='BLOCKED'>BLOCKED</span>" in page


def test_full_report_keeps_all_six_metrics(tmp_path):
    path = tmp_path / "report.html"
    write_observation_report(result(list(METRIC_NAMES)), path)
    page = path.read_text()
    assert "<h1>EchoMem 4U8G 六项黑盒观测</h1>" in page
    assert "本报告不包含" not in page
    for code in METRIC_NAMES:
        assert f"<article><b>{code}</b>" in page
    ordered = ("M1", "M3", "M4", "M2", "M5", "M6")
    cards = [page.index(f"<article><b>{code}</b>") for code in ordered]
    sections = [page.index(f"<section><h2>{code} ") for code in ordered]
    assert cards == sorted(cards)
    assert sections == sorted(sections)


def test_m1_report_keeps_failure_domains_and_provider_evidence(tmp_path):
    data = result(["M1"])
    data["metrics"]["M1"]["levels"] = [{
        "topology": "cross-tenant", "hot_users": 8, "load_mode": "search",
        "status": "MEASURED", "sent_search_rps": 8.0, "effective_search_rps": 3.0,
        "search": {
            "sent": 80, "success": 30, "p95_s": 5.0,
            "error_breakdown": {
                "denominator_sent": 80,
                "outcome_partition": {"strict_success": 30},
                "http_200_quality_failures": 40,
                "http_non_200": 10,
                "transport_errors": 0,
                "reason_code_counts": {"retrieval_inflight_full": 10},
                "failure_domain_counts": {"atomic_engine": 40, "echomem_admission": 10},
                "provider_error_code_counts": {},
                "provider_evidence_available": False,
                "unclassified_failures": 0,
                "partition_complete": True,
            },
        },
    }]
    path = tmp_path / "report.html"
    write_observation_report(data, path)
    page = path.read_text()
    assert "查看 M1 错误、API 异常与失败责任域" in page
    assert "retrieval_inflight_full" in page
    assert "atomic_engine" in page
    assert "Provider 证据未采集时" in page
