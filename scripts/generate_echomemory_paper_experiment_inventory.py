#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
OUT_JSON = ROOT / "web" / "static" / "generated-reports" / "echomemory_paper_experiment_inventory_20260615.json"
OUT_HTML = ROOT / "web" / "static" / "generated-reports" / "echomemory_paper_experiment_inventory_20260615.html"

ECHO_V006 = Path("/Users/chx/Code/echomemory/echo_memory_v006")


@dataclass
class InventoryItem:
    name: str
    level: str
    status: str
    role: str
    key_result: str
    can_be_main_table: str
    what_it_proves: str
    gap: str
    evidence_paths: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "status": self.status,
            "role": self.role,
            "key_result": self.key_result,
            "can_be_main_table": self.can_be_main_table,
            "what_it_proves": self.what_it_proves,
            "gap": self.gap,
            "evidence_paths": self.evidence_paths,
        }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def exists(path: str) -> bool:
    return Path(path).exists()


def collect_inventory() -> list[InventoryItem]:
    items: list[InventoryItem] = []

    nano = load_json(ROOT / "web" / "static" / "generated-reports" / "echomemory_research_evidence_board_20260615.json")
    items.append(
        InventoryItem(
            name="Nano mechanism suite",
            level="nano",
            status="complete",
            role="core method evidence",
            key_result="dual 8/12, readiness 5/5, self-check 8/8, type-aware second pass 5/5",
            can_be_main_table="yes, for mechanism table",
            what_it_proves="Supports dual-backbone, readiness, contract-aware retrieval, and self-check claims at mechanism level.",
            gap="Not benchmark-scale; does not estimate end-task leaderboard performance.",
            evidence_paths=[
                str(ROOT / "web" / "static" / "generated-reports" / "echomemory_research_evidence_board_20260615.html"),
                str(ROOT / "experiments" / "echomemory_nano" / "nano_dual_backbone_benchmark_results.json"),
            ],
        )
    )

    real_subset = load_json(ECHO_V006 / "experiments" / "realcode_selfcheck_subset_benchmark_results.json")
    summary = real_subset["summary"]
    items.append(
        InventoryItem(
            name="Real-code family subset",
            level="real-code subset",
            status="complete",
            role="bridge from nano to main code",
            key_result=f"{summary['num_passed']}/{summary['num_cases']} structural passes, {summary['num_review_ok']}/{summary['num_cases']} review-ok",
            can_be_main_table="yes, as real-code subset table",
            what_it_proves="Shows family-aware routing and self-check behavior on the actual SearchService path rather than only in toy code.",
            gap="Still a designed subset, not a benchmark-scale score.",
            evidence_paths=[
                str(ECHO_V006 / "experiments" / "realcode_selfcheck_subset_benchmark_results.json"),
                str(ROOT / "web" / "static" / "generated-reports" / "echomemory_realcode_subset_benchmark_20260614.html"),
            ],
        )
    )

    multimodal_smoke = load_json(ROOT / "experiments" / "echomemory_mm_real_smoke_20260613" / "mm_real_smoke_result.json")
    items.append(
        InventoryItem(
            name="Multimodal real smoke",
            level="real-code smoke",
            status="complete",
            role="multimodal feasibility evidence",
            key_result=f"image_node_found={multimodal_smoke['image_node_found']}, top1_memory_type={multimodal_smoke['top1_memory_type']}, ocr_visible={multimodal_smoke['ocr_visible_in_top1']}",
            can_be_main_table="appendix only",
            what_it_proves="Demonstrates that image-grounded evidence can enter the graph and be retrieved as primary visual evidence.",
            gap="Too small for a multimodal benchmark claim.",
            evidence_paths=[
                str(ROOT / "experiments" / "echomemory_mm_real_smoke_20260613" / "mm_real_smoke_result.json"),
                str(ROOT / "experiments" / "echomemory_mm_real_smoke_20260613" / "mm_real_smoke_report.html"),
            ],
        )
    )

    planner_gap = load_json(ROOT / "experiments" / "echomemory_search_planner_gap_probe_20260613" / "planner_gap_probe.json")
    items.append(
        InventoryItem(
            name="Planner gap probe",
            level="diagnostic",
            status="complete",
            role="architecture diagnosis",
            key_result=f"aligned_cases={planner_gap['summary']['aligned_cases']}/{planner_gap['summary']['total_cases']}, gap_cases={planner_gap['summary']['gap_cases']}",
            can_be_main_table="no",
            what_it_proves="Helps explain where mixed retrieval still diverges from explicit family-aware planning.",
            gap="Diagnostic only; not an evaluation headline.",
            evidence_paths=[
                str(ROOT / "experiments" / "echomemory_search_planner_gap_probe_20260613" / "planner_gap_probe.json"),
                str(ROOT / "experiments" / "echomemory_search_planner_gap_probe_20260613" / "planner_gap_probe.html"),
            ],
        )
    )

    locomo_cfg = ROOT / "configs" / "echomemory_mm_locomo_conv30_formal_subset20_20260614.json"
    locomo_run = ROOT / "runs" / "echomemory_mm_conv30_subset20_20260614"
    expected = [
        locomo_run / "subset20_manifest.json",
        locomo_run / "subset20_import.log",
        locomo_run / "echomemory_import" / "echomemory_model_preflight.json",
        locomo_run / "echomemory_import" / "echomem.runtime.yaml",
    ]
    missing_qa = not (locomo_run / "echomemory_qa").exists()
    items.append(
        InventoryItem(
            name="LoCoMo conv-30 formal subset-20",
            level="benchmark subset",
            status="partial",
            role="paper-facing benchmark development subset",
            key_result=f"protocol frozen; {sum(1 for p in expected if p.exists())}/{len(expected)} import-stage artifacts present; QA/judge outputs missing",
            can_be_main_table="not yet",
            what_it_proves="Protocol and subset definition are stable enough for repeated development runs.",
            gap="No final QA results, no judge summary, no final score table yet.",
            evidence_paths=[
                str(locomo_cfg),
                str(ROOT / "docs" / "echomemory_mm_locomo_conv30_formal_subset20_20260614.md"),
                str(ROOT / "web" / "static" / "generated-reports" / "echomemory_mm_benchmark_evidence_status_20260614.html"),
            ],
        )
    )

    longmem_ref_path = ROOT / "runs" / "formal_longmemeval_s_full_openviking_20260606_1530" / "longmemeval_official_summary.json"
    longmem_ref = load_json(longmem_ref_path)
    items.append(
        InventoryItem(
            name="LongMemEval reference baseline",
            level="reference baseline",
            status="complete",
            role="external / non-main comparison line",
            key_result=f"OpenViking official-style summary: {longmem_ref['correct']}/{longmem_ref['graded']} = {longmem_ref['overall_accuracy']:.3f}",
            can_be_main_table="reference only",
            what_it_proves="Provides a completed reference evaluation line for comparison and protocol anchoring.",
            gap="Not an EchoMemory-MM result; cannot be reported as the method’s main score.",
            evidence_paths=[str(longmem_ref_path)],
        )
    )

    return items


def render_html(items: list[InventoryItem]) -> str:
    rows = []
    for item in items:
        links = "<br />".join(f"<code>{p}</code>" for p in item.evidence_paths)
        rows.append(
            f"""
            <tr>
              <td><b>{item.name}</b><br /><span class="muted">{item.level}</span></td>
              <td>{item.status}</td>
              <td>{item.role}</td>
              <td>{item.key_result}</td>
              <td>{item.can_be_main_table}</td>
              <td>{item.what_it_proves}</td>
              <td>{item.gap}</td>
              <td>{links}</td>
            </tr>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Paper Experiment Inventory</title>
  <style>
    :root {{
      --bg:#f5f7fb; --panel:#fff; --text:#172033; --muted:#607089; --line:#dbe3ef;
      --blue:#2563eb; --green:#0f9f6e; --amber:#b26a00; --red:#b42318; --shadow:0 12px 28px rgba(15,23,42,.08);
      --soft-blue:#eef4ff; --soft-green:#ecfdf5; --soft-amber:#fff7ed; --soft-red:#fff1f2;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1320px;margin:0 auto;padding:26px 20px 54px}}
    .hero,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:28px 30px;margin-bottom:18px}}
    .card{{padding:20px 22px;margin-bottom:16px}}
    h1,h2{{margin:0 0 12px;line-height:1.25}}
    h1{{font-size:31px}} h2{{font-size:21px}}
    p{{margin:0 0 10px}}
    .pill{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;margin-right:8px;margin-bottom:8px}}
    .blue{{background:var(--soft-blue);color:var(--blue)}} .green{{background:var(--soft-green);color:var(--green)}} .amber{{background:var(--soft-amber);color:var(--amber)}}
    .muted{{color:var(--muted)}}
    .note{{border-left:4px solid var(--blue);background:#f7fbff;padding:12px 14px;border-radius:8px;margin-top:10px}}
    table{{width:100%;border-collapse:collapse;font-size:13px}}
    th,td{{text-align:left;vertical-align:top;padding:10px;border-top:1px solid var(--line)}}
    th{{background:#fbfcff;color:var(--muted);font-size:12px;text-transform:uppercase}}
    code{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f3f6fb;border:1px solid #e4ebf5;border-radius:6px;padding:1px 5px;font-size:12px;word-break:break-all}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="pill blue">paper-facing inventory</div>
      <div class="pill green">what is complete</div>
      <div class="pill amber">what is still partial</div>
      <h1>EchoMemory Paper Experiment Inventory</h1>
      <p class="muted">
        这页回答的是：当前有哪些实验线，分别处于什么状态，哪些能写进主稿，哪些只能当 appendix / reference / run-plan。
      </p>
      <div class="note">
        核心原则：<b>机制级证据、real-code 子集证据、benchmark 子集协议、reference baseline</b> 必须分开，不混成一个大而糊的“实验结果”。
      </div>
    </section>

    <section class="card">
      <h2>当前最诚实的实验分层</h2>
      <ul>
        <li><b>已经完整：</b> nano mechanism suite、real-code subset、multimodal smoke、reference baseline</li>
        <li><b>已经冻结但未跑完：</b> LoCoMo conv-30 formal subset-20</li>
        <li><b>主稿最适合写：</b> nano mechanism table + real-code subset table</li>
        <li><b>当前不该误写成主结果：</b> OpenViking LongMemEval summary、只到 import 阶段的 subset-20</li>
      </ul>
    </section>

    <section class="card">
      <h2>Inventory Table</h2>
      <table>
        <thead>
          <tr>
            <th>Experiment Line</th>
            <th>Status</th>
            <th>Role</th>
            <th>Key Result</th>
            <th>Main Table?</th>
            <th>What It Proves</th>
            <th>Gap</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    items = collect_inventory()
    payload = {
        "generated_at": "2026-06-15",
        "items": [item.as_dict() for item in items],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(items), encoding="utf-8")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
