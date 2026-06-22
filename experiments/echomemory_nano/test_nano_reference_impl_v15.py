from __future__ import annotations

from nano_reference_impl_v15 import build_demo_memory


def test_reference_v15_smoke() -> None:
    mem = build_demo_memory()
    assert mem.readiness.qa_ready is True
    assert mem.retrieve("When did Maya start the visa paperwork?", "2026-03-20")["answer"] == "2026-03-02"
    assert "Rua Augusta 14" in mem.retrieve("What was shown in the lease screenshot?", "2026-03-20")["answer"]
    assert mem.retrieve("What does Nora prefer?", "2026-04-10")["answer"] == "coffee"
    assert mem.retrieve("What is Kai's badge number?", "2026-04-14")["answer"] == "unknown_conflict"
