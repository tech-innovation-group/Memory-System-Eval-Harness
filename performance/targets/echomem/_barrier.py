"""Commit-barrier 租户分布计算（共享辅助，非场景/探针）。

把 ``count`` 个 commit 事务按分布分给各租户，返回 ``{tenant_idx: count}``
（引擎的 ``ctx.at_time(tenant_counts=...)`` 按此在单个并发阶段内执行）。

- ``uniform``：均分，余数给前几个租户；
- ``zipf``：rank 1..N 权重 ``1/rank^s`` 归一后按比例取整，余数补首位；
- ``explicit``：直接使用给定的每租户计数（长度必须 == 租户数且总和 == count）。
"""

from __future__ import annotations

from typing import Sequence


def barrier_tenant_counts(
    count: int,
    tenant_count: int,
    *,
    distribution: str = "uniform",
    zipf_exponent: float = 2.0,
    explicit: Sequence[int] | None = None,
) -> dict[int, int]:
    """把 ``count`` 个 commit 按分布分给 ``tenant_count`` 个租户。"""
    if count < 1:
        raise ValueError("barrier count must be >= 1")
    if tenant_count < 1:
        raise ValueError("commit barrier 需要至少一个租户")
    if distribution == "uniform":
        base, remainder = divmod(count, tenant_count)
        values = [base + (1 if index < remainder else 0) for index in range(tenant_count)]
    elif distribution == "zipf":
        weights = [1.0 / (rank ** zipf_exponent) for rank in range(1, tenant_count + 1)]
        weight_sum = sum(weights)
        values = [int(count * weight / weight_sum) for weight in weights]
        values[0] += count - sum(values)
    elif distribution == "explicit":
        values = list(explicit or [])
        if len(values) != tenant_count:
            raise ValueError(
                f"explicit barrier 分布需要 {tenant_count} 个租户计数，实际 {len(values)}"
            )
        if sum(values) != count:
            raise ValueError(f"explicit barrier 计数总和 {sum(values)} != count {count}")
        if any(value < 0 for value in values):
            raise ValueError("explicit barrier counts must be non-negative")
    else:
        raise ValueError(f"unknown barrier distribution: {distribution}")
    return {index: value for index, value in enumerate(values)}
