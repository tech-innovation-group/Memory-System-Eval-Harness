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


def test_combined_observation_uses_same_semantic_seed_hook(tmp_path, monkeypatch):
    from performance.targets.echomem.orchestrator import runner
    from performance.targets.echomem.acceptance import readiness
    from performance.targets.echomem.probes import tenant_observability
    monkeypatch.setattr(readiness, "check_readiness", lambda _: {"ok":True,"resource_evidence":{}})
    monkeypatch.setattr(tenant_observability, "collect", lambda **kwargs: {})
    captured = {}
    def suite(profile, **kwargs):
        captured.update(kwargs)
        return {"runs":[]}
    monkeypatch.setattr(runner, "run_suite_impl", suite)
    runner.run_suite({"six_metrics_observation":True,"semantic_seed_cache":"/unit/cache"},
        suite_dir=tmp_path, scenarios=["m2-fairness-4t","m3-baseline","m3-flood-uniform"])
    assert captured["seed"].func is runner._prepare_semantic_seed
    assert captured["seed"].keywords == {
        "reuse_seed": "/unit/cache", "dataset_path": "",
        "sample_id": "conv-30", "session_key": "session_1",
        "search_timeout_s": 60,
    }


@pytest.mark.parametrize("http,content,expected", [(200,"梧桐会议室",1),(200,"未知",0),(429,"梧桐会议室",0)])
def test_fault_probe_uses_fact_not_question_as_expected_answer(http, content, expected):
    from performance.targets.echomem.probes.fault_isolation import sample_search
    queries = []
    def search(session, query, **kwargs):
        queries.append(query)
        return SimpleNamespace(status_code=http, payload={"items":[{"content":content}]}, error=None)
    result = sample_search({"unit":SimpleNamespace(search=search)}, {"unit":"unit-session"},
        count=1, workers=1, timeout_s=1, phase="before", queries={"unit":{
            "id":"location", "query":"地点在哪？", "query_type":"recall", "aliases":["梧桐会议室"]}})
    assert queries == ["地点在哪？"]
    assert result["by_tenant"]["unit"]["quality_ok"] == expected


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
    assert len(contexts[0].query_cases) == len(contexts[0].queries) == 12
    assert summary["validated_queries_per_tenant"] == 4
    assert summary["seed_source"] == "locomo-single-session"
    assert summary["corpus_source"]["sample_id"] == "conv-30"
    assert summary["corpus_source"]["session_key"] == "session_1"
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


def test_report_labels_cached_seed_without_claiming_fresh_injection(tmp_path):
    from performance.targets.echomem.acceptance.observation import evaluate_observation, write_observation_report
    result = evaluate_observation({"runs": [], "seed": {"status": "completed",
        "seed_source": "validated-cache", "seed_documents_per_tenant": 5,
        "facts_per_tenant": 20, "query_variants_per_tenant": 40,
        "validated_queries_per_tenant": 4}}, {}, [], quick=False, selected_metrics=["M3"])
    assert result["setup_evidence"]["seed_source"] == "validated-cache"
    path = tmp_path / "report.html"
    write_observation_report(result, path)
    html = path.read_text()
    assert "复用已有记忆，本次重新验证召回" in html
    assert "预检抽样通过不代表整个问题池全部通过" in html


@pytest.mark.parametrize("healthy", [True, False])
def test_cached_validation_only_searches_current_returned_facts(healthy):
    from performance.targets.echomem.acceptance.capacity_seed import CapacityActor, validate_cached_actors
    calls = []

    def request(method, path, body, **kwargs):
        calls.append((method, path))
        return SimpleNamespace(status_code=200, transport_error_type="", payload={"items": [
            {"content": "remembered-place" if healthy else "unrelated"}]})

    client = SimpleNamespace(request=request, agent_id="unit-agent")
    corpus = {"recall_queries": [{"id": str(i), "query": "Where did I go?",
              "query_type": "recall", "aliases": ["remembered-place"]} for i in range(8)]}
    result = validate_cached_actors([CapacityActor(0, 0, client, corpus)], validation_queries=4)
    assert calls == [("POST", "/api/retrieval/search")] * 4
    assert result["healthy_actors"] == int(healthy)
    assert len(result["actors"][0]["queries"]) == 4


@pytest.mark.parametrize("identity_matches", [True, False])
def test_m3_cache_requires_exact_identity_and_reports_actual_corpus(monkeypatch, identity_matches):
    from performance.targets.echomem.orchestrator import runner
    from performance.targets.echomem.acceptance import capacity_seed, capacity_experiment
    from performance.targets.echomem.acceptance.semantic_corpus import build_locomo_session_corpus
    spec = SimpleNamespace(tenant_id="unit-tenant", auth_key="unit-secret", user_id="unit-user",
                           account_id="unit-account", agent_id="unit-agent")
    client = SimpleNamespace(**vars(spec))
    if not identity_matches:
        client.agent_id = "different-agent"
    corpus = build_locomo_session_corpus("unit-cache")
    corpus["documents"] = corpus["documents"][:2]
    actor = capacity_seed.CapacityActor(7, 0, client, corpus)
    monkeypatch.setattr(runner, "load_tenant_specs", lambda *a, **k: [spec])
    monkeypatch.setattr(capacity_experiment, "_load_actors", lambda *a: ([actor], {}))
    monkeypatch.setattr(capacity_seed, "prepare_actors", lambda *a, **k: pytest.fail("must not reseed"))
    checked = []

    def validate(actors, **kwargs):
        checked.extend(actors)
        return {"healthy_actors": 1, "actor_count": 1}

    monkeypatch.setattr(capacity_seed, "validate_cached_actors", validate)
    if not identity_matches:
        with pytest.raises(RuntimeError, match="match each configured identity"):
            runner._prepare_semantic_seed("http://unused.invalid", "unused", 1, 1, 1, reuse_seed="unused")
        assert not checked
        return
    _, summary = runner._prepare_semantic_seed("http://unused.invalid", "unused", 1, 1, 1, reuse_seed="unused")
    assert checked[0].tenant_index == 0
    assert summary["seed_source"] == "validated-cache"
    assert summary["seed_documents_per_tenant"] == 2
    assert "unit-secret" not in str(summary)
