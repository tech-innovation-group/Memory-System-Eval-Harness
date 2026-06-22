#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib import error, request

JUDGE_ALIGNMENT = "OpenViking benchmark/locomo/vikingbot/judge.py"
JUDGE_INPUT_MODE = "question_gold_generated_answer_only"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def heuristic_grade(row: dict[str, str]) -> tuple[str, str]:
    expected = norm(row.get("answer") or row.get("expected") or row.get("gold"))
    if not expected:
        return "NEEDS_JUDGE", "missing gold answer"
    return "NEEDS_JUDGE", "judge.py-aligned mode does not use heuristic correctness shortcuts"


def parse_model_grade(text: str) -> tuple[str, str]:
    upper = text.upper()
    if "CORRECT" in upper and "WRONG" not in upper.split("CORRECT", 1)[0][-20:]:
        return "CORRECT", text.strip()
    if "WRONG" in upper or "INCORRECT" in upper:
        return "WRONG", text.strip()
    return "WRONG", f"unparseable judge response: {text.strip()}"


def extract_chat_content(raw: str) -> str:
    try:
        data = json.loads(raw)
        return str(data["choices"][0]["message"]["content"])
    except json.JSONDecodeError:
        pass

    chunks: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in data.get("choices") or []:
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            content = delta.get("content") or message.get("content") or ""
            if content:
                chunks.append(str(content))
    if chunks:
        return "".join(chunks)
    raise RuntimeError(f"non-json judge API response: {raw[:800]}")


def parse_judge_json(text: str) -> tuple[str, str] | None:
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    verdict = str(parsed.get("result") or parsed.get("is_correct") or "").strip().upper()
    if verdict in {"CORRECT", "WRONG"}:
        return verdict, str(parsed.get("reasoning") or text)
    return None


def call_openai_compatible(base_url: str, model: str, token: str, row: dict[str, str], timeout: int) -> tuple[str, str]:
    url = base_url.rstrip("/") + "/chat/completions"
    system_prompt = "You are an expert grader that determines if answers to questions match a gold standard answer."
    prompt = (
        "Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. "
        "You will be given the following data:\n"
        "(1) a question (posed by one user to another user),\n"
        "(2) a 'gold' (ground truth) answer,\n"
        "(3) a generated answer\n"
        "which you will score as CORRECT/WRONG.\n\n"
        "The point of the question is to ask about something one user should know about the other user based on their prior conversations.\n"
        "The gold answer will usually be a concise and short answer that includes the referenced topic.\n"
        "The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.\n\n"
        "For time related questions, the gold answer will be a specific date, month, year, etc. "
        "The generated answer might be much longer or use relative time references (like 'last Tuesday' or 'next month'), "
        "but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. "
        "Even if the format differs (e.g., 'May 7th' vs '7 May'), consider it CORRECT if it's the same date.\n\n"
        "Now it's time for the real question:\n"
        f"Question: {row.get('question','')}\n"
        f"Gold answer: {row.get('answer') or row.get('expected') or row.get('gold')}\n"
        f"Generated answer: {row.get('response') or row.get('prediction')}\n\n"
        "First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.\n"
        "Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.\n\n"
        "Respond with JSON only: "
        "{\"is_correct\": \"CORRECT\" or \"WRONG\", \"reasoning\": \"your explanation\"}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:800]}") from exc
    if not raw.strip():
        raise RuntimeError("empty judge API response")
    content = extract_chat_content(raw)
    parsed = parse_judge_json(content)
    if parsed:
        return parsed
    return parse_model_grade(content)


def judge_row(row: dict[str, str], args: argparse.Namespace, token: str) -> dict[str, str]:
    existing = (row.get("result") or "").upper()
    if (
        not bool(getattr(args, "force_rejudge", False))
        and existing in {"CORRECT", "WRONG"}
        and not str(row.get("reasoning") or "").startswith("[JUDGE ERROR]")
    ):
        return row
    started = time.time()
    last_exc: Exception | None = None
    for attempt in range(max(1, args.retries + 1)):
        try:
            result, reasoning = call_openai_compatible(args.base_url, args.model, token, row, args.timeout_s)
            row["result"] = result
            row["reasoning"] = reasoning
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt < args.retries:
                time.sleep(min(8, 1.5 ** attempt))
    if last_exc is not None:
        row["result"] = "WRONG"
        row["reasoning"] = f"[JUDGE ERROR] {last_exc}"
    try:
        prior = float(row.get("time_cost") or 0)
        row["time_cost"] = f"{prior + (time.time() - started):.4f}"
    except ValueError:
        row["time_cost"] = f"{time.time() - started:.4f}"
    return row


def parse_csv_set(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def token_estimate(row: dict[str, str]) -> int:
    try:
        return int(float(row.get("injection_tokens_est") or 0))
    except ValueError:
        return 0


def row_matches_filters(index: int, row: dict[str, str], args: argparse.Namespace) -> bool:
    row_indexes = parse_csv_set(args.row_indexes)
    question_ids = parse_csv_set(args.question_ids)
    if row_indexes and str(index) not in row_indexes and str(index + 1) not in row_indexes:
        return False
    if question_ids and str(row.get("question_id") or "") not in question_ids:
        return False
    if args.only_pending and (row.get("result") or "").upper() in {"CORRECT", "WRONG"}:
        return False
    if args.category and str(row.get("category") or "") != args.category:
        return False
    if args.query:
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ["question_id", "sample_id", "question", "answer", "response", "category"]
        ).lower()
        if args.query.strip().lower() not in haystack:
            return False
    tokens = token_estimate(row)
    if args.min_tokens is not None and tokens < args.min_tokens:
        return False
    if args.max_tokens is not None and tokens > args.max_tokens:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI-compatible judge aligned with OpenViking LoCoMo VikingBot judge.py.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--base-url", default=os.environ.get("JUDGE_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--parallel", type=int, default=10)
    parser.add_argument(
        "--token",
        default=(
            os.environ.get("LOCOMO_JUDGE_TOKEN")
            or os.environ.get("JUDGE_TOKEN")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("ECHOMEM_CHAT_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ),
    )
    parser.add_argument("--timeout-s", type=int, default=90)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--only-pending", action="store_true", help="Judge only rows without CORRECT/WRONG result.")
    parser.add_argument("--question-ids", default="", help="Comma-separated question_id allowlist.")
    parser.add_argument("--row-indexes", default="", help="Comma-separated zero-based or one-based row indexes.")
    parser.add_argument("--category", default="", help="Judge only pending rows in this category.")
    parser.add_argument("--query", default="", help="Judge only rows whose id/question/gold/response contains this text.")
    parser.add_argument("--min-tokens", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--force-rejudge", action="store_true", help="Re-run the judge even for rows that already have CORRECT/WRONG.")
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("judge token is required in judge.py-aligned mode")

    path = Path(args.input).expanduser().resolve()
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows:
        raise SystemExit(f"no rows in {path}")
    fieldnames = list(rows[0].keys())
    for name in ["result", "reasoning"]:
        if name not in fieldnames:
            fieldnames.append(name)
            for row in rows:
                row[name] = ""

    selected = [(idx, row) for idx, row in enumerate(rows) if row_matches_filters(idx, row, args)]
    print(f"[judge] input={path}", flush=True)
    print(f"[judge] rows={len(rows)} selected={len(selected)} model={args.model} base_url={args.base_url or '-'} token={'set' if args.token else 'missing; heuristic fallback'}", flush=True)
    print(f"[judge] alignment={JUDGE_ALIGNMENT} input_mode={JUDGE_INPUT_MODE}", flush=True)
    if not selected:
        print("[judge] no rows matched filters; nothing to do", flush=True)
        return

    indexed: list[tuple[int, dict[str, str]]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {pool.submit(judge_row, dict(row), args, args.token): idx for idx, row in selected}
        for done, future in enumerate(as_completed(futures), 1):
            idx = futures[future]
            row = future.result()
            indexed.append((idx, row))
            print(f"[judge] {done}/{len(selected)} {row.get('question_id') or idx} -> {row.get('result') or 'NEEDS_JUDGE'}", flush=True)
    for idx, row in sorted(indexed):
        rows[idx] = row

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)

    correct = sum(1 for row in rows if (row.get("result") or "").upper() == "CORRECT")
    wrong = sum(1 for row in rows if (row.get("result") or "").upper() == "WRONG")
    graded = correct + wrong
    summary = {
        "count": len(rows),
        "graded": graded,
        "correct": correct,
        "wrong": wrong,
        "accuracy": correct / graded if graded else None,
        "status": "JUDGE_DONE",
        "input": str(path),
        "selected": len(selected),
        "judge_alignment": JUDGE_ALIGNMENT,
        "judge_input_mode": JUDGE_INPUT_MODE,
        "uses_retrieved_memory": False,
        "uses_message_jsonl": False,
        "judge_model": args.model,
    }
    (path.parent / "judge_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Grading completed: {correct}/{graded} correct, accuracy: {summary['accuracy'] * 100:.2f}%" if graded else "Grading completed: 0 graded", flush=True)


if __name__ == "__main__":
    main()
