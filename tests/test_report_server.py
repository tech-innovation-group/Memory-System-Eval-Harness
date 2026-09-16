from pathlib import Path

from scripts.serve_echomem_report import latest_run, public_file


def test_latest_run_uses_run_timestamp_not_directory_mtime(tmp_path: Path):
    old = tmp_path / "pr33-20260916T235959Z"
    current = tmp_path / "pr33-20260917T000001Z"
    old.mkdir()
    current.mkdir()
    (old / "runtime-diagnostics.json").write_text("newer mtime")
    assert latest_run(tmp_path, 33) == current


def test_report_server_allowlist_rejects_private_and_nested_files():
    assert public_file("/report.html") == "report.html"
    assert public_file("/report.json") == "report.json"
    assert public_file("/structured-stage-events.jsonl") == "structured-stage-events.jsonl"
    assert public_file("/M1/cross-tenant/seed-progress.json") == "M1/cross-tenant/seed-progress.json"
    assert public_file("/status.json") is None
    assert public_file("/identities.private.json") is None
    assert public_file("/../identities.private.json") is None


def test_latest_run_filters_pull_request_number(tmp_path: Path):
    (tmp_path / "pr32-20260917T000001Z").mkdir()
    (tmp_path / "pr33-20260917T000002Z").mkdir()
    assert latest_run(tmp_path, 33).name == "pr33-20260917T000002Z"
