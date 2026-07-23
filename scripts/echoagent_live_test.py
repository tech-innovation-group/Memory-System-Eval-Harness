#!/usr/bin/env python3
"""EchoAgent live test: end-to-end memory recall + prefill latency evaluation.

This script simulates a real user interacting with EchoAgent via HTTP API,
measuring memory recall quality and prefill (KV-cache warmup) effectiveness.

Usage:
    python scripts/echoagent_live_test.py \
        --echoagent-url http://127.0.0.1:31020 \
        --username test_user --password test_password \
        --out-dir runs/<run_id>/echoagent_live_test
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory import llm
from memory import dynamic_evaluator

# Import new metrics modules
try:
    from runtime_metrics_client import RuntimeMetricsClient, histogram_quantile, histogram_sum, histogram_mean
    from accuracy_evaluator import AccuracyEvaluator
    METRICS_MODULES_AVAILABLE = True
except ImportError:
    METRICS_MODULES_AVAILABLE = False


# ---------------------------------------------------------------------------
# EchoAgent HTTP client
# ---------------------------------------------------------------------------

def _encode_context_path(context_path: str) -> str:
    """Encode context_path for URL embedding — '/' must become %2F or NestJS treats it as a route separator."""
    return quote(context_path, safe="")


class EchoAgentClient:
    """Thin HTTP wrapper for the EchoAgent backend API."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: str = ""
        self._csrf_token: str = ""
        self._context_seq: dict[str, int] = {}  # key="{sessionId}:{contextPath}" → latestContextSeq

    def _headers(self, json_content: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8") if body else None
        req = Request(f"{self.base_url}{path}", data=data, headers=self._headers())
        req.method = "POST"
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def _get(self, path: str) -> dict[str, Any]:
        req = Request(f"{self.base_url}{path}", headers=self._headers(json_content=False))
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def login(self) -> None:
        body = {"username": self.username, "password": self.password}
        data = json.dumps(body).encode("utf-8")
        req = Request(
            f"{self.base_url}/v1/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8", "replace"))
        self.token = result.get("access_token") or ""
        self._csrf_token = result.get("csrfToken") or ""
        if not self.token:
            for cookie_header in resp.headers.get_all("Set-Cookie") or []:
                if "access_token=" in cookie_header:
                    self.token = cookie_header.split("access_token=")[1].split(";")[0]
        if not self.token:
            raise RuntimeError(f"Login succeeded but no token obtained: {list(result.keys())}")

    def create_session(self, title: str = "", memory_engine_endpoint: str = "") -> str:
        result = self._post("/v1/sessions", {"title": title or f"test-{uuid.uuid4().hex[:8]}"})
        session_id = result.get("data", result).get("id") or result.get("id", "")
        # Enable session-level memory engine so prefetch tick/finalize works.
        # Must test first, then enable.
        if session_id and memory_engine_endpoint:
            try:
                self._post(f"/v1/sessions/{session_id}/memory-engine/test", {
                    "endpoint": memory_engine_endpoint,
                })
                self._put(f"/v1/sessions/{session_id}/memory-engine", {
                    "enabled": True,
                    "endpoint": memory_engine_endpoint,
                })
            except Exception as exc:
                print(f"    [warn] failed to enable memory engine for session {session_id}: {exc}", flush=True)
        return session_id

    def _put(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8") if body else None
        req = Request(f"{self.base_url}{path}", data=data, headers=self._headers())
        req.method = "PUT"
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def prefetch_tick(self, session_id: str, context_path: str, client_turn_id: str, revision: int, draft_text: str) -> dict[str, Any]:
        path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/prefetch/tick"
        return self._post(path, {
            "clientTurnId": client_turn_id,
            "revision": revision,
            "draftText": draft_text,
        })

    def prefetch_finalize(self, session_id: str, context_path: str, client_turn_id: str, full_content: str) -> dict[str, Any]:
        path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/prefetch/finalize"
        return self._post(path, {
            "clientTurnId": client_turn_id,
            "fullContent": full_content,
        })

    def _seq_key(self, session_id: str, context_path: str) -> str:
        return f"{session_id}:{context_path}"

    def get_latest_context_seq(self, session_id: str, context_path: str) -> int:
        """Fetch the current latestContextSeq from the server."""
        try:
            # Send a history-only request (no content) to get latestContextSeq
            path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/messages"
            body = {"afterSeq": 0, "limit": 1}
            result = self._post(path, body)
            data = result.get("data", result)
            seq = data.get("latestContextSeq", 0) or 0
            self._context_seq[self._seq_key(session_id, context_path)] = seq
            return seq
        except Exception:
            return self._context_seq.get(self._seq_key(session_id, context_path), 0)

    def send_message(self, session_id: str, context_path: str, content: str, prefetch_client_turn_id: str = "") -> dict[str, Any]:
        """Send a message with proper afterSeq, retrying on CONTEXT_SEQ_OUTDATED."""
        key = self._seq_key(session_id, context_path)
        after_seq = self._context_seq.get(key, 0)
        path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/messages"
        for attempt in range(3):
            body: dict[str, Any] = {"content": content, "afterSeq": after_seq}
            if prefetch_client_turn_id:
                body["prefetchClientTurnId"] = prefetch_client_turn_id
            result = self._post(path, body)
            data = result.get("data", result)
            # Update tracked seq from server response
            server_seq = data.get("latestContextSeq")
            if isinstance(server_seq, int):
                self._context_seq[key] = server_seq
            # Check for seq outdated error — retry with server's authoritative seq
            if data.get("error") in ("CONTEXT_SEQ_OUTDATED", "SEQ_OUTDATED") and isinstance(server_seq, int):
                after_seq = server_seq
                continue
            return result
        return result

    def stream_reply(self, session_id: str, context_path: str, seq: int) -> dict[str, Any]:
        """Read SSE stream and return {reply, ttft_ms, done_event}."""
        url = f"{self.base_url}/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/streaming?seq={seq}"
        headers = self._headers(json_content=False)
        headers["Accept"] = "text/event-stream"
        headers["Last-Event-ID"] = "-1"
        req = Request(url, headers=headers)
        reply_parts: list[str] = []
        ttft_ms: float | None = None
        send_time = time.monotonic()
        done_event: dict[str, Any] = {}
        with urlopen(req, timeout=300) as resp:
            raw_buffer = b""
            text_buffer = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                raw_buffer += chunk
                # Decode incrementally, keeping incomplete multi-byte sequences in raw_buffer
                try:
                    text = raw_buffer.decode("utf-8")
                    raw_buffer = b""
                except UnicodeDecodeError:
                    # Try decoding all but the last 3 bytes (max UTF-8 char width)
                    text = raw_buffer[:-3].decode("utf-8", errors="replace")
                    raw_buffer = raw_buffer[-3:]
                text_buffer += text
                while "\n\n" in text_buffer:
                    event_block, text_buffer = text_buffer.split("\n\n", 1)
                    event_type = ""
                    data_lines: list[str] = []
                    for line in event_block.splitlines():
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[len("data:"):])
                    event_data = "\n".join(data_lines)
                    if not event_data:
                        continue
                    try:
                        data = json.loads(event_data)
                    except json.JSONDecodeError:
                        continue
                    if event_type in ("create", "append"):
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        content = ""
                        if isinstance(data, dict):
                            fragment = data.get("fragment") or data.get("content") or ""
                            if isinstance(fragment, dict):
                                content = fragment.get("content") or ""
                            else:
                                content = str(fragment)
                        reply_parts.append(content)
                    elif event_type == "done":
                        done_event = data if isinstance(data, dict) else {}
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}
                    elif event_type == "error":
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "error": str(data), "done_event": {}}
        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}

    def get_last_request(self, session_id: str, context_path: str = "/") -> dict[str, Any]:
        try:
            return self._get(f"/v1/sessions/{session_id}/primary-model/last-request?contextPath={_encode_context_path(context_path)}")
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Typing simulation
# ---------------------------------------------------------------------------

def simulate_typing(
    client: EchoAgentClient,
    session_id: str,
    context_path: str,
    text: str,
    typing_speed_ms: int = 100,
    jitter_ms: int = 20,
) -> str:
    """Simulate character-by-character typing via prefetch/tick, return client_turn_id."""
    client_turn_id = uuid.uuid4().hex[:12]
    last_tick_ok = False
    for i in range(1, len(text) + 1):
        draft = text[:i]
        try:
            tick_result = client.prefetch_tick(session_id, context_path, client_turn_id, i, draft)
            tick_data = tick_result.get("data", tick_result)
            if not tick_data.get("accepted") and i == 1:
                # First tick rejected — log reason and stop sending more ticks
                reason = tick_data.get("reason", "unknown")
                print(f"    [prefetch] tick 1 rejected: reason={reason}", flush=True)
                break
            last_tick_ok = tick_data.get("accepted", False)
        except Exception as exc:
            if i == 1:
                print(f"    [prefetch] tick 1 error: {exc}", flush=True)
            break
        delay = typing_speed_ms + random.randint(-jitter_ms, jitter_ms)
        time.sleep(max(10, delay) / 1000.0)
    return client_turn_id


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

def collect_round_metrics(
    round_data: dict[str, Any],
    reply_result: dict[str, Any],
    send_time: float,
    prefetch_committed: bool,
    last_request: dict[str, Any],
    memory_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reply = reply_result.get("reply") or ""
    ttft = reply_result.get("ttft_ms")
    done = reply_result.get("done_event") or {}
    cached_tokens = 0
    prompt_tokens = 0
    if isinstance(done, dict) and done:
        cached_tokens = int(done.get("cachedTokens") or done.get("cached_tokens") or 0)
        prompt_tokens = int(done.get("promptTokens") or done.get("prompt_tokens") or 0)
    elif not done:
        print(f"    [DEBUG] done_event is empty, reply_result keys={list(reply_result.keys())}", flush=True)
    if not cached_tokens and isinstance(last_request, dict):
        item = last_request.get("item") or last_request
        if isinstance(item, dict):
            cached_tokens = int(item.get("cached_tokens") or item.get("cachedTokens") or 0)
    return {
        "round_id": round_data.get("id", ""),
        "query": round_data.get("query", ""),
        "reply": reply,
        "reply_length": len(reply),
        "query_length": len(round_data.get("query", "")),
        "ttft_ms": round(ttft, 1) if ttft is not None else None,
        "cached_tokens": cached_tokens,
        "prompt_tokens": prompt_tokens,
        "prefetch_committed": prefetch_committed,
        "is_new_session": bool(round_data.get("new_session")),
        "is_injection": bool(round_data.get("is_injection")),
        "complexity": round_data.get("complexity", ""),
        "ground_facts": round_data.get("ground_facts", []),
        "error": reply_result.get("error", ""),
        "relevant_memory": json.dumps(memory_items or [], ensure_ascii=False),
    }


def compute_summary(rounds: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    ttft_values = [r["ttft_ms"] for r in rounds if r.get("ttft_ms") is not None and not r.get("is_injection")]
    cached_values = [r["cached_tokens"] for r in rounds if r.get("cached_tokens") and not r.get("is_injection")]
    prompt_values = [r["prompt_tokens"] for r in rounds if r.get("prompt_tokens") and not r.get("is_injection")]
    query_lengths = [r["query_length"] for r in rounds if not r.get("is_injection")]
    reply_lengths = [r["reply_length"] for r in rounds if not r.get("is_injection")]
    new_sessions = sum(1 for r in rounds if r.get("is_new_session"))
    total_queries = sum(1 for r in rounds if not r.get("is_injection"))
    return {
        "total_queries": total_queries,
        "total_rounds": len(rounds),
        "new_sessions": new_sessions,
        "avg_query_length": round(sum(query_lengths) / len(query_lengths), 1) if query_lengths else 0,
        "avg_reply_length": round(sum(reply_lengths) / len(reply_lengths), 1) if reply_lengths else 0,
        "avg_ttft_ms": round(sum(ttft_values) / len(ttft_values), 1) if ttft_values else None,
        "avg_cached_tokens": round(sum(cached_values) / len(cached_values), 1) if cached_values else None,
        "avg_prompt_tokens": round(sum(prompt_values) / len(prompt_values), 1) if prompt_values else None,
        "config": config,
    }


# ---------------------------------------------------------------------------
# Quality evaluation via LLM
# ---------------------------------------------------------------------------

QUALITY_EVAL_PROMPT = """You are an expert evaluator for a memory-augmented AI assistant. Given a set of test queries, the assistant's replies, and the ground-truth facts each query depends on, evaluate how well the assistant recalled and used those facts.

For each query, score on a 0-2 scale:
- 2: The reply correctly uses ALL ground-truth facts (complete recall)
- 1: The reply uses SOME but not all ground-truth facts (partial recall)
- 0: The reply does not use any ground-truth facts (recall failure)

Also assess:
- factual_accuracy: Does the reply contain correct information (no hallucination)? 0-2
- relevance: Is the reply relevant to the query? 0-2

Output ONLY valid JSON:
{
  "per_query": [
    {
      "round_id": "...",
      "query": "...",
      "ground_facts": ["f1", "f2"],
      "recall_score": 0-2,
      "factual_accuracy": 0-2,
      "relevance": 0-2,
      "reasoning": "brief explanation"
    }
  ],
  "overall_score": 0.0-2.0,
  "cross_session_score": 0.0-2.0,
  "same_session_score": 0.0-2.0,
  "summary": "2-3 sentence overall assessment"
}
"""


def generate_quality_report(
    query_rounds: list[dict[str, Any]],
    facts: dict[str, str],
    model: str = "deepseek-v4-flash",
    base_url: str = "",
    api_key: str = "",
) -> dict[str, Any]:
    """Use an LLM to evaluate recall quality based on queries, replies, and ground facts."""
    # Build evaluation context
    eval_items: list[dict[str, Any]] = []
    for r in query_rounds:
        ground_ids = r.get("ground_facts") or []
        ground_texts = [facts.get(fid, fid) for fid in ground_ids]
        eval_items.append({
            "round_id": r.get("round_id", ""),
            "query": r.get("query", ""),
            "reply": (r.get("reply") or "")[:500],  # Truncate long replies
            "ground_facts": ground_texts,
            "is_new_session": r.get("is_new_session", False),
            "complexity": r.get("complexity", ""),
        })

    user_content = json.dumps(eval_items, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": QUALITY_EVAL_PROMPT},
        {"role": "user", "content": f"Here are the test queries and replies to evaluate:\n\n{user_content}"},
    ]

    for attempt in range(3):
        try:
            result = llm.openai_chat(
                messages,
                model=model,
                temperature=0.3,
                api_key=api_key or None,
                base_url=base_url or None,
                timeout=120,
            )
            if "error" in result:
                raise RuntimeError(result["error"])
            text = str(result.get("answer") or "")
            json_match = re.search(r"\{[\s\S]*\}", text)
            if not json_match:
                raise RuntimeError(f"No JSON in response: {text[:200]}")
            report = json.loads(json_match.group())
            # Validate structure
            if "per_query" not in report:
                raise RuntimeError("Missing per_query in report")
            return report
        except Exception as exc:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    # Fallback: simple heuristic scoring
    return _heuristic_quality_report(query_rounds, facts)


def _heuristic_quality_report(
    query_rounds: list[dict[str, Any]],
    facts: dict[str, str],
) -> dict[str, Any]:
    """Fallback heuristic quality report when LLM evaluation fails."""
    per_query = []
    for r in query_rounds:
        ground_ids = r.get("ground_facts") or []
        ground_texts = [facts.get(fid, "") for fid in ground_ids]
        reply = (r.get("reply") or "").lower()
        # Simple keyword overlap score
        matched = sum(1 for gt in ground_texts if gt and any(w in reply for w in gt.split() if len(w) > 1))
        total = len([gt for gt in ground_texts if gt])
        recall_score = 2 if total > 0 and matched >= total else 1 if matched > 0 else 0
        per_query.append({
            "round_id": r.get("round_id", ""),
            "query": r.get("query", ""),
            "ground_facts": ground_ids,
            "recall_score": recall_score,
            "factual_accuracy": 1,
            "relevance": 1,
            "reasoning": "heuristic: keyword overlap",
        })
    cross = [q["recall_score"] for q in per_query if query_rounds[per_query.index(q)].get("is_new_session")]
    same = [q["recall_score"] for q in per_query if not query_rounds[per_query.index(q)].get("is_new_session")]
    return {
        "per_query": per_query,
        "overall_score": round(sum(q["recall_score"] for q in per_query) / len(per_query), 2) if per_query else 0,
        "cross_session_score": round(sum(cross) / len(cross), 2) if cross else None,
        "same_session_score": round(sum(same) / len(same), 2) if same else None,
        "summary": "Heuristic evaluation (LLM evaluation failed)",
    }


# ---------------------------------------------------------------------------
# LoCoMo-format dataset export
# ---------------------------------------------------------------------------

def save_as_locomo_dataset(
    session_conversations: dict[str, list[dict[str, Any]]],
    session_index_map: dict[str, int],
    all_rounds: list[dict[str, Any]],
    all_facts: dict[str, str],
    out_path: Path,
    sample_id: str = "",
) -> None:
    """Save test conversations as a locomo-compatible dataset file.

    Groups sessions into one locomo sample with conversation sessions and QA pairs.
    """
    sample_id = sample_id or f"conv-gen-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # Build conversation object
    conversation: dict[str, Any] = {
        "speaker_a": "用户",
        "speaker_b": "助手",
    }
    # Sort sessions by their index
    sorted_sessions = sorted(session_index_map.items(), key=lambda x: x[1])
    for session_id, session_idx in sorted_sessions:
        turns = session_conversations.get(session_id, [])
        if not turns:
            continue
        conversation[f"session_{session_idx}"] = turns
        conversation[f"session_{session_idx}_date_time"] = datetime.now().strftime("%Y-%m-%d")

    # Build QA pairs from non-injection rounds that have ground_facts
    qa_pairs = []
    for r in all_rounds:
        if r.get("is_injection"):
            continue
        ground_facts = r.get("ground_facts") or []
        # Map fact IDs to evidence references (best-effort: D{session}:{turn})
        evidence = []
        for fid in ground_facts:
            # Try to find which session/turn this fact was injected in
            for sid, turns in session_conversations.items():
                for turn in turns:
                    if turn.get("speaker") == "user" and fid in turn.get("text", ""):
                        evidence.append(turn.get("dia_id", ""))
                        break
        # Use ground truth (fact text) as the answer, not the AI's reply
        fact_texts = [all_facts.get(fid, "") for fid in ground_facts if fid in all_facts]
        answer = "; ".join(fact_texts) if fact_texts else r.get("reply", "")
        qa_pairs.append({
            "question": r.get("query", ""),
            "answer": answer,
            "evidence": evidence,
            "category": 1,  # Factual by default
        })

    sample = {
        "sample_id": sample_id,
        "qa": qa_pairs,
        "conversation": conversation,
        "event_summary": {},
        "observation": {},
        "session_summary": {},
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([sample], ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Dataset replay
# ---------------------------------------------------------------------------

def _resolve_dataset_path(dataset_name: str) -> Path:
    """Resolve dataset name to file path. Checks manifest, then treats as direct path."""
    dataset_dir = ROOT / "dataset"
    manifest_path = dataset_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("datasets", []):
            if entry.get("id") == dataset_name or entry.get("name") == dataset_name:
                return dataset_dir / entry["path"]
    # Direct path
    p = Path(dataset_name)
    if p.exists():
        return p
    # Try under dataset dir
    p2 = dataset_dir / dataset_name
    if p2.exists():
        return p2
    raise FileNotFoundError(f"Dataset not found: {dataset_name}")


def run_replay_test(
    args: argparse.Namespace,
    client: EchoAgentClient,
    log: Any,
    out_dir: Path,
    metrics_client: "RuntimeMetricsClient | None" = None,
    baseline_metrics: dict[str, Any] | None = None,
) -> None:
    """Replay a dataset against EchoAgent, measuring recall quality."""
    from memory import datasets
    scripts_dir = ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import benchmark_adapter

    dataset_path = _resolve_dataset_path(args.dataset)
    log(f"Loading dataset: {dataset_path}")
    data = datasets.read_dataset(dataset_path)
    fmt = datasets.infer_dataset_format(dataset_path, data)
    log(f"Dataset format: {fmt}, samples: {len(data) if isinstance(data, list) else '?'}")

    if fmt != "locomo":
        log(f"Only locomo format is supported for replay currently. Got: {fmt}")
        return

    # Extract jobs and plans using benchmark_adapter
    jobs, plans = benchmark_adapter.locomo_jobs(data, args.dataset_limit or None, args.dataset_sample)
    log(f"Extracted {len(jobs)} QA jobs from {len(plans)} samples")

    all_rounds: list[dict[str, Any]] = []
    all_facts: dict[str, str] = {}
    context_path = "/"

    # Process each sample
    for plan_idx, plan in enumerate(plans):
        sample_id = plan.get("sample_id", f"sample_{plan_idx}")
        events = plan.get("events") or []
        if not events:
            continue

        log(f"[sample {plan_idx + 1}/{len(plans)}] {sample_id} events={len(events)}")

        # Replay injection events: send user messages to EchoAgent
        # Group events by session (heuristic: each session is a batch of events)
        # For locomo, we replay all conversation turns as one session
        session_id = client.create_session(
            title=f"replay-{sample_id}",
            memory_engine_endpoint=args.memory_engine_endpoint,
        )
        log(f"  Replay session: {session_id}")

        for ev_idx, event in enumerate(events):
            text = event.get("text", "")
            if not text:
                continue
            # Only send user-side messages (skip assistant responses)
            # In locomo events, format is "Speaker D1:1: content"
            # We send all events as user messages to inject the conversation context
            log(f"  [{ev_idx + 1}/{len(events)}] injecting: {text[:60]}...")
            try:
                msg_result = client.send_message(session_id, context_path, text)
                msg_data = msg_result.get("data", msg_result)
                messages_list = msg_data.get("messages") or []
                seq = 0
                for m in reversed(messages_list):
                    if m.get("status") in ("generating", "completed"):
                        seq = m.get("seq", 0)
                        break
                if not seq and messages_list:
                    seq = messages_list[-1].get("seq", 0)
                if not seq:
                    seq = msg_data.get("latestContextSeq") or 0
                # Wait for reply
                reply_result = client.stream_reply(session_id, context_path, seq)
            except Exception as exc:
                log(f"    injection error: {exc}")
                continue

        # Now ask QA questions in a new session (cross-session recall test)
        sample_jobs = [j for j in jobs if j.sample_id == sample_id]
        if not sample_jobs:
            continue

        qa_session_id = client.create_session(
            title=f"replay-qa-{sample_id}",
            memory_engine_endpoint=args.memory_engine_endpoint,
        )
        log(f"  QA session: {qa_session_id}")

        for job_idx, job in enumerate(sample_jobs):
            query = job.question
            answer = job.answer
            log(f"  [QA {job_idx + 1}/{len(sample_jobs)}] q={query[:60]}...")

            # Simulate typing + send
            client_turn_id = ""
            prefetch_committed = False
            memory_items = []
            if len(query) > 2:
                try:
                    client_turn_id = simulate_typing(
                        client, qa_session_id, context_path, query,
                        args.typing_speed_ms, args.typing_jitter_ms,
                    )
                    finalize_result = client.prefetch_finalize(qa_session_id, context_path, client_turn_id, query)
                    fin_data = finalize_result.get("data", finalize_result)
                    prefetch_committed = bool(fin_data.get("accepted"))
                    memory_items = fin_data.get("memoryItems") or []
                except Exception:
                    client_turn_id = ""
                    memory_items = []

            send_time = time.monotonic()
            try:
                msg_result = client.send_message(qa_session_id, context_path, query, client_turn_id)
                msg_data = msg_result.get("data", msg_result)
                messages_list = msg_data.get("messages") or []
                seq = 0
                for m in reversed(messages_list):
                    if m.get("status") in ("generating", "completed"):
                        seq = m.get("seq", 0)
                        break
                if not seq and messages_list:
                    seq = messages_list[-1].get("seq", 0)
                if not seq:
                    seq = msg_data.get("latestContextSeq") or 0
            except Exception as exc:
                all_rounds.append({
                    "round_id": job.question_id, "query": query, "reply": "",
                    "reply_length": 0, "query_length": len(query), "ttft_ms": None,
                    "cached_tokens": 0, "prompt_tokens": 0, "prefetch_committed": prefetch_committed,
                    "is_new_session": True, "is_injection": False, "complexity": "",
                    "ground_facts": [answer], "error": str(exc),
                })
                continue

            try:
                reply_result = client.stream_reply(qa_session_id, context_path, seq)
            except Exception as exc:
                reply_result = {"reply": "", "ttft_ms": None, "error": str(exc)}

            round_data = {
                "id": job.question_id,
                "query": query,
                "ground_facts": [answer],
                "new_session": True,
                "is_injection": False,
                "complexity": job.category,
            }
            metrics = collect_round_metrics(round_data, reply_result, send_time, prefetch_committed, {}, memory_items)
            metrics["session_id"] = qa_session_id
            metrics["question_id"] = job.question_id
            metrics["gold_answer"] = answer
            all_rounds.append(metrics)
            all_facts[job.question_id] = answer
            log(f"    ttft={metrics['ttft_ms']}ms reply_len={metrics['reply_length']}")

    # Write results
    config = {
        "mode": "replay",
        "dataset": args.dataset,
        "dataset_sample": args.dataset_sample,
        "dataset_limit": args.dataset_limit,
        "echoagent_url": args.echoagent_url,
    }
    summary = compute_summary(all_rounds, config)
    log(f"Replay complete. avg_ttft={summary['avg_ttft_ms']}ms")

    results = {
        "testId": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "config": config,
        "summary": summary,
        "facts": all_facts,
        "rounds": all_rounds,
    }

    results_path = out_dir / "echoagent_live_test_results.json"
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"Results written to {results_path}")

    csv_path = out_dir / "echoagent_live_test_results.csv"
    fieldnames = [
        "round_id", "session_id", "query", "reply_length", "query_length",
        "ttft_ms", "cached_tokens", "prompt_tokens", "prefetch_committed", "is_new_session",
        "is_injection", "complexity", "error", "relevant_memory",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rounds)

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Quality report
    query_rounds = [r for r in all_rounds if not r.get("is_injection") and r.get("reply")]
    if query_rounds and all_facts:
        log("Generating quality evaluation report...")
        try:
            quality_report = generate_quality_report(
                query_rounds, all_facts,
                model=args.scenario_model,
                base_url=args.scenario_base_url,
                api_key=args.scenario_api_key,
            )
            report_path = out_dir / "quality_report.json"
            report_path.write_text(json.dumps(quality_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            log(f"Quality report written to {report_path}")
        except Exception as exc:
            log(f"Quality report generation failed: {exc}")


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_test(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"

    def log(msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"EchoAgent live test starting. out_dir={out_dir}")
    log(f"echoagent_url={args.echoagent_url} num_batches={args.num_batches} queries_per_batch={args.queries_per_batch}")

    client = EchoAgentClient(args.echoagent_url, args.username, args.password)
    log("Logging in...")
    client.login()
    log("Login successful.")

    config = {
        "echoagent_url": args.echoagent_url,
        "num_batches": args.num_batches,
        "queries_per_batch": args.queries_per_batch,
        "new_session_ratio": args.new_session_ratio,
        "typing_speed_ms": args.typing_speed_ms,
        "typing_jitter_ms": args.typing_jitter_ms,
        "scenario_model": args.scenario_model,
    }

    all_rounds: list[dict[str, Any]] = []
    all_facts: dict[str, dict[str, str]] = {}  # batch_idx → {fact_id: fact_text}
    session_conversations: dict[str, list[dict[str, Any]]] = {}
    session_index_map: dict[str, int] = {}
    session_turn_counters: dict[str, int] = {}
    next_session_index = 1
    context_path = "/"

    for batch_idx in range(args.num_batches):
        log(f"[batch {batch_idx + 1}/{args.num_batches}] Creating evaluator...")

        # Use MemoryDynamicEvaluator for generating memories and queries
        evaluator_config = {
            "mode": "dynamic",
            "num_memories": args.queries_per_batch // 2,
            "custom_scenario": args.custom_scenario,
            "llm_config": {
                "model": args.scenario_model,
                "base_url": args.scenario_base_url,
                "api_key": args.scenario_api_key,
            },
        }
        if args.user_simulator_config:
            sim_path = Path(args.user_simulator_config)
            if sim_path.is_file():
                evaluator_config["user_simulator_config_yaml"] = sim_path.read_text(encoding="utf-8")
            else:
                evaluator_config["user_simulator_config"] = args.user_simulator_config
        if args.evaluator_config:
            eval_path = Path(args.evaluator_config)
            if eval_path.is_file():
                evaluator_config["evaluator_config_yaml"] = eval_path.read_text(encoding="utf-8")
            else:
                evaluator_config["evaluator_config"] = args.evaluator_config
        evaluator = dynamic_evaluator.MemoryDynamicEvaluator(evaluator_config)

        # Generate background memories
        memories_result = evaluator.generate_background_memories()
        memories = memories_result.get("memories", [])
        log(f"[batch {batch_idx + 1}] theme={evaluator.theme} memories={len(memories)}")

        # Save fact texts for quality evaluation
        for fact in memories:
            fid = fact.get("id", "")
            ftext = fact.get("text", "")
            if fid and ftext:
                all_facts[fid] = ftext

        session_id = ""
        session_count = 0
        previous_queries: list[str] = []
        previous_replies: list[str] = []

        for round_idx in range(args.queries_per_batch):
            # Generate next query using the evaluator
            query_result = evaluator.generate_next_query({
                "round_index": round_idx,
                "previous_queries": previous_queries,
                "previous_replies": previous_replies,
                "is_new_session": session_id == "",
            })

            query = query_result.get("query", "")
            if not query:
                continue

            # Build round_data from query_result
            round_data = {
                "id": f"r{round_idx}",
                "query": query,
                "ground_facts": query_result.get("ground_facts", []),
                "new_session": query_result.get("new_session_hint", False),
                "complexity": query_result.get("complexity", "simple"),
                "is_injection": False,
            }

            # Decide whether to open a new session for cross-session recall testing.
            need_new = not session_id
            if not need_new and round_data.get("new_session"):
                if random.random() < args.new_session_ratio:
                    need_new = True
            if need_new:
                session_count += 1
                session_id = client.create_session(
                    title=f"test-{evaluator.theme}-{batch_idx}-s{session_count}",
                    memory_engine_endpoint=args.memory_engine_endpoint,
                )
                session_index_map[session_id] = next_session_index
                next_session_index += 1
                session_turn_counters[session_id] = 0
                session_conversations[session_id] = []
                log(f"  New session: {session_id}")

            query = round_data.get("query", "")
            if not query:
                continue

            log(f"  [{round_idx + 1}/{args.queries_per_batch}] query={query[:60]}...")

            client_turn_id = ""
            prefetch_committed = False
            memory_items = []
            if not round_data.get("is_injection") and len(query) > 2:
                try:
                    client_turn_id = simulate_typing(
                        client, session_id, context_path, query,
                        args.typing_speed_ms, args.typing_jitter_ms,
                    )
                    finalize_result = client.prefetch_finalize(session_id, context_path, client_turn_id, query)
                    fin_data = finalize_result.get("data", finalize_result)
                    prefetch_committed = bool(fin_data.get("accepted"))
                    if not prefetch_committed:
                        log(f"    prefetch finalize rejected: reason={fin_data.get('reason', 'unknown')}")
                    else:
                        log(f"    prefetch committed={prefetch_committed}")
                    memory_items = fin_data.get("memoryItems") or []
                except Exception as exc:
                    log(f"    prefetch error: {exc}")
                    client_turn_id = ""
                    memory_items = []

            send_time = time.monotonic()
            try:
                msg_result = client.send_message(session_id, context_path, query, client_turn_id)
                msg_data = msg_result.get("data", msg_result)
                # Check for error response
                if msg_data.get("error"):
                    raise RuntimeError(f"send failed: {msg_data.get('error')} {msg_data.get('message', '')}")
                # Extract seq: the newly created message is in the messages list
                messages_list = msg_data.get("messages") or []
                seq = 0
                for m in reversed(messages_list):
                    if m.get("status") in ("generating", "completed"):
                        seq = m.get("seq", 0)
                        break
                if not seq and messages_list:
                    seq = messages_list[-1].get("seq", 0)
                if not seq:
                    seq = msg_data.get("latestContextSeq") or msg_data.get("latestSeq") or 0
                log(f"    seq={seq} after_seq_updated={client._context_seq.get(client._seq_key(session_id, context_path))}")
            except Exception as exc:
                log(f"    send error: {exc}")
                all_rounds.append({
                    "round_id": round_data.get("id", ""),
                    "query": query,
                    "reply": "",
                    "reply_length": 0,
                    "query_length": len(query),
                    "ttft_ms": None,
                    "cached_tokens": 0,
                    "prefetch_committed": prefetch_committed,
                    "is_new_session": need_new,
                    "is_injection": bool(round_data.get("is_injection")),
                    "complexity": round_data.get("complexity", ""),
                    "ground_facts": round_data.get("ground_facts", []),
                    "error": str(exc),
                })
                continue

            try:
                reply_result = client.stream_reply(session_id, context_path, seq)
            except Exception as exc:
                reply_result = {"reply": "", "ttft_ms": None, "error": str(exc)}

            last_request = {}
            try:
                last_request = client.get_last_request(session_id, context_path)
            except Exception:
                pass

            metrics = collect_round_metrics(round_data, reply_result, send_time, prefetch_committed, last_request, memory_items)
            metrics["session_id"] = session_id
            all_rounds.append(metrics)
            # Track conversation for locomo export
            if session_id in session_conversations:
                session_turn_counters[session_id] = session_turn_counters.get(session_id, 0) + 1
                turn_idx = session_turn_counters[session_id]
                session_idx = session_index_map.get(session_id, 1)
                dia_id = f"D{session_idx}:{turn_idx}"
                session_conversations[session_id].append({"speaker": "用户", "dia_id": dia_id, "text": query})
                session_conversations[session_id].append({"speaker": "助手", "dia_id": f"D{session_idx}:{turn_idx + 1}", "text": metrics.get("reply", "")})
                session_turn_counters[session_id] = turn_idx + 1
            # Update history for next query generation
            previous_queries.append(query)
            previous_replies.append(metrics.get("reply", ""))
            log(f"    ttft={metrics['ttft_ms']}ms cached={metrics['cached_tokens']} prompt={metrics['prompt_tokens']} reply_len={metrics['reply_length']}")

    summary = compute_summary(all_rounds, config)
    log(f"Test complete. avg_ttft={summary['avg_ttft_ms']}ms avg_cached={summary['avg_cached_tokens']} avg_prompt={summary['avg_prompt_tokens']}")

    results = {
        "testId": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "config": config,
        "summary": summary,
        "facts": all_facts,
        "rounds": all_rounds,
    }

    results_path = out_dir / "echoagent_live_test_results.json"
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"Results written to {results_path}")

    csv_path = out_dir / "echoagent_live_test_results.csv"
    fieldnames = [
        "round_id", "session_id", "query", "reply_length", "query_length",
        "ttft_ms", "cached_tokens", "prompt_tokens", "prefetch_committed", "is_new_session",
        "is_injection", "complexity", "error", "relevant_memory",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rounds)
    log(f"CSV written to {csv_path}")

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"Summary written to {summary_path}")

    # Generate LLM-based quality evaluation report
    query_rounds = [r for r in all_rounds if not r.get("is_injection") and r.get("reply")]
    if query_rounds and all_facts:
        log("Generating quality evaluation report...")
        try:
            quality_report = generate_quality_report(
                query_rounds, all_facts,
                model=args.scenario_model,
                base_url=args.scenario_base_url,
                api_key=args.scenario_api_key,
            )
            report_path = out_dir / "quality_report.json"
            report_path.write_text(json.dumps(quality_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            log(f"Quality report written to {report_path}")
            log(f"  Overall recall score: {quality_report.get('overall_score')}")
            log(f"  Cross-session score: {quality_report.get('cross_session_score')}")
        except Exception as exc:
            log(f"Quality report generation failed: {exc}")

    # Save as locomo-format dataset if requested
    if args.save_dataset:
        save_path = Path(args.save_dataset)
        save_as_locomo_dataset(
            session_conversations, session_index_map, all_rounds, all_facts,
            save_path,
        )
        log(f"LoCoMo-format dataset saved to {save_path}")
        # Register in manifest if under dataset/ dir
        try:
            dataset_dir = ROOT / "dataset"
            if save_path.parent.resolve() == dataset_dir.resolve() or str(save_path).startswith(str(dataset_dir)):
                manifest_path = dataset_dir / "manifest.json"
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                else:
                    manifest = {"datasets": []}
                dataset_id = save_path.stem
                rel_path = str(save_path.relative_to(dataset_dir)) if save_path.is_relative_to(dataset_dir) else save_path.name
                # Check if already registered
                existing = [d for d in manifest.get("datasets", []) if d.get("id") == dataset_id]
                if not existing:
                    manifest.setdefault("datasets", []).append({
                        "id": dataset_id,
                        "name": f"EchoAgent Generated ({dataset_id})",
                        "path": rel_path,
                        "format": "locomo",
                        "description": f"Auto-generated by echoagent_live_test at {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    })
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                    log(f"Dataset registered in {manifest_path}")
        except Exception as exc:
            log(f"Failed to register dataset in manifest: {exc}")
    else:
        # Always save dataset.json in out_dir for reference
        dataset_out_path = out_dir / "dataset.json"
        save_as_locomo_dataset(
            session_conversations, session_index_map, all_rounds, all_facts,
            dataset_out_path,
        )
        log(f"LoCoMo-format dataset saved to {dataset_out_path}")


# ---------------------------------------------------------------------------
# Runtime metrics collection helpers
# ---------------------------------------------------------------------------

def collect_runtime_metrics_snapshot(
    metrics_client: "RuntimeMetricsClient",
    baseline_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect a snapshot of runtime metrics from EchoAgent/EchoMem.
    
    Args:
        metrics_client: RuntimeMetricsClient instance
        baseline_metrics: Optional baseline for delta calculation
        
    Returns:
        Dict with metrics snapshot
    """
    if not METRICS_MODULES_AVAILABLE:
        return {"error": "Metrics modules not available"}
    
    current = metrics_client.fetch_metrics()
    turn_metrics = metrics_client.extract_turn_metrics(current)
    
    result = {
        "timestamp": current.get("timestamp"),
        "metrics": turn_metrics,
    }
    
    if baseline_metrics:
        delta = metrics_client.diff_metrics(baseline_metrics, current)
        result["delta_since_baseline"] = delta
    
    return result


def save_runtime_report(
    snapshots: list[dict[str, Any]],
    out_dir: Path,
) -> None:
    """Save runtime metrics report to file.
    
    Args:
        snapshots: List of metric snapshots
        out_dir: Output directory
    """
    runtime_path = out_dir / "runtime.json"
    report = {
        "collection_points": snapshots,
        "summary": _compute_runtime_summary(snapshots),
    }
    runtime_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )


def _compute_runtime_summary(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from runtime snapshots."""
    if not snapshots:
        return {}
    
    ttft_values = [s.get("metrics", {}).get("ttft_p50_seconds") for s in snapshots if s.get("metrics", {}).get("ttft_p50_seconds")]
    cached_values = [s.get("metrics", {}).get("cached_tokens_sum", 0) for s in snapshots]
    retrieval_count = sum(s.get("metrics", {}).get("retrieval_count", 0) for s in snapshots)
    
    return {
        "num_snapshots": len(snapshots),
        "avg_ttft_p50_seconds": round(sum(ttft_values) / len(ttft_values), 3) if ttft_values else None,
        "total_cached_tokens": sum(cached_values),
        "total_retrieval_count": retrieval_count,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EchoAgent live test: end-to-end memory recall + prefill evaluation")
    parser.add_argument("--echoagent-url", default=os.environ.get("ECHOAGENT_URL", "http://127.0.0.1:31020"))
    parser.add_argument("--echomem-url", default=os.environ.get("ECHOMEM_URL", "http://127.0.0.1:8010"), help="EchoMem service URL for runtime metrics")
    parser.add_argument("--username", default=os.environ.get("ECHOAGENT_TEST_USERNAME", "test_user"))
    parser.add_argument(
        "--password",
        default=os.environ.get("ECHOAGENT_TEST_PASSWORD", ""),
        help="EchoAgent password. Defaults to ECHOAGENT_TEST_PASSWORD.",
    )
    parser.add_argument("--num-batches", type=int, default=int(os.environ.get("ECHOAGENT_TEST_BATCHES", "3")))
    parser.add_argument("--queries-per-batch", type=int, default=int(os.environ.get("ECHOAGENT_TEST_QUERIES", "5")))
    parser.add_argument("--new-session-ratio", type=float, default=float(os.environ.get("ECHOAGENT_TEST_NEW_SESSION_RATIO", "0.3")))
    parser.add_argument("--typing-speed-ms", type=int, default=int(os.environ.get("ECHOAGENT_TEST_TYPING_SPEED", "200")))
    parser.add_argument("--typing-jitter-ms", type=int, default=int(os.environ.get("ECHOAGENT_TEST_TYPING_JITTER", "20")))
    parser.add_argument("--scenario-model", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--scenario-base-url", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_BASE_URL", ""))
    parser.add_argument("--scenario-api-key", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_API_KEY", ""))
    parser.add_argument("--memory-engine-endpoint", default=os.environ.get("GLOBAL_MEMORY_ENGINE_ENDPOINT", "http://127.0.0.1:31030"))
    parser.add_argument("--dataset", default="", help="Dataset name (e.g. locomo10) or path. When specified, replay dataset conversations against EchoAgent instead of generating new scenarios")
    parser.add_argument("--dataset-sample", default="all", help="Dataset sample filter: all / sample_id / index number")
    parser.add_argument("--dataset-limit", type=int, default=0, help="Max QA questions to replay, 0=all")
    parser.add_argument("--save-dataset", default="", help="Save generated conversations as locomo-format dataset to this path (e.g. dataset/echoagent_gen_001.json)")
    parser.add_argument("--custom-scenario", default="", help="Custom scenario text. If provided, skip LLM generation and use this scenario directly")
    parser.add_argument("--user-simulator-config", default="", help="User simulator config name (searched in configs/user_simulator/) or file path")
    parser.add_argument("--evaluator-config", default="", help="Evaluator config name (searched in configs/evaluator/ or configs/custom/) or file path")

    # New options for runtime/accuracy metrics separation
    parser.add_argument("--collect-runtime-metrics", action="store_true", default=True, help="Collect runtime metrics from Prometheus endpoints")
    parser.add_argument("--no-runtime-metrics", action="store_true", help="Disable runtime metrics collection")
    parser.add_argument("--accuracy-method", default="llm", choices=["llm", "heuristic"], help="Accuracy evaluation method")
    parser.add_argument("--config", default="", help="Path to metrics_config.yaml")
    
    parser.add_argument("--out-dir", required=True)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.password:
        parser.error("provide --password or set ECHOAGENT_TEST_PASSWORD")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize runtime metrics client if enabled
    collect_runtime = args.collect_runtime_metrics and not args.no_runtime_metrics and METRICS_MODULES_AVAILABLE
    metrics_client = None
    baseline_metrics = None
    
    if collect_runtime:
        metrics_client = RuntimeMetricsClient(
            echoagent_url=args.echoagent_url,
            echomem_url=args.echomem_url,
        )
        baseline_metrics = metrics_client.fetch_metrics()
        print(f"[metrics] Runtime metrics collection enabled. EchoAgent={args.echoagent_url} EchoMem={args.echomem_url}")

    if args.dataset:
        # Replay mode
        client = EchoAgentClient(args.echoagent_url, args.username, args.password)
        log_path = out_dir / "run.log"
        def log(msg: str) -> None:
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
            print(line, flush=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        log(f"EchoAgent replay test starting. dataset={args.dataset} out_dir={out_dir}")
        if collect_runtime:
            log("[metrics] Runtime metrics collection enabled")
        log("Logging in...")
        client.login()
        log("Login successful.")
        run_replay_test(args, client, log, out_dir, metrics_client, baseline_metrics)
    else:
        # Generate mode (existing behavior)
        run_test(args)


if __name__ == "__main__":
    main()
