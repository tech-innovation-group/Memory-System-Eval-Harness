"""Fresh, publicly provisioned T x U identities with real semantic memories."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import math
import time
import uuid

from performance.targets.echomem.acceptance.semantic_corpus import (
    DEFAULT_LOCOMO_DATASET,
    assess_retrieval,
    build_corpus,
    build_fixed_fact_corpus,
    build_fixed_tenant_data_corpus,
    build_locomo_fragment_corpus,
    build_locomo_single_sentence_corpus,
    build_locomo_session_corpus,
)
from performance.targets.echomem.probes._client import EchoMemHTTP, extract_archive, status_from


@dataclass
class CapacityActor:
    tenant_index: int
    user_index: int
    client: EchoMemHTTP
    corpus: dict
    write_session: str = ""


def _result_body(payload: object) -> dict:
    """Unwrap EchoMem responses that put the useful body under a wrapper."""
    if not isinstance(payload, dict):
        return {}
    for wrapper in ("result", "summary"):
        nested = payload.get(wrapper)
        if isinstance(nested, dict):
            return nested
    return payload


def _reduce_locomo_corpus(corpus: dict, max_questions: int | None) -> dict:
    """Keep only evidence documents needed by a small real QA subset."""
    if not max_questions or max_questions >= len(corpus.get("recall_queries", [])):
        return corpus
    queries = list(corpus["recall_queries"][:max_questions])
    fact_ids = {str(query.get("fact_id")) for query in queries}
    aliases = {alias for query in queries for alias in query.get("aliases", [])}
    documents = [
        document for document in corpus.get("documents", [])
        if any(alias in document for alias in aliases)
    ]
    facts = [fact for fact in corpus.get("facts", []) if str(fact.get("id")) in fact_ids]
    reduced = {**corpus, "documents": documents, "facts": facts,
               "recall_queries": queries}
    reduced["input_characters"] = sum(map(len, documents))
    reduced["source"] = {**(corpus.get("source") or {}),
                          "reduced_questions": len(queries),
                          "reduced_documents": len(documents)}
    reduced["fingerprint"] = hashlib.sha256(
        json.dumps(reduced, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return reduced


def _limit_locomo_queries(corpus: dict, query_count: int | None) -> dict:
    """Keep the full session memory while fixing the Search QA sample."""
    if not query_count or query_count >= len(corpus.get("recall_queries", [])):
        return corpus
    if query_count < 1:
        raise ValueError("query_count must be positive")
    queries = list(corpus["recall_queries"][:query_count])
    limited = {**corpus, "recall_queries": queries}
    limited["source"] = {**(corpus.get("source") or {}),
                          "search_questions": len(queries),
                          "search_question_ids": [query["id"] for query in queries]}
    limited["fingerprint"] = hashlib.sha256(
        json.dumps(limited, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return limited


def validate_search_timeout(search_timeout_s: float) -> None:
    if isinstance(search_timeout_s, bool) or not isinstance(search_timeout_s, (int, float)) or not math.isfinite(search_timeout_s) or search_timeout_s <= 0:
        raise ValueError("seed search timeout must be finite and positive")


def semantic_checks(
    actor: CapacityActor,
    validation_queries: int,
    *,
    search_timeout_s: float = 60,
    validation_query_ids: list[str] | None = None,
):
    validate_search_timeout(search_timeout_s)
    available = actor.corpus["recall_queries"]
    if validation_query_ids:
        wanted = [str(query_id) for query_id in validation_query_ids]
        by_id = {str(sample.get("id")): sample for sample in available}
        missing = [query_id for query_id in wanted if query_id not in by_id]
        if missing:
            raise ValueError(f"validation query ids are not in the corpus: {missing[:3]}")
        selected = [by_id[query_id] for query_id in wanted]
    else:
        if not 1 <= validation_queries <= len(available):
            raise ValueError("validation_queries must be between 1 and the corpus size")
        selected = [available[index * len(available) // validation_queries]
                    for index in range(validation_queries)]
    if not selected:
        raise ValueError("validation_queries must be between 1 and the corpus size")
    for sample in selected:
        begin = time.monotonic()
        result = actor.client.request("POST", "/api/retrieval/search", {
            "query": sample["query"], "agent_id": actor.client.agent_id, "limit": 10,
            "include_debug": True, "include_explain": True,
        }, timeout_s=search_timeout_s, operation="search")
        check = assess_retrieval(result.payload, sample)
        # A latency run needs a real routed retrieval with non-empty results;
        # benchmark answer matching is diagnostic and must not gate the load.
        recall_hit = result.status_code == 200 and check.get("recall_served", False)
        yield {**check, "recall_hit": bool(recall_hit), "http_status": result.status_code,
               "elapsed_s": time.monotonic() - begin,
               "query": sample["query"], "expected_aliases": list(sample["aliases"]),
               "transport_error_type": result.transport_error_type,
               "request_timeout_s": search_timeout_s,
               "success": bool(recall_hit)}


def validate_cached_actors(
    actors: list[CapacityActor],
    validation_queries: int = 4,
    *,
    search_timeout_s: float = 60,
    validation_query_ids: list[str] | None = None,
) -> dict:
    """Revalidate existing facts through real Search; never submit another Commit."""
    validate_search_timeout(search_timeout_s)
    def validate(actor):
        row = {"tenant_index": actor.tenant_index, "user_index": actor.user_index,
               "queries": [], "status": "INCONCLUSIVE", "seed_source": "validated-cache"}
        try:
            for result in semantic_checks(
                actor,
                validation_queries,
                search_timeout_s=search_timeout_s,
                validation_query_ids=validation_query_ids,
            ):
                row["queries"].append(result)
            row["valid_semantic_queries"] = sum(q.get("recall_hit", False) for q in row["queries"])
            row["quality_ok_queries"] = sum(q.get("quality_ok", False) for q in row["queries"])
            expected = len(validation_query_ids) if validation_query_ids else validation_queries
            row["status"] = "PASS" if row["valid_semantic_queries"] == expected else "FAIL"
        except (RuntimeError, OSError, ValueError) as exc:
            row["error_class"] = type(exc).__name__
        return row

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(actors)))) as pool:
        rows = list(pool.map(validate, actors))
    return {"status": "PASS" if rows and all(r["status"] == "PASS" for r in rows) else "INCONCLUSIVE",
            "actors": rows, "actor_count": len(rows),
            "healthy_actors": sum(r["status"] == "PASS" for r in rows),
            "quality_healthy_actors": sum(
                bool(r["queries"]) and all(q.get("quality_ok", False) for q in r["queries"])
                for r in rows
            ),
            "raw_credentials_exported": False}


def provision_actors(base_url: str, tenants: int, users: int, *, memory_scale: int = 1,
                     seed: int = 42, tenant_offset: int = 0,
                     corpus_mode: str = "standard",
                     dataset_path: str | None = None,
                     sample_id: str = "conv-30",
                     session_key: str = "session_1",
                     sentence_id: str = "D1:2",
                     question_variant: int = 0,
                     repeat_count: int = 1,
                     session_keys: list[str] | None = None,
                     max_questions: int | None = None,
                     query_count: int | None = None,
                     fragment_seed_file: str | None = None) -> list[CapacityActor]:
    """Only for a dedicated test deployment with public bootstrap enabled."""
    actors = []
    run_tag = uuid.uuid4().hex[:12]
    public = EchoMemHTTP(base_url, timeout_s=20)
    for tenant_index in range(tenant_offset, tenant_offset + tenants):
        result = public.request("POST", "/api/auth/tenants", {"name": f"capacity-{run_tag}-{tenant_index}"})
        tenant = (result.payload.get("tenant") or {}).get("tenant_id")
        bootstrap = result.payload.get("bootstrap_key")
        if result.status_code != 200 or not tenant or not bootstrap:
            raise RuntimeError(f"tenant provisioning failed: HTTP {result.status_code}; bootstrap capability required")
        provision = EchoMemHTTP(base_url, bootstrap, timeout_s=20, auth_header="X-EchoMem-Bootstrap-Key")
        for user_index in range(users):
            result = provision.request("POST", f"/api/auth/tenants/{tenant}/users", {})
            user = (result.payload.get("user") or {}).get("user_id")
            if result.status_code != 200 or not user:
                raise RuntimeError(f"user provisioning failed: HTTP {result.status_code}")
            key = provision.request("POST", f"/api/auth/tenants/{tenant}/users/{user}/key", {})
            auth_key = key.payload.get("auth_key")
            if key.status_code != 200 or not auth_key:
                raise RuntimeError(f"credential provisioning failed: HTTP {key.status_code}")
            client = EchoMemHTTP(base_url, auth_key, timeout_s=20, tenant_id=tenant,
                                 user_id=user, account_id=tenant, agent_id=f"capacity-{run_tag}")
            identity = f"tenant-{tenant_index}/user-{user_index}"
            if corpus_mode == "fixed-tenant-data":
                if not dataset_path:
                    raise ValueError("fixed-tenant-data requires a seed data path")
                corpus = build_fixed_tenant_data_corpus(
                    identity,
                    seed_data_path=dataset_path,
                    fixture_index=tenant_index * users + user_index,
                )
            elif corpus_mode == "fixed-natural-fact":
                corpus = build_fixed_fact_corpus(identity)
            elif corpus_mode == "locomo-fragment-file":
                if not fragment_seed_file:
                    raise ValueError("locomo-fragment-file requires fragment_seed_file")
                corpus = build_locomo_fragment_corpus(
                    identity, seed_path=fragment_seed_file, tenant_index=tenant_index,
                )
            elif corpus_mode in {"locomo-single-session", "locomo-single-sentence"}:
                chosen_sample = sample_id
                chosen_session = session_key
                if session_keys:
                    token = session_keys[(tenant_index - tenant_offset) % len(session_keys)]
                    if "/" in token:
                        chosen_sample, chosen_session = token.split("/", 1)
                    else:
                        chosen_session = token
                if corpus_mode == "locomo-single-sentence":
                    corpus = build_locomo_single_sentence_corpus(
                        identity,
                        dataset_path=dataset_path or DEFAULT_LOCOMO_DATASET,
                        sample_id=chosen_sample,
                        session_key=chosen_session,
                        sentence_id=str(sentence_id or "D1:2"),
                        question_variant=question_variant + tenant_index,
                        repeat_count=repeat_count,
                    )
                else:
                    corpus = build_locomo_session_corpus(
                        identity,
                        dataset_path=dataset_path or DEFAULT_LOCOMO_DATASET,
                        sample_id=chosen_sample,
                        session_key=chosen_session,
                    )
                corpus = _reduce_locomo_corpus(corpus, max_questions)
                corpus["source"] = {**(corpus.get("source") or {}),
                                    "assigned_sample_id": chosen_sample,
                                    "assigned_session_key": chosen_session}
                corpus["fingerprint"] = hashlib.sha256(
                    json.dumps(corpus, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                corpus = _limit_locomo_queries(corpus, query_count)
            elif corpus_mode == "standard":
                corpus = build_corpus(identity, seed=seed, memory_scale=memory_scale)
            else:
                raise ValueError(
                    "corpus_mode must be standard, fixed-tenant-data, fixed-natural-fact, locomo-single-session or locomo-fragment-file"
                )
            actors.append(CapacityActor(tenant_index, user_index, client, corpus))
    return actors


def seed_actor(actor: CapacityActor, *, timeout_s: float = 180, checkpoint=None,
               validation_queries: int = 40, search_timeout_s: float = 60) -> dict:
    validate_search_timeout(search_timeout_s)
    client = actor.client
    available = actor.corpus["recall_queries"]
    if not 1 <= validation_queries <= len(available):
        raise ValueError("validation_queries must be between 1 and the corpus size")
    selected = [available[i * len(available) // validation_queries] for i in range(validation_queries)]
    row = {"tenant_index": actor.tenant_index, "user_index": actor.user_index,
           "input_characters": actor.corpus["input_characters"],
           "input_documents": len(actor.corpus["documents"]),
           "expected_facts": len(actor.corpus["facts"]), "semantic_queries": len(selected),
           "available_semantic_queries": len(available),
           "corpus_fingerprint": actor.corpus["fingerprint"], "queries": [],
           "corpus_source": actor.corpus.get("source"),
           "memory_count": None, "index_size_bytes": None, "status": "INCONCLUSIVE"}
    marker = "PERFANCHOR-capacity-" + uuid.uuid4().hex
    started = time.monotonic()
    phase = "open-session"

    def save():
        row["elapsed_s"] = time.monotonic() - started
        if checkpoint:
            checkpoint(row)

    try:
        sid, _ = client.open_session(client.tenant_id, "capacity-semantic-seed", retry_rate_limit=False)
        phase = "add-messages"
        for index, document in enumerate(actor.corpus["documents"]):
            if index == 0:
                document += f" 这份记录的编号是{marker}。"
            result = client.add_message(sid, f"seed-{index}", document)
            if result.status_code not in (200, 201):
                raise RuntimeError(f"seed message rejected: HTTP {result.status_code}")
        phase = "commit-submit"
        accepted = client.commit(sid)
        archive = extract_archive(accepted.payload)
        row["commit_http_status"] = accepted.status_code
        if accepted.status_code not in (200, 202) or not archive:
            raise RuntimeError(f"seed commit not accepted: HTTP {accepted.status_code}")
        phase = "commit-poll"
        deadline = time.monotonic() + timeout_s
        while True:
            status = client.commit_status(sid, archive)
            terminal = status_from(status.payload)
            row["commit_state"] = terminal
            save()
            if terminal == "completed" and status.status_code == 200:
                break
            if terminal in ("failed", "error") or time.monotonic() >= deadline:
                raise RuntimeError("seed commit failed or terminal deadline exceeded")
            time.sleep(1)
        # Once the asynchronous Commit is complete, the actor is safe to use
        # for a latency run even when the optional semantic quality probe is
        # degraded. Create the live session before those probes so one noisy
        # model judgment cannot remove a tenant from the concurrency sample.
        actor.write_session, _ = client.open_session(
            client.tenant_id, "capacity-live-messages", retry_rate_limit=False
        )
        # A synthetic identifier can route differently from personal-memory
        # questions. Keep it diagnostic; M1 only gates on a served Recall.
        phase = "marker-diagnostic"
        marker_question = "你还记得这份个人工作记录的编号是什么吗？"
        marker_sample = {"id": "seed-marker", "query": marker_question,
                         "query_type": "recall", "aliases": [marker]}
        result = client.search("", marker_question, timeout_s=10)
        check = assess_retrieval(result.payload, marker_sample)
        # Keep transport/service availability separate from evidence quality.
        # A non-empty candidate list can be unrelated to the seeded marker;
        # calling that marker-visible made the seed report overstate injection
        # success while still allowing Recall capacity to be measured.
        row["recall_served"] = result.status_code == 200 and check.get("recall_served", False)
        row["marker_visible"] = result.status_code == 200 and check.get("matched_expected_fact", False)
        row["marker_check"] = check
        save()
        memories = client.get_commit_memories(sid, archive)
        memory_body = _result_body(memories.payload)
        row["memory_endpoint_http_status"] = memories.status_code
        memory_kinds = memory_body.get("memory_kinds")
        if isinstance(memory_kinds, list):
            row["memory_kinds"] = [str(kind) for kind in memory_kinds]
        for field in ("memories", "items", "memory_ids"):
            value = memory_body.get(field)
            if memories.status_code == 200 and isinstance(value, list):
                row["memory_count"] = len(value)
                row["memory_count_field"] = field
                break
        row["memory_observation"] = (
            "counted" if row["memory_count"] is not None else
            "response_without_recognized_memory_list"
            if memories.status_code == 200 else
            "memory_endpoint_http_error"
        )
        phase = "semantic-validation"
        for check in semantic_checks(actor, validation_queries, search_timeout_s=search_timeout_s):
            row["queries"].append(check)
            save()
        row["valid_semantic_queries"] = sum(r.get("recall_hit", False) for r in row["queries"])
        row["quality_ok_queries"] = sum(r.get("quality_ok", False) for r in row["queries"])
        row["status"] = "PASS" if actor.write_session else "FAIL"
    except (RuntimeError, OSError, ValueError) as exc:
        # Server bodies and credentials are not exported through exception text.
        row["error_class"] = type(exc).__name__
        row["phase_failed"] = phase
    row["elapsed_s"] = time.monotonic() - started
    return row


def prepare_actors(actors: list[CapacityActor], *, timeout_s: float = 180, checkpoint=None,
                   validation_queries: int = 40, search_timeout_s: float = 60,
                   workers: int | None = None, retry_failed: int = 1) -> dict:
    validate_search_timeout(search_timeout_s)
    if not actors:
        raise ValueError("At least one capacity identity required")
    worker_count = min(max(1, int(workers or 4)), len(actors))
    def run_one(actor):
        return seed_actor(actor, timeout_s=timeout_s, checkpoint=checkpoint,
                          validation_queries=validation_queries,
                          search_timeout_s=search_timeout_s)

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        rows = list(pool.map(run_one, actors))
    retry_limit = max(0, int(retry_failed))
    for attempt in range(retry_limit):
        failed = [index for index, row in enumerate(rows) if row.get("status") != "PASS"]
        if not failed:
            break
        # Retry only failed identities with a single worker. This handles a
        # transient provider/atomic-engine failure without re-seeding healthy
        # tenants or hiding the first attempt from the evidence.
        for index in failed:
            first = rows[index]
            retry = run_one(actors[index])
            retry["retry_attempt"] = attempt + 1
            retry["initial_status"] = first.get("status")
            retry["initial_error_class"] = first.get("error_class")
            retry["initial_phase_failed"] = first.get("phase_failed")
            rows[index] = retry
    return {"status": "PASS" if all(row["status"] == "PASS" for row in rows) else "SEED_FAILED",
            "actors": rows, "actor_count": len(actors),
            "healthy_actors": sum(row["status"] == "PASS" for row in rows),
            "raw_credentials_exported": False,
            "worker_count": worker_count, "retry_failed": retry_limit}
