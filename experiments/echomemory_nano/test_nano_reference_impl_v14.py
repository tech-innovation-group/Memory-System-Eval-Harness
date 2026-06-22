from __future__ import annotations

from nano_reference_impl_v14 import build_demo_memory


def test_reference_v14_smoke() -> None:
    mem = build_demo_memory()
    assert mem.readiness.qa_ready is True
    assert mem.retrieve("When did Maya start the visa paperwork?", "2026-03-20")["answer"] == "2026-03-02"
    assert mem.retrieve("Who helped Maya with the visa paperwork?", "2026-03-20")["answer"] in {"Maya, Nora", "Nora, Maya", "Nora Maya", "Nora"}
    assert mem.retrieve("How did the apartment lease situation evolve?", "2026-03-20")["contract_ok"] is True
    assert mem.retrieve("What was shown in the lease screenshot?", "2026-03-20")["answer"]
    assert mem.retrieve("Can you answer now?", "2026-03-20")["answer"] == "ready"
