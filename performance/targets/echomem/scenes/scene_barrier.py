"""场景 S/H：Commit barrier 风暴。

读负载全程打满；在 ``barrier_at_s`` 处一次性并发注入 ``barrier_count`` 个
写事务（``protocol.task_write``：open -> add×N -> commit submit -> poll
done，四阶段独立计时）。事务按租户分布（uniform / zipf / explicit）在单个
并发阶段内执行（``ctx.at_time(tenant_counts=...)``），``max_workers`` 限制
总并发。``barrier_waves`` > 1 时按 ``barrier_cooldown_s`` 间隔多波注入
（每波 count 相同）。``floor_to_tenants`` 把 uniform 计数下限提到 ≥ 租户数，
保证公平性有界场景每个租户都有 commit 完成。

参数（``ctx.params``）：

- ``barrier_count``：总 commit 事务数（每波），默认 128
- ``barrier_distribution``：uniform / zipf / explicit，默认 uniform
- ``barrier_zipf_exponent``：zipf 指数，默认 2.0
- ``commit_tenant_counts``：explicit 分布的每租户计数列表
- ``barrier_max_workers``：阶段并发上限，默认 8
- ``barrier_at_s``：首波注入时刻，默认 0.0
- ``barrier_waves``：波数，默认 1
- ``barrier_cooldown_s``：波间隔，默认 0.0
- ``floor_to_tenants``：uniform 计数下限提到租户数，默认 False
"""

from __future__ import annotations

from performance.ctx import Ctx
from performance.targets.echomem._barrier import barrier_tenant_counts
from performance.targets.echomem.protocol import task_read, task_write


def _barrier_job(ctx: Ctx) -> None:
    task_write(ctx)


def schedule(ctx: Ctx) -> None:
    tenant_count = ctx.tenant_count
    count = int(ctx.params.get("barrier_count", 128))
    distribution = str(ctx.params.get("barrier_distribution", "uniform"))
    if distribution == "uniform" and ctx.params.get("floor_to_tenants", False):
        count = max(count, tenant_count)
    tenant_counts = barrier_tenant_counts(
        count,
        tenant_count,
        distribution=distribution,
        zipf_exponent=float(ctx.params.get("barrier_zipf_exponent", 2.0)),
        explicit=ctx.params.get("commit_tenant_counts"),
    )
    max_workers = int(ctx.params.get("barrier_max_workers", 8))
    at_s = float(ctx.params.get("barrier_at_s", 0.0))
    waves = int(ctx.params.get("barrier_waves", 1))
    cooldown = float(ctx.params.get("barrier_cooldown_s", 0.0))
    for wave in range(waves):
        ctx.at_time(
            at_s + wave * cooldown,
            _barrier_job,
            tenant_counts={tenant: jobs for tenant, jobs in tenant_counts.items() if jobs > 0},
            max_workers=max_workers,
            name="barrier",
        )


tasks = {"read": task_read}
