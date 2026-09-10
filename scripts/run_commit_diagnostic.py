"""Fail-fast real-provider and Commit diagnostic before a bounded 16-way rerun."""
from __future__ import annotations

import json
from pathlib import Path

from performance.probe import run_configured_probe
from performance.targets.echomem.acceptance.preflight import run_preflight
from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval
from performance.targets.echomem.probes._client import EchoMemHTTP, load_tenant_specs
from performance.targets.echomem.probes.concurrency_topology import _commit_call
from performance.targets.echomem.probes.failure_evidence import failure_evidence


def save(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def diagnose(out: Path, base: str):
    options_file = out / "diagnostic-options.json"
    options = json.loads(options_file.read_text()) if options_file.exists() else {}
    commit_chars = int(options.get("commit_chars", 65536))
    if not 1 <= commit_chars <= 1048576:
        raise ValueError("commit_chars must be between 1 and 1048576")
    preflight = run_preflight(out / "config.json", timeout_s=40, retry_attempts=1,
                              required_kinds=("llm", "embedding"))
    for entry in preflight.get("engines", []):
        entry["failure_evidence"] = entry.get("failure_evidence") or failure_evidence({"error": entry.get("error")})
        entry["error"] = ",".join(entry["failure_evidence"]["categories"] + entry["failure_evidence"]["provider_codes"]) or ("UNCLASSIFIED" if entry.get("error") else "")
    preflight["error"] = "MODEL_PREFLIGHT_FAILED" if not preflight.get("ok") else ""
    save(out / "model-preflight.json", preflight)
    result = {"status": "RUNNING", "stage": "model-preflight", "burst_started": False,
              "smoke": [], "serial_large": [], "seeds": [], "commit_chars": commit_chars}
    save(out / "diagnostic.json", result)
    if not preflight.get("ok"):
        result.update(status="BLOCKED", reason="MODEL_PREFLIGHT_FAILED")
        save(out / "diagnostic.json", result)
        print("diagnostic BLOCKED: model preflight failed", flush=True)
        return
    tenants = load_tenant_specs(out / "tenants.json")[:4]
    clients = [EchoMemHTTP(base, t.auth_key, timeout_s=20, tenant_id=t.tenant_id,
                          user_id=t.user_id, account_id=t.account_id, agent_id=t.agent_id) for t in tenants]
    fact = "My project review is on September 18 at 10 AM in meeting room Cedar. "
    sample = {"id": "room", "query_type": "recall", "query": "Which room is my project review meeting in?", "aliases": ["Cedar"]}
    try:
        result["stage"] = "small-commit-and-recall"
        for t, client in zip(tenants, clients):
            session, _ = client.open_session(t.tenant_id, "diagnostic-seed")
            row = _commit_call(client, t.tenant_id, session, fact, 90)()
            result["smoke"].append(row)
            search = client.search(session, sample["query"], 20)
            result["seeds"].append({"tenant_id": t.tenant_id, "http_status": search.status_code,
                "quality": assess_retrieval(search.payload, sample), "failure_evidence": failure_evidence(search.payload)})
            save(out / "diagnostic.json", result)
            if row.get("terminal_state") != "completed":
                result.update(status="BLOCKED", reason="SMALL_COMMIT_NOT_COMPLETED")
                save(out / "diagnostic.json", result)
                return
        result["stage"] = "serial-large-commit"
        large = (fact * (commit_chars // len(fact) + 1))[:commit_chars]
        for t, client in zip(tenants[1::2], clients[1::2]):
            session, _ = client.open_session(t.tenant_id, "diagnostic-large-serial")
            row = _commit_call(client, t.tenant_id, session, large, 90)()
            result["serial_large"].append(row)
            save(out / "diagnostic.json", result)
            if row.get("terminal_state") != "completed":
                result.update(status="BLOCKED", reason="SERIAL_LARGE_COMMIT_NOT_COMPLETED")
                save(out / "diagnostic.json", result)
                return
        result["seed_gate"] = "expected fact present; all degradation flags retained, not a healthy Search baseline"
        if not all(s["http_status"] == 200 and s["quality"]["matched_expected_fact"] for s in result["seeds"]):
            result.update(status="BLOCKED", reason="SEED_FACT_NOT_FOUND")
            save(out / "diagnostic.json", result)
            return
        result.update(stage="16-way-mixed", burst_started=True)
        save(out / "diagnostic.json", result)
        params = {"tenant_config": str(out / "tenants.json"), "levels": [16],
                  "topologies": ["heterogeneous-users"], "requests_per_level": 32,
                  "large_commit_chars": commit_chars, "commit_poll_timeout_s": 90,
                  "timeout_s": 20, "stop_after_boundary": True,
                  "queries": {t.tenant_id: sample for t in tenants}}
        payload, execution = run_configured_probe(params, probes_dir=Path(__file__).resolve().parents[1] / "performance/targets/echomem/probes",
            scene="concurrency_topology.py", output=out / "concurrency-topology.json", base_url=base, timeout_s=900)
        save(out / "execution.json", execution)
        result.update(status=payload.get("status", "UNKNOWN"), stage="finished")
    except Exception as exc:
        result.update(status="ERROR", exception_type=type(exc).__name__, failure_evidence=failure_evidence({"error": str(exc)}))
    save(out / "diagnostic.json", result)
    print("diagnostic", result["status"], result["stage"], flush=True)
