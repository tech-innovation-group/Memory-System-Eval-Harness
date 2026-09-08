"""EchoMem HTTP 协议适配层（不是场景）。

三层职责：

- **端点级函数**（``search`` / ``open_session`` / ``add_message`` /
  ``commit_session`` / ``poll_commit``）：封装 EchoMem 每个 HTTP 端点的
  请求构造、响应解析与记录语义，是「该系统支持哪些端点」的清单；
- **标准任务**（``task_read`` / ``task_write``）：端点组合成的读/写负载
  路径，是四场景共用的默认形态；
- 常量与响应解析辅助。

场景文件通过本模块组合端点；新增只测部分端点的场景时，直接调用端点级
函数即可，不必复用标准任务。
"""

from __future__ import annotations

from typing import Any
import json
import re
import time

from performance.ctx import Ctx, Response, PollResult
from performance.records import content_hash
from performance.targets.echomem.probes._client import status_from

ANCHOR_PREFIX = "PERFANCHOR"
WRITE_ANCHOR_PREFIX = "PERFTAIL"

DEFAULT_QUERIES = [
    "什么是记忆",
    "用户今天的心情怎么样",
    "帮我总结一下最近的对话",
    "上个月的项目进展",
    "用户的偏好是什么",
]


def is_anchor_query(query: str) -> bool:
    """Whether a read query is an anchor token (must always be recallable)."""
    return ANCHOR_PREFIX in query or WRITE_ANCHOR_PREFIX in query


def anchor_marker(query: str) -> str:
    """Extract the stable marker, never the surrounding question text."""
    match = re.search(r"(?:PERFANCHOR|PERFTAIL)-[A-Za-z0-9-]+", query)
    return match.group(0) if match else ""


def identity(ctx: Ctx, name: str) -> str:
    scoped = ctx.params.get("tenant_identities", {}).get(str(ctx.tenant_idx), {})
    return str(scoped.get(name, ctx.params.get(name, "default")))


def recall_quality(payload: Any, marker: str = "", query_type: str = "recall") -> dict:
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        result = {}
    items = result.get("items")
    valid = isinstance(items, list)
    items = items if valid else []
    degraded = bool(result.get("degraded_reasons")) or result.get("status") in {"degraded", "error", "failed"}
    hit = bool(marker and marker in json.dumps(items, ensure_ascii=False))
    return {
        "hit_count": len(items), "degraded": degraded,
        "real_recall": bool(items),
        "quality_ok": bool(valid and not degraded and (
            not items if query_type == "no_recall" else hit if marker else bool(items)
        )),
        "query_type": query_type, "expected_marker": marker,
        "marker_found": hit,
        "degraded_reasons": json.dumps(result.get("degraded_reasons") or [], ensure_ascii=False),
    }


# --------------------------------------------------------------------- #
#  端点级函数：一个函数 = 一个 EchoMem HTTP 端点                         #
# --------------------------------------------------------------------- #

def search(ctx: Ctx, query: str, *, top_k: int = 5) -> Response:
    """POST /api/retrieval/search 并记录质量断言字段。

    HTTP 错误记 error；200 但空结果、错误标记或降级均不能通过召回质量断言。
    """
    resp = ctx.post(
        "/api/retrieval/search",
        body={
            "query": query,
            "agent_id": identity(ctx, "agent_id"),
            "limit": top_k,
            "include_explain": True,
            "include_debug": True,
        },
        op="read",
        query=query,
    )
    marker = anchor_marker(query)
    sample = ctx.params.get("tenant_query_cases", {}).get(str(ctx.tenant_idx), {}).get(query)
    query_type = "recall" if marker else "no_recall" if query in NO_RECALL_QUERIES else "unclassified"
    if sample is not None:
        query_type = sample["query_type"]
    if not resp.ok:
        ctx.note(quality_ok=False, query_type=query_type, expected_marker=marker,
                 quality_assertion="fixed-fact-in-items" if sample is not None else "")
        return resp
    if sample is not None:
        from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval
        check = assess_retrieval(resp.json, sample)
        ctx.note(quality_ok=check["quality_ok"], query_type=query_type,
                 hit_count=check["hit_count"], real_recall=check["hit_count"] > 0,
                 degraded=check["degraded"], expected_fact_found=check["matched_expected_fact"],
                 quality_assertion="fixed-fact-in-items",
                 degraded_reasons=json.dumps(check["degraded_reasons"], ensure_ascii=False))
        return resp
    ctx.note(**recall_quality(resp.json, marker, query_type))
    return resp


def open_session(ctx: Ctx, *, title: str = "perf-write-tx") -> Response:
    """POST /api/sessions/open，返回响应（session_id 见 :func:`session_id`）。"""
    return ctx.post(
        "/api/sessions/open",
        body={
            "agent_id": identity(ctx, "agent_id"),
            "title": title,
            "metadata": {
                "title": title,
                "account_id": identity(ctx, "account_id"),
                "user_id": identity(ctx, "user_id"),
            },
        },
        op="open",
    )


def add_message(ctx: Ctx, session_id: str, content: str) -> Response:
    """POST /api/sessions/{sid}/messages 并记录内容指纹。"""
    resp = ctx.post(
        f"/api/sessions/{session_id}/messages",
        body={"role": "user", "content": content},
        op="add",
        session_id=session_id,
        content_hash=content_hash(content),
        content_bytes=len(content.encode("utf-8")),
    )
    if resp.ok:
        ctx.note(message_id=message_id(resp.json))
    return resp


def commit_session(ctx: Ctx, session_id: str) -> Response:
    """POST /api/sessions/{sid}/commit，返回响应（archive_id 见 :func:`archive_id`）。"""
    resp = ctx.post(
        f"/api/sessions/{session_id}/commit",
        body={"metadata": {"keep_recent_count": 0}},
        op="commit_submit",
        session_id=session_id,
    )
    if resp.http_status == 202 and archive_id(resp.json):
        ctx.note(
            archive_id=archive_id(resp.json),
            accepted_at_ms=time.time() * 1000,
        )
    elif resp.ok:
        resp.status = "error"
        resp.error_type = "commit_invalid_receipt"
        ctx.note(status="error", error_type="commit_invalid_receipt")
    return resp


def poll_commit(
    ctx: Ctx,
    session_id: str,
    archive_id: str,
    *,
    timeout_s: float | None = None,
    interval_s: float = 0.2,
) -> PollResult:
    """GET /api/sessions/{sid}/commits/{aid} 轮询到 completed/failed/timeout。"""
    audit = {"poll_evidence_version": "echomem-poll-v1", "poll_count": 0,
             "poll_http_errors": 0, "last_nonterminal_at_ms": None,
             "commit_terminal_state": ""}

    def observed(started_ms, status, body, error):
        audit["poll_count"] += 1
        audit["poll_http_errors"] += status != 200 or bool(error)
        value = body if isinstance(body, dict) else {}
        state = status_from(value)
        if status == 200 and not error:
            if state in {"pending", "queued", "running", "processing", "in_progress", "awaiting_engines"}:
                audit["last_nonterminal_at_ms"] = started_ms
            elif state in {"completed", "failed", "error"}:
                audit["commit_terminal_state"] = state

    result = ctx.poll(
        f"/api/sessions/{session_id}/commits/{archive_id}",
        op="commit_done",
        interval_s=interval_s,
        timeout_s=(
            timeout_s
            if timeout_s is not None
            else float(ctx.params.get("commit_poll_timeout_s", 600))
        ),
        session_id=session_id,
        archive_id=archive_id,
        on_response=observed,
        state_of=status_from,
        until=lambda _: audit["commit_terminal_state"] == "completed",
    )
    ended = time.time() * 1000
    if result.record is None:
        result.record = ctx.record(op="commit_done", stage_ms=result.elapsed_ms,
                                   status="error", error_type="commit_stopped",
                                   session_id=session_id, archive_id=archive_id)
    for key, value in {**audit, "poll_outcome": result.status,
                       "observation_ended_at_ms": ended,
                       "terminal_at_ms": ended if audit["commit_terminal_state"] else None,
                       "completed_at_ms": ended if audit["commit_terminal_state"] == "completed" else None}.items():
        setattr(result.record, key, value)
    return result


# --------------------------------------------------------------------- #
#  标准任务：端点组合成的读/写负载路径                                   #
# --------------------------------------------------------------------- #

def task_read(ctx: Ctx) -> None:
    """一次测量式检索（场景 A 读路径）：``search`` 取 query 池下一条。"""
    pool = ctx.params.get("tenant_query_pools", {}).get(str(ctx.tenant_idx))
    pool = pool or ctx.params.get("queries") or DEFAULT_QUERIES
    mode = ctx.params.get("query_mode", "recall")
    if mode in {"recall", "mixed"}:
        anchors = [q for q in pool if is_anchor_query(q)]
        pool = anchors or pool
    if mode == "mixed":
        pool = list(pool) * len(NO_RECALL_QUERIES) + NO_RECALL_QUERIES * len(pool)
    query = ctx.choose(pool)
    search(ctx, query, top_k=int(ctx.params.get("top_k", 5)))


NO_RECALL_QUERIES = ["你好", "谢谢", "计算 2 加 3", "把 hello 翻译成中文"]


def task_write(ctx: Ctx) -> None:
    """一个完整注入事务（场景 B 写路径）。

    对齐 ``loadgen.run_write_transaction``：open -> add×N（末条携带
    PERFTAIL anchor）-> commit submit -> commit done（poll 到 completed，
    默认 600s 超时）。四阶段独立计时记录；失败阶段即中止事务；commit
    提交默认不重试（与 ``--commit-retry-max 0`` 一致）。
    """
    messages = int(ctx.params.get("messages_per_session", 10))
    anchor = f"{WRITE_ANCHOR_PREFIX}-{ctx.tenant_idx}-{ctx.next_seq()}"

    open_resp = open_session(ctx)
    if not open_resp.ok:
        return
    sid = session_id(open_resp.json)
    if not sid:
        return

    for msg_idx in range(messages):
        last = msg_idx == messages - 1
        content = (
            f"压测写入会话消息 {anchor}-{msg_idx}"
            if last
            else f"压测写入会话消息-{msg_idx}"
        )
        if not add_message(ctx, sid, content).ok:
            return

    commit_resp = commit_session(ctx, sid)
    if not commit_resp.ok:
        return
    aid = archive_id(commit_resp.json)
    if not aid:
        return

    poll_commit(ctx, sid, aid)


# --------------------------------------------------------------------- #
#  响应解析辅助                                                          #
# --------------------------------------------------------------------- #

def session_id(body: dict[str, Any] | None) -> str:
    if not isinstance(body, dict):
        return ""
    sid = body.get("session_id") or body.get("id") or ""
    if not sid:
        scope = body.get("scope")
        if isinstance(scope, dict):
            sid = scope.get("session_id") or ""
    return str(sid)


def message_id(body: dict[str, Any] | None) -> str:
    if not isinstance(body, dict):
        return ""
    return str(body.get("message_id") or body.get("id") or body.get("msg_id") or "")


def archive_id(body: dict[str, Any] | None) -> str:
    if not isinstance(body, dict):
        return ""
    aid = body.get("archive_id") or body.get("task_id") or ""
    if not aid:
        result = body.get("result")
        if isinstance(result, dict):
            aid = result.get("archive_id") or result.get("task_id") or ""
    if not aid:
        aid = body.get("id", "")
    return str(aid)
