#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echomem.index_engine.atom.retriever import AtomMemoryRetriever
from echomem.index_engine.search_service import SearchService
from echomem.index_engine.temporal.query_resolver import TemporalQueryResolver
from echomem.utils.domain.atomic_memory import AtomicMemory, AtomStatus, AtomType
from echomem.utils.domain.context import RequestContext
from echomem.workers.organized_projector.projector import OrganizedProjector


OUT_DIR = Path("/Users/chx/locomo-eval-web/web/static/generated-reports")
OUT_HTML = OUT_DIR / "echomemory_maincode_temporal_regression_20260615.html"
OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/maincode_temporal_regression_20260615.json")


def esc(value: Any) -> str:
    return html.escape(str(value))


@dataclass
class CaseResult:
    case_id: str
    title: str
    expectation: str
    observed: str
    passed: bool
    notes: str = ""


class FakeFS:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    async def read_text(self, uri: str) -> str:
        return self.mapping.get(uri, "")

    async def tree(self, prefix: str, max_depth: int = 4) -> dict[str, Any]:
        return {}


class FakeAtomStorage:
    def __init__(self, atoms: list[AtomicMemory]) -> None:
        self._atoms = {atom.atom_id: atom for atom in atoms}

    async def find_by_subject(self, subject: str, ctx: RequestContext) -> set[str]:
        return {a.atom_id for a in self._atoms.values() if a.subject == subject}

    async def find_by_keyword(self, kw: str, ctx: RequestContext) -> set[str]:
        lowered = kw.lower()
        return {
            a.atom_id
            for a in self._atoms.values()
            if lowered in a.statement.lower()
        }

    async def list_active_atoms(self, ctx: RequestContext) -> list[AtomicMemory]:
        return [a for a in self._atoms.values() if a.status == AtomStatus.ACTIVE]

    async def read_atoms(self, atom_ids: list[str], ctx: RequestContext) -> list[AtomicMemory]:
        return [self._atoms[atom_id] for atom_id in atom_ids if atom_id in self._atoms]


async def case_query_time_anchor_autofill() -> CaseResult:
    fs = FakeFS(
        {
            "echo://acc/sessions/s1/messages.jsonl": "\n".join(
                [
                    json.dumps(
                        {
                            "message_id": "m1",
                            "created_at": "2026-06-10T09:00:00Z",
                            "content": "earlier message",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "message_id": "m2",
                            "created_at": "2026-06-12T11:30:00Z",
                            "content": "later message",
                        },
                        ensure_ascii=False,
                    ),
                ]
            ),
            "echo://acc/sessions/s1/meta.json": json.dumps(
                {"created_at": "2026-06-01T00:00:00Z"}, ensure_ascii=False
            ),
        }
    )
    svc = SearchService(fs)
    ctx = RequestContext(account_id="acc", user_id="user", session_id="s1")
    out = await svc._with_query_time_anchor(ctx)
    observed = out.query_time_anchor
    expected = "2026-06-12T11:30:00Z"
    return CaseResult(
        case_id="case1",
        title="SearchService auto-fills query_time_anchor from current session",
        expectation=expected,
        observed=observed,
        passed=observed == expected,
        notes="Should use the latest session message timestamp instead of runtime wall clock.",
    )


def case_relative_yesterday_resolution() -> CaseResult:
    ctx = RequestContext(
        account_id="acc",
        user_id="user",
        query_time_anchor="2025-05-03T10:00:00Z",
    )
    resolved = TemporalQueryResolver.resolve_relative_range("What happened yesterday?", ctx)
    observed = f"{resolved.start_iso} -> {resolved.end_iso}" if resolved else "None"
    expected = "2025-05-02T00:00:00+00:00 -> 2025-05-03T00:00:00+00:00"
    return CaseResult(
        case_id="case2",
        title="Relative-day resolution uses query-time anchor",
        expectation=expected,
        observed=observed,
        passed=observed == expected,
        notes="This is the query-side half of three-clock: relative time should resolve against dialogue-world anchor.",
    )


async def case_atom_retriever_prefers_story_time_then_mention_time() -> CaseResult:
    atoms = [
        AtomicMemory(
            atom_id="atom-old-event",
            atom_type=AtomType.EVENT,
            statement="Maya joined Acme on 2024-03-01.",
            subject="Maya",
            predicate="joined",
            object="Acme",
            created_at="2025-05-02T09:05:00Z",
            mention_time="2025-05-02T09:00:00Z",
            event_time="2024-03-01",
            status=AtomStatus.ACTIVE,
        ),
        AtomicMemory(
            atom_id="atom-yesterday-mention",
            atom_type=AtomType.EVENT,
            statement="Maya discussed the launch timeline yesterday.",
            subject="Maya",
            predicate="discussed",
            object="launch timeline",
            created_at="2025-05-02T20:05:00Z",
            mention_time="2025-05-02T20:00:00Z",
            event_time="",
            status=AtomStatus.ACTIVE,
        ),
    ]
    storage = FakeAtomStorage(atoms)
    retriever = AtomMemoryRetriever(storage, max_results=10)
    ctx = RequestContext(
        account_id="acc",
        user_id="user",
        query_time_anchor="2025-05-03T09:00:00Z",
    )
    items = await retriever.retrieve("What happened yesterday?", ctx)
    observed_ids = [item.trace.get("atom_id", "") for item in items]
    expected_ids = ["atom-yesterday-mention"]
    return CaseResult(
        case_id="case3",
        title="Atom retrieval excludes old story-time events from yesterday queries",
        expectation=str(expected_ids),
        observed=str(observed_ids),
        passed=observed_ids == expected_ids,
        notes="The 2024 event was mentioned yesterday, but it did not happen yesterday; retrieval should not confuse story_time with mention_time for a pure event-time query.",
    )


def case_projector_fallback_uses_mention_time_before_created_at() -> CaseResult:
    observed = OrganizedProjector._date_fields_from_event_time(
        "",
        "2025-05-02T20:00:00Z",
        "2025-05-10T08:00:00Z",
    )
    expected_date = "2025-05-02"
    return CaseResult(
        case_id="case4",
        title="Organized projector falls back to mention_time before created_at",
        expectation=expected_date,
        observed=observed["date"],
        passed=observed["date"] == expected_date,
        notes="When story_time is missing, the event projection should preserve conversational mention time before system write time.",
    )


async def main() -> None:
    results = [
        await case_query_time_anchor_autofill(),
        case_relative_yesterday_resolution(),
        await case_atom_retriever_prefers_story_time_then_mention_time(),
        case_projector_fallback_uses_mention_time_before_created_at(),
    ]
    passed = sum(1 for r in results if r.passed)
    payload = {
        "summary": {
            "passed": passed,
            "total": len(results),
        },
        "cases": [r.__dict__ for r in results],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for r in results:
        badge = "通过" if r.passed else "失败"
        badge_cls = "ok" if r.passed else "risk"
        rows.append(
            f"""
            <tr>
              <td>{esc(r.case_id)}</td>
              <td>{esc(r.title)}</td>
              <td>{esc(r.expectation)}</td>
              <td>{esc(r.observed)}</td>
              <td><span class="pill {badge_cls}">{badge}</span></td>
              <td>{esc(r.notes)}</td>
            </tr>
            """
        )

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Main-Code Temporal Regression</title>
  <style>
    :root {{
      --bg:#f6f8fb; --panel:#fff; --line:#d9e3ef; --text:#172435; --muted:#5f6f84;
      --blue:#2563eb; --blue-soft:#eef4ff; --green:#0f8a5f; --green-soft:#eaf8f1;
      --amber:#b26a00; --amber-soft:#fff4df; --red:#c43d3d; --red-soft:#fff2f2;
      --shadow:0 14px 34px rgba(15,23,42,.08);
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:26px 18px 72px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}}
    .hero{{padding:30px 32px}}
    .panel{{padding:20px 22px;margin-top:16px}}
    .tag,.pill{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;margin-right:6px;margin-bottom:6px}}
    .tag{{background:var(--blue-soft);color:var(--blue)}}
    .ok{{background:var(--green-soft);color:var(--green)}}
    .risk{{background:var(--red-soft);color:var(--red)}}
    .quote{{border-left:4px solid #b8ccff;background:#f8fbff;padding:12px 14px;border-radius:10px;margin-top:14px}}
    .kpis{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:18px}}
    .kpi{{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fbfcff}}
    .num{{font-size:24px;font-weight:700;line-height:1.1}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
    th{{background:#f7faff;color:#42556e;font-size:12px;text-transform:uppercase}}
    tr:last-child td{{border-bottom:none}}
    code{{background:#f3f6fb;border:1px solid #e4ebf5;border-radius:6px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="tag">main code</div>
      <div class="tag">temporal regression</div>
      <div class="tag">generic cases</div>
      <h1>EchoMemory Main-Code Temporal Regression</h1>
      <p class="muted">
        这不是 benchmark 跑分，而是一组完全泛化的机制级回归，直接调用主仓代码验证 three-clock 近期补丁在几个代表性 temporal failure mode 上是否真的生效。
      </p>
      <div class="kpis">
        <div class="kpi"><div class="num">{passed}/{len(results)}</div><div class="muted">cases passed</div></div>
        <div class="kpi"><div class="num">query side</div><div class="muted">anchor + relative resolver</div></div>
        <div class="kpi"><div class="num">memory side</div><div class="muted">event_time / mention_time fallback</div></div>
        <div class="kpi"><div class="num">generic</div><div class="muted">no dataset-specific entities or hacks</div></div>
      </div>
      <div class="quote">
        <strong>读法：</strong>
        这些 case 不是为了证明 EchoMemory 已经完成 temporal reasoning，而是为了证明最近补进去的主仓改动已经开始形成一个连贯的 three-clock 行为面。
      </div>
    </section>

    <section class="panel">
      <h2>Case Results</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Case</th>
            <th>Expectation</th>
            <th>Observed</th>
            <th>Status</th>
            <th>Why it matters</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Interpretation</h2>
      <ul>
        <li><strong>query side:</strong> <code>SearchService</code> now auto-fills <code>query_time_anchor</code> from the active session.</li>
        <li><strong>resolver side:</strong> relative time is resolved against that anchor instead of silently drifting to runtime wall clock.</li>
        <li><strong>retrieval side:</strong> pure event-time queries no longer trivially conflate “mentioned yesterday” with “happened yesterday”.</li>
        <li><strong>projection side:</strong> when story time is absent, event projection now prefers <code>mention_time</code> before <code>created_at</code>.</li>
      </ul>
    </section>
  </div>
</body>
</html>
"""
    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
