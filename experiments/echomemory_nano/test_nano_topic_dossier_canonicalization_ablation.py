from __future__ import annotations

from nano_topic_dossier_canonicalization_ablation import build_demo


def test_canonicalization_beats_naive_no_hint() -> None:
    bench, cases = build_demo()
    explicit = bench.score_mode("explicit_hint", cases)
    naive = bench.score_mode("naive_no_hint", cases)
    canonical = bench.score_mode("canonicalized_no_hint", cases)

    assert explicit["correct"] >= canonical["correct"] >= naive["correct"]
    assert canonical["purity"] >= naive["purity"]
    assert canonical["cluster_count"] <= naive["cluster_count"]
    assert explicit["correct"] == len(cases)
    assert canonical["correct"] == len(cases)
