from __future__ import annotations

import re


SCENARIO_ORDER = ("baseline", "mixed", "commit-storm", "search-storm", "soak")


def progress_position(
    line: str,
    *,
    total: int,
) -> tuple[int, int] | None:
    """Map formal-suite progress lines to a monotonic completed-case count."""
    progress = re.search(r"FORMAL_PROGRESS\s+(\d+)/(\d+)", line)
    if progress:
        return int(progress.group(1)), int(progress.group(2))
    heartbeat = re.search(
        r"FORMAL_HEARTBEAT\s+scenario=([^\s]+)\s+repeat=(\d+)",
        line,
    )
    if not heartbeat or total <= 0:
        return None
    try:
        scenario_index = SCENARIO_ORDER.index(heartbeat.group(1))
    except ValueError:
        return None
    repeats = max(1, total // len(SCENARIO_ORDER))
    position = scenario_index * repeats + int(heartbeat.group(2)) - 1
    return max(0, position), total
