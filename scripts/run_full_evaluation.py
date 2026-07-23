#!/usr/bin/env python3
"""完整的端到端评测脚本。

整合 Memory-System-Eval-Harness、EchoMem 和 EchoAgent 的完整评测流程：
1. 调用 generate_background_memories 生成背景记忆
2. 将记忆注入 EchoMem（通过 EchoAgent 的 MCP 工具）
3. 调用 generate_user_query 生成用户查询
4. 将查询发送到 EchoAgent，获取回复和运行时指标
5. 循环直到对话结束
6. 生成评估报告

Usage:
    python scripts/run_full_evaluation.py --config configs/dynamic_eval/dynamic_config.yaml
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

from memory import dynamic_evaluator


# ---------------------------------------------------------------------------
# EchoMem Client
# ---------------------------------------------------------------------------

class EchoMemClient:
    """EchoMem HTTP client for memory injection and retrieval."""

    def __init__(self, base_url: str, auth_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.auth_key = auth_key

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_key:
            headers["X-Auth-Key"] = self.auth_key
        return headers

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8") if body else None
        req = Request(f"{self.base_url}{path}", data=data, headers=self._headers())
        req.method = "POST"
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def _get(self, path: str) -> dict[str, Any]:
        req = Request(f"{self.base_url}{path}", headers=self._headers())
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def open_session(self, agent_id: str, user_id: str = "default", session_id: str = "") -> str:
        """Open a new session in EchoMem."""
        body = {"agent_id": agent_id, "user_id": user_id}
        if session_id:
            body["session_id"] = session_id
        result = self._post("/api/sessions/open", body)
        return result.get("scope", {}).get("session_id", "")

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        """Add a message to the session (for memory injection)."""
        return self._post(f"/api/sessions/{session_id}/messages", {
            "role": role,
            "content": content,
        })

    def commit_session(self, session_id: str) -> dict[str, Any]:
        """Commit the session to trigger memory extraction."""
        return self._post(f"/api/sessions/{session_id}/commit", {})

    def search(self, query: str, agent_id: str, session_id: str = "", limit: int = 8) -> dict[str, Any]:
        """Search for memories."""
        body = {"query": query, "agent_id": agent_id, "limit": limit}
        if session_id:
            body["session_id"] = session_id
        return self._post("/api/retrieval/search", body)

    def wait_for_commit(self, session_id: str, archive_id: str, timeout: float = 60.0) -> bool:
        """Wait for commit to complete."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                result = self._get(f"/api/sessions/{session_id}/commits/{archive_id}")
                status = result.get("status", {})
                if status.get("status") == "completed":
                    return True
                if status.get("status") == "failed":
                    return False
            except Exception:
                pass
            time.sleep(1.0)
        return False


# ---------------------------------------------------------------------------
# EchoAgent Client
# ---------------------------------------------------------------------------

def _encode_context_path(context_path: str) -> str:
    return quote(context_path, safe="")


class EchoAgentClient:
    """EchoAgent HTTP client for session and message operations."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token: str = ""
        self._context_seq: dict[str, int] = {}

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
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def _put(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body or {}).encode("utf-8") if body else None
        req = Request(f"{self.base_url}{path}", data=data, headers=self._headers())
        req.method = "PUT"
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
        if not self.token:
            raise RuntimeError(f"Login failed: {result}")

    def create_session(self, title: str = "", memory_engine_endpoint: str = "") -> str:
        result = self._post("/v1/sessions", {"title": title or f"eval-{uuid.uuid4().hex[:8]}"})
        session_id = result.get("data", result).get("id") or result.get("id", "")
        if session_id and memory_engine_endpoint:
            try:
                self._post(f"/v1/sessions/{session_id}/memory-engine/test", {
                    "endpoint": memory_engine_endpoint,
                })
                self._put(f"/v1/sessions/{session_id}/memory-engine", {
                    "enabled": True,
                    "endpoint": memory_engine_endpoint,
                })
            except Exception:
                pass
        return session_id

    def get_latest_context_seq(self, session_id: str, context_path: str = "/") -> int:
        try:
            path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/messages"
            result = self._post(path, {"afterSeq": 0, "limit": 1})
            data = result.get("data", result)
            return data.get("latestContextSeq", 0) or 0
        except Exception:
            return 0

    def send_message(self, session_id: str, content: str, context_path: str = "/") -> dict[str, Any]:
        key = f"{session_id}:{context_path}"
        after_seq = self._context_seq.get(key, 0)
        path = f"/v1/sessions/{session_id}/context-paths/{_encode_context_path(context_path)}/messages"
        result = self._post(path, {"content": content, "afterSeq": after_seq})
        data = result.get("data", result)
        server_seq = data.get("latestContextSeq")
        if isinstance(server_seq, int):
            self._context_seq[key] = server_seq
        return result

    def stream_reply(self, session_id: str, seq: int, context_path: str = "/") -> dict[str, Any]:
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
            buffer = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                while "\n\n" in buffer:
                    event_block, buffer = buffer.split("\n\n", 1)
                    event_type = ""
                    data_lines: list[str] = []
                    for line in event_block.splitlines():
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:])
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
                        fragment = data.get("fragment") or data.get("content") or ""
                        if isinstance(fragment, dict):
                            reply_parts.append(fragment.get("content") or "")
                        else:
                            reply_parts.append(str(fragment))
                    elif event_type == "done":
                        done_event = data if isinstance(data, dict) else {}
                        if ttft_ms is None:
                            ttft_ms = (time.monotonic() - send_time) * 1000
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}
                    elif event_type == "error":
                        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "error": str(data), "done_event": {}}

        return {"reply": "".join(reply_parts), "ttft_ms": ttft_ms, "done_event": done_event}


# ---------------------------------------------------------------------------
# Evaluation Runner
# ---------------------------------------------------------------------------

class EvaluationRunner:
    """完整的评测流程执行器。"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.out_dir = Path(config.get("output", {}).get("out_dir", "runs/evaluation"))

        # EchoAgent 配置
        echoagent_config = config.get("echoagent", {})
        self.echoagent_client = EchoAgentClient(
            base_url=echoagent_config.get("url", "http://127.0.0.1:31020"),
            username=echoagent_config.get("username", "test_user"),
            password=echoagent_config.get("password", "test_password"),
        )
        self.memory_engine_endpoint = echoagent_config.get("memory_engine_endpoint", "http://127.0.0.1:31030")

        # EchoMem 配置
        echomem_config = config.get("echomem", {})
        self.echomem_client = EchoMemClient(
            base_url=echomem_config.get("url", "http://127.0.0.1:8010"),
            auth_key=echomem_config.get("auth_key", ""),
        )

        # LLM 配置
        llm_config = config.get("llm_config", {})
        evaluator_init: dict[str, Any] = {
            "mode": config.get("mode", "dynamic"),
            "num_memories": config.get("num_memories", 10),
            "theme": config.get("theme", ""),
            "llm_config": llm_config,
        }
        if config.get("user_simulator_config"):
            evaluator_init["user_simulator_config"] = config["user_simulator_config"]
        if config.get("user_simulator_config_yaml"):
            evaluator_init["user_simulator_config_yaml"] = config["user_simulator_config_yaml"]
        if config.get("evaluator_config"):
            evaluator_init["evaluator_config"] = config["evaluator_config"]
        if config.get("evaluator_config_yaml"):
            evaluator_init["evaluator_config_yaml"] = config["evaluator_config_yaml"]
        self.evaluator = dynamic_evaluator.MemoryDynamicEvaluator(evaluator_init)

        # 评测参数
        self.queries_per_test = config.get("queries_per_test", 5)
        self.new_session_ratio = config.get("new_session_ratio", 0.3)
        self.agent_id = config.get("agent_id", "echo_evaluator")
        self.user_id = config.get("user_id", "eval_user")

        # 结果存储
        self.all_rounds: list[dict[str, Any]] = []
        self.all_memories: list[dict[str, Any]] = []

    def log(self, msg: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {msg}", flush=True)

    def run(self) -> dict[str, Any]:
        """执行完整的评测流程。"""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"Starting evaluation. out_dir={self.out_dir}")

        # 1. 登录 EchoAgent
        self.log("Logging in to EchoAgent...")
        self.echoagent_client.login()
        self.log("Login successful.")

        # 2. 生成背景记忆
        self.log("Generating background memories...")
        memories_result = self.evaluator.generate_background_memories()
        self.all_memories = memories_result.get("memories", [])
        self.log(f"Generated {len(self.all_memories)} background memories")

        # 3. 注入记忆到 EchoMem
        self.log("Injecting memories into EchoMem...")
        injection_session_id = self.echomem_client.open_session(
            agent_id=self.agent_id,
            user_id=self.user_id,
        )
        self.log(f"Opened EchoMem session: {injection_session_id}")

        # 将记忆作为用户消息注入
        for i, memory in enumerate(self.all_memories):
            text = memory.get("text", "")
            if text:
                self.echomem_client.add_message(injection_session_id, "user", f"记住：{text}")
                self.log(f"  Injected memory {i+1}/{len(self.all_memories)}: {text[:50]}...")

        # 提交会话以触发记忆抽取
        commit_result = self.echomem_client.commit_session(injection_session_id)
        archive_id = commit_result.get("result", {}).get("archive_id", "")
        self.log(f"Committed session, archive_id={archive_id}")

        # 等待提交完成
        if archive_id:
            self.log("Waiting for memory extraction to complete...")
            success = self.echomem_client.wait_for_commit(injection_session_id, archive_id, timeout=120.0)
            if success:
                self.log("Memory extraction completed.")
            else:
                self.log("Memory extraction timed out, continuing anyway...")

        # 4. 开始评测对话
        self.log("Starting evaluation conversation...")
        previous_queries: list[str] = []
        previous_replies: list[str] = []
        echoagent_session_id = ""

        for round_idx in range(self.queries_per_test):
            # 生成下一个查询
            query_result = self.evaluator.generate_next_query({
                "round_index": round_idx,
                "previous_queries": previous_queries,
                "previous_replies": previous_replies,
                "is_new_session": round_idx == 0 or random.random() < self.new_session_ratio,
            })

            query = query_result.get("query", "")
            if not query:
                self.log(f"Round {round_idx}: No query generated, skipping.")
                continue

            ground_facts = query_result.get("ground_facts", [])
            complexity = query_result.get("complexity", "simple")

            # 判断是否需要新会话
            need_new_session = round_idx == 0
            if not need_new_session and query_result.get("new_session_hint", False):
                if random.random() < self.new_session_ratio:
                    need_new_session = True

            if need_new_session:
                echoagent_session_id = self.echoagent_client.create_session(
                    title=f"eval-{self.evaluator.theme}-{round_idx}",
                    memory_engine_endpoint=self.memory_engine_endpoint,
                )
                self.log(f"  Created new EchoAgent session: {echoagent_session_id}")

            self.log(f"  Round {round_idx + 1}/{self.queries_per_test}: query={query[:60]}...")

            # 发送查询到 EchoAgent
            send_time = time.monotonic()
            try:
                msg_result = self.echoagent_client.send_message(echoagent_session_id, query)
                msg_data = msg_result.get("data", msg_result)

                # 获取消息序号
                messages_list = msg_data.get("messages", [])
                seq = 0
                for m in reversed(messages_list):
                    if m.get("status") in ("generating", "completed"):
                        seq = m.get("seq", 0)
                        break

                # 获取流式回复
                reply_result = self.echoagent_client.stream_reply(echoagent_session_id, seq)

            except Exception as exc:
                self.log(f"  Error: {exc}")
                reply_result = {"reply": "", "ttft_ms": None, "error": str(exc)}

            reply = reply_result.get("reply", "")
            ttft_ms = reply_result.get("ttft_ms")
            done_event = reply_result.get("done_event", {})

            # 记录结果
            round_data = {
                "round_id": f"r{round_idx}",
                "session_id": echoagent_session_id,
                "query": query,
                "reply": reply,
                "reply_length": len(reply),
                "query_length": len(query),
                "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
                "cached_tokens": done_event.get("cachedTokens", 0) or done_event.get("cached_tokens", 0),
                "prompt_tokens": done_event.get("promptTokens", 0) or done_event.get("prompt_tokens", 0),
                "is_new_session": need_new_session,
                "ground_facts": ground_facts,
                "complexity": complexity,
                "error": reply_result.get("error", ""),
            }
            self.all_rounds.append(round_data)

            # 更新历史
            previous_queries.append(query)
            previous_replies.append(reply)

            self.log(f"    ttft={ttft_ms}ms cached={round_data['cached_tokens']} reply_len={len(reply)}")

        # 5. 生成报告
        return self._generate_report()

    def _generate_report(self) -> dict[str, Any]:
        """生成评估报告。"""
        self.log("Generating evaluation report...")

        # 计算统计
        ttft_values = [r["ttft_ms"] for r in self.all_rounds if r.get("ttft_ms")]
        cached_values = [r["cached_tokens"] for r in self.all_rounds if r.get("cached_tokens")]
        query_lengths = [r["query_length"] for r in self.all_rounds]
        reply_lengths = [r["reply_length"] for r in self.all_rounds]

        summary = {
            "total_queries": len(self.all_rounds),
            "total_memories": len(self.all_memories),
            "avg_ttft_ms": round(sum(ttft_values) / len(ttft_values), 1) if ttft_values else None,
            "avg_cached_tokens": round(sum(cached_values) / len(cached_values), 1) if cached_values else None,
            "avg_query_length": round(sum(query_lengths) / len(query_lengths), 1) if query_lengths else 0,
            "avg_reply_length": round(sum(reply_lengths) / len(reply_lengths), 1) if reply_lengths else 0,
            "theme": self.evaluator.theme,
        }

        results = {
            "testId": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "config": self.config,
            "summary": summary,
            "memories": self.all_memories,
            "rounds": self.all_rounds,
        }

        # 保存 JSON
        results_path = self.out_dir / "evaluation_results.json"
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.log(f"Results saved to {results_path}")

        # 保存 CSV
        csv_path = self.out_dir / "evaluation_results.csv"
        fieldnames = [
            "round_id", "session_id", "query", "reply_length", "query_length",
            "ttft_ms", "cached_tokens", "prompt_tokens", "is_new_session",
            "ground_facts", "complexity", "error",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.all_rounds)
        self.log(f"CSV saved to {csv_path}")

        # 保存摘要
        summary_path = self.out_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"Summary saved to {summary_path}")

        self.log(f"Evaluation complete. avg_ttft={summary['avg_ttft_ms']}ms")

        return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict[str, Any]:
    """Load configuration from YAML file."""
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="Run full evaluation pipeline")
    parser.add_argument("--config", default="configs/dynamic_eval/dynamic_config.yaml", help="Path to config file")
    parser.add_argument("--echoagent-url", default="", help="Override EchoAgent URL")
    parser.add_argument("--echomem-url", default="", help="Override EchoMem URL")
    parser.add_argument("--queries", type=int, default=0, help="Override queries per test")
    parser.add_argument("--out-dir", default="", help="Override output directory")
    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 应用命令行覆盖
    if args.echoagent_url:
        config.setdefault("echoagent", {})["url"] = args.echoagent_url
    if args.echomem_url:
        config.setdefault("echomem", {})["url"] = args.echomem_url
    if args.queries > 0:
        config["queries_per_test"] = args.queries
    if args.out_dir:
        config.setdefault("output", {})["out_dir"] = args.out_dir

    # 运行评测
    runner = EvaluationRunner(config)
    runner.run()


if __name__ == "__main__":
    main()