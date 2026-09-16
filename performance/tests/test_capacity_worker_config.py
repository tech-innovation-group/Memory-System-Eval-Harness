import pytest
from types import SimpleNamespace
from performance.targets.echomem.acceptance.capacity_load import measure


def test_explicit_client_workers_are_recorded_without_service_cap(monkeypatch):
    monkeypatch.setattr('performance.targets.echomem.acceptance.capacity_load.arrival_plan', lambda *a, **k: [])
    result = measure([SimpleNamespace(tenant_index=0)], duration_s=0.001, q=1, search_workers=128, load_mode='search')
    assert result['pools']['read'] == 128


def test_commit_poll_workers_cover_all_planned_receipts(monkeypatch):
    class Client:
        timeout_s = 1
        agent_id = "agent"

        def add_message(self, *args):
            return SimpleNamespace(status_code=200, payload={}, reason_code="")

        def commit(self, *args, **kwargs):
            archive = f"archive-{args[0]}"
            return SimpleNamespace(status_code=202, payload={"archive_id": archive}, reason_code="")

        def commit_status(self, *args):
            return SimpleNamespace(status_code=200, payload={"status": "completed"}, reason_code="")

    planned = [(0.0, "add", 0, index) for index in range(5)]
    planned.extend([(0.02, "commit_submit", 0, index) for index in range(5)])
    monkeypatch.setattr(
        'performance.targets.echomem.acceptance.capacity_load.arrival_plan',
        lambda *a, **k: planned,
    )
    actor = SimpleNamespace(tenant_index=0, user_index=0, client=Client(),
                            corpus={}, write_session="session")
    result = measure([actor], duration_s=0.2, load_mode='commit',
                     commit_interval_s=1, commit_timeout_s=1)
    assert result['planned_commit_count'] == 5
    assert result['pools']['commit_poll'] == 5


@pytest.mark.parametrize('workers', [0, -1, True, 1.5])
def test_invalid_worker_count_rejected_before_work(workers):
    with pytest.raises(ValueError):
        measure([object()], duration_s=0, q=0, search_workers=workers)
