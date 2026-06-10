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


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def heuristic_grade(row: dict[str, str]) -> tuple[str, str]:
    expected = norm(row.get("answer") or row.get("expected") or row.get("gold"))
    actual = norm(row.get("response") or row.get("prediction") or "")
    evidence = norm(row.get("relevant_memory") or row.get("context_preview") or "")
    if not expected:
        return "NEEDS_JUDGE", "missing gold answer"
    if expected in actual:
        return "CORRECT", "gold answer appears in response"
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", expected)
    if years and all(year in actual for year in years):
        return "CORRECT", "all gold years appear in response"
    if expected in evidence and actual and actual in evidence:
        return "CORRECT", "gold answer and response are both supported by retrieved memory"
    return "NEEDS_JUDGE", "heuristic judge could not confidently match gold answer"


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


def call_openai_compatible(base_url: str, model: str, token: str, row: dict[str, str], timeout: int) -> tuple[str, str]:
    url = base_url.rstrip("/") + "/chat/completions"
    prompt = (
        "You are grading a memory benchmark answer. Reply with JSON only: "
        "{\"result\":\"CORRECT\" or \"WRONG\", \"reasoning\":\"short reason\"}.\n"
        "Mark CORRECT if the response is semantically equivalent to the gold answer, "
        "including relative dates supported by the evidence. Otherwise mark WRONG.\n\n"
        f"Question: {row.get('question','')}\n"
        f"Gold answer: {row.get('answer') or row.get('expected') or row.get('gold')}\n"
        f"Response: {row.get('response') or row.get('prediction')}\n"
        f"Retrieved memory: {(row.get('relevant_memory') or row.get('context_preview') or '')[:6000]}"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
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
    try:
        parsed = json.loads(content)
        result = str(parsed.get("result") or "").upper()
        if result in {"CORRECT", "WRONG"}:
            return result, str(parsed.get("reasoning") or content)
    except Exception:
        pass
    return parse_model_grade(content)


def judge_row(row: dict[str, str], args: argparse.Namespace, token: str) -> dict[str, str]:
    existing = (row.get("result") or "").upper()
    if existing in {"CORRECT", "WRONG"} and not str(row.get("reasoning") or "").startswith("[NO JUDGE TOKEN]"):
        return row
    simple = (row.get("simple_grade") or row.get("simple_match") or "").upper()
    if simple in {"CORRECT", "MATCH"}:
        row["result"] = "CORRECT"
        row["reasoning"] = "accepted existing simple match"
        return row
    started = time.time()
    if token:
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
            row["result"], fallback_reason = heuristic_grade(row)
            row["reasoning"] = f"[API ERROR] {last_exc}; fallback={fallback_reason}"
    else:
        result, reason = heuristic_grade(row)
        row["result"] = "CORRECT" if result == "CORRECT" else ""
        row["reasoning"] = (
            f"[NO JUDGE TOKEN] {reason}; pending model judge"
            if result != "CORRECT"
            else f"[NO JUDGE TOKEN] {reason}"
        )
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
    parser = argparse.ArgumentParser(description="Local OpenAI-compatible judge for Locomo Eval Web CSV files.")
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
    args = parser.parse_args()

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
    }
    (path.parent / "judge_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Grading completed: {correct}/{graded} correct, accuracy: {summary['accuracy'] * 100:.2f}%" if graded else "Grading completed: 0 graded", flush=True)


if __name__ == "__main__":
    main()
