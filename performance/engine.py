"""Load engine: worker pool, arrival gates, phase injection, recording.

The engine is fully generic.  It knows nothing about EchoMem — it drives
whatever task functions a scene file exports, split across workers by
the profile's ``mix`` weights, for the profile's duration, and executes
any phases the scene's ``schedule`` hook registered with
``ctx.at_time`` / ``ctx.at_ratio``.
"""

from __future__ import annotations

import importlib.util
import itertools
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from performance.ctx import ConnectionRegistry, Ctx, Phase
from performance.profile import ArrivalSpec, Profile, TenantSpec
from performance.records import RequestRecord

# Worker id reserved for phase-injected (burst) executions.
BURST_WORKER_ID = -1


@dataclass
class SceneModule:
    """A loaded scene file: the task functions and optional hooks."""

    name: str
    description: str
    tasks: dict[str, Callable[[Ctx], None]]
    schedule: Callable[[Ctx], None] | None
    report: Callable[[RunResult, Profile], dict] | None = None


class SceneError(ValueError):
    """A scene file is missing or does not export a valid task contract."""


@dataclass
class RunResult:
    records: list[RequestRecord]
    started_at: float
    finished_at: float

    @property
    def elapsed_s(self) -> float:
        return self.finished_at - self.started_at


def load_scene(path: str | Path) -> SceneModule:
    """Import a scene file and extract its task contract.

    A scene must export ``task`` (single callable) or ``tasks`` (mapping
    of task name -> callable); ``schedule(ctx)`` is optional and runs once
    before workers start so it can register phases; ``report(result,
    profile)`` is optional and returns a JSON-safe dict of system-specific
    analysis merged into the summary as ``custom``.
    """
    scene_path = Path(path)
    if not scene_path.is_file():
        raise SceneError(f"scene file not found: {scene_path}")
    name = scene_path.stem
    spec = importlib.util.spec_from_file_location(name, scene_path)
    if spec is None or spec.loader is None:
        raise SceneError(f"cannot load scene module: {scene_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise SceneError(f"scene module failed to import: {scene_path}: {exc}") from exc

    tasks: dict[str, Callable[[Ctx], None]] = {}
    if hasattr(module, "tasks"):
        raw_tasks = module.tasks
        if not isinstance(raw_tasks, dict) or not raw_tasks:
            raise SceneError(f"scene {name}: tasks must be a non-empty mapping")
        for task_name, fn in raw_tasks.items():
            if not isinstance(task_name, str) or not callable(fn):
                raise SceneError(f"scene {name}: tasks[{task_name!r}] must be callable")
            tasks[task_name] = fn
    elif hasattr(module, "task") and callable(module.task):
        tasks["main"] = module.task
    else:
        raise SceneError(
            f"scene {name}: export `task` (callable) or `tasks` (mapping of callables)"
        )

    schedule = module.schedule if callable(getattr(module, "schedule", None)) else None
    report = module.report if callable(getattr(module, "report", None)) else None
    description = (module.__doc__ or "").strip().splitlines()[0] if module.__doc__ else ""
    return SceneModule(name=name, description=description, tasks=tasks, schedule=schedule,
                       report=report)


def split_workers(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Split worker threads across tasks by integer weights.

    The first task gets ``round(total * weight / sum)``, the last task
    absorbs the remainder.
    """
    if total < 1:
        raise ValueError("total workers must be >= 1")
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("mix weights must sum to > 0")
    assigned: dict[str, int] = {}
    names = list(weights)
    for index, task_name in enumerate(names):
        if index == len(names) - 1:
            assigned[task_name] = total - sum(assigned.values())
        else:
            assigned[task_name] = round(total * weights[task_name] / total_weight)
    if sum(assigned.values()) != total:
        assigned[names[0]] += total - sum(assigned.values())
    return assigned


class RateGate:
    """Global arrival gate for one task type.

    ``fixed_rps`` spaces starts on an exact schedule: during ``ramp_s``
    the rate ramps linearly from zero (slot times follow
    ``t(k) = sqrt(2 * ramp_s * k / rps)``), then becomes constant.  The
    first start fires immediately.  ``poisson`` samples global
    exponential inter-arrival gaps at the target rate (no ramp).
    """

    def __init__(self, arrival: ArrivalSpec):
        self.arrival = arrival
        self._lock = threading.Lock()
        self._claimed = 0
        self._next_slot = 0.0

    def wait(self, started_at: float, stop: threading.Event) -> None:
        if self.arrival.model == "fixed_rps":
            with self._lock:
                k = self._claimed
                self._claimed += 1
            slot = self._fixed_slot_time(k)
        else:
            with self._lock:
                now = time.perf_counter() - started_at
                slot = max(now, self._next_slot)
                self._next_slot = slot + random.expovariate(self.arrival.rps)
        while True:
            remaining = started_at + slot - time.perf_counter()
            if remaining <= 0:
                return
            if stop.wait(remaining):
                return

    def _fixed_slot_time(self, k: int) -> float:
        arrival = self.arrival
        if arrival.ramp_s > 0:
            ramp_requests = arrival.rps * arrival.ramp_s / 2.0
            if k < ramp_requests:
                return math.sqrt(2.0 * arrival.ramp_s * k / arrival.rps)
            return arrival.ramp_s + (k - ramp_requests) / arrival.rps
        return k / arrival.rps


class Engine:
    """Drives a loaded scene against its profile for the load duration."""

    def __init__(self, profile: Profile, scene: SceneModule):
        self.profile = profile
        self.scene = scene
        self.records: list[RequestRecord] = []
        self._records_lock = threading.Lock()
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self._connections = ConnectionRegistry()
        self._seq_counter = itertools.count()
        self._data_cursor = 0
        self._data_lock = threading.Lock()
        self._phases: list[Phase] = []
        self._phases_lock = threading.Lock()
        self._started_at = 0.0
        self._finished_at = 0.0

    # -- lifecycle --------------------------------------------------------

    def run(self) -> RunResult:
        self._started_at = time.perf_counter()
        try:
            assignment = self._assign_workers()
            self._gates = self._build_gates()
            workers: list[tuple[threading.Thread, Callable[[Ctx], None]]] = []
            worker_index = 0
            for task_name, count in assignment.items():
                gate = self._gates.get(task_name)
                for _ in range(count):
                    tenant_idx = worker_index % len(self._tenants())
                    ctx = self._make_ctx(worker_id=worker_index, tenant_idx=tenant_idx)
                    thread = threading.Thread(
                        target=self._worker_loop,
                        args=(ctx, self.scene.tasks[task_name], gate),
                        name=f"perf-{task_name}-{worker_index}",
                        daemon=True,
                    )
                    workers.append(thread)
                    worker_index += 1
            for thread in workers:
                thread.start()

            if self.scene.schedule is not None:
                probe = self._make_ctx(worker_id=BURST_WORKER_ID, tenant_idx=0)
                try:
                    self.scene.schedule(probe)
                except Exception as exc:
                    self._record_worker_error(probe, exc)

            self._run_until_deadline()

            self._stop.set()
            for thread in workers:
                thread.join()
        finally:
            # 正常收尾（含异常路径）：关闭并注销本 Engine 全部 keep-alive
            # 连接，顺序 case 不跨进程累积客户端描述符。超时中断路径下注册
            # 表已在 stop() 中被 shutdown_all 清空，此处为幂等 no-op。
            self._connections.close_all()
        self._finished_at = time.perf_counter()
        return RunResult(
            records=list(self.records),
            started_at=self._started_at,
            finished_at=self._finished_at,
        )

    def stop(self) -> None:
        # 先传播停机，再有界打断在途 HTTP 请求：对本 Engine 注册表里的连接
        # 做 shutdown + 立即关闭 OS 句柄（不等缓冲响应读取，不阻塞），阻塞读
        # 立即返回并映射为 stopped；连接由 worker 在错误路径自行关闭。仅
        # suite 超时回收路径调用；引擎自然收尾只 set _stop，让在途请求正常
        # 完成，不产生 stopped 记录。
        self._interrupt.set()
        self._stop.set()
        self._connections.shutdown_all()

    # -- worker orchestration ---------------------------------------------

    def _assign_workers(self) -> dict[str, int]:
        weights = self.profile.load.mix
        if weights is None:
            weights = {name: 1 for name in self.scene.tasks}
        unknown = [name for name in weights if name not in self.scene.tasks]
        if unknown:
            raise SceneError(
                f"profile mix references unknown tasks: {', '.join(unknown)} "
                f"(scene exports: {', '.join(self.scene.tasks)})"
            )
        return split_workers(self.profile.load.workers, weights)

    def _build_gates(self) -> dict[str, RateGate]:
        gates: dict[str, RateGate] = {}
        for task_name, arrival in self.profile.load.arrival.items():
            if arrival.model != "none":
                gates[task_name] = RateGate(arrival)
        return gates

    def _tenants(self) -> list[TenantSpec]:
        return self.profile.tenants or [TenantSpec(name="default")]

    def _worker_loop(
        self,
        ctx: Ctx,
        task_fn: Callable[[Ctx], None],
        gate: RateGate | None,
    ) -> None:
        while not self._stop.is_set():
            if gate is not None:
                gate.wait(self._started_at, self._stop)
                if self._stop.is_set():
                    return
            try:
                task_fn(ctx)
            except Exception as exc:
                self._record_worker_error(ctx, exc)

    def _record_worker_error(self, ctx: Ctx, exc: Exception) -> None:
        record = RequestRecord(
            scene=self.scene.name,
            worker_id=ctx.worker_id,
            tenant_idx=ctx.tenant_idx,
            op="transaction",
            stage_ms=0.0,
            status="error",
            error_type="other",
            ts_ms=time.time() * 1000,
            extra=ctx.extra,
            detail=f"{type(exc).__name__}: {exc}",
        )
        with self._records_lock:
            self.records.append(record)

    # -- phases (schedule hook) -------------------------------------------

    def _run_until_deadline(self) -> None:
        duration_s = self.profile.load.duration_s
        deadline = self._started_at + duration_s if duration_s > 0 else float("inf")
        with self._phases_lock:
            phases = sorted(self._phases, key=lambda phase: phase.at_s)
        next_phase = 0
        while True:
            now = time.perf_counter()
            fire_at = deadline
            if next_phase < len(phases):
                fire_at = min(self._started_at + phases[next_phase].at_s, fire_at)
            remaining = fire_at - now
            if remaining > 0:
                if self._stop.wait(remaining):
                    return
            now = time.perf_counter()
            if now >= deadline:
                return
            while next_phase < len(phases) and self._started_at + phases[next_phase].at_s <= now + 1e-6:
                self._run_phase(phases[next_phase])
                next_phase += 1

    def _run_phase(self, phase: Phase) -> None:
        # 每个 phase job 用独立 Ctx：Ctx._last_record 是单指针，并发 job 共享
        # 同一 Ctx 会让 record() 后的 note() 把响应 ID 补写到别的请求记录。
        if phase.tenant_counts:
            jobs: list[Ctx] = []
            for tenant_idx, phase_count in phase.tenant_counts.items():
                for _ in range(phase_count):
                    jobs.append(
                        self._make_ctx(
                            worker_id=BURST_WORKER_ID,
                            tenant_idx=tenant_idx,
                            extra=phase.name,
                        )
                    )
        else:
            tenant_idx = phase.tenant_idx if phase.tenant_idx is not None else 0
            jobs = [
                self._make_ctx(
                    worker_id=BURST_WORKER_ID, tenant_idx=tenant_idx, extra=phase.name
                )
                for _ in range(phase.count)
            ]

        def job(ctx: Ctx) -> None:
            try:
                phase.fn(ctx)
            except Exception as exc:
                self._record_worker_error(ctx, exc)

        pool = ThreadPoolExecutor(max_workers=max(1, min(len(jobs), phase.max_workers)))
        try:
            futures = [pool.submit(job, ctx) for ctx in jobs]
            for future in futures:
                future.result()
        finally:
            pool.shutdown(wait=True)

    # -- context / recording ----------------------------------------------

    def _make_ctx(
        self, *, worker_id: int, tenant_idx: int, extra: str = ""
    ) -> Ctx:
        tenant = self._tenants()[tenant_idx % len(self._tenants())]
        headers = {**self.profile.target.headers, **tenant.headers}
        ctx = Ctx(
            scene=self.scene.name,
            worker_id=worker_id,
            tenant_idx=tenant_idx,
            headers=headers,
            base_url=self.profile.target.base_url,
            read_timeout_s=self.profile.target.read_timeout_s,
            params=self.profile.params,
            duration_s=self.profile.load.duration_s,
            stop=self._stop,
            interrupt=self._interrupt,
            record_fn=self._record,
            seq_fn=self._next_seq,
            choose_fn=self._choose,
            phases=self._phases,
            extra=extra,
            tenant_count=len(self._tenants()),
            registry=self._connections,
        )
        return ctx

    def _record(self, record: RequestRecord) -> None:
        with self._records_lock:
            self.records.append(record)

    def _next_seq(self) -> int:
        return next(self._seq_counter)

    def _choose(self, items: Any) -> Any:
        if not items:
            return None
        with self._data_lock:
            index = self._data_cursor
            self._data_cursor += 1
        return items[index % len(items)]
