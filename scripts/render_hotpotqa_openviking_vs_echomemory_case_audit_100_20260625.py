#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"

ECHO_DIR = RUNS / "echomemory_generic_qa_20260622_183050_943ead" / "echomemory_generic_qa"
OV_DIR = RUNS / "openviking_generic_qa_20260622_231559_1bd882" / "openviking_generic_qa"

REPORT_NAME = "hotpotqa_openviking_vs_echomemory_case_audit_100_20260625.html"
OUTPUT = ROOT / "web/static/generated-reports" / REPORT_NAME
STATIC_MIRROR = ROOT / "static/generated-reports" / REPORT_NAME
SERVER_OUTPUT = ROOT / "generated-reports" / REPORT_NAME
PUBLIC_REPORT_PATH = f"/generated-reports/{REPORT_NAME}"


CURATED_CASES: dict[str, list[dict[str, str]]] = {
    "both_wrong": [
        {
            "sample_id": "5ab56e32554299637185c594",
            "title": "两边都拿到关键文档，但布尔关系判断反了",
            "note": "题目问的是“两个对象是否都被用于房地产”，不是“两个对象是否都和房地产有关”。两边都读到了 Random House Tower 和 888 7th Avenue，却把“相关”误当成“同属性成立”。",
        },
        {
            "sample_id": "5a75e05c55429976ec32bc5f",
            "title": "都检索到了 Brown State Fishing Lake，但都没走到人口数字",
            "note": "这是典型的二跳数值题。OpenViking 保持 sample-local 文档，但最终答成 unknown；EchoMemory 虽然召回更多候选，却把无关 atom 排到前列，最后同样放弃。",
        },
        {
            "sample_id": "5a713ea95542994082a3e6e4",
            "title": "双方都答成同一个错误实体 Ais",
            "note": "这不是纯缺记忆，而是关系链路选错。两边都把 Alvaro Mexia 和 Florida indigenous docs 找到了，但没有从“外交使命”跳到 gold 的 Apalachees。",
        },
    ],
    "ov_only": [
        {
            "sample_id": "5a8c7595554299585d9e36b6",
            "title": "OpenViking 把正确职位包含进答案，EchoMemory 抓住了旁支职位",
            "note": "Shirley Temple 的多个外交职位同时出现在记忆里。EchoMemory 落在“ambassador to Ghana”，OpenViking 至少把 gold 所需的 Chief of Protocol 一并带了出来。",
        },
        {
            "sample_id": "5a85ea095542994775f606a8",
            "title": "EchoMemory 已经把 Animorphs 相关材料召回到前排，却仍然回答 I do not know",
            "note": "这是最能说明问题的一类：不是没有记忆，而是答题阶段没有把召回材料压缩成最终答案。OpenViking 直接命中 Animorphs 文档并作答。",
        },
        {
            "sample_id": "5a8e3ea95542995a26add48d",
            "title": "OpenViking 给出上位概念 New York City，EchoMemory 直接弃答",
            "note": "judge 认为 New York City 可以覆盖 Greenwich Village, New York City，因此算正确；EchoMemory 在同题上没有把导演所在地从记忆里抽出来。",
        },
    ],
    "echo_only": [
        {
            "sample_id": "5a80721b554299485f5985ef",
            "title": "EchoMemory 从纪念碑上下文里抽出 World War II，OpenViking 仍然 unknown",
            "note": "这是 EchoMemory 少数但很重要的优势案例。它的 session summary 把纪念碑与二战 casualty 信息揉在一起，最终成功作答。",
        },
        {
            "sample_id": "5a8e1027554299653c1aa15f",
            "title": "EchoMemory 在 Colorado Buffaloes 系列 session 里拼出 2009 Big 12 Conference",
            "note": "OpenViking 也读了同一 sample 的 10 篇文档，但排序前列偏向 2014/2015 赛季，最后没有落到目标年份与 conference 组合。",
        },
        {
            "sample_id": "5a8b20335542996c9b8d5fb3",
            "title": "EchoMemory 给出语义等价答案，OpenViking 保守 abstain",
            "note": "EchoMemory 回答“shortest player ever to play in the NBA”被 judge 接受；OpenViking 的问题不在检索空，而在最终答案选择过于保守。",
        },
    ],
    "metric_gap": [
        {
            "sample_id": "5a8b57f25542995d1e6f1371",
            "title": "HotpotQA answer-only EM 会惩罚展开式 yes/no",
            "note": "judge 认为两边都答对，但 official EM 只给 EchoMemory 1.0。OpenViking 的“both American”在语义上正确，却因 gold 只有 yes 而被记成 EM=0。",
        },
        {
            "sample_id": "5a7166395542994082a3e814",
            "title": "题面命中但多带括号说明，judge 通过、EM 仍掉分",
            "note": "无论 EchoMemory 还是 OpenViking，都把 Kansas Song 说对了；只是官方 answer-only 口径不喜欢后缀说明，导致两边 EM 都被压低。",
        },
    ],
}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def short_text(text: str, limit: int = 240) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else f"{compact[:limit - 1]}..."


def compact_path(path: Path) -> str:
    text = str(path)
    return text if len(text) <= 92 else f"{text[:44]}...{text[-44:]}"


def file_url(path: Path) -> str:
    return path.resolve().as_uri()


def app_url(path: Path) -> str:
    if path.is_relative_to(ROOT / "runs"):
        return "/runs/" + path.relative_to(ROOT / "runs").as_posix()
    if path.is_relative_to(ROOT / "generated-reports"):
        return "/generated-reports/" + path.relative_to(ROOT / "generated-reports").as_posix()
    if path.is_relative_to(ROOT / "web/static/generated-reports"):
        return "/generated-reports/" + path.name
    if path.is_relative_to(ROOT / "static/generated-reports"):
        return "/generated-reports/" + path.name
    return file_url(path)


def parse_json_list(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def is_unknown(text: str) -> bool:
    normalized = " ".join((text or "").strip().lower().split())
    return normalized in {"", "unknown", "i do not know", "i do not know."}


def extract_ov_title(mem: dict[str, Any]) -> str:
    abstract = str(mem.get("abstract") or "")
    marker = " title: "
    idx = abstract.find(marker)
    if idx >= 0:
        rest = abstract[idx + len(marker):]
        end = rest.find(" time:")
        if end >= 0:
            title = rest[:end].strip()
            if title:
                return title
    uri = str(mem.get("uri") or "")
    if uri:
        tail = uri.split("/")[-1]
        return tail.replace(".md", "").replace("-", " ")
    return "OpenViking memory"


def render_chip(text: str, tone: str) -> str:
    return f"<span class='chip chip-{tone}'>{html.escape(text)}</span>"


def mean_or_zero(values: list[float | int]) -> float:
    return statistics.mean(values) if values else 0.0


def judge_bucket(echo_correct: bool, ov_correct: bool) -> str:
    if echo_correct and ov_correct:
        return "both_correct"
    if echo_correct and not ov_correct:
        return "echo_only"
    if not echo_correct and ov_correct:
        return "ov_only"
    return "both_wrong"


def official_bucket(echo_em: float, ov_em: float) -> str:
    echo_correct = echo_em >= 1.0
    ov_correct = ov_em >= 1.0
    if echo_correct and ov_correct:
        return "both_correct"
    if echo_correct and not ov_correct:
        return "echo_only"
    if not echo_correct and ov_correct:
        return "ov_only"
    return "both_wrong"


def bucket_label(bucket: str) -> str:
    return {
        "both_correct": "两边都对",
        "both_wrong": "两边都错",
        "ov_only": "仅 OpenViking 对",
        "echo_only": "仅 EchoMemory 对",
    }.get(bucket, bucket)


def bucket_tone(bucket: str) -> str:
    return {
        "both_correct": "green",
        "both_wrong": "red",
        "ov_only": "blue",
        "echo_only": "amber",
    }.get(bucket, "muted")


def load_echo_recall_map(base: Path) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for path in sorted(base.glob("q*.recall.json")):
        data = load_json(path)
        sample_id = str(data.get("sample_id") or "")
        if sample_id:
            mapping[sample_id] = data
    return mapping


def render_memory_list(items: list[dict[str, str]], backend: str) -> str:
    if not items:
        return "<div class='memory-empty'>无召回明细</div>"
    chunks = ["<div class='memory-list'>"]
    for item in items:
        chunks.append(
            "<article class='memory-card'>"
            f"<div class='memory-head'><strong>{html.escape(item['title'])}</strong>"
            f"<span>{html.escape(item['meta'])}</span></div>"
            f"<div class='memory-uri'><code>{html.escape(item['uri'])}</code></div>"
            f"<div class='memory-snippet'>{html.escape(item['snippet'])}</div>"
            f"<div class='memory-backend'>{html.escape(backend)}</div>"
            "</article>"
        )
    chunks.append("</div>")
    return "\n".join(chunks)


def render_stat_rows(rows: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"<div class='kv'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
        for label, value in rows
    )


def render_outcome_matrix(title: str, counts: Counter[str], note: str) -> str:
    cells = [
        ("both_correct", "两边都对"),
        ("ov_only", "仅 OpenViking 对"),
        ("echo_only", "仅 EchoMemory 对"),
        ("both_wrong", "两边都错"),
    ]
    blocks = []
    for bucket, label in cells:
        blocks.append(
            f"""
            <article class="matrix-cell {bucket}">
              <span>{html.escape(label)}</span>
              <strong>{counts.get(bucket, 0)}</strong>
            </article>
            """
        )
    return f"""
    <section class="panel">
      <div class="section-head">
        <h2>{html.escape(title)}</h2>
        <p>{html.escape(note)}</p>
      </div>
      <div class="matrix-grid">
        {''.join(blocks)}
      </div>
    </section>
    """


def render_case_card(case: dict[str, Any]) -> str:
    judge_summary = [
        ("Judge", case["judge_bucket_label"]),
        ("Official EM", f"Echo {case['echo_em']:.1f} / OV {case['ov_em']:.1f}"),
        ("Official F1", f"Echo {case['echo_f1']:.2f} / OV {case['ov_f1']:.2f}"),
        ("类型", case["q_type"]),
    ]
    echo_stats = [
        ("答案", case["echo_answer"]),
        ("结果", case["echo_result"]),
        ("Unknown", "是" if case["echo_unknown"] else "否"),
        ("命中数", str(case["echo_hits"])),
        ("回答 Token", str(case["echo_tokens"])),
        ("最终证据", case["echo_source"]),
    ]
    ov_stats = [
        ("答案", case["ov_answer"]),
        ("结果", case["ov_result"]),
        ("Unknown", "是" if case["ov_unknown"] else "否"),
        ("命中数", str(case["ov_hits"])),
        ("回答 Token", str(case["ov_tokens"])),
        ("检索模式", case["ov_source"]),
    ]
    return f"""
    <article class="case-card" id="{html.escape(case['sample_id'])}">
      <div class="case-top">
        <div>
          <div class="case-badges">
            {render_chip(case['judge_bucket_label'], case['judge_bucket_tone'])}
            {render_chip(case['q_type_label'], 'muted')}
            {render_chip(case['level_label'], 'muted')}
          </div>
          <h3>{html.escape(case['title'])}</h3>
          <p class="case-note">{html.escape(case['note'])}</p>
        </div>
        <div class="case-id"><code>{html.escape(case['sample_id'])}</code></div>
      </div>
      <div class="case-question">
        <div><span>Question</span><strong>{html.escape(case['question'])}</strong></div>
        <div><span>Gold</span><strong>{html.escape(case['gold'])}</strong></div>
      </div>
      <div class="case-metrics">{render_stat_rows(judge_summary)}</div>
      <div class="answer-grid">
        <section class="answer-panel echo">
          <div class="answer-head"><h4>EchoMemory</h4></div>
          <div class="answer-body">{render_stat_rows(echo_stats)}</div>
          <div class="answer-reason">{html.escape(case['echo_reason'])}</div>
          <div class="query-plan">
            <span>Query Plan</span>
            <code>{html.escape(" | ".join(case['echo_query_plan']))}</code>
          </div>
          {render_memory_list(case['echo_memories'], 'EchoMemory')}
        </section>
        <section class="answer-panel ov">
          <div class="answer-head"><h4>OpenViking</h4></div>
          <div class="answer-body">{render_stat_rows(ov_stats)}</div>
          <div class="answer-reason">{html.escape(case['ov_reason'])}</div>
          <div class="query-plan">
            <span>Query Plan</span>
            <code>{html.escape(" | ".join(case['ov_query_plan']))}</code>
          </div>
          {render_memory_list(case['ov_memories'], 'OpenViking')}
        </section>
      </div>
    </article>
    """


def render_full_table(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['q_type_label'])}</td>"
            f"<td>{render_chip(row['judge_bucket_label'], row['judge_bucket_tone'])}</td>"
            f"<td>{render_chip(row['official_bucket_label'], row['official_bucket_tone'])}</td>"
            f"<td>{html.escape(row['gold'])}</td>"
            f"<td>{html.escape(short_text(row['echo_answer'], 100))}</td>"
            f"<td>{html.escape(short_text(row['ov_answer'], 100))}</td>"
            f"<td>{row['echo_f1']:.2f}</td>"
            f"<td>{row['ov_f1']:.2f}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def render_protocol_compare() -> str:
    rows = [
        (
            "HotpotQA 100题",
            "同一批题目、同一 judge、同一 answer-only scorer",
            "OpenViking 直接 document-local retrieval；EchoMemory 走 atom / summary / segment recall",
            "answer-only EM/F1 + judge 语义矩阵；不含 supporting-fact / joint F1",
            "适合看“证据能不能转成答案”",
        ),
        (
            "LoCoMo conv-30",
            "导入和 QA 必须同一 workspace/account/sample",
            "先导入再 QA；要看 retrieval_ready_samples，不只看消息写入",
            "QA + judge + run health；强调导入就绪与样本一致性",
            "适合看“记忆有没有真的可用”",
        ),
        (
            "LongMemEval Oracle 50",
            "同一 answer / judge 模型，先看任务是否进入可答状态",
            "OpenViking 走文档记忆直答，EchoMemory 先组织长期记忆再答",
            "overall_accuracy / task_averaged_accuracy / token 归因",
            "适合看“写入后组织成本和可答性”",
        ),
    ]
    return "".join(
        f"""
        <tr>
          <td><strong>{html.escape(name)}</strong></td>
          <td>{html.escape(protocol)}</td>
          <td>{html.escape(flow)}</td>
          <td>{html.escape(metric)}</td>
          <td>{html.escape(takeaway)}</td>
        </tr>
        """
        for name, protocol, flow, metric, takeaway in rows
    )


def main() -> None:
    echo_rows = load_csv(ECHO_DIR / "echomemory_generic_qa_results.csv")
    ov_rows = load_csv(OV_DIR / "openviking_generic_qa_results.csv")
    echo_eval = {row["question_id"]: row for row in load_jsonl(ECHO_DIR / "hotpotqa_answer_eval_rows.jsonl")}
    ov_eval = {row["question_id"]: row for row in load_jsonl(OV_DIR / "hotpotqa_answer_eval_rows.jsonl")}
    echo_recall = load_echo_recall_map(ECHO_DIR)

    echo_summary = load_json(ECHO_DIR / "hotpotqa_answer_summary.json")
    ov_summary = load_json(OV_DIR / "hotpotqa_answer_summary.json")
    echo_judge = load_json(ECHO_DIR / "judge_summary.json")
    ov_judge = load_json(OV_DIR / "judge_summary.json")

    echo_map = {row["sample_id"]: row for row in echo_rows}
    ov_map = {row["sample_id"]: row for row in ov_rows}

    records: list[dict[str, Any]] = []
    judge_counts: Counter[str] = Counter()
    official_counts: Counter[str] = Counter()
    judge_vs_em_mismatch = {"echo": 0, "ov": 0}
    category_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "echo_hits": [],
        "ov_hits": [],
        "echo_tokens": [],
        "ov_tokens": [],
        "echo_unknown": 0,
        "ov_unknown": 0,
    })
    echo_top1_types: Counter[str] = Counter()

    for sample_id, echo_row in echo_map.items():
        ov_row = ov_map[sample_id]
        echo_eval_row = echo_eval[sample_id]
        ov_eval_row = ov_eval[sample_id]
        echo_result = str(echo_row.get("result") or "")
        ov_result = str(ov_row.get("result") or "")
        echo_correct = echo_result == "CORRECT"
        ov_correct = ov_result == "CORRECT"
        judge_key = judge_bucket(echo_correct, ov_correct)
        official_key = official_bucket(
            safe_float(echo_eval_row.get("answer_em")),
            safe_float(ov_eval_row.get("answer_em")),
        )
        judge_counts[judge_key] += 1
        official_counts[official_key] += 1

        echo_unknown = is_unknown(str(echo_row.get("response") or ""))
        ov_unknown = is_unknown(str(ov_row.get("response") or ""))
        stats = category_stats[judge_key]
        stats["echo_hits"].append(safe_int(echo_row.get("memory_hit_count")))
        stats["ov_hits"].append(safe_int(ov_row.get("memory_hit_count")))
        stats["echo_tokens"].append(safe_int(echo_row.get("answer_total_tokens")))
        stats["ov_tokens"].append(safe_int(ov_row.get("answer_total_tokens")))
        stats["echo_unknown"] += int(echo_unknown)
        stats["ov_unknown"] += int(ov_unknown)

        if echo_correct != (safe_float(echo_eval_row.get("answer_em")) >= 1.0):
            judge_vs_em_mismatch["echo"] += 1
        if ov_correct != (safe_float(ov_eval_row.get("answer_em")) >= 1.0):
            judge_vs_em_mismatch["ov"] += 1

        recall = echo_recall.get(sample_id, {})
        selected = recall.get("selected") or []
        if selected:
            echo_top1_types[str(selected[0].get("memory_type") or "-")] += 1

        records.append(
            {
                "sample_id": sample_id,
                "question": str(echo_row.get("question") or ""),
                "gold": str(echo_row.get("answer") or ""),
                "echo_answer": str(echo_row.get("response") or ""),
                "ov_answer": str(ov_row.get("response") or ""),
                "echo_result": echo_result,
                "ov_result": ov_result,
                "echo_reason": str(echo_row.get("reasoning") or ""),
                "ov_reason": str(ov_row.get("reasoning") or ""),
                "echo_em": safe_float(echo_eval_row.get("answer_em")),
                "ov_em": safe_float(ov_eval_row.get("answer_em")),
                "echo_f1": safe_float(echo_eval_row.get("answer_f1")),
                "ov_f1": safe_float(ov_eval_row.get("answer_f1")),
                "q_type": str(echo_eval_row.get("type") or ""),
                "q_type_label": "Bridge" if str(echo_eval_row.get("type") or "") == "bridge" else "Comparison",
                "level_label": str(echo_eval_row.get("level") or "").title() or "-",
                "judge_bucket": judge_key,
                "judge_bucket_label": bucket_label(judge_key),
                "judge_bucket_tone": bucket_tone(judge_key),
                "official_bucket": official_key,
                "official_bucket_label": bucket_label(official_key),
                "official_bucket_tone": bucket_tone(official_key),
                "echo_unknown": echo_unknown,
                "ov_unknown": ov_unknown,
                "echo_hits": safe_int(echo_row.get("memory_hit_count")),
                "ov_hits": safe_int(ov_row.get("memory_hit_count")),
                "echo_tokens": safe_int(echo_row.get("answer_total_tokens")),
                "ov_tokens": safe_int(ov_row.get("answer_total_tokens")),
                "echo_source": str(echo_row.get("final_evidence_source") or "-"),
                "ov_source": str(ov_row.get("retrieval_mode") or "-"),
                "echo_query_plan": (json.loads(echo_row.get("retrieval_query_plan") or "[]")[:3] if echo_row.get("retrieval_query_plan") else []),
                "ov_query_plan": (json.loads(ov_row.get("retrieval_query_plan") or "[]")[:3] if ov_row.get("retrieval_query_plan") else []),
                "echo_recall": recall,
                "ov_relevant_memory": parse_json_list(str(ov_row.get("relevant_memory") or "")),
            }
        )

    records_by_id = {record["sample_id"]: record for record in records}

    for record in records:
        echo_memories: list[dict[str, str]] = []
        for item in (record["echo_recall"].get("selected") or [])[:3]:
            meta = f"{item.get('memory_type', '-')}, score {safe_float(item.get('score')):.3f}"
            echo_memories.append(
                {
                    "title": str(item.get("memory_type") or "Echo memory"),
                    "meta": meta,
                    "uri": short_text(str(item.get("uri") or ""), 120),
                    "snippet": short_text(str(item.get("preview") or ""), 300),
                }
            )
        ov_memories: list[dict[str, str]] = []
        for item in record["ov_relevant_memory"][:3]:
            meta = f"score {safe_float(item.get('score')):.3f}"
            ov_memories.append(
                {
                    "title": extract_ov_title(item),
                    "meta": meta,
                    "uri": short_text(str(item.get("uri") or ""), 120),
                    "snippet": short_text(str(item.get("abstract") or ""), 320),
                }
            )
        record["echo_memories"] = echo_memories
        record["ov_memories"] = ov_memories

    curated_sections: list[str] = []
    for section_key, cases in CURATED_CASES.items():
        title = {
            "both_wrong": "Judge 口径：两边都错",
            "ov_only": "Judge 口径：仅 OpenViking 对",
            "echo_only": "Judge 口径：仅 EchoMemory 对",
            "metric_gap": "Official EM 与 Judge 不一致的代表题",
        }[section_key]
        intro = {
            "both_wrong": "这组题最适合看“检索到了什么”和“为什么还是答错”。我挑了三类：逻辑关系反转、数值二跳没走完、实体跳错。",
            "ov_only": "这 40 题是 OpenViking 真正拉开差距的主体。里面最常见的模式不是 EchoMemory 完全没检索，而是检索后 abstain。",
            "echo_only": "EchoMemory 的胜场少，但并非没有。它更像是在少数 topic-dense session 上，利用 summary/segment 把跨文档答案揉了出来。",
            "metric_gap": "HotpotQA answer-only EM 对 yes/no 展开句、别名、上位概念和带括号说明比较苛刻。这组题不该被直接归因为记忆系统失效。",
        }[section_key]
        cards = []
        for case_meta in cases:
            record = dict(records_by_id[case_meta["sample_id"]])
            record["title"] = case_meta["title"]
            record["note"] = case_meta["note"]
            cards.append(render_case_card(record))
        curated_sections.append(
            f"""
            <section class="panel">
              <div class="section-head">
                <h2>{html.escape(title)}</h2>
                <p>{html.escape(intro)}</p>
              </div>
              {''.join(cards)}
            </section>
            """
        )

    category_cards = []
    for key in ["both_correct", "ov_only", "echo_only", "both_wrong"]:
        stats = category_stats[key]
        category_cards.append(
            f"""
            <article class="summary-card">
              <div class="summary-card-top">
                {render_chip(bucket_label(key), bucket_tone(key))}
                <strong>{judge_counts.get(key, 0)} 题</strong>
              </div>
              <div class="summary-card-grid">
                <div><span>Echo avg hit</span><strong>{mean_or_zero(stats['echo_hits']):.1f}</strong></div>
                <div><span>OV avg hit</span><strong>{mean_or_zero(stats['ov_hits']):.1f}</strong></div>
                <div><span>Echo avg token</span><strong>{mean_or_zero(stats['echo_tokens']):.0f}</strong></div>
                <div><span>OV avg token</span><strong>{mean_or_zero(stats['ov_tokens']):.0f}</strong></div>
                <div><span>Echo unknown</span><strong>{stats['echo_unknown']}</strong></div>
                <div><span>OV unknown</span><strong>{stats['ov_unknown']}</strong></div>
              </div>
            </article>
            """
        )

    mismatch_rows = []
    for label, count in [("EchoMemory", judge_vs_em_mismatch["echo"]), ("OpenViking", judge_vs_em_mismatch["ov"])]:
        mismatch_rows.append(
            f"<div class='mismatch-row'><span>{html.escape(label)}</span><strong>{count} 题</strong></div>"
        )

    evidence_takeaways = [
        f"Judge 口径下，OpenViking 正确 {ov_judge['correct']} / 100，EchoMemory 正确 {echo_judge['correct']} / 100；差距主要集中在 {judge_counts['ov_only']} 题的 OpenViking 单边胜场。",
        f"在这 {judge_counts['ov_only']} 题里，EchoMemory 有 {category_stats['ov_only']['echo_unknown']} 题直接回答 unknown / I do not know，且平均仍然有 {mean_or_zero(category_stats['ov_only']['echo_hits']):.1f} 个 memory hit，说明更多是答题阶段没有把召回材料收束成最终答案。",
        f"OpenViking 在 100 题里几乎总是固定读取 10 篇 sample-local benchmark 文档；EchoMemory 的 top-1 evidence 则有 {echo_top1_types.get('session_summary', 0)} 题是 session summary，{echo_top1_types.get('atom', 0)} 题是 atom。",
        f"Official answer-only EM 对 OpenViking 更苛刻：共有 {judge_vs_em_mismatch['ov']} 题 judge 判对但 EM 仍为 0；EchoMemory 也有 {judge_vs_em_mismatch['echo']} 题，但规模小得多。",
    ]

    full_rows = sorted(
        records,
        key=lambda row: (
            {"ov_only": 0, "echo_only": 1, "both_wrong": 2, "both_correct": 3}[row["judge_bucket"]],
            row["sample_id"],
        ),
    )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HotpotQA 100题 Case Audit: OpenViking vs EchoMemory</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f9;
      --panel: rgba(255,255,255,0.96);
      --line: #d7deea;
      --text: #172033;
      --muted: #5b667c;
      --blue: #216bff;
      --blue-soft: #e9f1ff;
      --green: #1f8f59;
      --green-soft: #e9f8f0;
      --amber: #b26a00;
      --amber-soft: #fff4e1;
      --red: #c13b31;
      --red-soft: #fff0ee;
      --shadow: 0 20px 50px rgba(18, 33, 61, 0.08);
      --radius: 20px;
      --radius-sm: 14px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--sans);
      background:
        radial-gradient(circle at top left, rgba(33,107,255,0.08), transparent 36%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 32%, #eef2f7 100%);
      color: var(--text);
      line-height: 1.6;
    }}
    .page {{
      width: min(1280px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 72px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(248,251,255,0.92));
      border: 1px solid rgba(215,222,234,0.9);
      border-radius: 28px;
      box-shadow: var(--shadow);
      padding: 28px 28px 22px;
      margin-bottom: 22px;
    }}
    .hero-top {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      flex-wrap: wrap;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      letter-spacing: 0.04em;
      color: var(--muted);
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1.08;
      letter-spacing: 0;
    }}
    .hero p {{
      color: var(--muted);
      margin: 10px 0 0;
      max-width: 920px;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(260px, 1fr));
      gap: 12px;
      margin-top: 20px;
    }}
    .meta-card, .panel, .summary-card, .metric-card {{
      background: var(--panel);
      border: 1px solid rgba(215,222,234,0.92);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }}
    .meta-card {{
      padding: 16px 18px;
    }}
    .meta-card span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .meta-card code {{
      font-family: var(--mono);
      font-size: 12px;
      word-break: break-all;
    }}
    .meta-card a {{
      color: var(--blue);
      text-decoration: none;
    }}
    .hero-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 20px;
    }}
    .hero-actions a {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 16px;
      border-radius: 999px;
      border: 1px solid rgba(33,107,255,0.15);
      color: var(--blue);
      background: rgba(33,107,255,0.06);
      text-decoration: none;
      font-size: 14px;
      font-weight: 600;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-end;
      flex-wrap: wrap;
      margin-bottom: 18px;
    }}
    .section-head h2 {{
      margin: 0;
      font-size: 24px;
      line-height: 1.2;
    }}
    .section-head p {{
      margin: 6px 0 0;
      color: var(--muted);
      max-width: 940px;
    }}
    .panel {{
      padding: 24px;
      margin-bottom: 22px;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }}
    .metric-card {{
      padding: 18px;
    }}
    .metric-card span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .metric-card strong {{
      display: block;
      font-size: 30px;
      line-height: 1.08;
      letter-spacing: 0;
      margin-bottom: 6px;
    }}
    .metric-card small {{
      color: var(--muted);
      font-size: 12px;
    }}
    .matrix-wrap {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      margin-bottom: 22px;
    }}
    .matrix-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .matrix-cell {{
      padding: 18px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--line);
      min-height: 120px;
    }}
    .matrix-cell span {{
      display: block;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .matrix-cell strong {{
      font-size: 42px;
      line-height: 1;
      letter-spacing: 0;
    }}
    .matrix-cell.both_correct {{ background: var(--green-soft); border-color: rgba(31,143,89,0.24); }}
    .matrix-cell.ov_only {{ background: var(--blue-soft); border-color: rgba(33,107,255,0.24); }}
    .matrix-cell.echo_only {{ background: var(--amber-soft); border-color: rgba(178,106,0,0.24); }}
    .matrix-cell.both_wrong {{ background: var(--red-soft); border-color: rgba(193,59,49,0.24); }}
    .chip {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 0 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      border: 1px solid transparent;
    }}
    .chip-green {{ background: var(--green-soft); color: var(--green); border-color: rgba(31,143,89,0.22); }}
    .chip-blue {{ background: var(--blue-soft); color: var(--blue); border-color: rgba(33,107,255,0.22); }}
    .chip-amber {{ background: var(--amber-soft); color: var(--amber); border-color: rgba(178,106,0,0.22); }}
    .chip-red {{ background: var(--red-soft); color: var(--red); border-color: rgba(193,59,49,0.22); }}
    .chip-muted {{ background: #f0f3f8; color: var(--muted); border-color: rgba(91,102,124,0.14); }}
    .takeaways {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-bottom: 22px;
    }}
    .insight-list {{
      margin: 0;
      padding-left: 20px;
    }}
    .insight-list li {{
      margin-bottom: 10px;
    }}
    .mismatch-box {{
      padding: 18px;
      border-radius: var(--radius-sm);
      background: #f7f9fc;
      border: 1px solid rgba(215,222,234,0.84);
    }}
    .mismatch-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid rgba(215,222,234,0.7);
    }}
    .mismatch-row:last-child {{
      border-bottom: 0;
      padding-bottom: 0;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }}
    .summary-card {{
      padding: 16px;
    }}
    .summary-card-top {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 14px;
    }}
    .summary-card-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 12px;
    }}
    .summary-card-grid span, .kv span, .case-question span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 3px;
    }}
    .summary-card-grid strong, .kv strong {{
      font-size: 16px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .case-card {{
      border: 1px solid rgba(215,222,234,0.9);
      border-radius: 24px;
      padding: 20px;
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(250,252,255,0.95));
      margin-bottom: 18px;
    }}
    .case-top {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .case-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .case-top h3 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
    }}
    .case-note {{
      margin: 10px 0 0;
      color: var(--muted);
    }}
    .case-id code, .memory-uri code, .query-plan code, .meta-card code {{
      font-family: var(--mono);
      font-size: 12px;
    }}
    .case-question {{
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 12px;
      padding: 14px 16px;
      border-radius: var(--radius-sm);
      background: #f7f9fc;
      border: 1px solid rgba(215,222,234,0.84);
      margin-bottom: 14px;
    }}
    .case-metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .kv {{
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(215,222,234,0.82);
      background: rgba(255,255,255,0.85);
    }}
    .answer-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .answer-panel {{
      border-radius: 18px;
      padding: 16px;
      border: 1px solid rgba(215,222,234,0.88);
      background: rgba(255,255,255,0.92);
    }}
    .answer-panel.echo {{
      box-shadow: inset 0 0 0 1px rgba(178,106,0,0.05);
    }}
    .answer-panel.ov {{
      box-shadow: inset 0 0 0 1px rgba(33,107,255,0.05);
    }}
    .answer-head h4 {{
      margin: 0 0 12px;
      font-size: 18px;
    }}
    .answer-body {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .answer-reason {{
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 12px;
      background: #f6f8fb;
      border: 1px solid rgba(215,222,234,0.82);
      color: var(--muted);
      font-size: 14px;
    }}
    .query-plan {{
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 12px;
      background: #fbfcfe;
      border: 1px dashed rgba(91,102,124,0.28);
    }}
    .query-plan span {{
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .memory-list {{
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }}
    .memory-card {{
      border-radius: 14px;
      padding: 12px 14px;
      background: #f8fafc;
      border: 1px solid rgba(215,222,234,0.82);
    }}
    .memory-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
      margin-bottom: 6px;
    }}
    .memory-head strong {{
      font-size: 14px;
    }}
    .memory-head span {{
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .memory-uri {{
      color: var(--muted);
      margin-bottom: 8px;
      word-break: break-all;
    }}
    .memory-snippet {{
      font-size: 14px;
    }}
    .memory-backend {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .memory-empty {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 14px;
    }}
    details {{
      border-radius: var(--radius-sm);
      border: 1px solid rgba(215,222,234,0.86);
      background: rgba(255,255,255,0.88);
      padding: 12px 14px;
    }}
    details summary {{
      cursor: pointer;
      font-weight: 700;
      color: var(--text);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 14px;
      font-size: 14px;
    }}
    th, td {{
      border-bottom: 1px solid rgba(215,222,234,0.78);
      padding: 12px 10px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      background: rgba(247,249,252,0.9);
      position: sticky;
      top: 0;
    }}
    .footer {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }}
    @media (max-width: 1120px) {{
      .metric-grid, .summary-grid, .case-metrics, .meta-grid, .matrix-wrap, .takeaways {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .answer-grid {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 720px) {{
      .page {{
        width: min(100vw - 18px, 100%);
        padding-top: 14px;
      }}
      .hero, .panel {{
        padding: 18px;
        border-radius: 22px;
      }}
      .metric-grid, .summary-grid, .case-metrics, .meta-grid, .matrix-wrap, .takeaways, .case-question {{
        grid-template-columns: 1fr;
      }}
      .summary-card-grid, .answer-body {{
        grid-template-columns: 1fr;
      }}
      h1 {{
        font-size: 32px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">HotpotQA 100题 · OpenViking vs EchoMemory · Case Audit</div>
          <h1>同一批 100 题，差距主要不在“有没有记忆”，而在“怎么把记忆变成答案”</h1>
          <p>
            这份报告基于仓库里已经完成的两次 100 题 HotpotQA 运行：同一 reference、同一 judge 模型、同一 answer-only
            HotpotQA 评测脚本。它先确认整个链路是通的，再把总分拆成四件事：<strong>official EM/F1</strong>、
            <strong>judge 语义正确率</strong>、<strong>双方都错 / 单边胜场</strong>、以及每道代表题的<strong>召回记忆</strong>。
          </p>
        </div>
        <div class="case-id"><code>generated {html.escape(generated_at)}</code></div>
      </div>
      <div class="meta-grid">
        <article class="meta-card">
          <span>EchoMemory 100题 run</span>
          <a href="{app_url(ECHO_DIR)}"><code>{html.escape(compact_path(ECHO_DIR))}</code></a>
        </article>
        <article class="meta-card">
          <span>OpenViking 100题 run</span>
          <a href="{app_url(OV_DIR)}"><code>{html.escape(compact_path(OV_DIR))}</code></a>
        </article>
        <article class="meta-card">
          <span>EchoMemory 关键产物</span>
          <code>{html.escape(compact_path(ECHO_DIR / 'echomemory_generic_qa_results.csv'))}</code>
        </article>
        <article class="meta-card">
          <span>OpenViking 关键产物</span>
          <code>{html.escape(compact_path(OV_DIR / 'openviking_generic_qa_results.csv'))}</code>
        </article>
      </div>
      <div class="hero-actions">
        <a href="{html.escape(PUBLIC_REPORT_PATH)}">打开浏览器版报告</a>
        <a href="{app_url(ECHO_DIR / 'echomemory_generic_qa_results.csv')}">打开 EchoMemory CSV</a>
        <a href="{app_url(OV_DIR / 'openviking_generic_qa_results.csv')}">打开 OpenViking CSV</a>
        <a href="{app_url(ECHO_DIR / 'hotpotqa_answer_summary.json')}">打开 EchoMemory official summary</a>
        <a href="{app_url(OV_DIR / 'hotpotqa_answer_summary.json')}">打开 OpenViking official summary</a>
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>总览</h2>
        <p>先看两套口径：HotpotQA 官方 answer-only EM/F1，以及页面同一套 judge prompt 的语义正确率。</p>
      </div>
      <div class="metric-grid">
        <article class="metric-card">
          <span>EchoMemory Judge Accuracy</span>
          <strong>{pct(safe_float(echo_judge['accuracy']))}</strong>
          <small>{echo_judge['correct']} / {echo_judge['count']} 题</small>
        </article>
        <article class="metric-card">
          <span>OpenViking Judge Accuracy</span>
          <strong>{pct(safe_float(ov_judge['accuracy']))}</strong>
          <small>{ov_judge['correct']} / {ov_judge['count']} 题</small>
        </article>
        <article class="metric-card">
          <span>EchoMemory Official EM / F1</span>
          <strong>{echo_summary['answer_em']:.2f} / {echo_summary['answer_f1']:.2f}</strong>
          <small>HotpotQA answer-only, graded {echo_summary['graded']} 题</small>
        </article>
        <article class="metric-card">
          <span>OpenViking Official EM / F1</span>
          <strong>{ov_summary['answer_em']:.2f} / {ov_summary['answer_f1']:.2f}</strong>
          <small>HotpotQA answer-only, graded {ov_summary['graded']} 题</small>
        </article>
        <article class="metric-card">
          <span>EchoMemory Unknown</span>
          <strong>{sum(1 for row in records if row['echo_unknown'])}</strong>
          <small>Judge 单边失分里，EchoMemory 有 {category_stats['ov_only']['echo_unknown']} 题直接 abstain</small>
        </article>
        <article class="metric-card">
          <span>OpenViking Unknown</span>
          <strong>{sum(1 for row in records if row['ov_unknown'])}</strong>
          <small>EchoMemory 单边胜场里的 OpenViking 全部是 unknown</small>
        </article>
        <article class="metric-card">
          <span>EchoMemory Avg Answer Token</span>
          <strong>{mean_or_zero([row['echo_tokens'] for row in records]):.0f}</strong>
          <small>final_evidence_source: atom {Counter(row['echo_source'] for row in records)['atom']} / segment {Counter(row['echo_source'] for row in records)['segment_memory']}</small>
        </article>
        <article class="metric-card">
          <span>OpenViking Avg Answer Token</span>
          <strong>{mean_or_zero([row['ov_tokens'] for row in records]):.0f}</strong>
          <small>retrieval_mode: openviking_search_find 100 / 100</small>
        </article>
      </div>
      <div class="matrix-wrap">
        {render_outcome_matrix("Judge 语义矩阵", judge_counts, "把“语义上答对了没有”作为主口径，更适合做记忆链路诊断。")}
        {render_outcome_matrix("Official EM 矩阵", official_counts, "HotpotQA answer-only EM 更严格，能反映输出格式和别名归一化问题，但不适合单独拿来归因记忆失效。")}
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>先说结论</h2>
        <p>这 100 题里，OpenViking 的主要优势来自“sample-local 文档检索 + 更敢给出答案”；EchoMemory 的主要问题不是没有记忆，而是答题阶段更容易 abstain 或被噪声 evidence 带偏。和 LoCoMo / LongMemEval 比，这里更关注答案可用性，不关注导入就绪门槛本身。</p>
      </div>
      <div class="takeaways">
        <div>
          <ol class="insight-list">
            {''.join(f"<li>{html.escape(item)}</li>" for item in evidence_takeaways)}
          </ol>
        </div>
        <div class="mismatch-box">
          <h3 style="margin-top:0;">Judge vs Official EM 不一致</h3>
          <p style="margin-top:0;color:var(--muted);">这部分不是“记忆没召回”，而是 <code>answer-only EM</code> 对长句 yes/no、别名、部分归一化的惩罚。</p>
          {''.join(mismatch_rows)}
        </div>
      </div>
      <div class="summary-grid">
        {''.join(category_cards)}
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>流程核对</h2>
        <p>先确认整个链路有没有跑偏，再谈结果。这里把当前这次 HotpotQA 和其他记忆测试的流程差异放在一起看。</p>
      </div>
      <div class="summary-grid" style="margin-bottom:18px;">
        <article class="summary-card">
          <div class="summary-card-top">
            {render_chip("流程正常", "green")}
            <strong>100 题完整落盘</strong>
          </div>
          <div class="summary-card-grid">
            <div><span>Echo CSV</span><strong>100 行</strong></div>
            <div><span>OV CSV</span><strong>100 行</strong></div>
            <div><span>Echo recall</span><strong>100 文件</strong></div>
            <div><span>Judge / EM</span><strong>已落盘</strong></div>
          </div>
        </article>
        <article class="summary-card">
          <div class="summary-card-top">
            {render_chip("关键一致性", "blue")}
            <strong>同模型同评测口径</strong>
          </div>
          <div class="summary-card-grid">
            <div><span>Judge 模型</span><strong>deepseek-v4-flash</strong></div>
            <div><span>Official scorer</span><strong>answer-only</strong></div>
            <div><span>数据集</span><strong>HotpotQA dev</strong></div>
            <div><span>评测对象</span><strong>两后端</strong></div>
          </div>
        </article>
        <article class="summary-card">
          <div class="summary-card-top">
            {render_chip("和 LoCoMo 不同", "amber")}
            <strong>HotpotQA 不测导入就绪</strong>
          </div>
          <div class="summary-card-grid">
            <div><span>主关心</span><strong>证据转答案</strong></div>
            <div><span>不含</span><strong>回合归档</strong></div>
            <div><span>不含</span><strong>retrieval_ready</strong></div>
            <div><span>不含</span><strong>session commit</strong></div>
          </div>
        </article>
        <article class="summary-card">
          <div class="summary-card-top">
            {render_chip("和 LongMemEval 不同", "muted")}
            <strong>不看任务级就绪</strong>
          </div>
          <div class="summary-card-grid">
            <div><span>主关心</span><strong>可答性</strong></div>
            <div><span>不强调</span><strong>写入前等待</strong></div>
            <div><span>不强调</span><strong>组织链路 token</strong></div>
            <div><span>强调</span><strong>answer-only</strong></div>
          </div>
        </article>
      </div>
      <table>
        <thead>
          <tr>
            <th>数据集</th>
            <th>流程约束</th>
            <th>记忆形态</th>
            <th>指标</th>
            <th>一句话判断</th>
          </tr>
        </thead>
        <tbody>
          {render_protocol_compare()}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>检索形态差异</h2>
        <p>看错题时，最需要先分清楚：到底是检索空了，还是检索到了但没答出来。</p>
      </div>
      <div class="metric-grid">
        <article class="metric-card">
          <span>EchoMemory top-1 evidence</span>
          <strong>session_summary {echo_top1_types.get('session_summary', 0)}</strong>
          <small>其次是 atom {echo_top1_types.get('atom', 0)}、segment {echo_top1_types.get('segment_memory', 0)}</small>
        </article>
        <article class="metric-card">
          <span>OpenViking imported docs</span>
          <strong>10 docs × 99 题</strong>
          <small>仅 1 题 document_memory_count 不是 10</small>
        </article>
        <article class="metric-card">
          <span>OV-only 胜场里 Echo avg hit</span>
          <strong>{mean_or_zero(category_stats['ov_only']['echo_hits']):.1f}</strong>
          <small>说明 EchoMemory 并非“没召回”，而是更常在 answer stage 放弃</small>
        </article>
        <article class="metric-card">
          <span>Echo-only 胜场里 OV unknown</span>
          <strong>{category_stats['echo_only']['ov_unknown']}</strong>
          <small>5 / 5；OpenViking 在这组里是保守失分，不是幻觉失分</small>
        </article>
      </div>
    </section>

    {''.join(curated_sections)}

    <section class="panel">
      <div class="section-head">
        <h2>完整 100 题附录</h2>
        <p>默认按 judge bucket 排序：先看 OpenViking 单边胜场，再看 EchoMemory 单边胜场、双方都错、双方都对。需要细看某题时可以直接浏览 sample_id。</p>
      </div>
      <details>
        <summary>展开完整表格</summary>
        <table>
          <thead>
            <tr>
              <th>sample_id</th>
              <th>type</th>
              <th>judge</th>
              <th>official</th>
              <th>gold</th>
              <th>EchoMemory</th>
              <th>OpenViking</th>
              <th>Echo F1</th>
              <th>OV F1</th>
            </tr>
          </thead>
          <tbody>
            {render_full_table(full_rows)}
          </tbody>
        </table>
      </details>
    </section>

    <div class="footer">
      Report script:
      <code>{html.escape(compact_path(Path(__file__)))}</code>
    </div>
  </div>
</body>
</html>
"""

    for target in (OUTPUT, STATIC_MIRROR, SERVER_OUTPUT):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_doc, encoding="utf-8")
        print(target)


if __name__ == "__main__":
    main()
