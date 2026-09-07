"""Request-level measurement records.

Every request and poll produces exactly one record; nothing is swallowed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def content_hash(content: str) -> str:
    """Deterministic hash of one injected message (reconciliation key)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class RequestRecord:
    scene: str
    worker_id: int
    tenant_idx: int
    op: str
    stage_ms: float
    status: str  # ok | error
    error_type: str  # "" | timeout | http_4xx | http_5xx | connection | other | ...
    ts_ms: float
    http_status: int | None = None
    session_id: str = ""
    archive_id: str = ""
    accepted_at_ms: float | None = None
    completed_at_ms: float | None = None
    extra: str = ""  # e.g. "burst"
    # -- write retry instrumentation (commit_submit) ---------------------
    retry_count: int = 0
    retried: bool = False
    retry_total_wait_ms: float = 0.0
    final_success: bool = False
    retry_after_s: float | None = None  # 429 Retry-After of the last failed attempt
    reason_code: str = ""  # server-side rejection reason (header/payload aliases)
    # -- message-level reconciliation (add) ------------------------------
    message_id: str = ""
    content_hash: str = ""
    content_bytes: int = 0
    # -- search quality assertion (read) ----------------------------------
    query: str = ""
    hit_count: int = 0
    real_recall: bool = False
    quality_ok: bool = True
    degraded: bool = False
    query_type: str = "unclassified"
    expected_marker: str = ""
    marker_found: bool = False
    degraded_reasons: str = ""
    # -- worker-level failure context --------------------------------------
    detail: str = ""

    def to_csv_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for name in CSV_FIELDS:
            value = getattr(self, name)
            if name in ("stage_ms", "retry_total_wait_ms"):
                value = round(value, 3)
            row[name] = value
        return row


CSV_FIELDS: list[str] = [
    "scene",
    "worker_id",
    "tenant_idx",
    "op",
    "stage_ms",
    "status",
    "error_type",
    "http_status",
    "ts_ms",
    "session_id",
    "archive_id",
    "accepted_at_ms",
    "completed_at_ms",
    "extra",
    "retry_count",
    "retried",
    "retry_total_wait_ms",
    "final_success",
    "retry_after_s",
    "reason_code",
    "message_id",
    "content_hash",
    "content_bytes",
    "hit_count",
    "real_recall",
    "quality_ok",
    "degraded",
    "query",
    "query_type",
    "expected_marker",
    "marker_found",
    "degraded_reasons",
    "detail",
]
