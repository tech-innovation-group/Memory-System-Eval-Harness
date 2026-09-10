"""Consume logs on the server and persist only whitelisted diagnostic fields."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from performance.targets.echomem.probes.failure_evidence import failure_evidence, public_label, reference
from scripts.module_timing_stats import TimingStats


def collect(lines, scene_rows=None):
    counts = Counter()
    samples = []
    timings = TimingStats()
    scene_rows = scene_rows or {}
    memberships = {}
    for name, rows in scene_rows.items():
        for row in rows:
            if row.get('request_ref'):
                memberships.setdefault(row['request_ref'], set()).add(name)
    per_scene = {name: TimingStats() for name in scene_rows}
    matched = {name: set() for name in scene_rows}
    scene_events = {name: Counter() for name in scene_rows}
    slow = {name: {r['request_ref']: {'client_elapsed_ms': r.get('elapsed_ms'),
                'http_status': r.get('http_status'), 'events': []}
            for r in sorted(rows, key=lambda x: x.get('elapsed_ms') or 0, reverse=True)[:20]
            if r.get('request_ref')} for name, rows in scene_rows.items()}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        event = public_label(row.get("event")) or "UNKNOWN"
        counts[event] += 1
        # Aggregate the whole stream before the detailed-event retention cap.
        timings.add(row)
        request_ref = reference(row.get('request_id'))
        owners = memberships.get(request_ref, set())
        if len(owners) == 1:
            name = next(iter(owners))
            per_scene[name].add(row)
            matched[name].add(request_ref)
            scene_events[name][event] += 1
            if request_ref in slow[name]:
                entry = {'event': event, 'stage': public_label(row.get('stage')),
                         'engine': public_label(row.get('engine_id') or row.get('engine'))}
                for key in ('duration_ms', 'queue_wait_ms', 'status_code'):
                    if isinstance(row.get(key), (int,float)):
                        entry[key] = row[key]
                slow[name][request_ref]['events'].append(entry)
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
    return {"events": dict(counts), "samples": samples, "sample_cap": 10000,
            "module_timings": timings.export(), "module_timing_scope": "full_input_log_stream",
            "scene_timings": {name: {"groups": stats.export(), "events": dict(scene_events[name]),
                "client_requests": len(scene_rows[name]),
                "requests_with_reference": sum(bool(r.get('request_ref')) for r in scene_rows[name]),
                "matched_requests": len(matched[name]), "slow_requests": slow[name]} for name, stats in per_scene.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scene-root", type=Path)
    args = parser.parse_args()
    scenes = {p.stem: json.loads(p.read_text()) for p in args.scene_root.glob('*-samples.json')} if args.scene_root else {}
    args.out.write_text(json.dumps(collect(sys.stdin, scenes), ensure_ascii=False, indent=2))
