import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from performance.targets.echomem.acceptance.commit_evidence import commit_outcomes, receipt
from performance.targets.echomem.acceptance.main_metric_samples import flood
from performance.targets.echomem.acceptance.main_metric_report import contention_matrix_counts, redacted_report, render
from performance.targets.echomem.probes._client import HttpResult, _server_observability


def response(status, payload=None, **kwargs):
    return HttpResult("POST", "/private-session", status, .01, payload or {}, **kwargs)


def test_receipt_keeps_public_reason_and_retry_not_private_body():
    result = response(503, {"error": {"code": "COMMIT_UNAVAILABLE", "message": "private-key"},
                            "request_id": "private-request"}, retry_after_s=3)
    public = receipt(result)
    assert public["reason_code"] == "COMMIT_UNAVAILABLE"
    assert public["retry_after_s"] == 3
    assert "private-" not in json.dumps(public)


def test_unknown_reason_is_present_but_not_exported_or_guessed():
    public = receipt(response(503, {"code": "private-token"}))
    assert public["reason_code"] == "UNRECOGNIZED"
    assert public["reason_code_present"] is True
    assert receipt(response(503))["reason_code"] == "NOT_RECORDED"
    assert receipt(response(503, retry_after_s=float("nan")))["retry_after_s"] is None


@pytest.mark.parametrize("code", ["HTTP_INGRESS_SATURATED", "HTTP_LANE_SATURATED",
                                 "COMMIT_UNAVAILABLE", "TENANT_RATE_LIMITED",
                                 "TEST_FAULT_INJECTED", "RETRIEVAL_BUSY"])
def test_echomem_string_error_enum_reaches_both_collectors(code):
    payload = {"error": code, "message": "private-key", "request_id": "private-id"}
    assert _server_observability(payload, {})["reason_code"] == code
    public = receipt(response(503, payload))
    assert public["reason_code"] == code
    assert "private-" not in json.dumps(public)


@pytest.mark.parametrize("error", ["private-key", {"message": "private-key"},
                                  ["private-key"], None])
def test_arbitrary_error_never_becomes_public_code(error):
    payload = {"error": error}
    assert "reason_code" not in _server_observability(payload, {})
    assert "private-" not in json.dumps(receipt(response(503, payload)))
    outcome = commit_outcomes([{"http_status": 503, "reason_code": error}])
    assert outcome["known_rejection_reasons"] == 0
    assert outcome["unknown_rejection_reasons"] == 1
    assert "private-" not in json.dumps(outcome)


class Clock:
    def __init__(self):
        self.current = 100.0

    def now(self):
        return self.current

    def sleep(self, duration):
        self.current += duration


class Client:
    def __init__(self, submit, polls=()):
        self.submit_response, self.polls = submit, iter(polls)
        self.commits = self.poll_count = 0

    def commit(self, session, **kwargs):
        self.commits += 1
        return self.submit_response

    def request(self, *args, **kwargs):
        self.poll_count += 1
        result = next(self.polls, response(None))
        if isinstance(result, Exception):
            raise result
        return result


def run_flood(monkeypatch, client):
    clock = Clock()
    monkeypatch.setattr("performance.targets.echomem.acceptance.main_metric_samples.time.monotonic", clock.now)
    monkeypatch.setattr("performance.targets.echomem.acceptance.main_metric_samples.time.sleep", clock.sleep)
    return flood([SimpleNamespace(client=client)], [(0, "private-session")],
                 delay_s=0, commit_timeout_s=3)


def test_rejected_commit_is_recorded_once_not_retried(monkeypatch):
    client = Client(response(503, {"code": "COMMIT_UNAVAILABLE"}))
    rows = run_flood(monkeypatch, client)
    assert client.commits == 1 and client.poll_count == 0
    assert rows[0]["accepted_202"] is False
    assert "accepted_at" not in rows[0]
    assert rows[0]["observed_until"] >= rows[0]["submit_at"]
    outcome = commit_outcomes(rows)
    assert outcome["http_rejected"] == 1
    assert outcome["rejection_reason_counts"] == {"COMMIT_UNAVAILABLE": 1}


def test_poll_failure_continues_existing_task_and_keeps_all_errors(monkeypatch):
    client = Client(response(202, {"archive_id": "private-archive"}), [
        TimeoutError("private-api-key"), response(503, {"code": "COMMIT_UNAVAILABLE"}),
        response(200, {"status": "completed"}),
    ])
    rows = run_flood(monkeypatch, client)
    assert client.commits == 1 and client.poll_count == 3
    assert rows[0]["completed"] is True
    assert len(rows[0]["polls"]) == 3
    outcome = commit_outcomes(rows)
    assert outcome["completed"] == 1 and outcome["unresolved"] == 0
    assert outcome["poll_http_errors"] == 2
    assert "private-" not in json.dumps(rows)


def test_timeout_bounds_polling_and_preserves_unresolved_task(monkeypatch):
    client = Client(response(202, {"archive_id": "private-archive"}))
    rows = run_flood(monkeypatch, client)
    assert client.commits == 1 and client.poll_count == 3
    assert rows[0]["observed_until"] - rows[0]["accepted_at"] == 3
    outcome = commit_outcomes(rows)
    assert outcome["unresolved"] == 1 and outcome["completed"] == 0
    assert outcome["poll_http_errors"] == 3


def test_202_without_archive_is_not_accepted_and_does_not_poll(monkeypatch):
    client = Client(response(202))
    outcome = commit_outcomes(run_flood(monkeypatch, client))
    assert client.poll_count == 0
    assert outcome["missing_archive_on_202"] == 1
    assert outcome["accepted_202"] == 0


def test_historical_unknown_reasons_and_polls_stay_unknown():
    rows = [{"http_status": 202, "accepted_202": True, "completed": True, "terminal_at": 200},
            {"http_status": 503, "accepted_202": False}]
    outcome = commit_outcomes(rows)
    assert outcome["http_status_counts"] == {"202": 1, "503": 1}
    assert outcome["unknown_rejection_reasons"] == 1
    assert outcome["rejection_reason_counts"] == {"NOT_RECORDED": 1}
    assert outcome["poll_http_errors"] is None


def test_unknown_response_is_not_success_or_http_rejection():
    outcome = commit_outcomes([{"http_status": None}, {"http_status": 200}])
    assert outcome["accepted_202"] == outcome["http_rejected"] == 0
    assert outcome["transport_or_unrecorded"] == 1
    assert outcome["unexpected_non202_success"] == 1


def test_per_repeat_outcomes_reach_public_report():
    outcome = commit_outcomes([{"http_status": 503, "accepted_202": False}])
    joint, _ = contention_matrix_counts({"samples": [{"repeat": 1, "M3_M4": {
        "tenants": [], "commit_outcomes": outcome}}]})
    assert joint["repeat_summaries"][0]["commit_outcomes"] == outcome
    public = redacted_report({"metrics": {"M3_M4": joint}}, {"levels": []})
    page = render(public)
    assert "HTTP状态分布" in page and "拒绝原因分布" in page
    assert "NOT_RECORDED" in page
    assert "503不自动归因于限流或API key" in page
    assert "32个Commit并发受理" not in page
    assert "每任务 180 秒" not in page
    assert "轮询间隔内的实际完成时刻未知" in page


def test_report_labels_separate_load_run_and_escapes_provenance():
    public = redacted_report({"metrics": {}}, {"levels": []})
    public["publication"]["metric_sources"] = [
        {"metrics": "M3/M4", "description": "<script>new batch</script>"}]
    page = render(public)
    assert "不是六项同时复测" in page
    assert "&lt;script&gt;new batch&lt;/script&gt;" in page
    assert "<script>new batch</script>" not in page


def test_slow_polling_does_not_hold_all_submission_workers():
    release_polls, all_submitted = threading.Event(), threading.Event()

    class BlockingPollClient:
        def __init__(self):
            self.lock, self.keys = threading.Lock(), []

        def commit(self, session, **kwargs):
            with self.lock:
                self.keys.append(kwargs["idempotency_key"])
                if len(self.keys) == 33:
                    all_submitted.set()
            return response(202, {"archive_id": "private-archive"})

        def request(self, *args, **kwargs):
            assert release_polls.wait(5)
            return response(200, {"status": "completed"})

    client = BlockingPollClient()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(flood, [SimpleNamespace(client=client)],
                              [(0, str(i)) for i in range(33)], delay_s=0)
        try:
            assert all_submitted.wait(2), "Polling blocked the 33rd independent submission"
        finally:
            release_polls.set()
        rows = pending.result(timeout=5)
    assert len(set(client.keys)) == 33
    assert commit_outcomes(rows)["completed"] == 33
    assert "private-" not in json.dumps(rows)


def test_paced_plan_records_actual_submission_lag_without_retry():
    client = Client(response(503, {"error": "HTTP_LANE_SATURATED"}))
    rows = flood([SimpleNamespace(client=client)], [(0, str(i)) for i in range(3)],
                 delay_s=0, commit_submit_rps=100)
    assert client.commits == 3 and client.poll_count == 0
    assert rows[1]["scheduled_submit_at"] - rows[0]["scheduled_submit_at"] == pytest.approx(.01)
    assert rows[2]["scheduled_submit_at"] - rows[0]["scheduled_submit_at"] == pytest.approx(.02)
    assert all(row["submit_at"] >= row["scheduled_submit_at"] for row in rows)
    result = commit_outcomes(rows)
    assert result["submission_timing_samples"] == 3
    assert result["submission_lag_max_s"] >= 0
    assert result["observed_submission_span_s"] >= 0


@pytest.mark.parametrize("rate", [-1, float("nan"), float("inf"), True])
def test_invalid_rate_is_rejected_before_work(rate):
    with pytest.raises(ValueError):
        flood([], [], delay_s=0, commit_submit_rps=rate)


def test_historical_submission_timing_is_unknown_not_zero():
    result = commit_outcomes([{"http_status": 503}])
    assert result["submission_timing_samples"] == 0
    assert result["submission_lag_max_s"] is None
    assert result["observed_submission_span_s"] is None


@pytest.mark.parametrize("rate", [-1, float("nan"), True, .1])
def test_contention_preflight_rejects_invalid_or_out_of_window_plan(tmp_path, monkeypatch, rate):
    from performance.targets.echomem.acceptance import contention_matrix

    def forbidden_inspect(*args):
        pytest.fail("Invalid plan reached Docker inspection")

    monkeypatch.setattr(contention_matrix, "inspect_container", forbidden_inspect)
    with pytest.raises(ValueError):
        contention_matrix.run(base_url="unused", seed_directory=tmp_path,
                              output=tmp_path / "out", container="unused", expected_lanes=[],
                              duration_s=60, commits_per_tenant=8, commit_submit_rps=rate)


def test_submission_plan_survives_repeat_reduction_and_html():
    schedule = {"mode": "paced", "requested_rps": 2, "planned_span_s": 15.5,
                "commit_timeout_s": 360}
    joint, _ = contention_matrix_counts({"samples": [{"repeat": 1, "M3_M4": {
        "submission_schedule": schedule}}]})
    assert joint["repeat_summaries"][0]["submission_schedule"] == schedule
    page = render(redacted_report({"metrics": {"M3_M4": joint}}, {"levels": []}))
    assert "paced" in page and "最大提交延迟 s" in page
    assert "名义提交速率不等于服务吞吐" in page
