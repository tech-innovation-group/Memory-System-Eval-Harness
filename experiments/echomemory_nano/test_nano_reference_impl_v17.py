from __future__ import annotations

from nano_reference_impl_v17 import build_demo_memory_v17


def test_reference_v17_end_to_end() -> None:
    mem = build_demo_memory_v17()
    assert mem.readiness.qa_ready is True
    assert mem.retrieve("When did Maya start the visa paperwork?", "2026-03-20")["answer"] == "2026-03-02"
    assert "Rua Augusta 14" in mem.retrieve("What address was shown in the lease screenshot?", "2026-03-20")["answer"]
    assert "2026-04-10" in mem.retrieve("What is Maya's latest preference now?", "2026-04-10")["answer"]
    assert mem.retrieve("What is Kai's badge number?", "2026-04-14")["answer"] == "unknown_conflict"
    assert mem.retrieve("Can the system answer now?", "2026-04-14")["answer"] == "ready"


def test_reference_v17_contract_driven_second_pass() -> None:
    mem = build_demo_memory_v17()
    result = mem.retrieve("When did Maya start the visa paperwork?", "2026-03-20")
    assert result["contract_ok"] is True
    assert "atom" in result["second_pass_sources"]
    assert "event_time" in result["present_layers"]


def test_reference_v17_dossier_and_visual_layers_exist() -> None:
    mem = build_demo_memory_v17()
    assert "visa_process" in mem.dossiers
    assert any(node.node_type == "image_evidence" for node in mem.nodes.values())
