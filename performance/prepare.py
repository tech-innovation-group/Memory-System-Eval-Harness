"""Tenant provisioning and seed-data injection for stress runs.

Two identity modes:
  --auth-mode provision : create tenants through /api/auth/tenants ->
                          /users -> /key (local / cluster_shared servers)
  --auth-mode static    : reuse a pre-provisioned identity (auth_key +
                          tenant/user ids). Typically the only sensible
                          mode against an internet-deployed EchoMem that
                          does not allow self-service tenant creation.

Seed data is injected with bounded tenant-level concurrency and is NOT part of
the measured load; it only guarantees the retrieval index has real content.
Each seeded message carries a unique anchor token that later serves as a
searchable query and as a write-read consistency probe.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backends.echomem.client import EchoMemClient

logger = logging.getLogger("performance.prepare")

ANCHOR_PREFIX = "PERFANCHOR"
WRITE_ANCHOR_PREFIX = "PERFTAIL"


def load_tenant_specs(path: str | Path) -> list[dict[str, Any]]:
    """读 tenants.json 独立凭据（每租户一条，用于隔离/公平结论的前提）。

    JSON 取 ``tenants`` 数组，每项 ``{"tenant_id","user_id","auth_key_env"}``，
    可选内联 ``auth_key``/``account_id``/``agent_id``。auth_key 内联优先，
    否则从 ``os.environ[auth_key_env]`` 读取（缺 env 抛 ValueError，错误信息
    含 env 名）。空/缺 tenants 抛 ValueError。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tenants = data.get("tenants")
    if not isinstance(tenants, list) or not tenants:
        raise ValueError(f"租户配置文件缺少 tenants 数组: {path}")
    specs: list[dict[str, Any]] = []
    for item in tenants:
        if not isinstance(item, dict):
            raise ValueError(f"租户配置项必须是对象: {item}")
        spec: dict[str, Any] = {
            "tenant_id": str(item.get("tenant_id") or ""),
            "user_id": str(item.get("user_id") or ""),
            "auth_key_env": str(item.get("auth_key_env") or ""),
            "auth_key": str(item.get("auth_key") or ""),
            "account_id": str(item.get("account_id") or ""),
            "agent_id": str(item.get("agent_id") or ""),
        }
        if not spec["auth_key"]:
            env_name = spec["auth_key_env"]
            if not env_name:
                raise ValueError(
                    f"租户 {spec['tenant_id'] or spec['user_id']} 未配置 auth_key "
                    f"或 auth_key_env"
                )
            if env_name not in os.environ:
                raise ValueError(f"auth_key 环境变量缺失: {env_name}")
            spec["auth_key"] = os.environ[env_name]
        specs.append(spec)
    return specs

_USER_MSGS = (
    "本周项目进展顺利，核心模块已完成联调，计划下周发布测试版本。",
    "会议纪要：周四下午三点评审新接口设计，需要准备演示环境。",
    "客户反馈新功能使用体验良好，希望增加导出报表能力。",
    "服务器资源申请已提交，预计两天内批复，需要重新评估部署拓扑。",
    "压测数据显示检索延迟波动明显，需排查索引重建期间的查询路径。",
)

_NO_RECALL_QUERIES = (
    "今天下午帮我整理一下会议纪要",
    "把最新的日报发我",
    "帮我查一下工单状态",
    "我想看一下昨天的待办",
    "把上周的结果汇总一下",
    "提醒我明天开会",
    "帮我找一下这个问题的联系人",
    "给我看一下部署进度",
)

_SYNTHETIC_FACTS = (
    ("林晓", "北极星知识库迁移", "杭州研发中心", "周三下午"),
    ("周宁", "海风客户画像项目", "上海交付中心", "周四上午"),
    ("陈默", "星桥告警治理专项", "深圳运维中心", "周五下午"),
    ("叶青", "远航检索优化项目", "北京算法实验室", "周二上午"),
    ("许言", "云杉权限改造计划", "成都平台团队", "周一下午"),
)


def build_search_query_pool(
    *,
    recall_queries: list[str],
    no_recall_queries: list[str],
    profile: str = "mixed",
    recall_ratio: float = 0.7,
) -> tuple[list[str], list[str]]:
    """Build a Search query pool and its parallel kind labels.

    ``profile``:
      - recall-only: only recall-capable queries
      - no-recall-only: only everyday queries that should usually not hit
      - mixed: interleave recall and no-recall queries by ``recall_ratio``
    """
    recall = [query for query in recall_queries if query]
    ordinary = [query for query in no_recall_queries if query]
    profile = str(profile or "mixed").strip().lower()
    if profile == "recall-only":
        return recall, ["recall"] * len(recall)
    if profile == "no-recall-only":
        return ordinary, ["no_recall"] * len(ordinary)
    if profile != "mixed":
        raise ValueError(f"unknown search query profile: {profile}")
    if not recall and not ordinary:
        return [], []
    if recall_ratio <= 0:
        combined = ordinary
        return combined, ["no_recall"] * len(combined)
    if recall_ratio >= 1:
        combined = recall
        return combined, ["recall"] * len(combined)
    # Reduce the ratio to a short deterministic cycle.  The source lists may
    # have different lengths; cycling each source independently keeps the
    # requested traffic mix stable over a long-running load test.
    from math import gcd

    recall_weight = max(1, int(round(recall_ratio * 100)))
    ordinary_weight = max(1, int(round((1.0 - recall_ratio) * 100)))
    divisor = gcd(recall_weight, ordinary_weight)
    recall_weight //= divisor
    ordinary_weight //= divisor
    cycle = ["recall"] * recall_weight + ["no_recall"] * ordinary_weight
    combined: list[str] = []
    kinds: list[str] = []
    indices = {"recall": 0, "no_recall": 0}
    sources = {"recall": recall, "no_recall": ordinary}
    # Keep one cycle per available source item, then stop. The load loop
    # itself wraps this pool indefinitely, preserving the ratio.
    cycle_count = max(len(recall), len(ordinary))
    for _ in range(cycle_count):
        for kind in cycle:
            source = sources[kind]
            if not source:
                continue
            idx = indices[kind] % len(source)
            combined.append(source[idx])
            kinds.append(kind)
            indices[kind] += 1
    return combined, kinds


def verify_recall_query_pool(
    client: EchoMemClient,
    candidates: list[str],
    *,
    top_k: int,
    timeout_s: float,
    limit: int = 20,
    expected_terms_by_query: dict[str, list[str]] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Keep only seeded queries that can actually recall a memory item.

    A completed Commit proves that the asynchronous write flow terminated.
    It does not prove that a later Search can retrieve the injected content.
    This setup-only probe establishes that second fact before measurement.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        query = str(candidate or "").strip()
        if query and query not in seen:
            unique.append(query)
            seen.add(query)
    checked = unique[: max(0, limit)]
    verified: list[str] = []
    errors = 0
    degraded = 0
    relevance_failures = 0
    started = time.perf_counter()
    for query in checked:
        try:
            items, meta = client.search_with_meta(
                query,
                top_k=top_k,
                timeout_s=timeout_s,
            )
            is_degraded = bool(meta.get("degraded_reasons")) or str(
                meta.get("status") or ""
            ).lower() == "degraded"
            if is_degraded:
                degraded += 1
            elif items and _items_match_expected_terms(
                items, (expected_terms_by_query or {}).get(query, [])
            ):
                verified.append(query)
            elif items:
                relevance_failures += 1
        except Exception:
            errors += 1
    return verified, {
        "candidates": len(unique),
        "checked": len(checked),
        "verified": len(verified),
        "errors": errors,
        "degraded": degraded,
        "relevance_failures": relevance_failures,
        "elapsed_s": round(time.perf_counter() - started, 3),
    }


def _items_match_expected_terms(items: list[Any], expected_terms: list[str]) -> bool:
    """Check that a retrieved item contains the fact marker for this query."""
    if not items:
        return False
    if not expected_terms:
        return True
    evidence = "\n".join(
        json.dumps(item.to_dict(), ensure_ascii=False)
        if hasattr(item, "to_dict")
        else str(item)
        for item in items
    ).lower()
    return all(str(term).lower() in evidence for term in expected_terms)


def _anchor(idx: int, session_idx: int, msg_idx: int) -> str:
    return f"{ANCHOR_PREFIX}-{idx}-{session_idx}-{msg_idx}"


def _synthetic_recall_case(
    idx: int, session_idx: int, msg_idx: int
) -> tuple[str, str, str, list[str]]:
    """A short fact paragraph plus a semantic question and expected marker."""
    anchor = _anchor(idx, session_idx, msg_idx)
    person, project, location, schedule = _SYNTHETIC_FACTS[
        (idx + session_idx + msg_idx) % len(_SYNTHETIC_FACTS)
    ]
    user_msg = (
        f"事实编号 {anchor}：{person} 负责{project}，"
        f"计划在{schedule}于{location}完成方案评审。"
    )
    assistant_msg = (
        f"已记录：{person} 的{project}安排，关联事实编号 {anchor}。"
    )
    question = f"{person} 在哪里负责什么项目？"
    return user_msg, assistant_msg, question, [anchor]


def _message_pair(idx: int, session_idx: int, msg_idx: int) -> tuple[str, str]:
    user_msg, assistant_msg, _, _ = _synthetic_recall_case(
        idx, session_idx, msg_idx
    )
    return user_msg, assistant_msg


def _query_fragments(messages: list[str]) -> list[str]:
    """Short leading fragments of seeded messages, usable as search queries.

    Splits on Chinese punctuation first, then on English sentence
    terminators (LoCoMo conversations are English).
    """
    fragments: list[str] = []
    for message in messages:
        for sep in ("。", "！", "？", "；", ".", "!", "?"):
            if sep in message:
                head = message.split(sep)[0].strip()
                if head:
                    fragments.append(head)
                    break
        else:
            fragments.append(message[:20])
    return fragments


def _conversation_roles(messages: list[dict[str, Any]]) -> list[str]:
    """Map each message to a user/assistant role for two-party chats.

    LoCoMo conversations are two-speaker dialogues whose speaker names are
    arbitrary (e.g. Gina/Jon); the dataset-level role heuristic only labels
    speakers literally named assistant/agent. For every other speaker we
    pick roles by first-appearance alternation: the first new speaker is
    the user, the second is the assistant.
    """
    speaker_to_role: dict[str, str] = {}
    next_role = "user"
    roles: list[str] = []
    for message in messages:
        speaker = str(message.get("role_id") or message.get("speaker") or "user")
        speaker = speaker.strip().lower()
        if speaker in ("assistant", "agent"):
            role = "assistant"
        elif speaker in speaker_to_role:
            role = speaker_to_role[speaker]
        else:
            role = next_role
            speaker_to_role[speaker] = role
            next_role = "assistant" if role == "user" else "user"
        roles.append(role)
    return roles


def load_locomo_seed_batches(
    dataset_path: str | Path,
    sample_filter: str = "conv-30",
) -> list[list[dict[str, Any]]]:
    """Load LoCoMo conversations as real-content seed sessions.

    ``sample_filter`` accepts a single sample id, comma-separated sample
    ids, or ``all`` (every sample in the dataset). Each returned element
    is one conversation session: ``[{"role", "content"}, ...]`` in
    chronological order. Raises ``ValueError`` when nothing matches.
    """
    from benchmarks.locomo.dataset import load_dataset

    filters = [part.strip() for part in str(sample_filter or "").split(",") if part.strip()]
    if not filters:
        raise ValueError("sample_filter 不能为空 (示例: conv-30 / conv-30,conv-41 / all)")
    batches: list[list[dict[str, Any]]] = []
    matched_ids: list[str] = []
    for filt in filters:
        _, plans = load_dataset(Path(dataset_path), sample_filter=filt)
        for plan in plans:
            matched_ids.append(str(plan.get("sample_id") or ""))
            for batch in plan.get("session_batches") or []:
                pool_messages = batch.get("messages") or []
                roles = _conversation_roles(pool_messages)
                messages = [
                    {
                        "role": roles[index] if index < len(roles) else "user",
                        "content": str(message.get("content") or ""),
                    }
                    for index, message in enumerate(pool_messages)
                ]
                messages = [message for message in messages if message["content"]]
                if messages:
                    batches.append(messages)
    if not matched_ids:
        raise ValueError(
            f"数据集 {dataset_path} 中没有匹配 sample_filter={sample_filter} 的样本"
        )
    logger.info(
        "locomo 种子: samples=%s sessions=%d",
        ",".join(sorted(set(matched_ids))), len(batches),
    )
    return batches


@dataclass
class TenantContext:
    """One tenant ready for load: identity, client, and its query pool."""

    idx: int
    tenant_id: str
    user_id: str
    auth_key: str
    client: EchoMemClient
    queries: list[str] = field(default_factory=list)
    query_kinds: list[str] = field(default_factory=list)
    recall_queries: list[str] = field(default_factory=list)
    recall_expected_terms: dict[str, list[str]] = field(default_factory=dict)
    no_recall_queries: list[str] = field(default_factory=list)
    recall_probe: dict[str, Any] = field(default_factory=dict)
    active_session_ids: list[str] = field(default_factory=list)
    seed_sessions: int = 0
    seed_messages: int = 0
    seed_elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "auth_key_configured": bool(self.auth_key),
            "queries": len(self.queries),
            "query_kinds": len(self.query_kinds),
            "recall_queries": len(self.recall_queries),
            "recall_expected_queries": len(self.recall_expected_terms),
            "no_recall_queries": len(self.no_recall_queries),
            "recall_probe": dict(self.recall_probe),
            "active_session_ids": len(self.active_session_ids),
            "seed_sessions": self.seed_sessions,
            "seed_messages": self.seed_messages,
            "seed_elapsed_s": round(self.seed_elapsed_s, 3),
        }


def _seed_session_flow(
    client: EchoMemClient,
    idx: int,
    session_idx: int,
    messages: list[tuple[str, str]],
    commit_poll_timeout_s: float,
    poll_interval_s: float,
) -> tuple[list[str], str]:
    """open -> add -> commit -> poll 一个种子会话，失败整会话重灌一次。

    返回该会话的全部消息文本（供 query 池构建）。Windows 上向量索引的
    原子替换偶发被文件锁占用（WinError 5）会让单次 commit 失败；种子
    不计入压测测量，因此允许整会话重灌一次，两次都失败才抛出。
    """
    for attempt in (1, 2):
        session_id = client.open_session(title=f"perf-seed-{idx}-{session_idx}")
        texts: list[str] = []
        try:
            for role, content in messages:
                client.add_message(session_id, role, content)
                texts.append(content)
            archive_id = client.commit_session(session_id)
            commit = client.poll_commit(
                session_id,
                archive_id,
                timeout_s=commit_poll_timeout_s,
                poll_interval_s=poll_interval_s,
            )
            if commit.status != "completed":
                raise RuntimeError(
                    f"seed commit failed for tenant {idx} session {session_id}: "
                    f"status={commit.status} error={commit.error}"
                )
            return texts, session_id
        except Exception as exc:
            if attempt == 1:
                logger.warning(
                    "种子会话失败，重灌一次 idx=%d session=%d error=%s",
                    idx,
                    session_idx,
                    _format_prepare_error(exc),
                )
                continue
            raise RuntimeError(
                f"seed session failed idx={idx} session={session_idx}: "
                f"{_format_prepare_error(exc)}"
            ) from exc
    raise RuntimeError("unreachable")


def _format_prepare_error(exc: Exception) -> str:
    """Render bounded HTTP context for setup failures."""
    status = getattr(exc, "echomem_status", None)
    url = str(getattr(exc, "echomem_url", "") or "")
    body = str(getattr(exc, "echomem_body", "") or "").strip()
    if status is None:
        return f"{type(exc).__name__}: {exc}"
    detail = f"HTTP {status}"
    if url:
        detail += f" {url}"
    if body:
        detail += f" body={body[:500]}"
    return detail


def seed_tenant(
    client: EchoMemClient,
    idx: int,
    sessions: int,
    messages_per_session: int,
    commit_poll_timeout_s: float,
    *,
    poll_interval_s: float = 1.0,
    active_sessions: int = 1,
) -> TenantContext:
    """Inject seed conversations and build the tenant's query pool.

    Uses complete write flows (open -> add -> commit -> poll) so the seed
    data goes through the same extraction path as production writes.
    """
    queries: list[str] = []
    anchor_queries: list[str] = []
    semantic_recall_queries: list[str] = []
    recall_expected_terms: dict[str, list[str]] = {}
    active_session_ids: list[str] = []
    seed_messages = 0
    started = time.perf_counter()
    if sessions <= 0:
        # Capacity/admission cases need real session identities but must not
        # pay for model-backed seed Commit.  Open empty sessions instead.
        for session_idx in range(max(1, int(active_sessions))):
            session_id = client.open_session(title=f"perf-active-{idx}-{session_idx}")
            active_session_ids.append(session_id)
        return TenantContext(
            idx=idx,
            tenant_id=client.account,
            user_id=client.user_id,
            auth_key=client.auth_key,
            client=client,
            queries=queries,
            query_kinds=["recall"] * len(queries),
            recall_queries=list(queries),
            recall_expected_terms=recall_expected_terms,
            no_recall_queries=list(_NO_RECALL_QUERIES),
            active_session_ids=active_session_ids,
            seed_sessions=0,
            seed_messages=0,
            seed_elapsed_s=time.perf_counter() - started,
        )
    for session_idx in range(sessions):
        messages: list[tuple[str, str]] = []
        for msg_idx in range(messages_per_session):
            user_msg, assistant_msg, question, expected_terms = _synthetic_recall_case(
                idx, session_idx, msg_idx
            )
            messages.append(("user", user_msg))
            messages.append(("assistant", assistant_msg))
            anchor = _anchor(idx, session_idx, msg_idx)
            anchor_queries.append(anchor)
            semantic_recall_queries.append(question)
            recall_expected_terms[anchor] = [anchor]
            recall_expected_terms[question] = expected_terms
        texts, session_id = _seed_session_flow(
            client,
            idx,
            session_idx,
            messages,
            commit_poll_timeout_s,
            poll_interval_s,
        )
        active_session_ids.append(session_id)
        seed_messages += len(texts)
        queries.extend(_query_fragments(texts))
    # The formal recall pool contains semantic questions and their marker
    # probes. Fragments are kept only as auxiliary queries, not accuracy data.
    queries = semantic_recall_queries + anchor_queries + queries
    recall_queries = semantic_recall_queries + anchor_queries
    elapsed = time.perf_counter() - started
    logger.info(
        "seeded tenant idx=%d sessions=%d messages=%d elapsed=%.1fs queries=%d",
        idx, sessions, seed_messages, elapsed, len(queries),
    )
    return TenantContext(
        idx=idx,
        tenant_id=client.account,
        user_id=client.user_id,
        auth_key=client.auth_key,
        client=client,
        queries=queries,
        query_kinds=["recall"] * len(queries),
        recall_queries=recall_queries,
        recall_expected_terms=recall_expected_terms,
        no_recall_queries=list(_NO_RECALL_QUERIES),
        active_session_ids=active_session_ids,
        seed_sessions=sessions,
        seed_messages=seed_messages,
        seed_elapsed_s=elapsed,
    )


def seed_tenant_from_conversations(
    client: EchoMemClient,
    idx: int,
    batches: list[list[dict[str, Any]]],
    commit_poll_timeout_s: float,
    *,
    poll_interval_s: float = 1.0,
) -> TenantContext:
    """seed_tenant 的 locomo 变体：把真实会话批次灌入单个租户。

    每个 batch（[{role, content}, ...]）写入一个 session，走与合成种子
    相同的完整写路径（open -> add -> commit -> poll）；query 池从用户
    消息的分句片段构建（真实内容没有 PERFANCHOR 锚词）。
    """
    queries: list[str] = []
    active_session_ids: list[str] = []
    seed_messages = 0
    started = time.perf_counter()
    for session_idx, messages in enumerate(batches):
        pairs: list[tuple[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "").strip()
            if content:
                pairs.append((role, content))
        texts, session_id = _seed_session_flow(
            client,
            idx,
            session_idx,
            pairs,
            commit_poll_timeout_s,
            poll_interval_s,
        )
        active_session_ids.append(session_id)
        seed_messages += len(texts)
        user_texts = [content for role, content in pairs if role == "user"]
        queries.extend(_query_fragments(user_texts))
    elapsed = time.perf_counter() - started
    logger.info(
        "seeded tenant idx=%d sessions=%d messages=%d elapsed=%.1fs queries=%d",
        idx, len(batches), seed_messages, elapsed, len(queries),
    )
    return TenantContext(
        idx=idx,
        tenant_id=client.account,
        user_id=client.user_id,
        auth_key=client.auth_key,
        client=client,
        queries=queries,
        query_kinds=["recall"] * len(queries),
        recall_queries=list(queries),
        recall_expected_terms={},
        no_recall_queries=list(_NO_RECALL_QUERIES),
        active_session_ids=active_session_ids,
        seed_sessions=len(batches),
        seed_messages=seed_messages,
        seed_elapsed_s=elapsed,
    )


class TenantPreparer:
    """Create (or bind) tenant identities and seed each with data."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_mode: str = "provision",
        auth_key: str = "",
        tenant_id: str = "",
        user_id: str = "",
        agent_id: str = "default",
        tenants: int = 8,
        timeout_s: float = 10.0,
        label_prefix: str = "perf",
        tenant_specs: list[dict[str, Any]] | None = None,
        seed_concurrency: int = 4,
        auth_header: str = "X-Auth-Key",
    ) -> None:
        if tenants < 1:
            raise ValueError("tenants must be >= 1")
        self.base_url = base_url
        self.auth_mode = auth_mode
        self.auth_key = auth_key
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.agent_id = agent_id
        self.tenants = tenants
        self.timeout_s = timeout_s
        self.label_prefix = label_prefix
        if seed_concurrency < 1:
            raise ValueError("seed_concurrency must be >= 1")
        self.seed_concurrency = seed_concurrency
        self.auth_header = auth_header
        # --tenant-config 独立凭据：非空时优先于 auth_mode，--tenants 忽略
        self.tenant_specs = tenant_specs
        self._provisioned: list[tuple[int, EchoMemClient]] = []
        if auth_mode == "static" and tenants != 1 and not tenant_specs:
            raise ValueError(
                "--auth-mode static 仅支持单租户（--tenants 1）：外网部署通常只有一个预置身份"
            )

    def prepare(
        self,
        seed_sessions: int,
        messages_per_session: int,
        commit_poll_timeout_s: float,
        *,
        locomo_batches: list[list[dict[str, Any]]] | None = None,
        active_sessions_per_tenant: int = 1,
    ) -> list[TenantContext]:
        """Provision/bind identities and seed tenants with bounded parallelism.

        ``locomo_batches`` 非空时用真实会话灌入每个租户（全部租户共享
        同一批会话，复制式多租户布局），否则用合成锚词消息。
        """
        contexts: list[TenantContext] = []
        if self.tenant_specs:
            # --tenant-config 独立凭据模式：逐条构造客户端并灌种。
            # 不向 _provisioned 记录（config 凭据不归本 preparer 清理）。
            def seed_spec(item: tuple[int, dict[str, Any]]) -> TenantContext:
                idx, spec = item
                client = EchoMemClient(
                    self.base_url,
                    auth_key=str(spec.get("auth_key") or ""),
                    account=str(spec.get("account_id") or spec.get("tenant_id") or ""),
                    user_id=str(spec.get("user_id") or ""),
                    agent_id=str(spec.get("agent_id") or "default"),
                    timeout_s=self.timeout_s,
                    max_retries=0,
                    auth_header=self.auth_header,
                )
                return self._seed_one(
                    client,
                    idx,
                    seed_sessions,
                    messages_per_session,
                    commit_poll_timeout_s,
                    locomo_batches,
                    active_sessions_per_tenant,
                )

            with ThreadPoolExecutor(
                max_workers=min(self.seed_concurrency, len(self.tenant_specs)),
                thread_name_prefix="stress-seed",
            ) as pool:
                futures = [
                    pool.submit(seed_spec, item)
                    for item in enumerate(self.tenant_specs)
                ]
                contexts.extend(future.result() for future in futures)
            return contexts
        if self.auth_mode == "provision":
            stamp = int(time.time())
            for idx in range(self.tenants):
                client = EchoMemClient(
                    self.base_url,
                    timeout_s=self.timeout_s,
                    max_retries=0,
                    auth_header=self.auth_header,
                )
                client.provision_isolated_identity(f"{self.label_prefix}-{stamp}-{idx}")
                self._provisioned.append((idx, client))
                contexts.append(
                    self._seed_one(
                        client,
                        idx,
                        seed_sessions,
                        messages_per_session,
                        commit_poll_timeout_s,
                        locomo_batches,
                        active_sessions_per_tenant,
                    )
                )
        elif self.auth_mode == "static":
            client = EchoMemClient(
                self.base_url,
                auth_key=self.auth_key,
                account=self.tenant_id,
                user_id=self.user_id,
                agent_id=self.agent_id,
                timeout_s=self.timeout_s,
                max_retries=0,
                auth_header=self.auth_header,
            )
            contexts.append(
                self._seed_one(
                    client,
                    0,
                    seed_sessions,
                    messages_per_session,
                    commit_poll_timeout_s,
                    locomo_batches,
                    active_sessions_per_tenant,
                )
            )
        else:
            raise ValueError(f"unknown auth mode: {self.auth_mode}")
        return contexts

    def keys_independent(self) -> bool:
        """tenant_specs 模式下各 spec 的 auth_key 是否非空且互不相同。

        独立且互异的 key 是隔离/公平结论的前提；非 config 模式（provision）
        天然独立，恒为 True。
        """
        if not self.tenant_specs:
            return True
        keys = [str(spec.get("auth_key") or "") for spec in self.tenant_specs]
        return all(keys) and len(set(keys)) == len(keys)

    def identity_mode(self) -> str:
        """Return the identity source actually used by this preparer."""
        return "tenant_config" if self.tenant_specs else self.auth_mode

    def cleanup(self) -> None:
        """Delete every tenant this preparer provisioned.

        Covers tenants whose seeding failed midway (they were recorded
        right after provisioning), so an aborted prepare still leaves no
        stress-run artifacts behind. static mode provisions nothing here,
        and --cleanup-identities is rejected for static in arg validation.
        """
        for idx, client in self._provisioned:
            try:
                client.delete_current_identity()
                logger.info("deleted tenant idx=%d", idx)
            except Exception as exc:
                logger.warning(
                    "tenant cleanup failed idx=%d: %s", idx, exc,
                )

    @staticmethod
    def _seed_one(
        client: EchoMemClient,
        idx: int,
        seed_sessions: int,
        messages_per_session: int,
        commit_poll_timeout_s: float,
        locomo_batches: list[list[dict[str, Any]]] | None,
        active_sessions_per_tenant: int,
    ) -> TenantContext:
        if locomo_batches:
            return seed_tenant_from_conversations(
                client,
                idx,
                locomo_batches,
                commit_poll_timeout_s,
            )
        return seed_tenant(
            client,
            idx,
            seed_sessions,
            messages_per_session,
            commit_poll_timeout_s,
            active_sessions=active_sessions_per_tenant,
        )
