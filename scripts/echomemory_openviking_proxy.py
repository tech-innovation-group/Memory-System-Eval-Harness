#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import concurrent.futures
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from echomemory_common import DEFAULT_ECHOMEM_ROOT, ctx, ensure_echomem_imports


def compact(text: Any, limit: int = 1800) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def hit_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def context_item_to_dict(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        data = asdict(item)
    elif isinstance(item, dict):
        data = dict(item)
    else:
        data = {
            "content": getattr(item, "content", ""),
            "source_uri": getattr(item, "source_uri", ""),
            "memory_type": getattr(item, "memory_type", ""),
            "confidence": getattr(item, "confidence", 0.0),
            "evidence_uri": getattr(item, "evidence_uri", ""),
            "path": getattr(item, "path", ""),
            "trace": getattr(item, "trace", {}),
        }
    return {
        "uri": data.get("source_uri") or data.get("uri") or data.get("path") or "",
        "score": data.get("confidence") or data.get("score") or 0.0,
        "content": data.get("content") or "",
        "memory_type": data.get("memory_type") or "",
        "evidence_uri": data.get("evidence_uri") or "",
        "path": data.get("path") or "",
        "trace": data.get("trace") or {},
        "backend": "echomemory",
    }


class EchoMemoryOpenVikingProxy:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.root = ensure_echomem_imports(args.echomem_root)
        self.sdk: Any = None
        self._runtime: Any = None
        self._content_by_uri: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.log_path = Path(args.log_file).expanduser().resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        try:
            from echomem.protocol.local_sdk.sdk import EchoMemSDK
            from echomem.runtime.runtime import open_runtime
        except ModuleNotFoundError:
            from echomem.entrypoints.plugins.echoagent.sdk import EchoMemSDK
            from echomem.runtime.bootstrap import open_runtime

        self._runtime = await open_runtime(str(Path(self.args.echomem_config).expanduser().resolve()))
        self.sdk = EchoMemSDK(self._runtime)
        self._loop = asyncio.get_running_loop()
        self._write_event(
            "proxy_started",
            {
                "workspace": str(Path(self.args.workspace).expanduser().resolve()),
                "account": self.args.account,
                "echomem_config": str(Path(self.args.echomem_config).expanduser().resolve()),
                "top_k": self.args.top_k,
                "score_threshold": self.args.score_threshold,
            },
        )

    def run_coro_from_handler(self, coro: Any) -> Any:
        if self._loop is None:
            raise RuntimeError("proxy event loop is not initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=float(self.args.request_timeout_s))
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"request timed out after {self.args.request_timeout_s}s") from exc

    def _write_event(self, event: str, payload: dict[str, Any]) -> None:
        record = {"ts": time.time(), "event": event, **payload}
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # Logging must never take down the compatibility proxy.
            pass

    def _mk_uri(self, index: int, item: dict[str, Any], target_uri: str) -> str:
        raw = str(item.get("uri") or item.get("path") or item.get("evidence_uri") or "")
        if raw.startswith("echo://"):
            return raw
        scope = "agent" if "/agent/" in target_uri or target_uri.startswith("viking://agent") else "user"
        safe_type = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(item.get("memory_type") or "memory")).strip("_") or "memory"
        return f"echo://{scope}/{self.args.account}/memories/{safe_type}/{index:04d}"

    async def retrieve(self, query: str, target_uri: str, limit: int | None = None) -> dict[str, Any]:
        try:
            return await self._retrieve_inner(query, target_uri, limit)
        except BaseException as exc:
            self._write_event(
                "retrieve_fatal",
                {"query": query, "target_uri": target_uri, "error": f"{type(exc).__name__}: {exc}"},
            )
            return {"memories": [], "resources": [], "skills": [], "total": 0}

    async def _retrieve_inner(self, query: str, target_uri: str, limit: int | None = None) -> dict[str, Any]:
        async with self._lock:
            context = ctx(self.args.account, self.args.user_id, self.args.agent_id)
            errors: list[str] = []
            items: list[dict[str, Any]] = []
            top_k = int(limit or self.args.top_k or 30)
            if self.args.retrieval_mode in {"find", "both"}:
                try:
                    found = await self.sdk.find(query, ctx=context)
                    items.extend(context_item_to_dict(item) for item in found)
                except Exception as exc:
                    errors.append(f"find: {exc}")
            if self.args.retrieval_mode in {"search", "both"}:
                try:
                    result = await self.sdk.search(query, ctx=context, budget={"max_results": top_k})
                    items.extend(context_item_to_dict(item) for item in getattr(result, "items", []))
                except Exception as exc:
                    errors.append(f"search: {exc}")

            seen: dict[str, dict[str, Any]] = {}
            for item in items:
                raw_uri = str(item.get("uri") or item.get("path") or item.get("evidence_uri") or "")
                if "/.system/" in raw_uri or "/access_log/" in raw_uri:
                    continue
                key = f"{item.get('uri')}::{compact(item.get('content'), 160)}"
                if key not in seen:
                    seen[key] = item
            hits = [item for item in seen.values() if hit_score(item) >= self.args.score_threshold]
            hits.sort(key=hit_score, reverse=True)
            hits = hits[:top_k]

            memories = []
            for index, item in enumerate(hits, 1):
                content = str(item.get("content") or "")
                uri = self._mk_uri(index, item, target_uri)
                self._content_by_uri[uri] = content
                abstract = compact(content, self.args.abstract_chars)
                memories.append(
                    {
                        "context_type": "memory",
                        "uri": uri,
                        "level": 2,
                        "score": hit_score(item),
                        "category": str(item.get("memory_type") or "memory"),
                        "match_reason": "EchoMemory retrieval via OpenViking-compatible proxy",
                        "relations": [],
                        "abstract": abstract,
                        "overview": abstract,
                        "is_leaf": True,
                    }
                )
            self._write_event(
                "find",
                {
                    "query": query,
                    "target_uri": target_uri,
                    "limit": top_k,
                    "count": len(memories),
                    "errors": errors,
                    "uris": [m["uri"] for m in memories],
                },
            )
            return {"memories": memories, "resources": [], "skills": [], "total": len(memories)}

    def read_content(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        if uri.startswith("viking://echo://"):
            uri = uri[len("viking://") :]
        elif uri.startswith("viking://atom://"):
            uri = uri[len("viking://") :]
        content = self._content_by_uri.get(uri, "")
        if not content:
            self._write_event("read_miss", {"uri": uri})
            return ""
        lines = content.splitlines()
        if offset > 0 or limit >= 0:
            end = None if limit < 0 else offset + limit
            content = "\n".join(lines[offset:end])
        self._write_event("read", {"uri": uri, "chars": len(content)})
        return content


def make_handler(proxy: EchoMemoryOpenVikingProxy):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EchoMemoryOpenVikingProxy/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            proxy._write_event("http_log", {"message": fmt % args})

        def _send_json(self, obj: Any, status: int = 200) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        def _ok(self, result: Any) -> None:
            self._send_json({"status": "success", "result": result})

        def _error(self, message: str, status: int = 500) -> None:
            self._send_json(
                {"status": "error", "error": {"code": "INTERNAL", "message": message}},
                status=status,
            )

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            path = parsed.path
            if path in {"/health", "/api/health", "/api/v1/health"}:
                self._ok({"is_healthy": True})
                return
            if path in {
                "/api/v1/observer/system",
                "/api/v1/observer/queue",
                "/api/v1/observer/vikingdb",
                "/api/v1/observer/models",
            }:
                self._ok({"is_healthy": True, "status": "ok"})
                return
            if path == "/api/v1/admin/accounts":
                self._ok(
                    [
                        {
                            "account_id": proxy.args.account,
                            "isolate_user_scope_by_agent": False,
                            "isolate_agent_scope_by_user": False,
                        }
                    ]
                )
                return
            match = re.fullmatch(r"/api/v1/admin/accounts/([^/]+)/users", path)
            if match:
                users = {
                    proxy.args.user_id,
                    proxy.args.admin_user_id,
                    "default",
                    "locomo",
                    "Jon",
                    "Gina",
                }
                self._ok(
                    [{"user_id": user_id, "role": "admin"} for user_id in sorted(u for u in users if u)]
                )
                return
            if path == "/api/v1/content/read":
                uri = params.get("uri", [""])[0]
                offset = int(params.get("offset", ["0"])[0] or 0)
                limit = int(params.get("limit", ["-1"])[0] or -1)
                self._ok(proxy.read_content(uri, offset, limit))
                return
            if path == "/api/v1/content/abstract" or path == "/api/v1/content/overview":
                uri = params.get("uri", [""])[0]
                self._ok(compact(proxy.read_content(uri), proxy.args.abstract_chars))
                return
            if path == "/api/v1/fs/stat":
                self._ok({"uri": params.get("uri", [""])[0], "type": "file", "exists": True})
                return
            if path == "/api/v1/fs/ls":
                self._ok([])
                return
            self._error(f"unsupported GET {path}", status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            body = self._read_body()
            if path in {"/api/v1/search/find", "/api/v1/search/search"}:
                query = str(body.get("query") or "")
                target_uri = body.get("target_uri") or ""
                if isinstance(target_uri, list):
                    target_uri_text = ",".join(str(x) for x in target_uri)
                else:
                    target_uri_text = str(target_uri)
                limit = int(body.get("limit") or body.get("node_limit") or proxy.args.top_k)
                try:
                    result = proxy.run_coro_from_handler(proxy.retrieve(query, target_uri_text, limit))
                    self._ok(result)
                except Exception as exc:
                    proxy._write_event("find_error", {"error": str(exc), "query": query, "target_uri": target_uri_text})
                    self._error(str(exc))
                return
            match = re.fullmatch(r"/api/v1/admin/accounts/([^/]+)/users", path)
            if match:
                user_id = str(body.get("user_id") or proxy.args.user_id)
                self._ok({"account_id": match.group(1), "user_id": user_id, "user_key": "echo-proxy-user-key"})
                return
            match = re.fullmatch(r"/api/v1/admin/accounts/([^/]+)/users/([^/]+)/key", path)
            if match:
                self._ok({"account_id": match.group(1), "user_id": match.group(2), "user_key": "echo-proxy-user-key"})
                return
            if path == "/api/v1/admin/accounts":
                account_id = str(body.get("account_id") or proxy.args.account)
                admin_user_id = str(body.get("admin_user_id") or proxy.args.admin_user_id)
                self._ok({"account_id": account_id, "admin_user_id": admin_user_id})
                return
            if path == "/api/v1/sessions":
                sid = f"echo-proxy-session-{int(time.time() * 1000)}"
                self._ok({"session_id": sid})
                return
            if re.fullmatch(r"/api/v1/sessions/[^/]+/messages/batch", path):
                messages = body.get("messages") if isinstance(body.get("messages"), list) else []
                self._ok({"message_count": len(messages), "added": len(messages)})
                return
            if re.fullmatch(r"/api/v1/sessions/[^/]+/commit", path):
                self._ok({"status": "committed"})
                return
            if path == "/api/v1/search/grep":
                self._ok({"matches": []})
                return
            if path == "/api/v1/system/wait":
                self._ok({"status": "ok"})
                return
            self._error(f"unsupported POST {path}", status=HTTPStatus.NOT_FOUND)

        def do_DELETE(self) -> None:
            self._ok({"status": "ok"})

        def do_PUT(self) -> None:
            self._ok({"status": "ok"})

    return Handler


async def async_main(args: argparse.Namespace) -> None:
    proxy = EchoMemoryOpenVikingProxy(args)
    await proxy.initialize()
    handler = make_handler(proxy)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        json.dumps(
            {
                "status": "started",
                "url": f"http://{args.host}:{args.port}",
                "workspace": str(Path(args.workspace).expanduser().resolve()),
                "account": args.account,
                "log_file": str(proxy.log_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        await asyncio.to_thread(server.serve_forever)
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenViking-compatible HTTP proxy backed by EchoMemory retrieval.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19431)
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--echomem-config", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--admin-user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--score-threshold", type=float, default=0.1)
    parser.add_argument("--retrieval-mode", choices=["find", "search", "both"], default="both")
    parser.add_argument("--abstract-chars", type=int, default=1200)
    parser.add_argument("--request-timeout-s", type=float, default=180.0)
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
