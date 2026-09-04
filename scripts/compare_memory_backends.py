#!/usr/bin/env python3
"""Compare two dynamic replay runs (EchoMem vs OpenViking) and emit an HTML report.

The two runs must replay the SAME dynamic v2 dataset through the SAME agent
plugin (vikingbot), differing only in the memory backend, so every difference
is attributable to the memory system.

Inputs:
  --echomem-run   <dir>   result dir of the echomem replay run
  --openviking-run <dir>  result dir of the openviking replay run
  --dataset <dataset.json>  the shared dynamic v2 dataset (background_memories)
  --output <path>         output html (default reports/echomem_vs_openviking_<ts>/index.html)

Reads per run: dynamic_results.json (rounds + config.inject_elapsed_s),
run.log (inject timing fallback), and the shared dataset.json (memory id->text).

Outputs a self-contained HTML file with inline SVG charts (no external deps).
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------ #
#  Chart colors / helpers                                             #
# ------------------------------------------------------------------ #

EM_COLOR = "#2f6fed"
OV_COLOR = "#e8871e"
GRID = "#d8d8d8"
TEXT = "#222222"


def e(value: object) -> str:
    return html.escape(str(value))


def _svg_axes(w: float, h: float, pad_l: float, pad_b: float, pad_t: float,
              max_v: float, y_ticks: int = 4) -> str:
    """Return svg for the axes frame; returns (svg_str)."""
    top = pad_t
    left = pad_l
    plot_w = w - pad_l - 12
    plot_h = h - pad_b - pad_t
    parts = [f'<line x1="{left}" y1="{h - pad_b}" x2="{w - 12}" y2="{h - pad_b}" '
             f'stroke="{GRID}"/>',
             f'<line x1="{left}" y1="{top}" x2="{left}" y2="{h - pad_b}" '
             f'stroke="{GRID}"/>']
    for i in range(y_ticks + 1):
        frac = i / y_ticks
        y = h - pad_b - plot_h * frac
        val = max_v * frac
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{w - 12}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-dasharray="2 3"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="10" fill="{TEXT}">{val:g}</text>'
        )
    return "".join(parts), plot_w, plot_h, left, h - pad_b


def _bar_chart(labels: list[str], series: list[dict], w: int = 620,
               h: int = 280, unit: str = "") -> str:
    """series: list of {label, color, values: [..aligned to labels..]}."""
    pad_l, pad_b, pad_t = 64, 46, 26
    axes, plot_w, plot_h, left, bottom = _svg_axes(
        w, h, pad_l, pad_b, pad_t, _safe_max([v for s in series for v in s["values"]])
    )
    n = len(labels)
    group_w = plot_w / max(n, 1)
    bar_w = min(34, group_w / (len(series) + 0.6))
    parts = [axes]
    for i, label in enumerate(labels):
        cx = left + group_w * i + group_w / 2
        for si, s in enumerate(series):
            val = s["values"][i] if i < len(s["values"]) else 0
            bw = bar_w
            bx = cx - (len(series) * bar_w) / 2 + si * bar_w
            bh = plot_h * (val / _safe_max([v for ss in series for v in ss["values"]])) \
                if val else 0
            parts.append(
                f'<rect x="{bx:.1f}" y="{bottom - bh:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" fill="{s["color"]}" rx="2"/>'
            )
            if val:
                parts.append(
                    f'<text x="{bx + bw / 2:.1f}" y="{bottom - bh - 3:.1f}" '
                    f'text-anchor="middle" font-size="9" fill="{TEXT}">{val:g}</text>'
                )
        parts.append(
            f'<text x="{cx:.1f}" y="{h - 26}" text-anchor="middle" '
            f'font-size="10" fill="{TEXT}">{e(label)}</text>'
        )
    legend = "".join(
        f'<rect x="{i * 130 + 10}" y="8" width="10" height="10" fill="{s["color"]}"/>'
        f'<text x="{i * 130 + 24}" y="17" font-size="11" fill="{TEXT}">{e(s["label"])}</text>'
        for i, s in enumerate(series)
    )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'{legend}{"".join(parts)}</svg>'
    )


def _box_plot(groups: list[dict], w: int = 620, h: int = 240, unit: str = "") -> str:
    """groups: [{label, color, values:[numbers]}]. Hand-drawn SVG box plot."""
    pad_l, pad_b, pad_t = 64, 40, 20
    all_vals = [v for g in groups for v in g["values"] if v is not None]
    if not all_vals:
        return "<p>无数据</p>"
    max_v = _safe_max(all_vals)
    axes, plot_w, plot_h, left, bottom = _svg_axes(
        w, h, pad_l, pad_b, pad_t, max_v
    )
    n = len(groups)
    group_w = plot_w / max(n, 1)
    parts = [axes]
    for i, g in enumerate(groups):
        vals = sorted(v for v in g["values"] if v is not None)
        if not vals:
            continue
        cx = left + group_w * i + group_w / 2
        bw = min(56, group_w * 0.5)
        q1, med, q3 = _quantiles(vals)
        lo, hi = vals[0], vals[-1]
        y = lambda v: bottom - plot_h * (v / max_v)  # noqa: E731
        parts.append(
            f'<line x1="{cx:.1f}" y1="{y(lo):.1f}" x2="{cx:.1f}" y2="{y(hi):.1f}" '
            f'stroke="{g["color"]}"/>'
        )
        parts.append(
            f'<line x1="{cx - 8:.1f}" y1="{y(lo):.1f}" x2="{cx + 8:.1f}" '
            f'y2="{y(lo):.1f}" stroke="{g["color"]}"/>'
        )
        parts.append(
            f'<line x1="{cx - 8:.1f}" y1="{y(hi):.1f}" x2="{cx + 8:.1f}" '
            f'y2="{y(hi):.1f}" stroke="{g["color"]}"/>'
        )
        parts.append(
            f'<rect x="{cx - bw / 2:.1f}" y="{y(q3):.1f}" width="{bw:.1f}" '
            f'height="{max(1, y(q1) - y(q3)):.1f}" fill="{g["color"]}" opacity="0.35" '
            f'stroke="{g["color"]}"/>'
        )
        parts.append(
            f'<line x1="{cx - bw / 2:.1f}" y1="{y(med):.1f}" x2="{cx + bw / 2:.1f}" '
            f'y2="{y(med):.1f}" stroke="{g["color"]}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{h - 26}" text-anchor="middle" font-size="10" '
            f'fill="{TEXT}">{e(g["label"])}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{h - 12}" text-anchor="middle" font-size="9" '
            f'fill="{TEXT}">med={med:g} q1={q1:g} q3={q3:g}</text>'
        )
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(parts)}</svg>'


def _line_chart(labels: list[str], series: list[dict], w: int = 620,
                h: int = 260, unit: str = "") -> str:
    pad_l, pad_b, pad_t = 64, 44, 20
    all_vals = [v for s in series for v in s["values"] if v is not None]
    max_v = _safe_max(all_vals) if all_vals else 1
    axes, plot_w, plot_h, left, bottom = _svg_axes(
        w, h, pad_l, pad_b, pad_t, max_v
    )
    n = max(len(labels), 2)
    parts = [axes]
    step = plot_w / (n - 1)
    for s in series:
        pts = []
        for i, v in enumerate(s["values"]):
            if v is None:
                continue
            x = left + step * i
            y = bottom - plot_h * (v / max_v)
            pts.append(f"{x:.1f},{y:.1f}")
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{s["color"]}"/>'
            )
        if pts:
            parts.append(
                f'<polyline points="{" ".join(pts)}" fill="none" '
                f'stroke="{s["color"]}" stroke-width="1.6"/>'
            )
    for i, label in enumerate(labels):
        if i % max(1, n // 10):
            continue
        x = left + step * i
        parts.append(
            f'<text x="{x:.1f}" y="{h - 30}" text-anchor="middle" font-size="9" '
            f'fill="{TEXT}">{e(label)}</text>'
        )
    legend = "".join(
        f'<rect x="{i * 130 + 10}" y="6" width="10" height="10" fill="{s["color"]}"/>'
        f'<text x="{i * 130 + 24}" y="15" font-size="11" fill="{TEXT}">{e(s["label"])}</text>'
        for i, s in enumerate(series)
    )
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{legend}{"".join(parts)}</svg>'


def _radar_chart(labels: list[str], series: list[dict], w: int = 520,
                 h: int = 420) -> str:
    n = len(labels)
    cx, cy = w / 2, h / 2 + 10
    r = min(w, h) * 0.34
    parts = []
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(
            f"{cx + r * ring * math.cos(2 * math.pi * i / n):.1f},"
            f"{cy + r * ring * math.sin(2 * math.pi * i / n):.1f}"
            for i in range(n)
        )
        parts.append(f'<polygon points="{pts}" fill="none" stroke="{GRID}"/>')
    for i, label in enumerate(labels):
        ang = 2 * math.pi * i / n - math.pi / 2
        x, y = cx + r * 1.12 * math.cos(ang), cy + r * 1.12 * math.sin(ang)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" font-size="10" '
            f'fill="{TEXT}">{e(label)}</text>'
        )
    for s in series:
        pts = " ".join(
            f"{cx + r * (v / _safe_max(s['values'])) * math.cos(2 * math.pi * i / n - math.pi / 2):.1f},"
            f"{cy + r * (v / _safe_max(s['values'])) * math.sin(2 * math.pi * i / n - math.pi / 2):.1f}"
            for i, v in enumerate(s["values"])
        )
        parts.append(
            f'<polygon points="{pts}" fill="{s["color"]}" fill-opacity="0.22" '
            f'stroke="{s["color"]}" stroke-width="1.6"/>'
        )
    legend = "".join(
        f'<rect x="{i * 130 + 10}" y="8" width="10" height="10" fill="{s["color"]}"/>'
        f'<text x="{i * 130 + 24}" y="17" font-size="11" fill="{TEXT}">{e(s["label"])}</text>'
        for i, s in enumerate(series)
    )
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{legend}{"".join(parts)}</svg>'


def _safe_max(values: list[float]) -> float:
    vals = [v for v in values if v is not None and v > 0]
    return max(vals) if vals else 1.0


def _quantiles(vals: list[float]) -> tuple[float, float, float]:
    s = sorted(vals)
    n = len(s)
    return s[n // 4], s[n // 2], s[3 * n // 4]


def _stats(vals: list[float]) -> dict:
    s = sorted(v for v in vals if v is not None)
    if not s:
        return {"avg": None, "median": None, "p95": None, "total": None, "count": 0}
    avg = sum(s) / len(s)
    return {
        "avg": avg,
        "median": s[len(s) // 2],
        "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
        "total": sum(s),
        "count": len(s),
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


# ------------------------------------------------------------------ #
#  Recall precision                                                   #
# ------------------------------------------------------------------ #

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    """Tokenize into latin words + single CJK chars (then 2-gram them)."""
    base = _TOKEN_RE.findall(str(text).lower())
    grams: list[str] = list(base)
    for i in range(len(base) - 1):
        grams.append(f"{base[i]}|{base[i + 1]}")
    return grams


def _f1(a: str, b: str) -> float:
    ta, tb = Counter(_tokens(a)), Counter(_tokens(b))
    if not ta or not tb:
        return 0.0
    inter = sum((ta & tb).values())
    denom = sum(ta.values()) + sum(tb.values())
    return 2 * inter / denom if denom else 0.0


_RELEVANCE_THRESHOLD = 0.35


def recall_metrics(rounds: list[dict], bg: dict[str, str]) -> dict:
    """Compute per-round precision/recall/F1 vs ground memory texts."""
    rows = []
    for row in rounds:
        ground_ids = [str(g) for g in (row.get("ground_facts") or [])]
        ground_texts = [bg[g] for g in ground_ids if g in bg]
        recalled = _parse_memory_items(row.get("relevant_memory"))
        rec_texts = [
            str(it.get("content") or it.get("text") or "")
            for it in recalled
        ]
        if not ground_texts:
            continue
        if not rec_texts:
            rows.append({"round_id": row.get("round_id"), "precision": 0.0,
                         "recall": 0.0, "f1": 0.0, "k": 0, "n": len(ground_texts)})
            continue
        relevant = [
            any(_f1(rt, gt) >= _RELEVANCE_THRESHOLD for gt in ground_texts)
            for rt in rec_texts
        ]
        precision = sum(relevant) / len(relevant)
        matched = sum(
            1 for gt in ground_texts
            if any(_f1(rt, gt) >= _RELEVANCE_THRESHOLD for rt in rec_texts)
        )
        recall = matched / len(ground_texts)
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        rows.append({
            "round_id": row.get("round_id"),
            "precision": precision, "recall": recall, "f1": f1,
            "k": len(relevant), "n": len(ground_texts),
        })
    if not rows:
        return {"avg_precision": None, "avg_recall": None, "avg_f1": None,
                "rows": []}
    return {
        "avg_precision": sum(r["precision"] for r in rows) / len(rows),
        "avg_recall": sum(r["recall"] for r in rows) / len(rows),
        "avg_f1": sum(r["f1"] for r in rows) / len(rows),
        "rows": rows,
    }


def _parse_memory_items(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [it for it in raw if isinstance(it, dict)]
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return []
    return [it for it in parsed if isinstance(it, dict)] if isinstance(parsed, list) else []


# ------------------------------------------------------------------ #
#  Run loading                                                        #
# ------------------------------------------------------------------ #

class RunData:
    def __init__(self, name: str, color: str, run_dir: Path, dataset: dict):
        self.name = name
        self.color = color
        self.dir = run_dir
        results = json.loads(
            (run_dir / "dynamic_results.json").read_text(encoding="utf-8")
        )
        self.config = results.get("config") or {}
        rounds = results.get("rounds") or []
        # dynamic_results.json is written before the quality pass; the judge
        # output lives in quality_report.json, merge it in by round_id.
        quality_by_round: dict[str, dict] = {}
        qr_path = run_dir / "quality_report.json"
        if qr_path.exists():
            qr = json.loads(qr_path.read_text(encoding="utf-8"))
            for item in qr.get("results") or []:
                quality_by_round[str(item.get("round_id"))] = item
        for r in rounds:
            q = quality_by_round.get(str(r.get("round_id")))
            if q:
                r.setdefault("quality_score", q.get("quality_score"))
                r.setdefault("dimension_scores", q.get("dimension_scores"))
                r.setdefault("hallucination_detected", q.get("hallucination_detected"))
                r.setdefault("task_completed", q.get("task_completed"))
        self.rounds = rounds
        self.queries = [
            r for r in self.rounds
            if not r.get("is_injection") and r.get("reply")
        ]
        self.bg = {
            str(m.get("id") or ""): str(m.get("text") or "")
            for m in (dataset.get("background_memories") or [])
        }
        self.inject_elapsed_s = self._inject_time(results)
        self.tokens_prompt = [float(r.get("prompt_tokens") or 0) for r in self.queries]
        self.tokens_completion = [float(r.get("completion_tokens") or 0) for r in self.queries]
        self.tokens_total = [p + c for p, c in zip(self.tokens_prompt, self.tokens_completion)]
        self.retrieval = [float(r["retrieval_latency_s"]) for r in self.queries
                          if r.get("retrieval_latency_s") is not None]
        self.quality = [float(r["quality_score"]) for r in self.queries
                        if r.get("quality_score") is not None]
        self.halluc = [bool(r.get("hallucination_detected")) for r in self.queries
                       if r.get("quality_score") is not None]
        self.task_ok = [bool(r.get("task_completed")) for r in self.queries
                        if r.get("quality_score") is not None]
        self.dims: dict[str, list[float]] = {}
        for r in self.queries:
            for name, score in (r.get("dimension_scores") or {}).items():
                try:
                    self.dims.setdefault(name, []).append(float(score))
                except (TypeError, ValueError):
                    pass
        self.recall = recall_metrics(self.queries, self.bg)

    def _inject_time(self, results: dict) -> float | None:
        raw = (self.config or {}).get("inject_elapsed_s")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        log_path = self.dir / "run.log"
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.search(r"(\d+(?:\.\d+)?)s \(session=", line)
                if m and "inject" in line.lower():
                    return float(m.group(1))
                m2 = re.search(r"injected in (\d+(?:\.\d+)?)s", line)
                if m2:
                    return float(m2.group(1))
        return None

    @property
    def summary_row(self) -> dict:
        return {
            "prompt": _stats(self.tokens_prompt),
            "completion": _stats(self.tokens_completion),
            "total": _stats(self.tokens_total),
            "retrieval": _stats(self.retrieval),
            "quality": _stats(self.quality),
            "inject": self.inject_elapsed_s,
            "halluc_rate": (sum(self.halluc) / len(self.halluc)) if self.halluc else None,
            "task_rate": (sum(self.task_ok) / len(self.task_ok)) if self.task_ok else None,
            "queries": len(self.queries),
            "errors": sum(bool(r.get("error")) for r in self.queries),
            "recall": self.recall,
        }


# ------------------------------------------------------------------ #
#  HTML rendering                                                     #
# ------------------------------------------------------------------ #

def _pct(a: float | None, b: float | None) -> str:
    if a is None or b is None or b == 0:
        return "-"
    return f"{((a - b) / b) * 100:+.1f}%"


def build_html(em: RunData, ov: RunData, dataset: dict, output: Path) -> str:
    em_s, ov_s = em.summary_row, ov.summary_row
    dim_order = sorted(set(em.dims) | set(ov.dims))

    # --- overview table ---
    rows_html = []
    def row(label, emv, ovv, extra=""):
        rows_html.append(
            f"<tr><td>{label}</td><td>{emv}</td><td>{ovv}</td><td>{extra}</td></tr>"
        )
    row("查询数", em_s["queries"], ov_s["queries"])
    row("出错轮次", em_s["errors"], ov_s["errors"])
    row("prompt tokens (avg)", _fmt(em_s["prompt"]["avg"]), _fmt(ov_s["prompt"]["avg"]),
        _pct(em_s["prompt"]["avg"], ov_s["prompt"]["avg"]))
    row("completion tokens (avg)", _fmt(em_s["completion"]["avg"]), _fmt(ov_s["completion"]["avg"]),
        _pct(em_s["completion"]["avg"], ov_s["completion"]["avg"]))
    row("总 token (avg/轮)", _fmt(em_s["total"]["avg"]), _fmt(ov_s["total"]["avg"]),
        _pct(em_s["total"]["avg"], ov_s["total"]["avg"]))
    row("总 token (全 run)", _fmt(em_s["total"]["total"], 0), _fmt(ov_s["total"]["total"], 0),
        _pct(em_s["total"]["total"], ov_s["total"]["total"]))
    row("检索延迟 avg (s)", _fmt(em_s["retrieval"]["avg"], 3), _fmt(ov_s["retrieval"]["avg"], 3),
        _pct(em_s["retrieval"]["avg"], ov_s["retrieval"]["avg"]))
    row("检索延迟 median (s)", _fmt(em_s["retrieval"]["median"], 3), _fmt(ov_s["retrieval"]["median"], 3))
    row("检索延迟 p95 (s)", _fmt(em_s["retrieval"]["p95"], 3), _fmt(ov_s["retrieval"]["p95"], 3))
    row(f"注入耗时 (s, {em.config.get('inject_memory_count', '?')} 条)",
        _fmt(em_s["inject"], 2), _fmt(ov_s["inject"], 2),
        _pct(em_s["inject"], ov_s["inject"]))
    row("召回 precision@k", _fmt(em_s["recall"]["avg_precision"], 3), _fmt(ov_s["recall"]["avg_precision"], 3))
    row("召回 recall@k", _fmt(em_s["recall"]["avg_recall"], 3), _fmt(ov_s["recall"]["avg_recall"], 3))
    row("召回 F1", _fmt(em_s["recall"]["avg_f1"], 3), _fmt(ov_s["recall"]["avg_f1"], 3))
    row("答案质量总分 (avg/100)", _fmt(em_s["quality"]["avg"]), _fmt(ov_s["quality"]["avg"]),
        _pct(em_s["quality"]["avg"], ov_s["quality"]["avg"]))
    row("幻觉检出率", f"{em_s['halluc_rate'] * 100:.1f}%" if em_s["halluc_rate"] is not None else "-",
        f"{ov_s['halluc_rate'] * 100:.1f}%" if ov_s["halluc_rate"] is not None else "-")
    row("任务完成率", f"{em_s['task_rate'] * 100:.1f}%" if em_s["task_rate"] is not None else "-",
        f"{ov_s['task_rate'] * 100:.1f}%" if ov_s["task_rate"] is not None else "-")
    overview = f"""
    <table>
      <tr><th>指标</th><th>EchoMem</th><th>OpenViking</th><th>差异 (EM vs OV)</th></tr>
      {''.join(rows_html)}
    </table>"""

    # --- charts ---
    labels = ["prompt avg", "completion avg", "总token avg/轮", "总token 全run"]
    token_chart = _bar_chart(labels, [
        {"label": "EchoMem", "color": EM_COLOR, "values": [
            em_s["prompt"]["avg"] or 0, em_s["completion"]["avg"] or 0,
            em_s["total"]["avg"] or 0, em_s["total"]["total"] or 0]},
        {"label": "OpenViking", "color": OV_COLOR, "values": [
            ov_s["prompt"]["avg"] or 0, ov_s["completion"]["avg"] or 0,
            ov_s["total"]["avg"] or 0, ov_s["total"]["total"] or 0]},
    ])
    lat_chart = _bar_chart(["avg", "median", "p95"], [
        {"label": "EchoMem", "color": EM_COLOR, "values": [
            em_s["retrieval"]["avg"] or 0, em_s["retrieval"]["median"] or 0,
            em_s["retrieval"]["p95"] or 0]},
        {"label": "OpenViking", "color": OV_COLOR, "values": [
            ov_s["retrieval"]["avg"] or 0, ov_s["retrieval"]["median"] or 0,
            ov_s["retrieval"]["p95"] or 0]},
    ])
    inject_chart = _bar_chart(["注入耗时 (s)"], [
        {"label": "EchoMem", "color": EM_COLOR, "values": [em_s["inject"] or 0]},
        {"label": "OpenViking", "color": OV_COLOR, "values": [ov_s["inject"] or 0]},
    ], h=220)
    quality_chart = _bar_chart(["质量总分", "任务完成率×100", "幻觉率×100"], [
        {"label": "EchoMem", "color": EM_COLOR, "values": [
            em_s["quality"]["avg"] or 0,
            (em_s["task_rate"] or 0) * 100, (em_s["halluc_rate"] or 0) * 100]},
        {"label": "OpenViking", "color": OV_COLOR, "values": [
            ov_s["quality"]["avg"] or 0,
            (ov_s["task_rate"] or 0) * 100, (ov_s["halluc_rate"] or 0) * 100]},
    ])
    recall_chart = _bar_chart(["precision@k", "recall@k", "F1"], [
        {"label": "EchoMem", "color": EM_COLOR, "values": [
            em_s["recall"]["avg_precision"] or 0, em_s["recall"]["avg_recall"] or 0,
            em_s["recall"]["avg_f1"] or 0]},
        {"label": "OpenViking", "color": OV_COLOR, "values": [
            ov_s["recall"]["avg_precision"] or 0, ov_s["recall"]["avg_recall"] or 0,
            ov_s["recall"]["avg_f1"] or 0]},
    ])
    token_box = _box_plot([
        {"label": "EchoMem", "color": EM_COLOR, "values": em.tokens_total},
        {"label": "OpenViking", "color": OV_COLOR, "values": ov.tokens_total},
    ], unit="token")
    lat_box = _box_plot([
        {"label": "EchoMem", "color": EM_COLOR, "values": em.retrieval},
        {"label": "OpenViking", "color": OV_COLOR, "values": ov.retrieval},
    ], unit="s")
    rounds_id = [r.get("round_id") for r in em.queries]
    line_lat = _line_chart(rounds_id, [
        {"label": "EchoMem", "color": EM_COLOR, "values": [
            r.get("retrieval_latency_s") for r in em.queries]},
        {"label": "OpenViking", "color": OV_COLOR, "values": [
            r.get("retrieval_latency_s") for r in ov.queries]},
    ], unit="s")
    line_token = _line_chart(rounds_id, [
        {"label": "EchoMem", "color": EM_COLOR, "values": em.tokens_total},
        {"label": "OpenViking", "color": OV_COLOR, "values": ov.tokens_total},
    ], unit="token")
    dim_em = [sum(em.dims.get(d, [0])) / len(em.dims[d]) for d in dim_order] if dim_order else []
    dim_ov = [sum(ov.dims.get(d, [0])) / len(ov.dims[d]) for d in dim_order] if dim_order else []
    radar = _radar_chart(
        [d[:6] + ("…" if len(d) > 6 else "") for d in dim_order],
        [
            {"label": "EchoMem", "color": EM_COLOR, "values": dim_em},
            {"label": "OpenViking", "color": OV_COLOR, "values": dim_ov},
        ],
    ) if dim_order else "<p>无维度评分</p>"

    # --- detail tables ---
    def detail_table(data: RunData) -> str:
        trs = []
        for r in data.queries:
            recalled = _parse_memory_items(r.get("relevant_memory"))
            trs.append(
                f"<tr>"
                f"<td>{e(r.get('round_id'))}</td>"
                f"<td>{_fmt(r.get('prompt_tokens'), 0)}</td>"
                f"<td>{_fmt(r.get('completion_tokens'), 0)}</td>"
                f"<td>{_fmt(r.get('retrieval_latency_s'), 3)}</td>"
                f"<td>{_fmt(r.get('llm_latency_s'), 3)}</td>"
                f"<td>{_fmt(r.get('tool_call_count'), 0)}</td>"
                f"<td>{len(recalled)}</td>"
                f"<td>{_fmt(r.get('quality_score'))}</td>"
                f"<td>{'是' if r.get('hallucination_detected') else ''}</td>"
                f"<td title='{e(r.get('query'))}'>{(r.get('reply') or '')[:80]}</td>"
                f"</tr>"
            )
        return (
            f"<details><summary>{data.name} 逐轮明细 ({len(data.queries)} 轮)</summary>"
            f"<table><tr><th>轮</th><th>prompt</th><th>completion</th><th>检索(s)</th>"
            f"<th>LLM(s)</th><th>工具数</th><th>召回条数</th><th>质量分</th><th>幻觉</th>"
            f"<th>回复摘要</th></tr>{''.join(trs)}</table></details>"
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bg_count = len(em.bg)
    theme = dataset.get("theme", "")
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>EchoMem vs OpenViking 对比报告</title>
<style>
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px; color: {TEXT}; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 32px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  table {{ border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 10px; text-align: right; }}
  th {{ background: #f4f4f4; }} td:first-child, th:first-child {{ text-align: left; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 20px; }}
  .card {{ border: 1px solid #ddd; border-radius: 6px; padding: 10px; }}
  details {{ margin: 8px 0; }} summary {{ cursor: pointer; font-weight: 600; }}
  .meta {{ color: #666; font-size: 13px; }}
</style>
</head>
<body>
<h1>EchoMem vs OpenViking 记忆系统对比报告</h1>
<p class="meta">生成时间 {now} · 同 agent 隔离口径（vikingbot 插件，仅切换记忆后端）· 场景规模 {bg_count} 记忆 / {len(em.queries)} 查询<br>
主题: {e(theme)} · EchoMem run: <code>{e(em.dir.name)}</code> · OpenViking run: <code>{e(ov.dir.name)}</code></p>

<h2>1. 概览</h2>
{overview}

<h2>2. Token 消耗</h2>
<div class="grid"><div class="card">{token_chart}</div><div class="card">每轮总 token 分布（箱线图）{token_box}</div></div>
<div class="grid"><div class="card">逐轮总 token{line_token}</div></div>

<h2>3. 检索速度</h2>
<div class="grid"><div class="card">{lat_chart}</div><div class="card">每轮检索延迟分布（箱线图）{lat_box}</div></div>
<div class="grid"><div class="card">逐轮检索延迟{line_lat}</div></div>

<h2>4. 记忆注入速度</h2>
<div class="grid"><div class="card">{inject_chart}</div>
<div class="card"><p>口径：open_session → add_message×N → commit → poll 完成（含后端抽取/向量化），同一 harness 代码路径。</p></div></div>

<h2>5. 召回精度</h2>
<div class="grid"><div class="card">{recall_chart}</div>
<div class="card"><p>口径：每轮 ground_facts（背景记忆 id→text）与 vikingbot 实际召回内容做词元 F1（阈值 {_RELEVANCE_THRESHOLD}）判定相关。<br>
precision@k = 相关召回/召回总数；recall@k = 命中 ground fact 数/ground fact 总数。</p></div></div>

<h2>6. 答案质量（LLM Judge，满分 100）</h2>
<div class="grid"><div class="card">{quality_chart}</div><div class="card">{radar}</div></div>

<h2>7. 逐轮明细</h2>
{detail_table(em)}
{detail_table(ov)}

<h2>8. 方法论与局限</h2>
<ul>
<li><b>口径</b>：两边使用同一 vikingbot agent 管线、同一份 generate 产出的场景数据（背景记忆 + 查询 + ground_facts）、同一回答/评判 LLM；唯一变量是记忆后端（EchoMem 8010 vs OpenViking 19080）。</li>
<li><b>token</b>：vikingbot 每轮聚合工具循环内全部 LLM 调用的 prompt/completion tokens；差异主要来自召回内容长短与工具迭代次数。</li>
<li><b>检索延迟</b>：vikingbot 工具循环内所有 memory_client.search 的累计耗时。</li>
<li><b>召回精度</b>：词元 F1 判定对后端改写/截断敏感（content 若被后端截断可能漏判），结论偏保守。</li>
<li><b>不覆盖</b>：本口径不体现 EchoMem 生产管线（EchoAgent prefill → cached tokens → TTFT）的优势；vikingbot 非流式，无 TTFT/cached tokens 指标。</li>
<li>结果受回答 LLM、场景规模与生成随机性影响，建议固定模型复跑对比。</li>
</ul>
</body>
</html>"""


# ------------------------------------------------------------------ #
#  Main                                                               #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(description="EchoMem vs OpenViking 对比报告")
    parser.add_argument("--echomem-run", required=True, help="echomem replay 结果目录")
    parser.add_argument("--openviking-run", required=True, help="openviking replay 结果目录")
    parser.add_argument("--dataset", required=True, help="共享的动态 v2 dataset.json")
    parser.add_argument("--output", default="", help="输出 HTML 路径")
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    em = RunData("EchoMem", EM_COLOR, Path(args.echomem_run), dataset)
    ov = RunData("OpenViking", OV_COLOR, Path(args.openviking_run), dataset)

    output = Path(args.output) if args.output else (
        Path(__file__).resolve().parents[1] / "reports"
        / f"echomem_vs_openviking_{datetime.now().strftime('%Y%m%d_%H%M%S')}" / "index.html"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(em, ov, dataset, output), encoding="utf-8")
    print(f"[report] {output}")

    print("[overview]")
    print(f"  queries          EM={em.summary_row['queries']} OV={ov.summary_row['queries']}")
    print(f"  total_tokens     EM={em.summary_row['total']['total']:.0f} OV={ov.summary_row['total']['total']:.0f}")
    print(f"  retrieval avg(s) EM={em.summary_row['retrieval']['avg']:.3f} OV={ov.summary_row['retrieval']['avg']:.3f}")
    print(f"  inject(s)        EM={em.summary_row['inject']} OV={ov.summary_row['inject']}")
    print(f"  recall F1        EM={em.summary_row['recall']['avg_f1']:.3f} OV={ov.summary_row['recall']['avg_f1']:.3f}")
    print(f"  quality avg      EM={_fmt(em.summary_row['quality']['avg'])} OV={_fmt(ov.summary_row['quality']['avg'])}")


if __name__ == "__main__":
    main()
