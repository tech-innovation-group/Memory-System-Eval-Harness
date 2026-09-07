"""Observe actual service failure and post-load drain, without latency SLOs."""

from __future__ import annotations

import time

from performance.targets.echomem.probes._client import status_from
from performance.targets.echomem.probes.docker_inspect import inspect_container


def lifecycle(container: str) -> dict:
    if not container:
        return {}
    raw = inspect_container(container)
    state = raw.get("State", {})
    return {"running": state.get("Running"), "oom_killed": state.get("OOMKilled"),
            "exit_code": state.get("ExitCode"), "restart_count": raw.get("RestartCount"),
            "started_at": state.get("StartedAt")}


def crash_reason(before: dict, after: dict) -> str | None:
    if after.get("oom_killed"):
        return "container-oom"
    if after.get("running") is False:
        return "container-exited"
    if (before.get("restart_count") is not None and after.get("restart_count") is not None
            and after["restart_count"] > before["restart_count"]):
        return "container-restarted-during-load"
    if before.get("started_at") and after.get("started_at") and before["started_at"] != after["started_at"]:
        return "container-restarted-during-load"
    return None


def observe_recovery(actors: list, measurement: dict, *, container: str = "",
                     before: dict | None = None, timeout_s: float = 300,
                     checkpoint=None) -> dict:
    if timeout_s <= 0:
        raise ValueError("Recovery observation timeout must be positive")
    after = lifecycle(container)
    crash = crash_reason(before or {}, after)
    if crash:
        return {"status": "BOUNDARY_OBSERVED", "reason": crash, "container": after,
                "recovery_window_s": timeout_s}
    reads = [r for r in measurement["rows"] if r.get("op") == "read" and r.get("sent")]
    if reads and all(r.get("http_status") in {400, 401, 403, 404, 405, 422} for r in reads):
        return {"status": "INCONCLUSIVE", "reason": "request-or-authentication-precondition",
                "container": after, "capacity_boundary_proven": False}
    no_http_success = bool(reads) and not any(r.get("http_status") == 200 for r in reads)
    unfinished = [r for r in measurement["rows"] if r.get("op") == "commit_done"
                  and r.get("status") not in {"completed", "failed", "error"}]
    receipts = measurement.get("commit_receipts")
    if unfinished and receipts is None:
        return {"status": "INCONCLUSIVE", "reason": "missing-receipts-for-backlog-drain",
                "unresolved": len(unfinished), "container": after}
    if not unfinished and not no_http_success:
        return {"status": "NO_BOUNDARY_OBSERVED", "container": after,
                "pending_after_drain": 0, "recovery_window_s": timeout_s}
    pending = {(r["identity_index"], r["session_id"], r["archive_id"]) for r in receipts or []} if unfinished else set()
    started, samples = time.monotonic(), []
    search_recovered = not no_http_success
    while True:
        completed, failed = 0, 0
        for identity, sid, archive in list(pending):
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                result = actors[identity].client.request("GET", f"/api/sessions/{sid}/commits/{archive}",
                    timeout_s=min(5, remaining), operation="commit_poll")
                state = status_from(result.payload)
            except Exception:
                continue
            if result.status_code == 200 and state in {"completed", "failed", "error"}:
                pending.remove((identity, sid, archive))
                completed += state == "completed"
                failed += state != "completed"
        if not search_recovered:
            for actor in actors[:4]:
                remaining = timeout_s - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    result = actor.client.search("", actor.corpus["recall_queries"][0]["query"],
                                                  timeout_s=min(10, remaining))
                    search_recovered |= result.status_code == 200
                except Exception:
                    pass
        elapsed = time.monotonic() - started
        samples.append({"at_s": elapsed, "pending": len(pending), "completed_since_poll": completed,
                        "failed_since_poll": failed, "search_http_recovered": search_recovered})
        result = {"status": "RECOVERING", "samples": samples, "pending_after_drain": len(pending),
                  "elapsed_s": elapsed, "recovery_window_s": timeout_s, "container": after}
        if checkpoint:
            checkpoint(result)
        if not pending and search_recovered:
            result.update(status="RECOVERED", reason="load-stopped-and-service-drained")
            return result
        if elapsed >= timeout_s:
            result.update(status="BOUNDARY_OBSERVED",
                          reason="backlog-not-drained-within-window" if pending else "search-unavailable-after-load")
            return result
        time.sleep(min(5, timeout_s - elapsed))
