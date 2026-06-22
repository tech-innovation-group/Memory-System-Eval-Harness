#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from echomem.utils.domain.atomic_memory import (
        AtomStatus,
        AtomType,
        AtomicMemory,
        CandidateAtom,
    )
    from echomem.workers.extractors.atomic.atom_merge_engine import AtomMergeEngine
except ModuleNotFoundError:
    AtomStatus = AtomType = AtomicMemory = CandidateAtom = AtomMergeEngine = None


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "write_governance_ablation_20260615.json"
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_write_governance_ablation_20260615.html")


def esc(value: Any) -> str:
    return html.escape(str(value))


def ts(text: str) -> str:
    if "T" in text:
        return text
    return f"{text}T00:00:00Z"


@dataclass
class Observation:
    subject: str
    predicate: str
    obj: str
    atom_type: str = "fact"
    state_kind: str = "state"
    event_time: str = ""
    mention_time: str = ""
    write_time: str = ""
    correction: bool = False
    note: str = ""


@dataclass
class StoredAtom:
    atom_id: str
    subject: str
    predicate: str
    obj: str
    atom_type: str
    state_kind: str
    event_time: str
    mention_time: str
    write_time: str
    valid_from: str = ""
    valid_until: str = ""
    status: str = "active"
    superseded_by: str = ""
    conflict_with: list[str] = field(default_factory=list)
    source_note: str = ""


@dataclass
class EvalCase:
    case_id: str
    title: str
    observations: list[Observation]
    query_kind: str
    subject: str
    predicate: str
    query_time: str
    expected: str
    why_it_matters: str


class AppendOnlyMemory:
    name = "append_only"

    def __init__(self) -> None:
        self.atoms: list[StoredAtom] = []
        self._counter = 0

    def ingest(self, obs: Observation) -> None:
        self._counter += 1
        self.atoms.append(
            StoredAtom(
                atom_id=f"ao-{self._counter:03d}",
                subject=obs.subject,
                predicate=obs.predicate,
                obj=obs.obj,
                atom_type=obs.atom_type,
                state_kind=obs.state_kind,
                event_time=obs.event_time,
                mention_time=obs.mention_time,
                write_time=obs.write_time,
                valid_from=obs.event_time or obs.write_time,
                source_note=obs.note,
            )
        )

    def answer(self, case: EvalCase) -> str:
        matches = [
            atom for atom in self.atoms
            if atom.subject == case.subject and atom.predicate == case.predicate
        ]
        if not matches:
            return "unknown"
        unique_values = {atom.obj for atom in matches}
        if len(unique_values) == 1:
            return next(iter(unique_values))
        return "ambiguous"


class WriteTimeLatestMemory:
    name = "write_time_latest"

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], StoredAtom] = {}
        self._counter = 0

    def ingest(self, obs: Observation) -> None:
        self._counter += 1
        atom = StoredAtom(
            atom_id=f"wl-{self._counter:03d}",
            subject=obs.subject,
            predicate=obs.predicate,
            obj=obs.obj,
            atom_type=obs.atom_type,
            state_kind=obs.state_kind,
            event_time=obs.event_time,
            mention_time=obs.mention_time,
            write_time=obs.write_time,
            valid_from=obs.event_time or obs.write_time,
            source_note=obs.note,
        )
        key = (obs.subject, obs.predicate)
        prev = self._by_key.get(key)
        if prev is None or atom.write_time >= prev.write_time:
            self._by_key[key] = atom

    def answer(self, case: EvalCase) -> str:
        atom = self._by_key.get((case.subject, case.predicate))
        return atom.obj if atom else "unknown"


class GovernedVersionedMemory:
    name = "governed_versioned"

    def __init__(self) -> None:
        self.atoms: list[StoredAtom] = []
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"gv-{self._counter:03d}"

    def ingest(self, obs: Observation) -> None:
        new_atom = StoredAtom(
            atom_id=self._next_id(),
            subject=obs.subject,
            predicate=obs.predicate,
            obj=obs.obj,
            atom_type=obs.atom_type,
            state_kind=obs.state_kind,
            event_time=obs.event_time,
            mention_time=obs.mention_time,
            write_time=obs.write_time,
            valid_from=obs.event_time or obs.write_time,
            source_note=obs.note,
        )
        key_atoms = [
            atom for atom in self.atoms
            if atom.subject == obs.subject and atom.predicate == obs.predicate
        ]

        if not key_atoms:
            self.atoms.append(new_atom)
            return

        # Exact duplicate / repeat mention: keep earliest atom, don't grow another version.
        for atom in key_atoms:
            if atom.obj == new_atom.obj and atom.event_time == new_atom.event_time:
                return

        # Unresolved same-time disagreement: preserve conflict instead of silently overwriting.
        same_time = [
            atom for atom in key_atoms
            if atom.event_time and new_atom.event_time and atom.event_time == new_atom.event_time
        ]
        if same_time and not obs.correction:
            new_atom.status = "conflicted"
            for atom in same_time:
                atom.status = "conflicted"
                atom.conflict_with.append(new_atom.atom_id)
                new_atom.conflict_with.append(atom.atom_id)
            self.atoms.append(new_atom)
            return

        active_states = [
            atom for atom in key_atoms
            if atom.state_kind in ("state", "preference") and atom.status == "active"
        ]
        latest_active = max(active_states, key=lambda a: (a.valid_from or "", a.write_time or ""), default=None)

        if obs.state_kind in ("state", "preference") and latest_active is not None:
            # Retrospective mention of an older state should stay historical and not replace the current state.
            if new_atom.event_time and latest_active.valid_from and new_atom.event_time < latest_active.valid_from and not obs.correction:
                new_atom.status = "historical"
                self.atoms.append(new_atom)
                return

            latest_active.status = "superseded"
            latest_active.valid_until = new_atom.event_time or new_atom.write_time
            latest_active.superseded_by = new_atom.atom_id
            self.atoms.append(new_atom)
            return

        self.atoms.append(new_atom)

    def answer(self, case: EvalCase) -> str:
        matches = [
            atom for atom in self.atoms
            if atom.subject == case.subject and atom.predicate == case.predicate
        ]
        if not matches:
            return "unknown"

        conflicted = [
            atom for atom in matches
            if atom.status == "conflicted" and atom.valid_from <= case.query_time
        ]
        if conflicted:
            return "unknown_conflict"

        active_as_of = []
        for atom in matches:
            start = atom.valid_from or atom.event_time or atom.write_time
            end = atom.valid_until or "9999-12-31T00:00:00Z"
            if start <= case.query_time < end and atom.status in ("active", "historical", "superseded"):
                active_as_of.append(atom)

        if not active_as_of:
            return "unknown"

        # Prefer current active state, then latest historical / superseded version
        # that was still valid at the query time.
        active = [atom for atom in active_as_of if atom.status == "active"]
        if active:
            best = max(active, key=lambda a: (a.valid_from or "", a.write_time or ""))
            return best.obj

        best = max(active_as_of, key=lambda a: (a.valid_from or "", a.write_time or ""))
        return best.obj


def build_cases() -> list[EvalCase]:
    return [
        EvalCase(
            case_id="wg1",
            title="Current state should follow the newer state version after a move",
            observations=[
                Observation("Ava", "lives_in", "Boston", event_time=ts("2025-01-10"), mention_time=ts("2025-01-10"), write_time=ts("2025-01-10"), note="initial city"),
                Observation("Ava", "lives_in", "Seattle", event_time=ts("2025-03-01"), mention_time=ts("2025-03-02"), write_time=ts("2025-03-02"), note="later move"),
            ],
            query_kind="state_as_of",
            subject="Ava",
            predicate="lives_in",
            query_time=ts("2025-03-10"),
            expected="Seattle",
            why_it_matters="Current-state questions should prefer the latest valid state, not all remembered states at once.",
        ),
        EvalCase(
            case_id="wg2",
            title="Retrospective mention of an older state should not overwrite the current state",
            observations=[
                Observation("Ava", "lives_in", "Seattle", event_time=ts("2025-03-01"), mention_time=ts("2025-03-02"), write_time=ts("2025-03-02"), note="current city"),
                Observation("Ava", "lives_in", "Boston", event_time=ts("2024-09-01"), mention_time=ts("2025-04-01"), write_time=ts("2025-04-01"), note="retrospective older city"),
            ],
            query_kind="state_as_of",
            subject="Ava",
            predicate="lives_in",
            query_time=ts("2025-04-02"),
            expected="Seattle",
            why_it_matters="A write-time-latest policy confuses old-story mentions with new-state updates.",
        ),
        EvalCase(
            case_id="wg3",
            title="Explicit correction should replace the mistaken value for the same fact",
            observations=[
                Observation("Project Atlas", "budget", "15k", event_time=ts("2025-02-10"), mention_time=ts("2025-02-10"), write_time=ts("2025-02-10"), note="initial budget"),
                Observation("Project Atlas", "budget", "18k", event_time=ts("2025-02-10"), mention_time=ts("2025-02-11"), write_time=ts("2025-02-11"), correction=True, note="explicit correction"),
            ],
            query_kind="state_as_of",
            subject="Project Atlas",
            predicate="budget",
            query_time=ts("2025-02-12"),
            expected="18k",
            why_it_matters="A governed memory should support correction without keeping the mistaken value as equally current.",
        ),
        EvalCase(
            case_id="wg4",
            title="Historical query should return the older preference instead of the newest write",
            observations=[
                Observation("Nora", "prefers", "tea", atom_type="preference", state_kind="preference", event_time=ts("2025-01-15"), mention_time=ts("2025-01-15"), write_time=ts("2025-01-15"), note="earlier preference"),
                Observation("Nora", "prefers", "coffee", atom_type="preference", state_kind="preference", event_time=ts("2025-05-01"), mention_time=ts("2025-05-01"), write_time=ts("2025-05-01"), note="later preference drift"),
            ],
            query_kind="state_as_of",
            subject="Nora",
            predicate="prefers",
            query_time=ts("2025-02-10"),
            expected="tea",
            why_it_matters="Write governance should support as-of questions, not only the latest-state snapshot.",
        ),
        EvalCase(
            case_id="wg5",
            title="Unresolved same-time disagreement should surface conflict rather than overwrite silently",
            observations=[
                Observation("Kai", "badge_number", "3142", event_time=ts("2025-03-01"), mention_time=ts("2025-03-01"), write_time=ts("2025-03-01"), note="handoff note"),
                Observation("Kai", "badge_number", "3147", event_time=ts("2025-03-01"), mention_time=ts("2025-03-02"), write_time=ts("2025-03-02"), note="spreadsheet value"),
            ],
            query_kind="state_as_of",
            subject="Kai",
            predicate="badge_number",
            query_time=ts("2025-03-03"),
            expected="unknown_conflict",
            why_it_matters="When two sources disagree about the same fact at the same time, a memory system should avoid confident silent overwrite.",
        ),
    ]


def run_variant(variant_cls: type[Any], cases: list[EvalCase]) -> dict[str, Any]:
    rows = []
    passed = 0
    for case in cases:
        mem = variant_cls()
        for obs in case.observations:
            mem.ingest(obs)
        answer = mem.answer(case)
        ok = answer == case.expected
        passed += int(ok)
        rows.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "expected": case.expected,
                "observed": answer,
                "passed": ok,
                "why_it_matters": case.why_it_matters,
            }
        )
    return {
        "variant": variant_cls.name,
        "passed": passed,
        "total": len(cases),
        "rows": rows,
    }


def run_maincode_merge_audit() -> dict[str, Any]:
    if AtomMergeEngine is None:
        return {
            "available": False,
            "reason": "echomem package not importable in the current Python environment",
            "audits": [],
        }
    engine = AtomMergeEngine(enable_llm_arbitration=False)

    def existing_atom(subject: str, predicate: str, obj: str, event_time: str, atom_type: AtomType = AtomType.FACT) -> AtomicMemory:
        return AtomicMemory(
            atom_id=f"exist-{subject}-{predicate}-{obj}".replace(" ", "_"),
            atom_type=atom_type,
            statement=f"{subject} {predicate} {obj} on {event_time[:10]}.",
            subject=subject,
            predicate=predicate,
            object=obj,
            created_at=event_time,
            mention_time=event_time,
            event_time=event_time,
            status=AtomStatus.ACTIVE,
        )

    def candidate(
        subject: str,
        predicate: str,
        obj: str,
        event_time: str,
        atom_type: AtomType = AtomType.FACT,
        qualifiers: dict[str, Any] | None = None,
        statement_prefix: str = "",
    ) -> CandidateAtom:
        return CandidateAtom(
            atom_type=atom_type,
            statement=f"{statement_prefix}{subject} {predicate} {obj} on {event_time[:10]}.",
            subject=subject,
            predicate=predicate,
            object=obj,
            event_time=event_time,
            mention_time=event_time,
            confidence=0.95,
            qualifiers=qualifiers or {},
        )

    audits = []
    scenarios = [
        {
            "case_id": "main1",
            "title": "Retrospective older state mention",
            "existing": existing_atom("Ava", "lives_in", "Seattle", ts("2025-03-01")),
            "candidate": candidate("Ava", "lives_in", "Boston", ts("2024-09-01")),
            "desired": "keep historical, do not replace current state",
        },
        {
            "case_id": "main2",
            "title": "Unresolved same-time disagreement",
            "existing": existing_atom("Kai", "badge_number", "3142", ts("2025-03-01")),
            "candidate": candidate("Kai", "badge_number", "3147", ts("2025-03-01")),
            "desired": "conflict or arbitration, not blind replace",
        },
        {
            "case_id": "main3",
            "title": "Explicit correction-like newer value",
            "existing": existing_atom("Project Atlas", "budget", "15k", ts("2025-02-10")),
            "candidate": candidate(
                "Project Atlas",
                "budget",
                "18k",
                ts("2025-02-10"),
                qualifiers={"correction": True},
                statement_prefix="Correction: ",
            ),
            "desired": "replace is acceptable here",
        },
    ]

    for spec in scenarios:
        report = engine.merge((spec["candidate"],), (spec["existing"],), source_uri="echo://audit")
        result = report.results[0]
        audits.append(
            {
                "case_id": spec["case_id"],
                "title": spec["title"],
                "decision": result.decision.value,
                "reason": result.reason,
                "desired": spec["desired"],
            }
        )
    return {"available": True, "reason": "", "audits": audits}


def build_html(variant_results: list[dict[str, Any]], merge_audit: dict[str, Any]) -> str:
    summary_cards = []
    for result in variant_results:
        summary_cards.append(
            f"""
            <div class="kpi">
              <div class="num">{esc(result["passed"])}/{esc(result["total"])}</div>
              <div class="muted">{esc(result["variant"])}</div>
              <div>{esc({
                  "append_only": "pure append, no lifecycle semantics",
                  "write_time_latest": "latest write silently wins",
                  "governed_versioned": "version chain + conflict-aware state handling",
              }[result["variant"]])}</div>
            </div>
            """
        )

    per_variant_sections = []
    for result in variant_results:
        rows = []
        for row in result["rows"]:
            badge = "通过" if row["passed"] else "失败"
            cls = "ok" if row["passed"] else "risk"
            rows.append(
                f"""
                <tr>
                  <td>{esc(row["case_id"])}</td>
                  <td>{esc(row["title"])}</td>
                  <td>{esc(row["expected"])}</td>
                  <td>{esc(row["observed"])}</td>
                  <td><span class="pill {cls}">{badge}</span></td>
                  <td>{esc(row["why_it_matters"])}</td>
                </tr>
                """
            )
        per_variant_sections.append(
            f"""
            <section class="panel">
              <h2>{esc(result["variant"])}: {esc(result["passed"])}/{esc(result["total"])}</h2>
              <table>
                <thead>
                  <tr>
                    <th>ID</th><th>Case</th><th>Expected</th><th>Observed</th><th>Status</th><th>Why it matters</th>
                  </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
              </table>
            </section>
            """
        )

    audit_rows = []
    for row in merge_audit["audits"]:
        cls = "warn" if row["decision"] == "replace" and "not blind replace" in row["desired"] else "ok"
        label = "需要加强" if cls == "warn" else "可接受"
        audit_rows.append(
            f"""
            <tr>
              <td>{esc(row["case_id"])}</td>
              <td>{esc(row["title"])}</td>
              <td>{esc(row["decision"])}</td>
              <td>{esc(row["reason"])}</td>
              <td>{esc(row["desired"])}</td>
              <td><span class="pill {cls}">{label}</span></td>
            </tr>
            """
        )

    if merge_audit.get("available"):
        audit_panel = f"""
    <section class="panel">
      <h2>Main-Code Audit: 当前 AtomMergeEngine 哪些地方已经好，哪些地方还偏激进</h2>
      <p class="muted">
        下面不是 toy merge，而是直接调用当前主仓的 <code>AtomMergeEngine</code> 做的小审计。它已经有五分支 merge 骨架，但对“回溯旧状态”和“同时间冲突”仍偏容易走到 <code>REPLACE</code>。
      </p>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Case</th><th>Current decision</th><th>Reason</th><th>Desired behavior</th><th>Status</th>
          </tr>
        </thead>
        <tbody>{''.join(audit_rows)}</tbody>
      </table>
    </section>
"""
    else:
        audit_panel = f"""
    <section class="panel">
      <h2>Main-Code Audit</h2>
      <p class="muted">
        本次输出已完整跑完 standalone write-governance 实验；主仓 <code>AtomMergeEngine</code> 审计在当前 Python 环境中未执行，
        因为 {esc(merge_audit.get("reason", "main-code import unavailable"))}。
      </p>
      <p>
        这不影响上面的通用策略对比结果，但意味着“主仓当前 merge 行为”和“nano versioned policy”的对照，需要在能导入
        <code>echomem</code> 包的环境里再补跑一轮。
      </p>
    </section>
"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Write Governance Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fb; --panel:#fff; --line:#d9e3ef; --text:#172435; --muted:#5f6f84;
      --blue:#2563eb; --blue-soft:#eef4ff; --green:#0f8a5f; --green-soft:#eaf8f1;
      --amber:#b26a00; --amber-soft:#fff4df; --red:#c43d3d; --red-soft:#fff2f2; --shadow:0 14px 34px rgba(15,23,42,.08);
    }}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1240px;margin:0 auto;padding:26px 18px 72px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}}
    .hero{{padding:30px 32px}} .panel{{padding:20px 22px;margin-top:16px}}
    .tag,.pill{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;margin-right:6px;margin-bottom:6px}}
    .tag{{background:var(--blue-soft);color:var(--blue)}} .ok{{background:var(--green-soft);color:var(--green)}} .warn{{background:var(--amber-soft);color:var(--amber)}} .risk{{background:var(--red-soft);color:var(--red)}}
    .kpis{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px}} .kpi{{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fbfcff}}
    .num{{font-size:24px;font-weight:700;line-height:1.1}} h1,h2{{margin:0}} h1{{font-size:34px;line-height:1.15;margin-top:8px}} h2{{font-size:21px;margin-bottom:12px}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}} .quote{{border-left:4px solid #b8ccff;background:#f8fbff;padding:12px 14px;border-radius:10px;margin-top:14px}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{background:#f7faff;color:#42556e;font-size:12px;text-transform:uppercase}} tr:last-child td{{border-bottom:none}}
    code{{background:#f3f6fb;border:1px solid #e4ebf5;border-radius:6px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="tag">write governance</div>
      <div class="tag">generic nano</div>
      <div class="tag">version chain</div>
      <div class="tag">main-code audit</div>
      <h1>EchoMemory Write Governance Ablation</h1>
      <p class="muted">
        这组实验专门补 EchoMemory-MM 研究包里最缺的一条证据线：<strong>记忆写进去之后，系统如何决定 add / update / replace / conflict / historical keep</strong>。
        这里没有用任何数据集关键词，也没有绑定特定 benchmark 实体，只比较三种通用策略：
        <code>append_only</code>、<code>write_time_latest</code>、<code>governed_versioned</code>。
      </p>
      <div class="kpis">{''.join(summary_cards)}</div>
      <div class="quote">
        <strong>一句话结论：</strong>
        纯追加会把当前态和旧态混成一团；只按写入时间覆盖会把“后来提到的旧事”误当成新状态；带 version chain 和 conflict 语义的写入治理，才能同时守住 current-state、as-of、correction 和 unresolved conflict 这几类问题。
      </div>
    </section>

    <section class="panel">
      <h2>Why this matters</h2>
      <p>
        这条线直接对应最近两年文献里对 EchoMemory 最有压力的几个方向：
        <code>Mem0</code> 关心 write path，
        <code>From RAG to Memory</code> 强调记忆是持续演化而不是静态索引，
        <code>ConvMemory</code> 指向 learned conflict editor，
        而前面的 temporal / graph / contract 线如果没有写入治理，后面检索再聪明也只能在脏记忆里打转。
      </p>
    </section>

    {''.join(per_variant_sections)}

    {audit_panel}

    <section class="panel">
      <h2>Next code changes implied by this ablation</h2>
      <ul>
        <li>在 <code>atom_merge_engine.py</code> 里把 <strong>retrospective older-state mention</strong> 从盲目 replace 拆出来，允许落成 historical version。</li>
        <li>同一 <code>subject/predicate</code> 且同一 <code>event_time</code> 的不同 object，不该直接 replace，应该进 <code>conflict</code> 或仲裁路径。</li>
        <li>把 <code>valid_from / valid_until / superseded_by / conflict_with</code> 这些语义更稳定地下沉到 graph 与 organized 投影里。</li>
        <li>后续可以再接一层 learned conflict editor，但第一步先把规则层 version chain 做实。</li>
      </ul>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    cases = build_cases()
    variant_results = [
        run_variant(AppendOnlyMemory, cases),
        run_variant(WriteTimeLatestMemory, cases),
        run_variant(GovernedVersionedMemory, cases),
    ]
    merge_audit = run_maincode_merge_audit()
    payload = {
        "variants": variant_results,
        "main_code_audit": merge_audit,
        "cases": [
            {
                "case_id": case.case_id,
                "title": case.title,
                "expected": case.expected,
                "query_time": case.query_time,
                "why_it_matters": case.why_it_matters,
            }
            for case in cases
        ],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(build_html(variant_results, merge_audit), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
