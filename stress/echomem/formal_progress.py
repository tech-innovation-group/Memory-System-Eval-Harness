from __future__ import annotations

import re


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
        r"FORMAL_HEARTBEAT\s+.*?\bcompleted=(\d+)\s+total=(\d+)\b",
        line,
    )
    if not heartbeat:
        return None
    return int(heartbeat.group(1)), int(heartbeat.group(2))
