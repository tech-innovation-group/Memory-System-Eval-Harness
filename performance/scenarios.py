"""Scenario matrix for performance stress runs.

Scenarios:
    A  pure-read baseline (no writes, used as the comparison reference)
    B  pure-write injection (open -> add -> commit submit -> commit done)
    C  mixed read/write at configurable read:write ratios
    D  injection burst on top of sustained reads (detects write/read coupling)
    S  saturation: commit barrier fired over sustained reads
    H  hot-tenant skew: explicit-distribution commit barrier (multi-wave)
    K  capacity: rate-based mixed read/write at fixed rps / commit-rpm
    I  N×N isolation marker probe (independent one-shot, not part of matrix)

``expand_matrix`` expands (concurrency steps x scenarios x mix ratios) into
an ordered list of :class:`SceneRun` during which server metrics stay running.
S/H/K/I are single-shot scenes: one run each, appended after the A/B/C/D
matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCENARIO_IDS = ("A", "B", "C", "D", "S", "H", "K", "I")

SCENARIO_NAMES: dict[str, str] = {
    "A": "pure-read baseline",
    "B": "pure-write injection",
    "C": "mixed read/write",
    "D": "injection burst over reads",
    "S": "saturation (commit barrier over reads)",
    "H": "hot-tenant skew (explicit barrier)",
    "K": "capacity (rate-based mixed at tenant count)",
    "I": "N×N isolation marker probe",
}


@dataclass(frozen=True)
class SceneRun:
    """One atomic stress step: one scenario at one concurrency step.

    ``per_tenant_conc`` is the number of worker threads per tenant; total
    worker threads = tenants * per_tenant_conc.
    """

    scene_id: str
    per_tenant_conc: int
    duration_s: float
    mix: tuple[int, int] | None = None  # (read, write) ratio, scene C only
    burst_commits: int = 0  # scene D only
    burst_window_s: float = 0.0  # scene D only
    # -- commit barrier (scenes S/H, also carried by K) -------------------
    barrier_commits: int = 0  # 本场景 commit barrier 的 commit 总数
    barrier_distribution: str = "uniform"  # uniform | zipf | explicit
    barrier_zipf_exponent: float = 1.0  # zipf 分布的指数
    barrier_tenant_counts: list[int] | None = None  # explicit 分布的每租户计数
    barrier_waves: int = 1  # H 场景 barrier 波数
    barrier_cooldown_s: float = 0.0  # H 场景波间冷却秒数

    @property
    def key(self) -> str:
        """Stable identifier used as the summary section key."""
        if self.scene_id == "C" and self.mix is not None:
            return f"C:{self.mix[0]}:{self.mix[1]}@{self.per_tenant_conc}"
        return f"{self.scene_id}@{self.per_tenant_conc}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "per_tenant_conc": self.per_tenant_conc,
            "duration_s": self.duration_s,
            "mix": f"{self.mix[0]}:{self.mix[1]}" if self.mix else None,
            "burst_commits": self.burst_commits,
            "burst_window_s": self.burst_window_s,
            "barrier_commits": self.barrier_commits,
            "barrier_distribution": self.barrier_distribution,
            "barrier_zipf_exponent": self.barrier_zipf_exponent,
            "barrier_tenant_counts": self.barrier_tenant_counts,
            "barrier_waves": self.barrier_waves,
            "barrier_cooldown_s": self.barrier_cooldown_s,
        }


def parse_mix_ratio(value: str) -> tuple[int, int]:
    """Parse a "READ:WRITE" ratio string into an int pair.

    ``"8:1"`` means 8 read operations per 1 write transaction.
    """
    left, _, right = value.partition(":")
    read = int(left.strip())
    write = int(right.strip())
    if read < 0 or write < 0 or read + write == 0:
        raise ValueError(f"invalid mix ratio '{value}': expected READ:WRITE")
    return (read, write)


def parse_mix_ratios(values: list[str]) -> list[tuple[int, int]]:
    return [parse_mix_ratio(value) for value in values]


def parse_concurrency_steps(value: str) -> list[int]:
    steps = [int(part.strip()) for part in str(value).split(",") if part.strip()]
    if not steps or any(step < 1 for step in steps):
        raise ValueError(f"invalid concurrency steps '{value}': positive ints expected")
    return steps


def expand_matrix(
    *,
    scenario_ids: list[str],
    concurrency_steps: list[int],
    mix_ratios: list[tuple[int, int]],
    duration_s: float,
    burst_commits: int,
    burst_window_s: float,
    barrier_commits: int = 0,
    barrier_distribution: str = "uniform",
    barrier_zipf_exponent: float = 1.0,
    barrier_tenant_counts: list[int] | None = None,
    barrier_waves: int = 1,
    barrier_cooldown_s: float = 0.0,
) -> list[SceneRun]:
    """Expand the scenario matrix into an ordered list of runs.

    Order: scenario-major, concurrency-minor (A@1, A@4, ... C:8:1@1, ...).
    S/H/K/I are single-shot scenes: each id yields exactly one run at the
    first concurrency step, appended after the A/B/C/D matrix.
    """
    unknown = [sid for sid in scenario_ids if sid not in SCENARIO_IDS]
    if unknown:
        raise ValueError(f"unknown scenario ids: {', '.join(unknown)}")
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")

    runs: list[SceneRun] = []
    for sid in scenario_ids:
        for conc in concurrency_steps:
            if sid == "A":
                runs.append(SceneRun("A", conc, duration_s))
            elif sid == "B":
                runs.append(SceneRun("B", conc, duration_s))
            elif sid == "C":
                for mix in mix_ratios:
                    runs.append(SceneRun("C", conc, duration_s, mix=mix))
            elif sid == "D":
                runs.append(
                    SceneRun(
                        "D",
                        conc,
                        duration_s,
                        burst_commits=burst_commits,
                        burst_window_s=burst_window_s,
                    )
                )

    # 单发场景（S/H/K/I）：每个 id 只产出一个 SceneRun，追加在矩阵之后。
    single_conc = concurrency_steps[0] if concurrency_steps else 1
    for sid in scenario_ids:
        if sid == "S":
            counts = (
                list(barrier_tenant_counts)
                if barrier_distribution == "explicit" and barrier_tenant_counts
                else None
            )
            s_commits = sum(counts) if counts is not None else barrier_commits
            s_distribution = "explicit" if counts is not None else barrier_distribution
            runs.append(
                SceneRun(
                    "S",
                    single_conc,
                    duration_s,
                    burst_commits=s_commits or burst_commits,
                    burst_window_s=burst_window_s,
                    barrier_commits=s_commits,
                    barrier_distribution=s_distribution,
                    barrier_zipf_exponent=barrier_zipf_exponent,
                    barrier_tenant_counts=counts,
                    barrier_waves=barrier_waves,
                    barrier_cooldown_s=barrier_cooldown_s,
                )
            )
        elif sid == "H":
            counts = list(barrier_tenant_counts) if barrier_tenant_counts else None
            if counts is not None:
                h_commits = sum(counts)
                h_distribution = "explicit"
            else:
                h_commits = barrier_commits
                h_distribution = barrier_distribution
            runs.append(
                SceneRun(
                    "H",
                    single_conc,
                    duration_s,
                    barrier_commits=h_commits,
                    barrier_distribution=h_distribution,
                    barrier_tenant_counts=counts,
                    barrier_waves=barrier_waves,
                    barrier_cooldown_s=barrier_cooldown_s,
                )
            )
        elif sid == "K":
            runs.append(SceneRun("K", single_conc, duration_s))
        elif sid == "I":
            runs.append(SceneRun("I", single_conc, duration_s))
    return runs
