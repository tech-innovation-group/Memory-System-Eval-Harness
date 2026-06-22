#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(text: Any, limit: int = 900) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def token_estimate(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4) if text else 0


def token_char_estimate(tokens: Any, chars_per_token: int = 4) -> int:
    try:
        token_count = max(0, int(tokens or 0))
    except (TypeError, ValueError):
        return 0
    try:
        multiplier = max(1, int(chars_per_token or 4))
    except (TypeError, ValueError):
        multiplier = 4
    return token_count * multiplier


def resolve_openviking_workspace() -> str:
    token_dir_env = str(os.environ.get("OPENVIKING_LLM_TOKEN_USAGE_DIR") or "").strip()
    if token_dir_env:
        token_dir = Path(token_dir_env).expanduser().resolve()
        if token_dir.name == "llm_token_usage" and token_dir.parent.name == "_system":
            return str(token_dir.parent.parent)

    config_file = str(os.environ.get("OPENVIKING_CONFIG_FILE") or "").strip()
    if config_file:
        try:
            payload = json.loads(Path(config_file).expanduser().read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        storage = (payload.get("storage") or {}) if isinstance(payload, dict) else {}
        workspace_value = str(storage.get("workspace") or "").strip()
        if workspace_value:
            return str(Path(workspace_value).expanduser().resolve())
    return ""


def resolve_openviking_token_usage_dir() -> str:
    workspace = resolve_openviking_workspace()
    if workspace:
        return str((Path(workspace) / "_system" / "llm_token_usage").resolve())
    token_dir_env = str(os.environ.get("OPENVIKING_LLM_TOKEN_USAGE_DIR") or "").strip()
    if token_dir_env:
        return str(Path(token_dir_env).expanduser().resolve())
    return ""


def session_number(key: str) -> int:
    return int(str(key).split("_")[1])


def locomo_samples(data: list[dict[str, Any]], sample_filter: str) -> list[tuple[int, dict[str, Any]]]:
    rows = []
    for index, sample in enumerate(data):
        sample_id = str(sample.get("sample_id") or f"sample_{index}")
        if sample_filter not in ("", "all") and sample_filter not in {str(index), sample_id}:
            continue
        rows.append((index, sample))
    return rows


def parse_datetime(value: str) -> datetime | None:
    value = str(value or "").strip()
    for fmt in ("%I:%M %p on %d %B, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def build_session_batches(sample: dict[str, Any], group_chat: bool = False) -> tuple[list[dict[str, Any]], int]:
    conv = sample.get("conversation") or {}
    keys = [key for key, value in conv.items() if re.fullmatch(r"session_\d+", str(key)) and isinstance(value, list)]
    keys.sort(key=session_number)
    sessions: list[dict[str, Any]] = []
    total_tokens = 0
    for key in keys:
        base_dt = parse_datetime(str(conv.get(f"{key}_date_time") or ""))
        messages: list[dict[str, Any]] = []
        for idx, raw in enumerate(conv.get(key) or []):
            if not isinstance(raw, dict):
                continue
            speaker = raw.get("speaker") or raw.get("role") or "speaker"
            dia_id = raw.get("dia_id") or f"{key}:{idx}"
            if group_chat:
                content = compact(str(raw.get("text") or ""))
            else:
                content = compact(f"{speaker}: {raw.get('text') or ''}")
            if not content:
                continue
            item = {
                "role": "user",
                "content": content,
                "parts": [{"type": "text", "text": content}],
                "speaker": str(speaker),
                "dia_id": str(dia_id),
            }
            if base_dt:
                item["created_at"] = (base_dt + timedelta(seconds=idx)).isoformat()
            if group_chat and raw.get("speaker"):
                item["role_id"] = str(raw.get("speaker"))
            messages.append(item)
        total_tokens += sum(token_estimate(msg["content"]) for msg in messages)
        if messages:
            sessions.append(
                {
                    "session_key": key,
                    "date_time": str(conv.get(f"{key}_date_time") or ""),
                    "messages": messages,
                }
            )
    return sessions, total_tokens


def build_messages(sample: dict[str, Any], group_chat: bool = False) -> tuple[list[dict[str, Any]], int]:
    sessions, total_tokens = build_session_batches(sample, group_chat=group_chat)
    messages: list[dict[str, Any]] = []
    for session in sessions:
        messages.extend(session["messages"])
    return messages, total_tokens


class OpenVikingHTTP:
    def __init__(self, base_url: str, api_key: str, account: str, user: str, agent: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.account = account
        self.user = user
        self.agent = agent
        self.timeout = timeout

    def headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-OpenViking-Account": self.account,
            "X-OpenViking-User": self.user,
            "X-OpenViking-Agent": self.agent,
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = Request(self.base_url + path, data=body, headers=self.headers(), method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {path}: {detail[:1000]}") from exc
        except URLError as exc:
            raise RuntimeError(f"cannot connect OpenViking {self.base_url}: {exc}") from exc
        return json.loads(text) if text else {}

    def try_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        try:
            return self.request(method, path, payload), ""
        except RuntimeError as exc:
            return None, str(exc)

    @staticmethod
    def result(data: dict[str, Any]) -> dict[str, Any]:
        if data.get("status") == "error":
            raise RuntimeError(json.dumps(data, ensure_ascii=False)[:1000])
        inner = data.get("result")
        return inner if isinstance(inner, dict) else data


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def poll_task(client: OpenVikingHTTP, task_id: str, timeout_s: int) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.result(client.request("GET", f"/api/v1/tasks/{task_id}"))
        status = str(last.get("status") or "").lower()
        print(f"[commit] task={task_id} status={status or 'unknown'}", flush=True)
        if status in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(2)
    last["status"] = last.get("status") or "timeout"
    return last


def extract_import_token_usage(session_record: dict[str, Any]) -> dict[str, int]:
    task_usage = (((session_record.get("task") or {}).get("result") or {}).get("token_usage") or {})
    task_llm = task_usage.get("llm") or {}
    task_embedding = task_usage.get("embedding") or {}
    task_total = task_usage.get("total") or {}
    after_commit = session_record.get("session_after_commit") or {}
    after_llm = after_commit.get("llm_token_usage") or {}
    after_embedding = after_commit.get("embedding_token_usage") or {}

    llm_prompt_tokens = int(task_llm.get("prompt_tokens") or after_llm.get("prompt_tokens") or 0)
    llm_completion_tokens = int(
        task_llm.get("completion_tokens") or after_llm.get("completion_tokens") or 0
    )
    llm_total_tokens = int(
        task_llm.get("total_tokens")
        or after_llm.get("total_tokens")
        or (llm_prompt_tokens + llm_completion_tokens)
    )
    embedding_total_tokens = int(
        task_embedding.get("total_tokens") or after_embedding.get("total_tokens") or 0
    )
    import_total_tokens = int(
        task_total.get("total_tokens") or (llm_total_tokens + embedding_total_tokens)
    )
    return {
        "import_llm_prompt_tokens": llm_prompt_tokens,
        "import_llm_completion_tokens": llm_completion_tokens,
        "import_llm_total_tokens": llm_total_tokens,
        "import_embedding_total_tokens": embedding_total_tokens,
        "import_total_tokens": import_total_tokens,
    }


def aggregate_import_token_usage(records: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "import_llm_prompt_tokens": 0,
        "import_llm_completion_tokens": 0,
        "import_llm_total_tokens": 0,
        "import_embedding_total_tokens": 0,
        "import_total_tokens": 0,
    }
    for record in records:
        for key in totals:
            totals[key] += int(record.get(key) or 0)
    return totals


def import_one_openviking_session(
    args: argparse.Namespace,
    client: OpenVikingHTTP,
    session_id: str,
    messages: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    print(f"[import] session={session_id} label={label} expected_messages={len(messages)}", flush=True)
    create = client.result(client.request("POST", "/api/v1/sessions", {"session_id": session_id}))
    added = 0
    batch_supported = True
    for part_no, batch in enumerate(chunked(messages, args.batch_size), start=1):
        print(f"[import] {label} batch={part_no} adding={len(batch)}", flush=True)
        for offset, msg in enumerate(batch, start=1):
            print(
                "[message] "
                + json.dumps(
                    {
                        "label": label,
                        "message_index": added + offset,
                        "message_total": len(messages),
                        "role": msg.get("role") or "",
                        "role_id": msg.get("role_id") or "",
                        "speaker": msg.get("speaker") or msg.get("role_id") or "",
                        "dia_id": msg.get("dia_id") or "",
                        "content": compact(msg.get("content") or "", 260),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if batch_supported:
            raw, error = client.try_request("POST", f"/api/v1/sessions/{session_id}/messages/batch", {"messages": batch})
            if raw is not None:
                res = client.result(raw)
                added += int(res.get("added") or len(batch))
                print(f"[verify] {label} live_messages={res.get('message_count')} added_total={added}/{len(messages)}", flush=True)
                continue
            if "HTTP 404" not in error:
                raise RuntimeError(error)
            batch_supported = False
            print("[import] batch endpoint unavailable; fallback to single-message API", flush=True)
        for msg in batch:
            client.result(client.request("POST", f"/api/v1/sessions/{session_id}/messages", msg))
            added += 1
            if added == len(messages) or added % 25 == 0:
                print(f"[verify] {label} added_total={added}/{len(messages)}", flush=True)
    before_commit = client.result(client.request("GET", f"/api/v1/sessions/{session_id}"))
    live_count = int(before_commit.get("message_count") or before_commit.get("messages_count") or added)
    live_complete = live_count == len(messages)
    print(f"[verify] {label} before_commit expected={len(messages)} actual={live_count} complete={live_complete}", flush=True)
    commit = client.result(client.request("POST", f"/api/v1/sessions/{session_id}/commit", {}))
    task_id = str(commit.get("task_id") or "")
    print(f"[commit] {label} status={commit.get('status')} task_id={task_id or '-'}", flush=True)
    task = poll_task(client, task_id, args.commit_timeout_s) if task_id and args.wait_commit else {}
    after_commit = client.result(client.request("GET", f"/api/v1/sessions/{session_id}"))
    pending = int(after_commit.get("message_count") or after_commit.get("messages_count") or after_commit.get("pending_messages") or 0)
    task_status = str(task.get("status") or commit.get("status") or "").lower()
    committed = str(commit.get("status") or "").lower() in {"accepted", "committed", "ok"} or task_status == "completed"
    archive_complete = committed and pending == 0
    integrity = "complete" if live_complete and archive_complete else "incomplete"
    result = {
        "session_id": session_id,
        "expected_messages": len(messages),
        "submitted_messages": added,
        "live_message_count_before_commit": live_count,
        "pending_message_count_after_commit": pending,
        "live_complete_before_commit": live_complete,
        "archive_complete_after_commit": archive_complete,
        "integrity": integrity,
        "create_response": create,
        "commit_response": commit,
        "task": task,
        "session_before_commit": before_commit,
        "session_after_commit": after_commit,
    }
    result.update(extract_import_token_usage(result))
    return result


def import_sample(args: argparse.Namespace, sample_index: int, sample: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    sample_id = str(sample.get("sample_id") or f"sample_{sample_index}")
    session_batches, estimated_tokens = build_session_batches(sample, group_chat=args.group_chat)
    if int(args.max_sessions or 0) > 0:
        session_batches = session_batches[: int(args.max_sessions)]
        estimated_tokens = sum(
            token_estimate(msg["content"])
            for batch in session_batches
            for msg in batch["messages"]
        )
    client = OpenVikingHTTP(
        args.openviking_url,
        args.api_key,
        args.account,
        args.user_id or sample_id,
        args.agent_id or sample_id,
        args.timeout_s,
    )
    print(f"[import] sample={sample_id} mode={args.session_mode} sessions={len(session_batches)}", flush=True)
    session_records: list[dict[str, Any]] = []
    if args.session_mode == "locomo":
        for batch in session_batches:
            suffix = batch["session_key"].replace("session_", "s")
            session_id = f"locomo-{sample_id}-{suffix}-{uuid.uuid4().hex[:8]}"
            rec = import_one_openviking_session(args, client, session_id, batch["messages"], f"{sample_id}/{batch['session_key']}")
            rec["session_key"] = batch["session_key"]
            rec["date_time"] = batch["date_time"]
            session_records.append(rec)
    else:
        messages, _ = build_messages(sample, group_chat=args.group_chat)
        session_id = f"locomo-{sample_id}-{uuid.uuid4().hex[:8]}"
        rec = import_one_openviking_session(args, client, session_id, messages, sample_id)
        rec["session_key"] = "all"
        rec["date_time"] = ""
        session_records.append(rec)
    expected_messages = sum(item["expected_messages"] for item in session_records)
    submitted_messages = sum(item["submitted_messages"] for item in session_records)
    pending_messages = sum(item["pending_message_count_after_commit"] for item in session_records)
    integrity = "complete" if session_records and all(item["integrity"] == "complete" for item in session_records) else "incomplete"
    token_usage = aggregate_import_token_usage(session_records)
    record = {
        "sample_index": sample_index,
        "sample_id": sample_id,
        "session_id": session_records[0]["session_id"] if len(session_records) == 1 else f"locomo-{sample_id}-*",
        "session_mode": args.session_mode,
        "group_chat": bool(args.group_chat),
        "user_id": args.user_id or sample_id,
        "agent_id": args.agent_id or sample_id,
        "session_count": len(session_records),
        "session_records": session_records,
        "expected_messages": expected_messages,
        "submitted_messages": submitted_messages,
        "live_message_count_before_commit": expected_messages,
        "pending_message_count_after_commit": pending_messages,
        "live_complete_before_commit": submitted_messages == expected_messages,
        "archive_complete_after_commit": pending_messages == 0,
        "integrity": integrity,
        "estimated_import_tokens": estimated_tokens,
    }
    record.update(token_usage)
    (out_dir / f"{sample_id}_messages.json").write_text(json.dumps(session_batches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] sample={sample_id} integrity={integrity}", flush=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Import LoCoMo conversations into OpenViking and verify commit_session completeness.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--openviking-url", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--sample", default="all")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--session-mode", choices=["locomo", "single"], default="locomo")
    parser.add_argument("--max-sessions", type=int, default=0)
    parser.add_argument("--group-chat", dest="group_chat", action="store_true", default=True)
    parser.add_argument("--no-group-chat", dest="group_chat", action="store_false")
    parser.add_argument("--timeout-s", type=int, default=60)
    parser.add_argument("--commit-timeout-s", type=int, default=300)
    parser.add_argument("--wait-commit", action="store_true", default=True)
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data = read_json(Path(args.dataset).expanduser().resolve())
    if not isinstance(data, list):
        raise ValueError("LoCoMo dataset must be a JSON list")
    samples = locomo_samples(data, args.sample)
    if not samples:
        raise ValueError(f"no LoCoMo sample matched: {args.sample}")
    print(f"[start] dataset={args.dataset} samples={len(samples)} openviking={args.openviking_url}", flush=True)
    records = [import_sample(args, index, sample, out_dir) for index, sample in samples]
    complete = sum(1 for item in records if item["integrity"] == "complete")
    token_usage = aggregate_import_token_usage(records)
    workspace = resolve_openviking_workspace()
    summary = {
        "status": "OPENVIKING_IMPORT_DONE" if complete == len(records) else "OPENVIKING_IMPORT_INCOMPLETE",
        "samples": len(records),
        "complete_samples": complete,
        "incomplete_samples": len(records) - complete,
        "expected_messages": sum(item["expected_messages"] for item in records),
        "submitted_messages": sum(item["submitted_messages"] for item in records),
        "estimated_import_tokens": sum(item["estimated_import_tokens"] for item in records),
        "openviking_url": args.openviking_url,
        "workspace": workspace,
        "llm_log_dir": resolve_openviking_token_usage_dir(),
        "sample": args.sample,
        "group_chat": bool(args.group_chat),
        "session_limit": int(args.max_sessions or 0),
        "identity_mode": "sample_id_user_agent" if not args.user_id and not args.agent_id else "fixed_user_agent",
        "records": records,
    }
    summary.update(token_usage)
    summary.update(
        {
            "import_llm_prompt_chars_est": token_char_estimate(summary.get("import_llm_prompt_tokens")),
            "import_llm_completion_chars_est": token_char_estimate(summary.get("import_llm_completion_tokens")),
            "import_llm_total_chars_est": token_char_estimate(summary.get("import_llm_total_tokens")),
            "import_embedding_chars_est": token_char_estimate(summary.get("import_embedding_total_tokens")),
            "import_total_chars_est": token_char_estimate(summary.get("import_total_tokens")),
        }
    )
    (out_dir / "openviking_import_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if summary["incomplete_samples"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
