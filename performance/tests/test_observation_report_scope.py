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
    assert "本报告不包含：M1、M2、M4、M5、M6" in page
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
