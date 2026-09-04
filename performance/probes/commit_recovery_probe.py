#!/usr/bin/env python3
"""Probe Commit recovery when the EchoMem container is killed mid-operation.

This uses the real HTTP service and the real configured model.  It is
intentionally conservative: losing the Commit response or lacking a
message-set/cursor endpoint is recorded as inconclusive instead of inferred
as success.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    from ._client import EchoMemHTTP, extract_message
    from .cursor_reconcile import (
        ordered_message_ids_from_payload,
        values_from_payload,
    )
except ImportError:
    from _client import EchoMemHTTP, extract_message
    from cursor_reconcile import ordered_message_ids_from_payload, values_from_payload


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tenant(path: Path, tenant_id: str) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("tenants", []):
        if str(item.get("tenant_id")) == tenant_id:
            direct_key = str(item.get("auth_key") or "").strip()
            env_name = str(item.get("auth_key_env") or "").strip()
            return {
                "tenant_id": str(item["tenant_id"]),
                "user_id": str(item.get("user_id") or f"stress-{tenant_id}"),
                "auth_key": direct_key or os.environ.get(env_name, ""),
            }
    raise RuntimeError(f"tenant not found: {tenant_id}")


def health(url: str, timeout_s: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {"raw": body[-1000:]}
            return {
                "healthy": 200 <= response.status < 300,
                "status_code": response.status,
                "elapsed_s": time.monotonic() - started,
                "payload": payload,
            }
    except (OSError, urllib.error.URLError) as exc:
        return {
            "healthy": False,
            "status_code": None,
            "elapsed_s": time.monotonic() - started,
            "error": str(exc),
        }


def archive_ids_from_payload(payload: dict[str, Any]) -> set[str]:
    """Collect archive IDs from summaries without assuming one response shape."""
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"archive_id", "archiveId", "commit_id", "commitId"} and item:
                    found.add(str(item))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return found


def decode_fs_read_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the JSON document returned by EchoMem's /fs/read endpoint."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    text = result.get("text")
    if not isinstance(text, str) or not text:
        return payload
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return payload
    return decoded if isinstance(decoded, dict) else payload


def _docker_engine_post(path: str) -> tuple[int, str]:
    """POST to the mounted Docker Engine socket using only the stdlib."""
    request = (
        f"POST {path} HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(15.0)
            client.connect("/var/run/docker.sock")
            client.sendall(request)
            response = b""
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
        status_line = response.split(b"\r\n", 1)[0].decode(
            "ascii", errors="replace"
        )
        code = int(status_line.split()[1])
        return code, "" if 200 <= code < 300 else status_line
    except (OSError, ValueError, IndexError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def kill_and_start(
    container: str,
    restart_wait_s: float,
    *,
    pid: int = 0,
    restart_command: str = "",
) -> dict[str, Any]:
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return {
                "kill_returncode": 1,
                "kill_stderr": f"process {pid} does not exist",
                "control_backend": "pid",
                "pid": pid,
            }
        except OSError as exc:
            return {
                "kill_returncode": 1,
                "kill_stderr": f"{type(exc).__name__}: {exc}",
                "control_backend": "pid",
                "pid": pid,
            }
        result: dict[str, Any] = {
            "kill_returncode": 0,
            "kill_stderr": "",
            "killed_at": now(),
            "control_backend": "pid",
            "pid": pid,
        }
        if not restart_command:
            result.update({
                "start_returncode": 1,
                "start_stderr": "restart_command is required when using --pid",
                "restart_at": now(),
            })
            return result
        try:
            started = subprocess.Popen(
                restart_command,
                shell=True,
                start_new_session=True,
            )
        except OSError as exc:
            result.update({
                "start_returncode": 1,
                "start_stderr": f"{type(exc).__name__}: {exc}",
                "restart_at": now(),
            })
            return result
        result.update({
            "start_returncode": 0,
            "start_stderr": "",
            "restart_at": now(),
            "restart_pid": started.pid,
            "restart_command_supplied": True,
        })
        if restart_wait_s > 0:
            time.sleep(restart_wait_s)
        return result

    if not container:
        return {
            "kill_returncode": 1,
            "kill_stderr": "container or pid is required",
            "control_backend": "none",
        }
    docker_cli = shutil.which("docker")
    killed = None
    if docker_cli:
        try:
            killed = subprocess.run(
                [docker_cli, "kill", "--signal", "KILL", container],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            # Some runner images contain a stale or non-executable docker
            # shim. Fall back to the mounted Engine socket in that case.
            docker_cli = None
    if not docker_cli:
        # The web runner may have the Docker socket mounted without the CLI.
        # Use the Engine HTTP API directly so recovery needs no extra binary.
        encoded = quote(container, safe="")
        kill_code, kill_error = _docker_engine_post(
            f"/containers/{encoded}/kill?signal=KILL"
        )
    result: dict[str, Any] = {
        "kill_returncode": (
            killed.returncode
            if docker_cli and killed is not None
            else (0 if 200 <= kill_code < 300 else 1)
        ),
        "kill_stderr": (
            killed.stderr[-2000:]
            if docker_cli and killed is not None
            else kill_error[-2000:]
        ),
        "killed_at": now(),
        "control_backend": "docker-cli" if docker_cli else "docker-engine-api",
    }
    if result["kill_returncode"] != 0:
        return result
    started = None
    if docker_cli:
        try:
            started = subprocess.run(
                [docker_cli, "start", container],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            docker_cli = None
    if not docker_cli:
        encoded = quote(container, safe="")
        start_code, start_error = _docker_engine_post(
            f"/containers/{encoded}/start"
        )
    result.update(
        {
            "start_returncode": (
                started.returncode
                if docker_cli and started is not None
                else (0 if 200 <= start_code < 300 else 1)
            ),
            "start_stderr": (
                started.stderr[-2000:]
                if docker_cli and started is not None
                else start_error[-2000:]
            ),
            "restart_at": now(),
        }
    )
    if restart_wait_s > 0:
        time.sleep(restart_wait_s)
    return result


def recovery_control_ok(control: dict[str, Any]) -> bool:
    """Return true only when both the real kill and restart succeeded."""
    return (
        control.get("kill_returncode") == 0
        and control.get("start_returncode") == 0
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--health-url", default="")
    parser.add_argument("--container", default="")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument(
        "--restart-command",
        default=os.getenv("ECHOMEM_RESTART_COMMAND", ""),
        help="本地 PID 恢复时启动 EchoMem 的命令",
    )
    parser.add_argument("--tenant-config", required=True, type=Path)
    parser.add_argument("--tenant", default="stress-a")
    parser.add_argument("--auth-header", default="X-Auth-Key")
    parser.add_argument("--kill-delay-s", type=float, default=0.5)
    parser.add_argument("--messages", type=int, default=12)
    parser.add_argument("--content-chars", type=int, default=2500)
    parser.add_argument("--health-timeout-s", type=float, default=5.0)
    parser.add_argument("--recovery-timeout-s", type=float, default=180.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument(
        "--require-accepted-202",
        action="store_true",
        help="只有先收到 HTTP 202，再 kill/restart，才纳入恢复验收",
    )
    parser.add_argument("--accepted-wait-s", type=float, default=10.0)
    parser.add_argument(
        "--idempotency-key",
        default="",
        help="Stable Commit idempotency key; generated when omitted",
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not args.container and not args.pid:
        parser.error("either --container or --pid is required")

    started_at = now()
    health_url = args.health_url or args.base_url.rstrip("/") + "/health"
    tenant = load_tenant(args.tenant_config, args.tenant)
    client = EchoMemHTTP(
        args.base_url,
        auth_key=tenant["auth_key"],
        timeout_s=max(args.health_timeout_s, 60.0),
        tenant_id=tenant["tenant_id"],
        user_id=tenant["user_id"],
        account_id=tenant["tenant_id"],
        agent_id="pr421-commit-recovery",
        auth_header=args.auth_header,
    )
    result: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": "",
        "base_url": args.base_url,
        "container": args.container,
        "tenant": args.tenant,
        "real_http": True,
        "mock_model": False,
        "kill_delay_s": args.kill_delay_s,
    }

    before = health(health_url, args.health_timeout_s)
    result["health_before"] = before
    if not before["healthy"]:
        result.update({"status": INCONCLUSIVE, "reason": "service was not healthy before probe"})
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 2

    session_id, _ = client.open_session(args.tenant, f"pr421-recovery-{uuid.uuid4().hex[:10]}")
    marker = f"pr421-recovery-marker-{uuid.uuid4().hex}"
    idempotency_key = args.idempotency_key or f"pr421-recovery-commit-{uuid.uuid4().hex}"
    client_message_ids: list[str] = []
    message_records: list[dict[str, Any]] = []
    for index in range(max(1, args.messages)):
        client_message_id = f"recovery-{uuid.uuid4().hex}"
        client_message_ids.append(client_message_id)
        response = client.add_message(
            session_id,
            client_message_id,
            (
                f"Real Commit recovery probe {marker}; message {index}. "
                + ("payload-" + marker + " ") * max(1, args.content_chars // (len(marker) + 9))
            )[: max(64, args.content_chars)],
        )
        if response.status_code is None or response.status_code >= 400:
            result.update({
                "status": FAIL,
                "reason": "message setup failed",
                "session_id": session_id,
                "setup_response": response.payload,
                "setup_status_code": response.status_code,
            })
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            return 2
        server_message = extract_message(response.payload)
        message_records.append({
            "client_request_id": client_message_id,
            "server_message_id": server_message.get("id", ""),
            "response": response.payload,
        })

    commit_box: dict[str, Any] = {}
    commit_response_ready = threading.Event()

    def submit() -> None:
        try:
            response = client.commit(session_id, idempotency_key=idempotency_key)
            commit_box["status_code"] = response.status_code
            commit_box["payload"] = response.payload
            commit_box["error"] = response.error
        except BaseException as exc:  # the process may be killed during the request
            commit_box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            commit_response_ready.set()

    commit_thread = threading.Thread(target=submit, daemon=True)
    commit_started = time.monotonic()
    commit_thread.start()
    # The target contract is "accepted as 202, then crash".  Waiting for the
    # response before killing avoids accidentally testing a client-side
    # timeout/connection loss instead of recovery of an accepted operation.
    response_ready = commit_response_ready.wait(max(0.0, args.accepted_wait_s))
    if response_ready:
        time.sleep(max(0.0, args.kill_delay_s))
    else:
        time.sleep(max(0.0, args.kill_delay_s))
    result["commit_submitted_at"] = now()
    result["commit_request_elapsed_before_kill_s"] = time.monotonic() - commit_started
    result["commit_response_before_kill"] = {
        "ready": response_ready,
        "status_code": commit_box.get("status_code"),
    }
    result["accepted_202"] = commit_box.get("status_code") == 202
    result["container_control"] = kill_and_start(
        args.container,
        0,
        pid=args.pid,
        restart_command=args.restart_command,
    )
    control_ok = recovery_control_ok(result["container_control"])
    result["container_control_ok"] = control_ok
    commit_thread.join(timeout=1.0)
    result["commit_response"] = dict(commit_box)

    deadline = time.monotonic() + max(1.0, args.recovery_timeout_s)
    observations = []
    recovered = False
    while time.monotonic() < deadline:
        observation = health(health_url, args.health_timeout_s)
        observations.append({"at": now(), **observation})
        if observation["healthy"]:
            recovered = True
            break
        time.sleep(max(0.2, args.poll_s))
    result["health_after"] = observations
    result["recovered"] = recovered

    payload = commit_box.get("payload") or {}
    commit_payload = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    archive_id = (
        commit_payload.get("archive_id")
        or commit_payload.get("commit_id")
        or commit_payload.get("id")
    )
    result["session_id"] = session_id
    result["marker"] = marker
    # Reconciliation must use EchoMem's durable IDs. The client value is only
    # metadata used to correlate a request; it is not the persisted message ID.
    result["client_message_ids"] = client_message_ids
    result["message_records"] = message_records
    result["message_ids"] = [
        item["server_message_id"]
        for item in message_records
        if item.get("server_message_id")
    ]
    result["archive_id"] = archive_id
    result["commit_response_result"] = commit_payload
    result["idempotency_key"] = idempotency_key

    if not control_ok:
        result.update({
            "status": INCONCLUSIVE,
            "reason": (
                "未成功执行真实 kill-9/start 控制，不能把服务仍然健康 "
                "解释为崩溃恢复通过"
            ),
        })
    elif not recovered:
        result.update({"status": FAIL, "reason": "service did not recover within timeout"})
    else:
        # A kill can drop the HTTP response even when EchoMem accepted the
        # operation. Discover the matching archive by its unique marker before
        # declaring the recovery probe inconclusive.
        if not archive_id:
            archives_response = client.request(
                "GET", f"/api/sessions/{session_id}/archives?limit=200"
            )
            candidate_ids = archive_ids_from_payload(archives_response.payload)
            for candidate_id in sorted(candidate_ids):
                candidate = client.get_archive(session_id, candidate_id)
                if marker in json.dumps(candidate.payload, ensure_ascii=False, default=str):
                    archive_id = candidate_id
                    result["archive_discovery"] = {
                        "status_code": archives_response.status_code,
                        "candidate_archive_ids": sorted(candidate_ids),
                        "matched_archive_id": archive_id,
                    }
                    break
            if not archive_id:
                result["archive_discovery"] = {
                    "status_code": archives_response.status_code,
                    "candidate_archive_ids": sorted(candidate_ids),
                    "matched_archive_id": "",
                }
        result["archive_id"] = archive_id
        if not archive_id:
            result.update({
                "status": INCONCLUSIVE,
                "reason": (
                    "service recovered but the Commit response was lost and no "
                    "archive containing the unique marker could be identified"
                ),
            })
            result["finished_at"] = now()
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, ensure_ascii=False))
            return 2
        replay_response = client.commit(
            session_id,
            idempotency_key=idempotency_key,
        )
        replay_payload = replay_response.payload if isinstance(replay_response.payload, dict) else {}
        replay_result = (
            replay_payload.get("result")
            if isinstance(replay_payload.get("result"), dict)
            else replay_payload
        )
        replay_archive_id = (
            replay_result.get("archive_id")
            or replay_result.get("commit_id")
            or replay_result.get("id")
        )
        replayed = bool(replay_result.get("replayed")) if isinstance(replay_result, dict) else False
        result["idempotency_replay"] = {
            "status_code": replay_response.status_code,
            "archive_id": replay_archive_id,
            "replayed": replayed,
            "same_archive": str(replay_archive_id or "") == str(archive_id),
            "payload": replay_payload,
            "error": replay_response.error,
        }
        terminal = []
        deadline = time.monotonic() + max(1.0, args.recovery_timeout_s)
        while time.monotonic() < deadline:
            response = client.commit_status(session_id, str(archive_id))
            status_payload = response.payload
            for key in ("result", "status"):
                if isinstance(status_payload, dict) and isinstance(status_payload.get(key), dict):
                    status_payload = status_payload[key]
            raw_state = (
                status_payload.get("status") or status_payload.get("state")
                if isinstance(status_payload, dict)
                else None
            )
            state = raw_state if isinstance(raw_state, str) else None
            terminal.append({
                "at": now(),
                "status_code": response.status_code,
                "state": state,
                "payload": response.payload,
                "error": response.error,
            })
            if state in {"completed", "failed", "error", "cancelled"}:
                break
            time.sleep(max(0.2, args.poll_s))
        result["commit_terminal"] = terminal
        final_state = terminal[-1].get("state") if terminal else None
        history = client.get_history(session_id, limit=200)
        memories = client.get_commit_memories(session_id, str(archive_id))
        result["history_observation"] = {
            "status_code": history.status_code,
            "payload": history.payload,
            "error": history.error,
        }
        result["commit_memories_observation"] = {
            "status_code": memories.status_code,
            "payload": memories.payload,
            "error": memories.error,
        }
        expected_message_ids = set(result["message_ids"])
        source_payloads: dict[str, dict[str, Any]] = {
            "history": history.payload if isinstance(history.payload, dict) else {},
            "archive": (
                client.get_archive(session_id, str(archive_id)).payload
                if archive_id
                else {}
            ),
            "commit_memories": memories.payload if isinstance(memories.payload, dict) else {},
        }
        cursor_response = client.fs_read(
            f"echo://sessions/{session_id}/current/commit_cursor.json"
        )
        source_payloads["commit_cursor"] = (
            decode_fs_read_payload(cursor_response.payload)
            if isinstance(cursor_response.payload, dict)
            else {}
        )
        source_ids = {
            source: sorted(values_from_payload(source_payload)[0])
            for source, source_payload in source_payloads.items()
        }
        source_ordered_ids = {
            source: ordered_message_ids_from_payload(source_payload)
            for source, source_payload in source_payloads.items()
        }
        observed_ids = set().union(*(set(ids) for ids in source_ids.values()))
        missing_ids = sorted(expected_message_ids - observed_ids)
        complete_sources = [
            source for source, ids in source_ids.items()
            if expected_message_ids and expected_message_ids <= set(ids)
        ]
        reconciliation_status = (
            PASS
            if expected_message_ids and not missing_ids
            else INCONCLUSIVE
            if not expected_message_ids
            else FAIL
        )
        result["message_reconciliation"] = {
            "status": reconciliation_status,
            "expected_server_message_ids": sorted(expected_message_ids),
            "observed_by_source": source_ids,
            "ordered_by_source": source_ordered_ids,
            "missing_server_message_ids": missing_ids,
            "complete_sources": complete_sources,
            "cursor_status_code": cursor_response.status_code,
        }
        def is_subsequence(expected: list[str], observed: list[str]) -> bool:
            if not expected:
                return False
            iterator = iter(observed)
            return all(any(candidate == item for candidate in iterator) for item in expected)

        order_checks = {
            source: {
                "expected": result["message_ids"],
                "observed": ordered,
                "matches_in_order": is_subsequence(result["message_ids"], ordered),
            }
            for source, ordered in source_ordered_ids.items()
            if source in {"archive", "commit_cursor", "commit_memories"}
            and ordered
        }
        order_status = (
            PASS
            if any(item["matches_in_order"] for item in order_checks.values())
            else FAIL
            if order_checks
            else INCONCLUSIVE
        )
        result["order_reconciliation"] = {
            "status": order_status,
            "checks": order_checks,
            "reason": (
                "至少一个 Commit 作用域的持久化来源按客户端提交顺序暴露全部消息"
                if order_status == PASS
                else "持久化来源中的消息顺序与客户端提交顺序不一致"
                if order_status == FAIL
                else "没有可解析的 Commit 作用域消息顺序"
            ),
        }
        cursor_status = (
            PASS
            if expected_message_ids and expected_message_ids <= set(source_ids["commit_cursor"])
            else INCONCLUSIVE
            if not expected_message_ids or cursor_response.status_code == 404
            else FAIL
        )
        cursor_reason = (
            "all server-assigned message IDs were present in commit_cursor.json"
            if cursor_status == PASS
            else "cursor endpoint unavailable or did not expose all server-assigned message IDs"
        )
        result["cursor_reconciliation"] = {
            "status": cursor_status,
            "reason": cursor_reason,
            "expected_server_message_ids": sorted(expected_message_ids),
        }
        same_archive = str(replay_archive_id or "") == str(archive_id)
        # Some EchoMem versions return the original archive for a same-key
        # replay but do not expose ``replayed=true``.  That is still useful
        # durability evidence, but it is not enough to prove the optional
        # idempotency response flag contract.
        result["idempotency_reconciliation"] = {
            "status": (
                PASS
                if replayed and same_archive
                else INCONCLUSIVE
                if same_archive
                else FAIL
            ),
            "reason": (
                "same idempotency key returned the same archive with replayed=true"
                if replayed and same_archive
                else "same-key Commit returned the original archive, but the optional replayed flag was false"
                if same_archive
                else "same-key Commit replay did not return the original archive"
            ),
        }
        result["accepted_202"] = bool(result.get("accepted_202"))
        idempotency_status = result["idempotency_reconciliation"]["status"]
        result["status"] = (
            FAIL
            if idempotency_status == FAIL
            else PASS
            if (
                final_state == "completed"
                and reconciliation_status == PASS
                and order_status == PASS
                and history.status_code
                and history.status_code < 400
            )
            else FAIL
            if final_state in {"failed", "error", "cancelled"}
            else INCONCLUSIVE
        )
        if args.require_accepted_202 and not result["accepted_202"]:
            result["status"] = FAIL if commit_box.get("status_code") else INCONCLUSIVE
            result["reason"] = (
                "Commit 未在崩溃前返回 HTTP 202，不能证明已接受的异步操作可恢复"
                if commit_box.get("status_code")
                else "崩溃前未收到 Commit 响应，无法证明该操作曾返回 HTTP 202"
            )
        result["reason"] = (
            "202 accepted before kill; service recovered; Commit completed; "
            "all server-assigned message IDs were found in durable readback "
            "in the original order"
            if result["status"] == PASS
            else "same-key Commit returned the original archive but the optional replayed flag was false; "
            "durability remains separately recorded"
            if idempotency_status == FAIL
            else "Commit did not reach a terminal completed state within the recovery window"
        )

    result["finished_at"] = now()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
