#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import hashlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from openviking_memory_qa import ModelCallError, call_openai, token_estimate


TASK_TYPES = [
    "single-session-user",
    "single-session-preference",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(text: Any, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_reference(path: Path) -> dict[str, dict[str, Any]]:
    data = read_json(path)
    rows = data if isinstance(data, list) else next((v for v in data.values() if isinstance(v, list)), [])
    return {str(row.get("question_id") or ""): row for row in rows if isinstance(row, dict)}


def get_anscheck_prompt(task: str, question: str, answer: str, response: str, abstention: bool = False) -> str:
    if not abstention:
        if task in {"single-session-user", "single-session-assistant", "multi-session"}:
            template = (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains a subset "
                "of the information required by the answer, answer no. \n\n"
                "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, response)
        if task == "temporal-reasoning":
            template = (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains a subset "
                "of the information required by the answer, answer no. In addition, do not penalize off-by-one "
                "errors for the number of days. If the question asks for the number of days/weeks/months, etc., "
                "and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), "
                "the model's response is still correct. \n\n"
                "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, response)
        if task == "knowledge-update":
            template = (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response contains some previous information along with an updated answer, the response "
                "should be considered as correct as long as the updated answer is the required answer.\n\n"
                "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, response)
        if task == "single-session-preference":
            template = (
                "I will give you a question, a rubric for desired personalized response, and a response from a model. "
                "Please answer yes if the response satisfies the desired response. Otherwise, answer no. "
                "The model does not need to reflect all the points in the rubric. The response is correct as long "
                "as it recalls and utilizes the user's personal information correctly.\n\n"
                "Question: {}\n\nRubric: {}\n\nModel Response: {}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, response)

    template = (
        "I will give you an unanswerable question, an explanation, and a response from a model. "
        "Please answer yes if the model correctly identifies the question as unanswerable. "
        "The model could say that the information is incomplete, or some other information is given but "
        "the asked information is not.\n\n"
        "Question: {}\n\nExplanation: {}\n\nModel Response: {}\n\n"
        "Does the model correctly identify the question as unanswerable? Answer yes or no only."
    )
    return template.format(question, answer, response)


def parse_yes_no(text: str) -> bool:
    value = str(text or "").strip().lower()
    if re.search(r"\byes\b", value):
        return True
    if re.search(r"\bno\b", value):
        return False
    return False


def read_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    done: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            qid = str(row.get("question_id") or "")
            if qid:
                done[qid] = row
    return done


def hypothesis_fingerprint(text: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_hypotheses(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({
                "question_id": row.get("question_id") or row.get("sample_id") or "",
                "hypothesis": row.get("response") or "",
            }, ensure_ascii=False) + "\n")


def eval_one(row: dict[str, str], ref: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    qid = str(row.get("question_id") or row.get("sample_id") or "")
    response = str(row.get("response") or "")
    task = str(ref.get("question_type") or row.get("category") or "")
    prompt = get_anscheck_prompt(
        task,
        str(ref.get("question") or row.get("question") or ""),
        str(ref.get("answer") or row.get("answer") or ""),
        response,
        abstention="_abs" in qid,
    )
    started = time.time()
    try:
        result = call_openai(
            args.base_url,
            args.model,
            args.token,
            [{"role": "user", "content": prompt}],
            args.timeout_s,
            args.retries,
        )
        text = str(result.get("answer") or "")
        label = parse_yes_no(text)
        error = ""
        retry_count = int(result.get("model_retry_count") or 0)
        total_tokens = int(result.get("total_tokens") or 0)
    except ModelCallError as exc:
        text = ""
        label = False
        error = str(exc)
        retry_count = exc.retry_count
        total_tokens = token_estimate(prompt)
    return {
        "question_id": qid,
        "question_type": task,
        "question": ref.get("question") or row.get("question") or "",
        "answer": ref.get("answer") or row.get("answer") or "",
        "hypothesis": response,
        "hypothesis_sha256": hypothesis_fingerprint(response),
        "autoeval_label": {"model": args.model, "label": bool(label)},
        "judge_response": text,
        "judge_error": error,
        "judge_retry_count": retry_count,
        "judge_total_tokens": total_tokens,
        "time_cost": round(time.time() - started, 4),
    }


def summarize(log_rows: list[dict[str, Any]], total_reference: int, model: str) -> dict[str, Any]:
    type_acc: dict[str, list[int]] = {key: [] for key in TASK_TYPES}
    all_acc: list[int] = []
    abstention_acc: list[int] = []
    errors = 0
    for row in log_rows:
        label = 1 if row.get("autoeval_label", {}).get("label") else 0
        qtype = str(row.get("question_type") or "")
        if qtype in type_acc:
            type_acc[qtype].append(label)
        all_acc.append(label)
        if "_abs" in str(row.get("question_id") or ""):
            abstention_acc.append(label)
        if row.get("judge_error"):
            errors += 1

    def avg(values: list[int]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    task_values = [avg(values) for values in type_acc.values() if values]
    return {
        "status": "LONGMEMEVAL_OFFICIAL_STYLE_EVAL_DONE",
        "judge_model": model,
        "reference_count": total_reference,
        "graded": len(log_rows),
        "correct": sum(all_acc),
        "wrong": len(all_acc) - sum(all_acc),
        "overall_accuracy": avg(all_acc),
        "task_averaged_accuracy": round(sum(v for v in task_values if v is not None) / len(task_values), 4) if task_values else None,
        "abstention_accuracy": avg(abstention_acc),
        "abstention_count": len(abstention_acc),
        "type_accuracy": {
            key: {"accuracy": avg(values), "count": len(values), "correct": sum(values)}
            for key, values in type_acc.items()
        },
        "judge_error_count": errors,
        "judge_total_tokens": sum(int(row.get("judge_total_tokens") or 0) for row in log_rows),
        "judge_retry_total": sum(int(row.get("judge_retry_count") or 0) for row in log_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Official-style LongMemEval QA scorer for OpenViking CSV outputs.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--base-url", default=os.environ.get("JUDGE_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--retries", type=int, default=5)
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("judge token missing; set JUDGE_TOKEN/LOCOMO_JUDGE_TOKEN or pass --token")
    csv_path = Path(args.csv).expanduser().resolve()
    ref_path = Path(args.reference).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    hyp_path = out_dir / "longmemeval_hypotheses.jsonl"
    log_path = out_dir / "longmemeval_official_eval.jsonl"
    summary_path = out_dir / "longmemeval_official_summary.json"

    rows = load_csv(csv_path)
    refs = load_reference(ref_path)
    rows = [row for row in rows if str(row.get("question_id") or row.get("sample_id") or "") in refs]
    if args.limit:
        rows = rows[: args.limit]
    write_hypotheses(hyp_path, rows)

    existing = read_existing(log_path)
    selected: list[dict[str, str]] = []
    retained: dict[str, dict[str, Any]] = {}
    for row in rows:
        qid = str(row.get("question_id") or row.get("sample_id") or "")
        previous = existing.get(qid)
        current_hash = hypothesis_fingerprint(row.get("response") or "")
        previous_hash = str(previous.get("hypothesis_sha256") or "") if previous else ""
        previous_hypothesis = str(previous.get("hypothesis") or "") if previous else ""
        is_stale = bool(previous) and previous_hash not in {"", current_hash} and previous_hash != current_hash
        if previous and not previous_hash and hypothesis_fingerprint(previous_hypothesis) != current_hash:
            is_stale = True
        if previous and not is_stale:
            retained[qid] = previous
            if args.only_missing:
                continue
        selected.append(row)
    print(f"[longmemeval-eval] rows={len(rows)} selected={len(selected)} model={args.model} base_url={args.base_url} token=set", flush=True)

    if selected:
        log_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in retained.values()),
            encoding="utf-8",
        )
    completed = list(retained.values())
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {
            pool.submit(eval_one, row, refs[str(row.get("question_id") or row.get("sample_id") or "")], args): row
            for row in selected
        }
        for done, future in enumerate(as_completed(futures), 1):
            result = future.result()
            append_jsonl(log_path, result)
            completed.append(result)
            label = "yes" if result.get("autoeval_label", {}).get("label") else "no"
            print(f"[longmemeval-eval] {done}/{len(selected)} {result.get('question_id')} -> {label}", flush=True)

    by_qid = {str(row.get("question_id") or ""): row for row in completed}
    ordered = [by_qid[str(row.get("question_id") or row.get("sample_id") or "")] for row in rows if str(row.get("question_id") or row.get("sample_id") or "") in by_qid]
    summary = summarize(ordered, len(refs), args.model)
    summary.update({
        "input_csv": str(csv_path),
        "reference": str(ref_path),
        "hypotheses": str(hyp_path),
        "eval_log": str(log_path),
    })
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
