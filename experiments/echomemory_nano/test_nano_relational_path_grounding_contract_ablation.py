from __future__ import annotations

import json

from nano_relational_path_grounding_contract_ablation import coverage


def test_path_grounding_is_required_for_relational_contract() -> None:
    graph_seed = {
        "source": "graph://entity/Alice",
        "layer": "graph",
        "content": "Alice and Bob are both linked to Orchard Labs",
        "path_edge_ids": [],
    }
    fact_hit = {
        "source": "atom://orchard_fact",
        "layer": "fact",
        "content": "Alice worked at Orchard Labs in 2026.",
        "path_edge_ids": [],
    }
    grounded_path = {
        "source": "graph://relation/alice-orchard-bob",
        "layer": "graph",
        "content": "Alice knew Bob because both worked on the Orchard Labs launch team.",
        "path_edge_ids": ["entity:Alice->company:Orchard", "company:Orchard->entity:Bob"],
    }

    graph_fact_only = coverage(["graph", "fact"], [graph_seed, fact_hit])
    relational_contract = coverage(["graph", "fact", "path_grounding"], [graph_seed, fact_hit])
    grounded_contract = coverage(
        ["graph", "fact", "path_grounding"],
        [graph_seed, fact_hit, grounded_path],
    )

    assert graph_fact_only["contract_ok"] is True
    assert graph_fact_only["has_path_grounding"] is False

    assert relational_contract["contract_ok"] is False
    assert relational_contract["missing"] == ["path_grounding"]

    assert grounded_contract["contract_ok"] is True
    assert grounded_contract["has_path_grounding"] is True
