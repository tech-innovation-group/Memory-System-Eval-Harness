"""Fresh, publicly provisioned T x U identities with real semantic memories."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import time
import uuid

from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval, build_corpus
from performance.targets.echomem.probes._client import EchoMemHTTP, extract_archive, status_from


@dataclass
class CapacityActor:
    tenant_index: int
    user_index: int
    client: EchoMemHTTP
    corpus: dict
    write_session: str = ""


def semantic_checks(actor: CapacityActor, validation_queries: int):
    available = actor.corpus["recall_queries"]
    if not 1 <= validation_queries <= len(available):
        raise ValueError("validation_queries must be between 1 and the corpus size")
    for index in range(validation_queries):
        sample = available[index * len(available) // validation_queries]
        begin = time.monotonic()
        result = actor.client.request("POST", "/api/retrieval/search", {
            "query": sample["query"], "agent_id": actor.client.agent_id, "limit": 10,
            "include_debug": True, "include_explain": True,
        }, timeout_s=10, operation="search")
        check = assess_retrieval(result.payload, sample)
        yield {**check, "http_status": result.status_code, "elapsed_s": time.monotonic() - begin,
               "success": result.status_code == 200 and check["quality_ok"]}


def validate_cached_actors(actors: list[CapacityActor], validation_queries: int = 4) -> dict:
    """Revalidate existing facts through real Search; never submit another Commit."""
    def validate(actor):
        row = {"tenant_index": actor.tenant_index, "user_index": actor.user_index,
               "queries": [], "status": "INCONCLUSIVE", "seed_source": "validated-cache"}
        try:
            for result in semantic_checks(actor, validation_queries):
                row["queries"].append(result)
            row["valid_semantic_queries"] = sum(q["success"] for q in row["queries"])
            row["status"] = "PASS" if row["valid_semantic_queries"] == validation_queries else "FAIL"
        except (RuntimeError, OSError, ValueError) as exc:
            row["error_class"] = type(exc).__name__
        return row

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(actors)))) as pool:
        rows = list(pool.map(validate, actors))
    return {"status": "PASS" if rows and all(r["status"] == "PASS" for r in rows) else "INCONCLUSIVE",
            "actors": rows, "actor_count": len(rows), "healthy_actors": sum(r["status"] == "PASS" for r in rows),
            "raw_credentials_exported": False}


def provision_actors(base_url: str, tenants: int, users: int, *, memory_scale: int = 1,
                     seed: int = 42, tenant_offset: int = 0) -> list[CapacityActor]:
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
            actors.append(CapacityActor(tenant_index, user_index, client,
                          build_corpus(f"tenant-{tenant_index}/user-{user_index}", seed=seed, memory_scale=memory_scale)))
    return actors


def seed_actor(actor: CapacityActor, *, timeout_s: float = 180, checkpoint=None,
               validation_queries: int = 40) -> dict:
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
        # A synthetic identifier can route differently from personal-memory
        # questions. Keep it diagnostic; only the fixed semantic facts gate M1.
        phase = "marker-diagnostic"
        marker_question = "你还记得这份个人工作记录的编号是什么吗？"
        marker_sample = {"id": "seed-marker", "query": marker_question,
                         "query_type": "recall", "aliases": [marker]}
        result = client.search("", marker_question, timeout_s=10)
        check = assess_retrieval(result.payload, marker_sample)
        row["marker_visible"] = result.status_code == 200 and check["quality_ok"]
        row["marker_check"] = check
        save()
        memories = client.get_commit_memories(sid, archive)
        for field in ("memories", "items", "memory_ids"):
            value = memories.payload.get(field)
            if memories.status_code == 200 and isinstance(value, list):
                row["memory_count"] = len(value)
                break
        phase = "semantic-validation"
        for check in semantic_checks(actor, validation_queries):
            row["queries"].append(check)
            save()
        row["valid_semantic_queries"] = sum(r["success"] for r in row["queries"])
        row["status"] = "PASS" if row["valid_semantic_queries"] == len(selected) else "FAIL"
        actor.write_session, _ = client.open_session(client.tenant_id, "capacity-live-messages", retry_rate_limit=False)
    except (RuntimeError, OSError, ValueError) as exc:
        # Server bodies and credentials are not exported through exception text.
        row["error_class"] = type(exc).__name__
        row["phase_failed"] = phase
    row["elapsed_s"] = time.monotonic() - started
    return row


def prepare_actors(actors: list[CapacityActor], *, timeout_s: float = 180, checkpoint=None,
                   validation_queries: int = 40) -> dict:
    if not actors:
        raise ValueError("At least one capacity identity required")
    with ThreadPoolExecutor(max_workers=min(4, len(actors))) as pool:
        rows = list(pool.map(lambda actor: seed_actor(actor, timeout_s=timeout_s, checkpoint=checkpoint,
                                                     validation_queries=validation_queries), actors))
    return {"status": "PASS" if all(row["status"] == "PASS" for row in rows) else "INCONCLUSIVE",
            "actors": rows, "actor_count": len(actors),
            "healthy_actors": sum(row["status"] == "PASS" for row in rows),
            "raw_credentials_exported": False}
