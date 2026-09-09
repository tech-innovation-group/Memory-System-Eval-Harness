"""Corpus/quality unit contracts, not real-model performance measurements."""

from performance.targets.echomem.acceptance.semantic_corpus import (
    assess_retrieval,
    build_corpus,
    build_locomo_session_corpus,
)


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


def test_intent_rejection_is_not_reported_as_retrieval_miss():
    sample = build_corpus("unit-a")["recall_queries"][0]
    result = assess_retrieval({"items": [], "explain": {
        "outcome": "no_recall", "final_verdicts": {"intent": "reject"}}}, sample)
    assert result["intent_rejected"] is True
    assert result["search_executed"] is False
    assert result["matched_expected_fact"] is False
    assert result["quality_ok"] is False


def test_locomo_single_session_uses_real_questions_and_local_evidence_only():
    corpus = build_locomo_session_corpus("tenant-a")
    assert corpus["source"] == {
        "kind": "locomo-single-session", "sample_id": "conv-30",
        "session_key": "session_1", "session_messages": 28,
        "eligible_questions": 12,
    }
    assert len(corpus["documents"]) == 28
    assert len(corpus["recall_queries"]) == 12
    assert all("LOCOMO-EVIDENCE" not in row["query"] for row in corpus["recall_queries"])
    assert all(row["evidence_ids"] and all(value.startswith("D1:") for value in row["evidence_ids"])
               for row in corpus["recall_queries"])
    assert all(row["aliases"] for row in corpus["recall_queries"])


def test_locomo_evidence_markers_are_tenant_specific_without_changing_questions():
    first = build_locomo_session_corpus("tenant-a")
    second = build_locomo_session_corpus("tenant-b")
    assert [row["query"] for row in first["recall_queries"]] == [
        row["query"] for row in second["recall_queries"]]
    assert first["recall_queries"][0]["aliases"] != second["recall_queries"][0]["aliases"]


def test_locomo_multi_evidence_question_requires_every_source_memory():
    corpus = build_locomo_session_corpus("tenant-a")
    sample = next(row for row in corpus["recall_queries"] if len(row["aliases"]) > 1)
    partial = assess_retrieval({"items": [{"content": sample["aliases"][0]}]}, sample)
    complete = assess_retrieval({"items": [{"content": " ".join(sample["aliases"])}]}, sample)
    assert partial["quality_ok"] is False
    assert partial["matched_evidence_count"] == 1
    assert complete["quality_ok"] is True
    assert complete["matched_evidence_count"] == len(sample["aliases"])
