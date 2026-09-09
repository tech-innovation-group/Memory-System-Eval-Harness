import pytest
from types import SimpleNamespace
from performance.targets.echomem.acceptance.capacity_load import measure


def test_explicit_client_workers_are_recorded_without_service_cap(monkeypatch):
    monkeypatch.setattr('performance.targets.echomem.acceptance.capacity_load.arrival_plan', lambda *a, **k: [])
    result = measure([SimpleNamespace(tenant_index=0)], duration_s=0.001, q=1, search_workers=128, load_mode='search')
    assert result['pools']['read'] == 128


@pytest.mark.parametrize('workers', [0, -1, True, 1.5])
def test_invalid_worker_count_rejected_before_work(workers):
    with pytest.raises(ValueError):
        measure([object()], duration_s=0, q=0, search_workers=workers)
