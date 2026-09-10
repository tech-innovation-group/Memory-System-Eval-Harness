"""Consume logs on the server and persist only whitelisted diagnostic fields."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from performance.targets.echomem.probes.failure_evidence import failure_evidence, public_label, reference


def collect(lines):
    counts = Counter()
    samples = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        event = public_label(row.get("event")) or "UNKNOWN"
        counts[event] += 1
        if row.get("level") not in ("WARNING", "ERROR") and not any(word in event for word in ("commit", "extraction", "atomic", "recall", "provider")):
            continue
        if len(samples) >= 10000:
            continue
        entry = {"event": event, "level": public_label(row.get("level")),
                 "evidence": failure_evidence(row), "commit_ref": reference(row.get("commit_id")),
                 "archive_id_ref": reference(row.get("archive_id")),
                 "archive_ref": reference(str(row.get("tenant_id", "")) + ":" + str(row.get("session_id", "")) + ":" + str(row.get("archive_id", ""))) if row.get("archive_id") else ""}
        for key in ("engine_id", "engine", "model", "model_alias", "call_site", "logger", "logger_name"):
            if public_label(row.get(key)):
                entry[key] = public_label(row[key])
        message = str(row.get("message") or row.get("msg") or "")
        entry["message_class"] = next((label for phrase, label in (
            ("Atomic extraction LLM call", "atomic_extraction_llm"),
            ("Overview generation failed", "base_overview_llm"),
            ("Abstract generation failed", "base_abstract_llm"),
            ("legacy Engine state requires adoption", "legacy_state_adoption"),
        ) if phrase in message), "")
        for key in ("duration_ms", "queue_wait_ms", "status_code"):
            if isinstance(row.get(key), (int, float)):
                entry[key] = row[key]
        samples.append(entry)
    return {"events": dict(counts), "samples": samples, "sample_cap": 10000}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.write_text(json.dumps(collect(sys.stdin), ensure_ascii=False, indent=2))
