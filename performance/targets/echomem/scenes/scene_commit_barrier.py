"""场景：同步 Commit 屏障 + 复用记忆 Search（M1 Commit 容量 + M3 前半段）。

对齐 echomem-m1m2m3-complete-plan-20260916 方案 02/03 节。

执行流程：
1. 每租户预先写好自己的 LoCoMo session 消息（不立即 commit）
2. 同步屏障释放 → 所有租户同时发出一次 commit submit
3. 轮询到 completed/failed/timeout
4. 复用已注入记忆：每租户 1 QPS Search × 60s

task_write_barrier: 写消息 + barrier 同步 commit + poll
task_search_capacity: 每租户 1 QPS Search（复用 Phase1 记忆）
"""

from __future__ import annotations

import threading
import time

from performance.targets.echomem.protocol import (
    add_message,
    commit_session,
    open_session,
    poll_commit,
    search,
    identity,
    WRITE_ANCHOR_PREFIX,
)

_barrier = threading.Barrier
_ready: list[dict] = []  # 每租户预提交的 session 信息
_lock = threading.Lock()


def _prepare_session(ctx) -> dict:
    """打开 session，写入消息，但暂不 commit。"""
    sid = open_session(ctx, title=f"commit-barrier-{ctx.tenant_idx}").json.get("session_id", "")
    if not sid:
        return {"tenant": ctx.tenant_idx, "session_id": "", "messages": 0, "chars": 0, "error": "open_failed"}

    # 使用租户对应的语料，否则回退到默认消息
    corpus = ctx.params.get("tenant_corpora", {}).get(str(ctx.tenant_idx))
    messages = []
    if corpus and corpus.get("documents"):
        for doc in corpus["documents"]:
            messages.append(str(doc))
    else:
        messages = [f"tenant-{ctx.tenant_idx} barrier session message #{i}" for i in range(10)]

    chars = 0
    count = 0
    anchor_msg = f"压测锚定 {WRITE_ANCHOR_PREFIX}-{ctx.tenant_idx}-barrier-session"
    for msg in messages:
        add_message(ctx, sid, str(msg))
        chars += len(str(msg).encode("utf-8"))
        count += 1
    add_message(ctx, sid, anchor_msg)
    chars += len(anchor_msg.encode("utf-8"))
    count += 1

    return {"tenant": ctx.tenant_idx, "session_id": sid, "messages": count, "chars": chars}


def task_write_barrier(ctx) -> None:
    """每租户：写消息 → 同步屏障 → commit → poll。"""
    info = _prepare_session(ctx)
    with _lock:
        _ready.append(info)

    # 同步屏障：等所有租户写完消息
    barrier = _barrier(1)
    try:
        barrier = _barrier(ctx.params.get("tenants", 1))
    except Exception:
        pass
    try:
        barrier.wait(timeout=30)
    except threading.BrokenBarrierError:
        pass

    # 同步起跑 commit
    t0 = time.time()
    resp = commit_session(ctx, info["session_id"])
    aid = resp.json.get("archive_id", "") if resp.ok else ""
    submit_ms = (time.time() - t0) * 1000
    ctx.note(barrier_commit_submit_ms=submit_ms)

    if not aid:
        ctx.note(commit_status="submit_failed", session_id=info["session_id"])
        return

    # poll 到终态
    poll_result = poll_commit(ctx, info["session_id"], aid,
                              timeout_s=ctx.params.get("commit_poll_timeout_s", 300))
    e2e_ms = (time.time() - t0) * 1000
    ctx.note(
        commit_e2e_ms=e2e_ms,
        commit_status=poll_result.get("status", "unknown"),
        commit_terminal=poll_result.get("terminal", False),
        commit_timeout=poll_result.get("timeout", False),
        session_id=info["session_id"],
        archive_id=aid,
    )


def task_search_capacity(ctx) -> None:
    """每租户 1 QPS Search，复用已注入的记忆。"""
    queries = ctx.params.get("queries", [])
    if not queries:
        # 回退到 PERFTAIL anchor 查询
        queries = [f"{WRITE_ANCHOR_PREFIX}-{ctx.tenant_idx}-barrier-session 回忆内容"]

    idx = 0
    duration = ctx.params.get("duration_s", 60)
    deadline = time.time() + duration

    while time.time() < deadline:
        query = queries[idx % len(queries)]
        search(ctx, str(query))
        idx += 1
        # 固定 1 QPS：sleep 到下一整秒
        next_tick = time.time() - (time.time() % 1) + 1.0
        wait = next_tick - time.time()
        if wait > 0:
            time.sleep(wait)


tasks = {
    "barrier_commit": task_write_barrier,
    "search_capacity": task_search_capacity,
}