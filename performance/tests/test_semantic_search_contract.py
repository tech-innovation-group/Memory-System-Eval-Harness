"""Synthetic unit payloads verify scoring, not EchoMem accuracy."""
from types import SimpleNamespace
import pytest

from performance.targets.echomem.protocol import search


class Context:
    tenant_idx = 0
    params = {"tenant_query_cases": {"0": {"我记下的地点在哪？": {
        "id": "place", "query_type": "recall", "aliases": ["梧桐会议室"]}}}}

    def __init__(self, payload, ok=True):
        self.response = SimpleNamespace(ok=ok, json=payload)
        self.fields = {}

    def post(self, *args, **kwargs):
        return self.response

    def note(self, **fields):
        self.fields.update(fields)


@pytest.mark.parametrize("payload, expected", [
    ({"items": [{"content": "地点为梧桐会议室"}]}, True),
    ({"items": [{"content": "地点为别的地方"}]}, False),
    ({"items": [], "query": "梧桐会议室"}, False),
    ({"items": [{"debug": "梧桐会议室", "content": "未知"}]}, False),
    ({"items": [{"content": "梧桐会议室"}], "status": "degraded"}, False),
])
def test_only_returned_memory_content_can_satisfy_fact(payload, expected):
    ctx = Context(payload)
    search(ctx, "我记下的地点在哪？")
    assert ctx.fields["quality_ok"] is expected
    assert ctx.fields["quality_assertion"] == "fixed-fact-in-items"
    assert ctx.fields["query_type"] == "recall"


def test_failed_http_preserves_assertion_and_denominator():
    ctx = Context({}, ok=False)
    search(ctx, "我记下的地点在哪？")
    assert ctx.fields["quality_ok"] is False
    assert ctx.fields["quality_assertion"] == "fixed-fact-in-items"


def test_m3_seed_failure_retains_public_diagnostics(monkeypatch):
    from performance.suite import SeedPreparationError
    from performance.targets.echomem.orchestrator import runner
    from performance.targets.echomem.acceptance import capacity_seed
    spec = SimpleNamespace(tenant_id="test-tenant", auth_key="unit-secret",
                           user_id="test-user", account_id="test-tenant", agent_id="test-agent")
    monkeypatch.setattr(runner, "load_tenant_specs", lambda *a, **k: [spec])
    evidence = {"status": "INCONCLUSIVE", "healthy_actors": 0, "actor_count": 1}
    monkeypatch.setattr(capacity_seed, "prepare_actors", lambda *a, **k: evidence)
    with pytest.raises(SeedPreparationError) as failure:
        runner._prepare_semantic_seed("http://unused.invalid", "unused", 1, 1, 1)
    assert failure.value.public_evidence == evidence
    assert "unit-secret" not in str(failure.value)


def test_m3_seed_carries_ground_truth_separately_from_query(monkeypatch):
    from performance.targets.echomem.orchestrator import runner
    from performance.targets.echomem.acceptance import capacity_seed
    spec = SimpleNamespace(tenant_id="test-tenant", auth_key="unit-secret",
                           user_id="test-user", account_id="test-tenant", agent_id="test-agent")
    monkeypatch.setattr(runner, "load_tenant_specs", lambda *a, **k: [spec])
    monkeypatch.setattr(capacity_seed, "prepare_actors", lambda *a, **k:
                        {"status": "PASS", "healthy_actors": 1, "actor_count": 1})
    contexts, summary = runner._prepare_semantic_seed("http://unused.invalid", "unused", 1, 1, 1)
    assert len(contexts[0].query_cases) == len(contexts[0].queries) == 40
    assert summary["validated_queries_per_tenant"] == 4
    for query, sample in contexts[0].query_cases.items():
        assert sample["aliases"]
        assert all(alias not in query for alias in sample["aliases"])
    assert "unit-secret" not in str(summary)


def test_no_performance_blame_before_seed_passes():
    from performance.targets.echomem.acceptance.observation import derive_observation_recommendations
    advice = derive_observation_recommendations({"selected_metrics": ["M3"], "metrics": {},
        "setup_evidence": {"seed_status": "ENV_ERROR", "load_cases_completed": 0}})
    assert len(advice) == 1
    assert advice[0]["module"] == "测试准备 / Recall 验证"
