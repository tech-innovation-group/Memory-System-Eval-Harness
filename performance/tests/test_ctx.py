"""Unit tests for the scene API (Ctx / Response / PollResult)."""

from __future__ import annotations

import http.client
import http.server
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from performance.ctx import (
    AssertionFailure,
    ConnectionRegistry,
    Ctx,
    Phase,
    TransportError,
    _drop_connection,
    _reuse_connection,
)
from performance.tests.conftest import MockState


def make_ctx(
    base_url: str,
    *,
    params: dict | None = None,
    extra: str = "",
    stop: threading.Event | None = None,
    interrupt: threading.Event | None = None,
    registry: ConnectionRegistry | None = None,
) -> tuple[Ctx, list, list]:
    records: list = []
    seq_values: list = []
    phases: list = []
    connections = registry if registry is not None else ConnectionRegistry()

    def record_fn(record) -> None:
        records.append(record)

    def seq_fn() -> int:
        value = len(seq_values)
        seq_values.append(value)
        return value

    cursor = {"index": 0}

    def choose_fn(items) -> object:
        if not items:
            return None
        index = cursor["index"] % len(items)
        cursor["index"] += 1
        return items[index]

    ctx = Ctx(
        scene="test",
        worker_id=3,
        tenant_idx=1,
        headers={"X-Auth-Key": "k"},
        base_url=base_url,
        read_timeout_s=5.0,
        params=params or {},
        duration_s=60.0,
        stop=stop if stop is not None else threading.Event(),
        record_fn=record_fn,
        seq_fn=seq_fn,
        choose_fn=choose_fn,
        phases=phases,
        extra=extra,
        interrupt=interrupt,
        registry=connections,
    )
    return ctx, records, phases


def _start_raw_server(handler_cls) -> http.server.ThreadingHTTPServer:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_request_ok(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", body={"query": "q"}, op="read", query="q")
    assert resp.ok
    assert resp.http_status == 200
    assert resp.json["result"]["items"]
    assert resp.record.status == "ok"
    assert resp.record.op == "read"
    assert resp.record.worker_id == 3
    assert resp.record.tenant_idx == 1
    assert resp.record.query == "q"
    assert resp.record.http_status == 200
    assert resp.record.stage_ms >= 0
    assert len(records) == 1
    assert records[0] is resp.record


def test_request_http_5xx(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/fail500", op="boom")
    assert not resp.ok
    assert resp.http_status == 500
    assert resp.status == "error"
    assert resp.record.error_type == "http_5xx"
    assert resp.record.status == "error"
    assert resp.record.op == "boom"
    assert len(records) == 1


def test_request_http_4xx(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/fail404", op="boom")
    assert resp.status == "error"
    assert resp.record.error_type == "http_4xx"
    assert resp.record.http_status == 404


def test_request_default_op_from_path(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    resp = ctx.post("/api/sessions/open")
    assert resp.op == "open"


def test_request_connection_error(mock_server):
    ctx, records, _ = make_ctx("http://127.0.0.1:1")
    resp = ctx.post("/api/x", op="conn")
    assert resp.status == "error"
    assert resp.record.error_type == "connection"
    assert resp.http_status is None
    assert len(records) == 1


def test_request_timeout(mock_server):
    httpd, state, base_url = mock_server(MockState(delay_s=0.5))
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", op="slow", timeout_s=0.1)
    assert resp.status == "error"
    assert resp.record.error_type == "timeout"
    assert resp.http_status is None


def test_keepalive_reuses_connection(server):
    httpd, state, base_url = server
    ctx, records, _ = make_ctx(base_url)
    for _ in range(50):
        resp = ctx.post("/api/retrieval/search", body={"query": "q"}, op="read")
        assert resp.ok
    # One thread, one persistent connection: the mock must not see a new
    # TCP connection per request (that would skew latency and exhaust
    # client sockets under load).
    assert state.connections <= 2


def test_note_updates_last_record(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", body={"query": "q"}, op="read", query="q")
    ctx.note(hit_count=2, quality_ok=False)
    assert resp.record.hit_count == 2
    assert resp.record.quality_ok is False
    assert records[0].hit_count == 2


def test_require_status(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    resp = ctx.post("/api/retrieval/search", op="read")
    assert resp.require_status(200) is resp
    with pytest.raises(AssertionFailure):
        ctx.post("/api/fail404", op="x").require_status(200)


def test_poll_completed(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=5, interval_s=0.02)
    assert result.status == "completed"
    assert result.polls >= 2  # pending -> pending -> completed
    assert result.record.status == "ok"
    assert result.record.error_type == ""
    assert result.record.op == "commit_done"
    assert len(records) == 1


def test_poll_timeout(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=0.05, interval_s=0.01,
                      until=lambda body: False)
    assert result.status == "timeout"
    assert result.record.status == "error"
    assert result.record.error_type == "commit_timeout"


def test_poll_failed(mock_server):
    httpd, state, base_url = mock_server(MockState(poll_fail_after=1))
    ctx, records, _ = make_ctx(base_url)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=5, interval_s=0.02)
    assert result.status == "failed"
    assert result.record.status == "error"
    assert result.record.error_type == "commit_failed"


def test_poll_stopped(server):
    _, _, base_url = server
    stop = threading.Event()
    stop.set()
    ctx, records, _ = make_ctx(base_url, stop=stop)
    result = ctx.poll("/api/sessions/s1/commits/a1", op="commit_done",
                      timeout_s=5, interval_s=100)
    assert result.status == "stopped"
    assert result.record is None
    assert records == []


def test_choose_round_robin(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    assert ctx.choose(["a", "b", "c"]) == "a"
    assert ctx.choose(["a", "b", "c"]) == "b"
    assert ctx.choose(["a", "b", "c"]) == "c"
    assert ctx.choose(["a", "b", "c"]) == "a"


def test_next_seq(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)
    assert ctx.next_seq() == 0
    assert ctx.next_seq() == 1


def test_at_time_and_at_ratio(server):
    _, _, base_url = server
    ctx, _, phases = make_ctx(base_url)

    def fn(ctx):
        pass

    ctx.at_time(5.0, fn, count=3, max_workers=2, name="burst")
    ctx.at_ratio(0.5, fn, count=1, max_workers=1, name="mid")
    assert len(phases) == 2
    assert phases[0].at_s == 5.0
    assert phases[0].count == 3
    assert phases[0].max_workers == 2
    assert phases[0].name == "burst"
    assert phases[1].at_s == 30.0  # 0.5 * duration_s(60)
    assert phases[1].name == "mid"


def test_at_time_invalid(server):
    _, _, base_url = server
    ctx, _, _ = make_ctx(base_url)

    def fn(ctx):
        pass

    with pytest.raises(ValueError):
        ctx.at_time(-1, fn)


def test_extra_default_and_override(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url, extra="burst")
    ctx.post("/api/retrieval/search", op="read")
    assert records[0].extra == "burst"
    ctx.post("/api/retrieval/search", op="read", extra="")
    assert records[1].extra == ""


def test_manual_record_and_note(server):
    _, _, base_url = server
    ctx, records, _ = make_ctx(base_url)
    ctx.record(op="txn", stage_ms=1.5, status="ok", session_id="s1")
    ctx.note(archive_id="a1")
    assert len(records) == 1
    assert records[0].op == "txn"
    assert records[0].archive_id == "a1"


# -- transport interrupt (engine timeout): bounded exit -------------------


def test_request_aborts_on_interrupt_while_body_stalled():
    """引擎超时中断 shutdown 连接后，阻塞在响应体读取的请求立即以 stopped 返回。

    同时验证 F-002（停机本身有界：body 仍挂起时 shutdown_all 立即返回）与
    F-006（worker 退出后注册表回到基线）。
    """
    release = threading.Event()

    class StallHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            # 发完 header 后挂起 body，直到测试收尾释放。
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "4096")
            self.end_headers()
            release.wait(10)

    httpd = _start_raw_server(StallHandler)
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        interrupt = threading.Event()
        registry = ConnectionRegistry()
        ctx, records, _ = make_ctx(base_url, interrupt=interrupt, registry=registry)
        outcome: dict = {}

        def _run():
            outcome["resp"] = ctx.get("/stall", op="stall")

        worker = threading.Thread(target=_run)
        worker.start()
        time.sleep(0.3)  # 让请求进入 body 读取并阻塞
        started = time.perf_counter()
        interrupt.set()  # 模拟 case 超时后 Engine.stop() 的中断
        registry.shutdown_all()  # body 仍挂起：shutdown 必须立即返回（F-002 有界停机）
        shutdown_elapsed = time.perf_counter() - started
        worker.join(2.0)
        assert not worker.is_alive(), "interrupt 后阻塞读应立即返回"
        assert shutdown_elapsed < 1.0, (
            f"停机调用不应等待挂起的响应体（耗时 {shutdown_elapsed:.2f}s）"
        )
        resp = outcome["resp"]
        assert resp.status == "error"
        assert resp.record.error_type == "stopped"
        assert len(registry) == 0, "worker 退出后注册表应回到基线"
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()


def test_request_aborts_on_interrupt_when_connection_close_detaches_socket():
    """F-002：``Connection: close`` 响应把 sock 摘到响应句柄后，shutdown_all
    仍能打断阻塞的 body 读取，worker 有界退出（不依赖服务端结束响应）。"""
    release = threading.Event()

    class CloseStallHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            # ``Connection: close`` 让客户端 getresponse 把 sock 从连接上摘除
            # （所有权转给响应句柄）；挂起 body 直到测试收尾释放。
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "4096")
            self.send_header("Connection", "close")
            self.end_headers()
            release.wait(10)

    httpd = _start_raw_server(CloseStallHandler)
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        interrupt = threading.Event()
        registry = ConnectionRegistry()
        ctx, records, _ = make_ctx(base_url, interrupt=interrupt, registry=registry)
        outcome: dict = {}

        def _run():
            outcome["resp"] = ctx.get("/stall", op="stall")

        worker = threading.Thread(target=_run)
        worker.start()
        time.sleep(0.3)  # 让请求进入 body 读取并阻塞（sock 已摘到响应句柄）
        started = time.perf_counter()
        interrupt.set()  # 模拟 case 超时后 Engine.stop() 的中断
        registry.shutdown_all()  # body 仍挂起：shutdown 必须立即返回（F-002 有界停机）
        shutdown_elapsed = time.perf_counter() - started
        worker.join(2.0)
        assert not worker.is_alive(), "响应摘除 sock 后停机仍应打断阻塞读"
        assert shutdown_elapsed < 1.0, (
            f"停机调用不应等待挂起的响应体（耗时 {shutdown_elapsed:.2f}s）"
        )
        resp = outcome["resp"]
        assert resp.status == "error"
        assert resp.record.error_type == "stopped"
        assert len(registry) == 0, "worker 退出后注册表应回到基线"
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()


def test_registry_rejects_registration_after_shutdown():
    """F-002：注册表停机后，迟到的连接/响应注册立即被关闭并拒绝（stopped）。"""
    registry = ConnectionRegistry()
    registry.shutdown_all()

    left, right = socket.socketpair()
    conn = http.client.HTTPConnection("127.0.0.1", 1)
    conn.sock = left
    with pytest.raises(TransportError) as excinfo:
        registry.add(conn)
    assert excinfo.value.error_type == "stopped"
    assert left._closed, "停机后迟到注册的连接应被立即关闭"
    assert len(registry) == 0

    left2, right2 = socket.socketpair()
    response = SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=left2)))
    with pytest.raises(TransportError) as excinfo:
        registry.register_response(response)
    assert excinfo.value.error_type == "stopped"
    assert left2.fileno() == -1, "停机后迟到的响应句柄应被立即取消"
    right.close()
    right2.close()


def test_reuse_connection_connects_before_registering(server):
    """F-002：登记前必已建连——停机快照总能拿到 socket，杜绝停止后建连/重连发请求。"""
    _, _, base_url = server
    registry = ConnectionRegistry()
    conn = _reuse_connection(base_url, 5.0, registry)
    try:
        assert conn.sock is not None, "登记前必须完成建连（不得出现未连接连接）"
        assert len(registry) == 1
    finally:
        _drop_connection(registry)


def test_connection_registry_lifecycle():
    """ConnectionRegistry：add/discard 幂等，close_all 清空注册表。"""
    registry = ConnectionRegistry()
    assert len(registry) == 0
    conn = http.client.HTTPConnection("127.0.0.1", 1)
    registry.add(conn)
    registry.add(conn)
    assert len(registry) == 1
    registry.discard(conn)
    assert len(registry) == 0
    other = http.client.HTTPConnection("127.0.0.1", 1)
    registry.add(other)
    registry.close_all()
    assert len(registry) == 0


def test_request_slow_stream_succeeds_within_budget():
    """数据间隙大于请求轮询粒度但小于整体预算的慢流响应仍成功。"""
    class SlowHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "3")
            self.end_headers()
            for _ in range(3):
                time.sleep(0.3)  # 数据间隙（0.3s）小于 read_timeout_s(5s) 的慢流不被误杀
                self.wfile.write(b"x")
            self.wfile.flush()

    httpd = _start_raw_server(SlowHandler)
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        ctx, records, _ = make_ctx(base_url)  # read_timeout_s=5.0
        resp = ctx.get("/slow", op="slow")
        assert resp.ok
        assert resp.body_text == "xxx"
    finally:
        httpd.shutdown()
        httpd.server_close()
