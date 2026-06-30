#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import string
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_reference(path: Path) -> dict[str, dict[str, Any]]:
    data = load_json(path)
    rows = data if isinstance(data, list) else next((value for value in data.values() if isinstance(value, list)), [])
    refs: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        qid = str(row.get("_id") or row.get("id") or row.get("question_id") or f"hotpotqa_{index}")
        refs[qid] = row
    return refs


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def sanitize_prediction_text(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = value.replace("\r", "\n")
    for pattern in (
        r"<\|?DSML\|?[\s\S]*$",
        r"<｜DSML｜[\s\S]*$",
        r"<memory_search[\s\S]*$",
        r"<functioncall[\s\S]*$",
        r"<function[\s\S]*$",
        r"<invoke[\s\S]*$",
        r"<execute[\s\S]*$",
    ):
        value = re.sub(pattern, "", value, flags=re.I)
    value = re.sub(r"`{3}[\s\S]*?`{3}", "", value)
    value = re.sub(r"`[^`]*`", "", value)
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    lead_patterns = (
        r"^(based on (?:the )?(?:available|retrieved) memor(?:y|ies)[^.!?]*[.!?]\s*)",
        r"^(based on my (?:knowledge|memory)[^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll check memory[^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll check [^.!?]*[.!?]\s*)",
        r"^(i will check [^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll search[^.!?]*[.!?]\s*)",
        r"^(i will search[^.!?]*[.!?]\s*)",
        r"^(let me check[^.!?]*[.!?]\s*)",
        r"^(let me search[^.!?]*[.!?]\s*)",
        r"^(let me retrieve[^.!?]*[.!?]\s*)",
        r"^(let me look[^.!?]*[.!?]\s*)",
        r"^(searching for[^.!?]*[.!?]\s*)",
    )
    changed = True
    while changed and value:
        changed = False
        for pattern in lead_patterns:
            updated = re.sub(pattern, "", value, flags=re.I).strip()
            if updated != value:
                value = updated
                changed = True
    for phrase in (
        "让我搜索一下。",
        "让我搜索一下",
        "我来搜索一下。",
        "我来搜索一下",
        "让我查一下。",
        "让我查一下",
        "根据记忆中的信息，",
        "基于记忆中的信息，",
    ):
        value = value.replace(phrase, "").strip()
    value = re.sub(
        r"\bto (?:find|answer|confirm|check|verify)[^.!?]*(?:let me|i(?:'| a)?ll|i will)\s+(?:search|retrieve|look up|check)[^.!?]*[.!?]?",
        "",
        value,
        flags=re.I,
    ).strip()
    value = re.sub(
        r"\bi (?:know|found) from the retrieved memories that\s+",
        "",
        value,
        flags=re.I,
    ).strip()
    filtered_sentences = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+", value):
        piece = sentence.strip()
        if not piece:
            continue
        lowered = piece.lower()
        if (
            re.search(r"\b(let me|i(?:'| a)?ll|i will)\s+(?:search|retrieve|look up|check)\b", lowered)
            or "search my memory" in lowered
            or "check memory" in lowered
            or "retrieved memories" in lowered
            or re.search(r"(让我|我来|我会).*(搜索|查询|检索|查一下)", piece)
            or re.search(r"(需要|还需|仍需).*(查询|搜索|检索|确认)", piece)
        ):
            continue
        filtered_sentences.append(piece)
    value = " ".join(filtered_sentences).strip()
    tail_patterns = (
        r"(?:however, )?(?:the )?retrieved memor(?:y|ies) do(?:es)? not [^.!?]*[.!?]?$",
        r"(?:therefore, )?i cannot confirm[^.!?]*[.!?]?$",
        r"(?:to be thorough, )?let me verify[^.!?]*[.!?]?$",
        r"(?:i )?need to (?:search|retrieve|look up|check)[^.!?]*[.!?]?$",
        r"(?:it )?requires? (?:search|retrieval|looking up)[^.!?]*[.!?]?$",
        r"(?:about|for) [^.!?]* need(?:s)? further (?:search|lookup|retrieval)[^.!?]*[.!?]?$",
        r"(?:关于|对于)[^。！？]*?(?:需要|还需|仍需)(?:进一步)?(?:查询|搜索|检索|确认)[^。！？]*[。！？]?$",
        r"(?:让我|我来)(?:继续)?(?:搜索|查询|检索|查一下)[^。！？]*[。！？]?$",
        r"(?:还需要|仍需要)(?:进一步)?(?:查询|搜索|检索|确认)[^。！？]*[。！？]?$",
    )
    changed = True
    while changed and value:
        changed = False
        for pattern in tail_patterns:
            updated = re.sub(pattern, "", value, flags=re.I).strip()
            if updated != value:
                value = updated
                changed = True
    value = re.sub(r"\b(?:need(?:s)?|requires?) to (?:search|retrieve|look up)[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"\b(?:let me|i(?:'| a)?ll|i will) (?:search|retrieve|look up|check)[^.!?]*$", "", value, flags=re.I).strip()
    value = re.sub(r"(?:to find [^.!?]*, )?let me search[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"(?:to answer [^.!?]*, )?i(?:'| a)?ll check memory[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"\s+", " ", value).strip(" -:\n\t")
    return value


def normalize_answer(text: Any) -> str:
    value = str(text or "").lower()
    value = "".join(ch for ch in value if ch not in set(string.punctuation))
    value = re.sub(r"\b(a|an|the)\b", " ", value)
    return " ".join(value.split())


def exact_match(prediction: str, gold: str) -> float:
    return 1.0 if normalize_answer(prediction) == normalize_answer(gold) else 0.0


def f1_score(prediction: str, gold: str) -> float:
    pred_tokens = normalize_answer(prediction).split()
    gold_tokens = normalize_answer(gold).split()
    if not pred_tokens and not gold_tokens:
        return 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(gold_tokens)
    same = sum(common.values())
    if same == 0:
        return 0.0
    precision = same / len(pred_tokens)
    recall = same / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer-only HotpotQA scorer for OpenViking CSV outputs.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--prediction-field", default="response")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    ref_path = Path(args.reference).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    refs = load_reference(ref_path)
    rows = []
    predictions: dict[str, str] = {}
    for row in load_csv(csv_path):
        qid = str(row.get("question_id") or row.get("sample_id") or row.get("native_question_id") or "")
        if not qid or qid not in refs:
            continue
        raw_prediction = str(row.get(args.prediction_field) or row.get("answer") or "")
        prediction = sanitize_prediction_text(raw_prediction) or raw_prediction
        ref = refs[qid]
        gold = str(ref.get("answer") or "")
        rows.append(
            {
                "question_id": qid,
                "question": ref.get("question") or row.get("question") or "",
                "answer": gold,
                "prediction": prediction,
                "answer_em": exact_match(prediction, gold),
                "answer_f1": f1_score(prediction, gold),
                "type": ref.get("type") or row.get("category") or "",
                "level": ref.get("level") or "",
            }
        )
        predictions[qid] = prediction
        if args.limit and len(rows) >= args.limit:
            break

    def avg(key: str) -> float | None:
        return round(sum(float(row[key]) for row in rows) / len(rows), 4) if rows else None

    type_counts: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        type_counts.setdefault(str(row.get("type") or "unknown"), []).append(row)

    summary = {
        "status": "HOTPOTQA_ANSWER_EVAL_DONE",
        "metric_scope": "answer_only",
        "official_metric_note": "Answer EM/F1 matches the HotpotQA answer normalization style; supporting-fact and joint EM/F1 are not computed because the OpenViking QA CSV does not emit supporting sentence predictions.",
        "input_csv": str(csv_path),
        "reference": str(ref_path),
        "prediction_field": args.prediction_field,
        "reference_count": len(refs),
        "graded": len(rows),
        "answer_em": avg("answer_em"),
        "answer_f1": avg("answer_f1"),
        "by_type": {
            key: {
                "count": len(items),
                "answer_em": round(sum(float(item["answer_em"]) for item in items) / len(items), 4),
                "answer_f1": round(sum(float(item["answer_f1"]) for item in items) / len(items), 4),
            }
            for key, items in sorted(type_counts.items())
        },
        "predictions": str(out_dir / "hotpotqa_answer_predictions.json"),
        "eval_log": str(out_dir / "hotpotqa_answer_eval_rows.jsonl"),
    }

    (out_dir / "hotpotqa_answer_predictions.json").write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "hotpotqa_answer_eval_rows.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "hotpotqa_answer_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
