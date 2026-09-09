"""Bounded open-loop M1 load; reuse EchoMemHTTP for the actual protocol."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import random
import threading
import time
import uuid

from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval
from performance.targets.echomem.acceptance.stage_observability import response_trace_ref
from performance.targets.echomem.probes._client import extract_archive, status_from


LOAD_MODES = {"search", "commit", "mixed", "hotspot"}


def arrival_plan(
    identities: int,
    duration_s: float,
    q: float,
    mixed: bool = False,
    *,
    seed: int = 42,
    load_mode: str | None = None,
    commit_interval_s: float = 30.0,
    hotspot_multiplier: float = 8.0,
) -> list[tuple]:
    if identities < 1 or duration_s <= 0 or q <= 0 or not all(math.isfinite(v) for v in (duration_s, q)):
        raise ValueError("Positive finite identities, duration and per-user rate required")
    legacy_mixed = load_mode is None and mixed
    mode = load_mode or ("mixed" if mixed else "search")
    if mode not in LOAD_MODES:
        raise ValueError(f"load_mode must be one of {sorted(LOAD_MODES)}")
    if commit_interval_s <= 0 or not math.isfinite(commit_interval_s):
        raise ValueError("commit_interval_s must be positive and finite")
    if hotspot_multiplier < 1 or not math.isfinite(hotspot_multiplier):
        raise ValueError("hotspot_multiplier must be finite and >= 1")
    include_search = mode in {"search", "mixed", "hotspot"}
    include_commit = mode in {"commit", "mixed", "hotspot"}
    events = []
    for actor in range(identities):
        # Seeded independent Poisson streams keep runs reproducible without
        # synchronising every actor on the same fixed tick.
        rate = q * (hotspot_multiplier if mode == "hotspot" and actor == 0 else 1.0)
        if include_search:
            rng = random.Random(f"{seed}:read:{actor}")
            at, seq = rng.expovariate(rate), 0
            while at < duration_s:
                events.append((at, "read", actor, seq))
                seq += 1
                at += rng.expovariate(rate)
        if include_commit:
            if legacy_mixed:
                events.append((actor / identities, "add", actor, 0))
                rng = random.Random(f"{seed}:add:{actor}")
                at, seq = rng.expovariate(1 / 30), 1
                while at < duration_s:
                    events.append((at, "add", actor, seq))
                    seq += 1
                    at += rng.expovariate(1 / 30)
                for seq in range(math.ceil(duration_s / 300)):
                    at = 30 + actor / identities * 30 + seq * 300
                    if at < duration_s:
                        events.append((at, "commit_submit", actor, seq))
                continue
            # Pair every planned Commit with a preceding real message. The
            # small offset keeps the two operations in independent pools while
            # preserving a stable, auditable arrival schedule.
            offset = actor / identities * min(1.0, commit_interval_s / 4)
            sequence = 0
            at = offset
            while at < duration_s:
                events.append((at, "add", actor, sequence))
                commit_at = at + min(0.5, commit_interval_s / 4)
                if commit_at < duration_s:
                    events.append((commit_at, "commit_submit", actor, sequence))
                sequence += 1
                at += commit_interval_s
    return sorted(e for e in events if e[0] < duration_s)


def query_for(actor, sequence: int, mixed: bool) -> dict:
    kind = "no_recall_queries" if mixed and sequence % 10 >= 7 else "recall_queries"
    pool = actor.corpus[kind]
    index = sequence
    if mixed:
        index = sequence // 10 * 3 + sequence % 10 - 7 if kind == "no_recall_queries" else sequence // 10 * 7 + sequence % 10
    return pool[index % len(pool)]


def measure(actors: list, *, duration_s: float, q: float = 1, mixed: bool = False,
            commit_timeout_s: float = 180, request_timeout_s: float = 60,
            seed: int = 42, load_mode: str | None = None,
            commit_interval_s: float = 30.0,
            hotspot_multiplier: float = 8.0, isolate_read_workers: bool = False,
            search_workers: int | None = None) -> dict:
    if search_workers is not None and (isinstance(search_workers, bool) or not isinstance(search_workers, int) or search_workers < 1):
        raise ValueError("search_workers must be a positive integer")
    if isolate_read_workers and search_workers is not None and search_workers < len(actors):
        raise ValueError("isolated read workers require at least one worker per identity")
    mode = load_mode or ("mixed" if mixed else "search")
    plan = arrival_plan(
        len(actors), duration_s, q, mixed, seed=seed, load_mode=mode,
        commit_interval_s=commit_interval_s,
        hotspot_multiplier=hotspot_multiplier,
    )
    rows = []
    receipts = []
    window_id = uuid.uuid4().hex
    lock = threading.Lock()
    started = time.monotonic()
    end = started + duration_s
    dirty = [0] * len(actors)
    widths = {"read": search_workers if search_workers is not None else min(512, max(16, len(actors) * 8)), "add": min(32, max(4, len(actors))),
              "commit_submit": min(32, max(4, len(actors)))}
    if isolate_read_workers:
        per_identity = max(1, widths.pop("read") // len(actors))
        widths.update({f"read:{i}": per_identity for i in range(len(actors))})

    def pool_key(op, index):
        return f"read:{index}" if isolate_read_workers and op == "read" else op
    pools = {name: ThreadPoolExecutor(max_workers=width) for name, width in widths.items()}
    slots = {name: threading.BoundedSemaphore(width) for name, width in widths.items()}
    polls = ThreadPoolExecutor(max_workers=min(512, max(4, len(actors))))

    def append(row):
        with lock:
            rows.append(row)

    def poll(actor, index, sid, archive, accepted_at, deadline):
        count = 0
        observed_trace = ""
        while True:
            poll_at = time.monotonic()
            if poll_at >= deadline:
                terminal, code = "timeout", None
                break
            try:
                result = actor.client.commit_status(sid, archive)
            except Exception as exc:
                append({"op": "commit_poll", "identity_index": index,
                        "tenant_index": actor.tenant_index, "user_index": actor.user_index,
                        "start_s": poll_at - started, "end_s": time.monotonic() - started,
                        "http_status": None, "status": "transport_error", "error": type(exc).__name__})
                count += 1
                time.sleep(min(1, max(0, deadline - time.monotonic())))
                continue
            count += 1
            code = result.status_code
            terminal = status_from(result.payload)
            observed_trace = response_trace_ref(result.payload) or observed_trace
            append({"op": "commit_poll", "identity_index": index,
                    "tenant_index": actor.tenant_index, "user_index": actor.user_index,
                    "start_s": poll_at - started, "end_s": time.monotonic() - started,
                    "http_status": code, "status": terminal, "trace_ref": observed_trace})
            if terminal in ("completed", "failed", "error") and code == 200:
                break
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        append({"op": "commit_done", "identity_index": index, "tenant_index": actor.tenant_index,
                "user_index": actor.user_index, "accepted_at_s": accepted_at - started,
                "end_s": time.monotonic() - started, "elapsed_s": time.monotonic() - accepted_at,
                "status": terminal, "http_status": code, "polls": count, "trace_ref": observed_trace,
                "success": terminal == "completed" and time.monotonic() <= deadline})

    def execute(event):
        scheduled, op, index, sequence = event
        actor = actors[index]
        begin = time.monotonic()
        record = {"op": op, "identity_index": index, "tenant_index": actor.tenant_index,
                  "user_index": actor.user_index, "sequence": sequence, "scheduled_s": scheduled,
                  "start_s": begin - started, "generator_lag_s": max(0, begin - started - scheduled)}
        if op == "read":
            sample = query_for(actor, sequence, mode in {"mixed", "hotspot"})
            record.update(query_type=sample["query_type"], query_id=sample["id"])
        try:
            if begin >= end:
                record.update(sent=False, success=False, error="missed_window")
                return
            record["sent"] = True
            if op == "read":
                result = actor.client.request("POST", "/api/retrieval/search", {
                    "query": sample["query"], "agent_id": actor.client.agent_id,
                    "session_id": "", "limit": 10, "include_explain": True,
                    "include_debug": True,
                }, timeout_s=request_timeout_s, operation="search")
                quality = assess_retrieval(result.payload, sample)
                record.update(quality)
                record["success"] = result.status_code == 200 and quality["quality_ok"]
            elif op == "add":
                result = actor.client.add_message(actor.write_session, f"live-{sequence}",
                    f"我刚完成了第{sequence}轮资料整理，并记录下一轮的待办事项。" * 15)
                record["success"] = result.status_code in (200, 201)
                if record["success"]:
                    with lock:
                        dirty[index] += 1
            else:
                with lock:
                    pending_messages = dirty[index]
                if not pending_messages:
                    record.update(sent=False, success=False, error="no_new_messages")
                    return
                result = actor.client.commit(actor.write_session,
                                              idempotency_key=f"capacity-{window_id}-{index}-{sequence}")
                archive = extract_archive(result.payload)
                accepted_at = time.monotonic()
                record["success"] = result.status_code == 202 and bool(archive)
                record["accepted_202"] = record["success"]
                if record["success"]:
                    with lock:
                        dirty[index] = max(0, dirty[index] - pending_messages)
                    record["accepted_at_s"] = accepted_at - started
                    with lock:
                        receipts.append({"identity_index": index, "session_id": actor.write_session,
                                         "archive_id": archive, "accepted_at_s": accepted_at - started})
                    polls.submit(poll, actor, index, actor.write_session, archive, accepted_at,
                                 accepted_at + commit_timeout_s)
            record["http_status"] = result.status_code
            record["trace_ref"] = response_trace_ref(result.payload)
            if result.reason_code:
                record["reason_code"] = result.reason_code
            if result.status_code is None:
                error_type = getattr(result, "transport_error_type", "")
                if not error_type and getattr(result, "error", ""):
                    error_type = str(result.error).split(":", 1)[0]
                record["transport_error_type"] = error_type or "unknown_transport_error"
            record["elapsed_s"] = time.monotonic() - begin
            timeout = request_timeout_s if op == "read" else actor.client.timeout_s
            record["timeout_censored"] = result.status_code is None and record["elapsed_s"] >= timeout
        except Exception as exc:
            record.update(success=False, error=type(exc).__name__,
                          transport_error_type=type(exc).__name__,
                          elapsed_s=time.monotonic() - begin)
        finally:
            record["end_s"] = time.monotonic() - started
            append(record)
            slots[pool_key(op, index)].release()

    try:
        for event in plan:
            at, op, index, sequence = event
            delay = started + at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            key = pool_key(op, index)
            if slots[key].acquire(blocking=False):
                pools[key].submit(execute, event)
            else:
                missed = {"op": op, "identity_index": index, "tenant_index": actors[index].tenant_index,
                        "user_index": actors[index].user_index, "sequence": sequence,
                        "scheduled_s": at, "sent": False, "success": False, "error": "generator_saturated"}
                if op == "read":
                    sample = query_for(
                        actors[index], sequence, mode in {"mixed", "hotspot"}
                    )
                    missed.update(query_type=sample["query_type"], query_id=sample["id"])
                append(missed)
        remaining = end - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
    finally:
        for pool in pools.values():
            pool.shutdown(wait=True)
        polls.shutdown(wait=True)
    return {"rows": sorted(rows, key=lambda r: r.get("scheduled_s", r.get("start_s", r.get("end_s", 0)))),
            "planned_search": sum(event[1] == "read" for event in plan),
            "planned_add": sum(event[1] == "add" for event in plan),
            "planned_commit": sum(event[1] == "commit_submit" for event in plan),
            "started_at_monotonic_s": started,
            "duration_s": duration_s, "elapsed_with_drain_s": time.monotonic() - started,
            "pools": {**widths, "commit_poll": polls._max_workers},
            "read_worker_isolation": "per_identity" if isolate_read_workers else "shared",
            "per_user_search_rps": q if mode != "commit" else 0,
            "mixed": mode in {"mixed", "hotspot"},
            "load_mode": mode,
            "hotspot_identity_index": 0 if mode == "hotspot" else None,
            "hotspot_multiplier": hotspot_multiplier if mode == "hotspot" else None,
            "per_user_message_rate_per_min": 60 / commit_interval_s if mode != "search" else 0,
            "per_user_commit_interval_s": commit_interval_s if mode != "search" else None,
            "recall_query_fraction": .7 if mode in {"mixed", "hotspot"} else 1.0,
            "request_timeout_s": request_timeout_s, "commit_deadline_s": commit_timeout_s,
            "arrival_process": "independent-seeded-poisson", "arrival_seed": seed,
            "commit_receipts": receipts,
            "identity_count": len(actors), "tenant_count": len({a.tenant_index for a in actors})}
