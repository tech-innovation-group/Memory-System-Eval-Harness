"""Corpus/quality unit contracts, not real-model performance measurements."""

from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval, build_corpus


def test_corpus_has_fixed_facts_paraphrases_and_non_recall_queries():
    corpus = build_corpus("unit-user")
    assert len(corpus["documents"]) == 5
    assert all(300 <= len(d) <= 500 for d in corpus["documents"])
    assert len(corpus["facts"]) == 20
    assert len(corpus["recall_queries"]) == 40
    assert len(corpus["no_recall_queries"]) == 20
    for query in corpus["recall_queries"]:
        assert all(a not in query["query"] for a in query["aliases"])
        assert "PERFANCHOR" not in query["query"]


def test_corpus_reproducible_and_ten_times_history_keeps_same_test_questions():
    base = build_corpus("unit-a")
    assert base == build_corpus("unit-a")
    assert base["fingerprint"] != build_corpus("unit-b")["fingerprint"]
    large = build_corpus("unit-a", memory_scale=10)
    assert len(large["documents"]) == 50
    assert base["recall_queries"] == large["recall_queries"]


def test_semantic_assertions_ignore_echo_and_metadata():
    sample = build_corpus("unit-a")["recall_queries"][0]
    answer = sample["aliases"][0]
    assert not assess_retrieval({"items": [], "debug": answer}, sample)["quality_ok"]
    assert not assess_retrieval({"items": [{"query": answer, "id": answer}]}, sample)["quality_ok"]
    assert assess_retrieval({"items": [{"content": answer}]}, sample)["quality_ok"]
    assert not assess_retrieval({"items": [{"content": answer}], "status": "degraded"}, sample)["quality_ok"]


def test_no_recall_requires_valid_empty_items():
    sample = build_corpus("unit-a")["no_recall_queries"][0]
    assert assess_retrieval({"items": []}, sample)["quality_ok"]
    assert not assess_retrieval({}, sample)["quality_ok"]
    assert not assess_retrieval({"items": [{"content": "unrelated"}]}, sample)["quality_ok"]
