"""场景：Commit 洪泛 + 旁观 Search（M3 S1/S2 + M2 混合窗口）。

对齐 echomem-m1m2m3-complete-plan-20260916 方案 05 节。

S0：无后台 Commit（复用 M1，不在此场景）
S1 均匀 Commit 洪泛：每租户 Search 1 QPS + 后台维持一个 Commit
S2 单租户 Commit 洪泛：一个租户灌 Commit，其余旁观 Search

task_flood_search: 恒定 1 QPS Search
task_flood_commit: 后台持续 Commit（完成后补充下一个）
task_flood_commit_single: 仅指定租户执行 Commit（S2）
"""

from __future__ import annotations

import threading
import time

from performance.targets.echomem.protocol import (
    add_message, commit_session, open_session, poll_commit,
    search, identity, WRITE_ANCHOR_PREFIX, ANCHOR_PREFIX,
)

_flood_active = threading.Event()
_flood_stop = threading.Event()


def _do_one_commit(ctx, seq: int) -> dict:
    """执行一次完整的 open→add→commit→poll 写链路。"""
    t0 = time.time()
    sid = open_session(ctx, title=f"flood-{ctx.tenant_idx}-{seq}").json.get("session_id", "")
    if not sid:
        return {"status": "open_failed", "e2e_ms": (time.time() - t0) * 1000}

    # 写消息
    chars = 0
    corpus = ctx.params.get("tenant_corpora", {}).get(str(ctx.tenant_idx))
    if corpus and corpus.get("documents"):
        for doc in corpus["documents"]:
            add_message(ctx, sid, str(doc)[:2000])
            chars += len(str(doc)[:2000].encode("utf-8"))
    else:
        for i in range(5):
            msg = f"flood-{ctx.tenant_idx}-seq{seq}-msg{i}"
            add_message(ctx, sid, msg)
            chars += len(msg.encode("utf-8"))

    anchor = f"{WRITE_ANCHOR_PREFIX}-flood-{ctx.tenant_idx}-{seq}"
    add_message(ctx, sid, anchor)

    # commit
    resp = commit_session(ctx, sid)
    aid = resp.json.get("archive_id", "") if resp.ok else ""
    if not aid:
        return {"status": "commit_failed", "e2e_ms": (time.time() - t0) * 1000}

    # poll
    poll_result = poll_commit(ctx, sid, aid,
                              timeout_s=ctx.params.get("commit_poll_timeout_s", 120))
    return {
        "status": poll_result.get("status", "unknown"),
        "terminal": poll_result.get("terminal", False),
        "timeout": poll_result.get("timeout", False),
        "e2e_ms": (time.time() - t0) * 1000,
        "sid": sid, "aid": aid, "seq": seq,
    }


def task_flood_search(ctx) -> None:
    """恒定 1 QPS Search（旁观测试）。"""
    queries = ctx.params.get("queries", [f"{ANCHOR_PREFIX}-flood-{ctx.tenant_idx}"])
    idx = 0
    duration = ctx.params.get("duration_s", 60)
    deadline = time.time() + duration

    while time.time() < deadline and not _flood_stop.is_set():
        query = queries[idx % len(queries)]
        search(ctx, str(query))
        idx += 1
        next_tick = time.time() - (time.time() % 1) + 1.0
        wait = next_tick - time.time()
        if wait > 0:
            time.sleep(min(wait, 1.0))


def task_flood_commit(ctx) -> None:
    """后台持续 Commit：完成后立即补充下一个。"""
    seq = 0
    duration = ctx.params.get("duration_s", 60)
    deadline = time.time() + duration
    max_inflight = ctx.params.get("flood_max_inflight", 1)

    inflight = 0

    while time.time() < deadline and not _flood_stop.is_set():
        if inflight >= max_inflight:
            time.sleep(0.5)
            continue

        inflight += 1
        result = _do_one_commit(ctx, seq)
        inflight -= 1

        ctx.note(
            flood_commit_seq=seq,
            flood_commit_status=result["status"],
            flood_commit_e2e_ms=result["e2e_ms"],
            flood_commit_timeout=result.get("timeout", False),
        )
        seq += 1

        if result["status"] in ("open_failed", "commit_failed"):
            time.sleep(0.5)


def task_flood_commit_single(ctx) -> None:
    """仅指定租户执行 Commit（S2 场景）。"""
    flood_tenant = ctx.params.get("flood_tenant", 0)
    if ctx.tenant_idx == flood_tenant:
        task_flood_commit(ctx)
    else:
        # 旁观租户只做 Search
        task_flood_search(ctx)


tasks = {
    "flood_search": task_flood_search,
    "flood_commit": task_flood_commit,
    "flood_commit_single": task_flood_commit_single,
}