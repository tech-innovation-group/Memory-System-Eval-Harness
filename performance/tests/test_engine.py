"""Engine-level tests: worker split, mix, burst phases, recording."""

from __future__ import annotations

import http.server
import threading
import time
from pathlib import Path

import pytest

from performance.engine import Engine, SceneError, SceneModule, load_scene, split_workers
from performance.profile import Profile, TargetSpec, load_profile
from performance.targets.echomem.protocol import task_read


def _scene(*, tasks: dict | None = None, task=None, schedule=None, name="test") -> SceneModule:
    if tasks is None:
        tasks = {"main": task} if task is not None else {"main": lambda ctx: None}
    return SceneModule(name=name, description="", tasks=tasks, schedule=schedule)


def _profile(base_url: str, **load_changes) -> Profile:
    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": 2, "duration_s": 1.0, **load_changes},
            "params": {"queries": ["q1", "q2"]},
        }
    )
    return profile


# -- split_workers ------------------------------------------------------


def test_split_equal():
    assert split_workers(2, {"read": 1, "write": 1}) == {"read": 1, "write": 1}


def test_split_8_to_1():
    assert split_workers(9, {"read": 8, "write": 1}) == {"read": 8, "write": 1}


def test_split_round_read_first():
    # round(10 * 8/9) = 9; write gets the remainder 1
    assert split_workers(10, {"read": 8, "write": 1}) == {"read": 9, "write": 1}


def test_split_single_task():
    assert split_workers(4, {"read": 1}) == {"read": 4}


def test_split_zero_total():
    with pytest.raises(ValueError):
        split_workers(2, {"read": 0})


# -- basic run ----------------------------------------------------------


def test_single_task_run(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", body={"query": ctx.choose(ctx.params["queries"])},
                 op="read")

    profile = _profile(base_url)
    result = Engine(profile, _scene(task=read)).run()
    ops = {r.op for r in result.records}
    assert "read" in ops
    assert result.records
    assert all(r.status == "ok" for r in result.records)
    assert result.elapsed_s > 0


def test_search_request_carries_agent_id(server):
    """回归：/api/retrieval/search 必须携带非空 agent_id（EchoMem require_text 校验）。"""
    httpd, state, base_url = server
    profile = _profile(base_url, workers=2, duration_s=1.5, mix={"read": 1})
    result = Engine(profile, _scene(tasks={"read": task_read})).run()
    assert result.records
    assert state.search_agent_ids, "no search request reached the server"
    assert all(aid == "default" for aid in state.search_agent_ids)


def test_worker_ids_and_tenant_binding(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    profile = _profile(base_url, workers=3, duration_s=2.0)
    result = Engine(profile, _scene(task=read)).run()
    worker_ids = {r.worker_id for r in result.records}
    assert worker_ids == {0, 1, 2}


def test_mix_runs_both_tasks(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def write(ctx):
        ctx.post("/api/sessions/open", op="open")

    tasks = {"read": read, "write": write}
    profile = _profile(base_url, workers=3, duration_s=2.0, mix={"read": 2, "write": 1})
    result = Engine(profile, _scene(tasks=tasks)).run()
    ops = {r.op for r in result.records}
    assert "read" in ops
    assert "open" in ops
    read_workers = {r.worker_id for r in result.records if r.op == "read"}
    write_workers = {r.worker_id for r in result.records if r.op == "open"}
    assert len(read_workers) == 2
    assert len(write_workers) == 1
    assert read_workers.isdisjoint(write_workers)


def test_mix_unknown_task_rejected(server):
    _, _, base_url = server
    profile = _profile(base_url, mix={"read": 1, "bogus": 1})
    with pytest.raises(SceneError, match="unknown tasks"):
        Engine(profile, _scene(task=lambda ctx: None)).run()


def test_burst_phase(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def burst_job(ctx):
        ctx.post("/api/sessions/open", op="open")

    def schedule(ctx):
        ctx.at_time(0.1, burst_job, count=3, max_workers=2, name="burst")

    profile = _profile(base_url, workers=2, duration_s=2.0)
    result = Engine(profile, _scene(task=read, schedule=schedule)).run()
    burst_records = [r for r in result.records if r.extra == "burst"]
    assert len(burst_records) == 3
    assert all(r.op == "open" for r in burst_records)
    assert all(r.worker_id == -1 for r in burst_records)
    read_records = [r for r in result.records if r.op == "read"]
    assert read_records


def test_phase_tenant_counts_distributes_across_tenants(server):
    _, _, base_url = server
    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": 1, "duration_s": 1.0},
            "tenants": [{"name": "t0"}, {"name": "t1"}, {"name": "t2"}],
        }
    )

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def job(ctx):
        ctx.post("/api/sessions/open", op="open")

    def schedule(ctx):
        ctx.at_time(0.1, job, tenant_counts={0: 2, 1: 1, 2: 1}, max_workers=4, name="barrier")

    result = Engine(profile, _scene(task=read, schedule=schedule)).run()
    burst = [r for r in result.records if r.extra == "barrier"]
    assert len(burst) == 4
    assert all(r.worker_id == -1 for r in burst)
    assert sorted(r.tenant_idx for r in burst) == [0, 0, 1, 2]


def test_burst_phase_jobs_keep_own_response_ids():
    """并发 phase job 各持独立 Ctx：record() 后的 note() 只补写本 job 记录。

    两个 job 的 record 与 note 之间用 Event 固定交错；旧实现共享 Ctx 时，
    前一个响应的 message_id/archive_id 会落到后一个请求记录上。
    """
    import itertools
    import threading

    counter = itertools.count()
    lock = threading.Lock()
    reached = threading.Event()
    order = {"n": 0}

    def main(ctx):
        pass

    def burst_job(ctx):
        i = next(counter)
        ctx.record(op="open", stage_ms=1.0, status="ok", session_id=f"s{i}")
        with lock:
            order["n"] += 1
            if order["n"] == 2:
                reached.set()
        reached.wait(3.0)
        ctx.note(message_id=f"m{i}", archive_id=f"a{i}")

    def schedule(ctx):
        ctx.at_time(0.0, burst_job, count=2, max_workers=2, name="burst")

    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": "http://127.0.0.1:8010", "read_timeout_s": 5},
            "load": {"workers": 1, "duration_s": 0.5},
        }
    )
    result = Engine(profile, _scene(task=main, schedule=schedule)).run()
    burst = [r for r in result.records if r.extra == "burst"]
    assert len(burst) == 2
    for record in burst:
        idx = record.session_id[1:]  # "s0" -> "0"
        assert record.message_id == f"m{idx}"
        assert record.archive_id == f"a{idx}"


def test_at_time_rejects_empty_tenant_counts(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def schedule(ctx):
        ctx.at_time(0.1, read, tenant_counts={})

    profile = _profile(base_url, duration_s=0.3)
    result = Engine(profile, _scene(task=read, schedule=schedule)).run()
    assert any(r.op == "transaction" and "tenant_counts" in r.detail
               for r in result.records)


def test_worker_exception_recorded(server):
    _, _, base_url = server

    def boom(ctx):
        raise RuntimeError("kaboom")

    profile = _profile(base_url, duration_s=1.5)
    result = Engine(profile, _scene(task=boom)).run()
    txn_errors = [r for r in result.records if r.op == "transaction"]
    assert txn_errors
    assert all(r.status == "error" for r in txn_errors)
    assert any("kaboom" in r.detail for r in txn_errors)


def test_fixed_rps_gate_limits_rate(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {
                "workers": 1,
                "duration_s": 1.5,
                "arrival": {"main": {"model": "fixed_rps", "rps": 5}},
            },
        }
    )
    result = Engine(profile, _scene(task=read)).run()
    reads = [r for r in result.records if r.op == "read"]
    assert 3 <= len(reads) <= 8  # ~5 rps over ~1s, tolerant of scheduling
    assert all(r.status == "ok" for r in reads)


def test_schedule_exception_recorded(server):
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", op="read")

    def bad_schedule(ctx):
        raise RuntimeError("schedule boom")

    profile = _profile(base_url, duration_s=0.3)
    result = Engine(profile, _scene(task=read, schedule=bad_schedule)).run()
    assert any(r.op == "transaction" and "schedule boom" in r.detail for r in result.records)


# -- load_scene ---------------------------------------------------------


def test_load_scene_task(tmp_path):
    path = tmp_path / "my_scene.py"
    path.write_text(
        "def task(ctx):\n    pass\n",
        encoding="utf-8",
    )
    scene = load_scene(path)
    assert scene.name == "my_scene"
    assert list(scene.tasks) == ["main"]


def test_load_scene_tasks_and_schedule(tmp_path):
    path = tmp_path / "my_scene.py"
    path.write_text(
        "def task_read(ctx):\n    pass\n\n"
        "def task_write(ctx):\n    pass\n\n"
        "tasks = {'read': task_read, 'write': task_write}\n\n"
        "def schedule(ctx):\n    pass\n",
        encoding="utf-8",
    )
    scene = load_scene(path)
    assert list(scene.tasks) == ["read", "write"]
    assert scene.schedule is not None


def test_load_scene_missing_contract(tmp_path):
    path = tmp_path / "bad_scene.py"
    path.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(SceneError, match="task"):
        load_scene(path)


def test_load_scene_not_found(tmp_path):
    with pytest.raises(SceneError, match="not found"):
        load_scene(tmp_path / "nope.py")


# -- connection lifecycle (PR#31 F-006 / F-002) -------------------------


def test_sequential_engines_release_connections(server):
    """F-006：连续健康 Engine 结束后连接注册表回到基线，不跨 case 累积。"""
    _, _, base_url = server

    def read(ctx):
        ctx.post("/api/retrieval/search", body={"query": "q"}, op="read")

    profile = load_profile(
        {
            "name": "p",
            "target": {"base_url": base_url, "read_timeout_s": 5},
            "load": {"workers": 4, "duration_s": 0.4},
        }
    )
    for _ in range(2):
        engine = Engine(profile, _scene(task=read))
        assert len(engine._connections) == 0
        result = engine.run()
        assert result.records
        assert all(r.status == "ok" for r in result.records)
        assert len(engine._connections) == 0, "run() 收尾后注册表应回到基线"


def test_engine_stop_bounded_with_stalled_request():
    """F-002：响应体挂起时 Engine.stop() 有界返回，worker 在窗口内退出。"""
    release = threading.Event()
    arrived = threading.Event()

    class StallHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "4096")
            self.end_headers()
            arrived.set()
            release.wait(10)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StallHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"

        def read(ctx):
            ctx.post("/api/stall", op="stall")

        profile = load_profile(
            {
                "name": "p",
                "target": {"base_url": base_url, "read_timeout_s": 5},
                "load": {"workers": 1, "duration_s": 30.0},
            }
        )
        engine = Engine(profile, _scene(task=read))
        holder: dict = {}

        def _run():
            holder["result"] = engine.run()

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        assert arrived.wait(2.0), "请求未到达服务端"
        time.sleep(0.2)  # 让客户端进入 body 读取并阻塞
        started = time.perf_counter()
        engine.stop()
        elapsed = time.perf_counter() - started
        worker.join(2.0)
        assert elapsed < 1.0, f"Engine.stop 不应等待挂起的响应体（耗时 {elapsed:.2f}s）"
        assert not worker.is_alive(), "stop 后 worker 应在确认窗口内退出"
        records = holder["result"].records
        assert records
        assert all(r.status == "error" and r.error_type == "stopped"
                   for r in records)
        assert len(engine._connections) == 0
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()


def test_engine_stop_bounded_with_connection_close_stalled():
    """F-002：``Connection: close`` 把 sock 摘到响应句柄的挂起响应，Engine.stop()
    仍有界返回，worker 在窗口内退出（不依赖服务端结束响应）。"""
    release = threading.Event()
    arrived = threading.Event()

    class CloseStallHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "4096")
            self.send_header("Connection", "close")
            self.end_headers()
            arrived.set()
            release.wait(10)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CloseStallHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"

        def read(ctx):
            ctx.post("/api/stall", op="stall")

        profile = load_profile(
            {
                "name": "p",
                "target": {"base_url": base_url, "read_timeout_s": 5},
                "load": {"workers": 1, "duration_s": 30.0},
            }
        )
        engine = Engine(profile, _scene(task=read))
        holder: dict = {}

        def _run():
            holder["result"] = engine.run()

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        assert arrived.wait(2.0), "请求未到达服务端"
        time.sleep(0.2)  # 让客户端进入 body 读取并阻塞（sock 已摘到响应句柄）
        started = time.perf_counter()
        engine.stop()
        elapsed = time.perf_counter() - started
        worker.join(2.0)
        assert elapsed < 1.0, f"Engine.stop 不应等待挂起的响应体（耗时 {elapsed:.2f}s）"
        assert not worker.is_alive(), "stop 后 worker 应在确认窗口内退出"
        records = holder["result"].records
        assert records
        assert all(r.status == "error" and r.error_type == "stopped"
                   for r in records)
        assert len(engine._connections) == 0
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()


def test_engine_stop_prevents_reconnect_after_connection_close():
    """F-002：``Connection: close`` 后线程内连接已摘除 sock，stop 后的自动重连
    不得发出请求——服务端收不到第二条请求，worker 有界退出。"""
    requests_received: list[int] = []
    proceed = threading.Event()

    class CloseEveryHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            requests_received.append(1)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CloseEveryHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"

        def txn(ctx):
            ctx.post("/api/a", op="first")
            proceed.wait(10)
            ctx.post("/api/b", op="second")

        profile = load_profile(
            {
                "name": "p",
                "target": {"base_url": base_url, "read_timeout_s": 5},
                "load": {"workers": 1, "duration_s": 30.0},
            }
        )
        engine = Engine(profile, _scene(task=txn))
        holder: dict = {}

        def _run():
            holder["result"] = engine.run()

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        deadline = time.time() + 5.0
        while len(requests_received) < 1 and time.time() < deadline:
            time.sleep(0.02)
        assert requests_received, "第一条请求未到达服务端"
        engine.stop()  # 线程内连接已因 Connection: close 摘除 sock
        proceed.set()  # 放行第二条请求 → 触发自动重连
        worker.join(2.0)
        assert not worker.is_alive(), "stop 后 worker 应在确认窗口内退出"
        time.sleep(0.3)
        assert len(requests_received) == 1, "stop 后不得发出任何请求"
        records = holder["result"].records
        ops = [(r.op, r.status, r.error_type) for r in records]
        assert ("first", "ok", "") in ops
        assert ("second", "error", "stopped") in ops
        assert len(engine._connections) == 0
    finally:
        proceed.set()
        httpd.shutdown()
        httpd.server_close()
