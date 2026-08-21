"""LoCoMo judge workflow."""

from __future__ import annotations

import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm

from shared.qa import QAResult


LOCOMO_JUDGE_SYSTEM = (
    "You are an expert grader that determines if answers to questions "
    "match a gold standard answer."
)
LOCOMO_JUDGE_TEMPLATE = """Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it is time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {response}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Respond with JSON only: {{"is_correct": "CORRECT" or "WRONG", "reasoning": "your explanation"}}"""


JUDGE_FIELDS = (
    "question_id",
    "question",
    "answer",
    "response",
    "verdict",
    "reasoning",
    "judge_error",
    "judge_prompt_tokens",
    "judge_completion_tokens",
    "judge_total_tokens",
    "judge_retry_count",
    "judge_latency_ms",
)

# Small judge models sometimes return empty or truncated JSON.  On retry we
# append a corrective instruction and raise temperature above 0 so the model
# does not reproduce the identical bad output (temperature 0 is deterministic).
JUDGE_REPAIR_PROMPT = (
    "\n\nYour previous response was empty or not valid JSON. "
    'Respond with JSON only: {"is_correct": "CORRECT" or "WRONG", '
    '"reasoning": "your explanation"}'
)
JUDGE_RETRY_TEMPERATURE = 0.3


@dataclass
class JudgeReport:
    rows: list[dict[str, str]]
    correct: int
    wrong: int
    errors: int
    graded: int
    accuracy: float


def _write_judge_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=JUDGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def parse_judge_json(text: str) -> tuple[str, str]:
    """Extract a CORRECT/WRONG verdict from a judge response.

    Tolerant: accepts a well-formed JSON object, but also recovers a verdict
    from truncated JSON or prose that already names CORRECT/WRONG in
    uppercase (common failure modes of small judge models).  Raises only
    when no usable verdict is present.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            verdict = str(payload.get("is_correct") or "").strip().upper()
            if verdict in {"CORRECT", "WRONG"}:
                return verdict, str(payload.get("reasoning") or "")
    match = re.search(r"\b(CORRECT|WRONG)\b", text)
    if match is not None:
        return match.group(0), ""
    if start != -1 and end != -1 and end >= start:
        raise ValueError(
            f"judge response has unknown verdict: {text[:200]}"
        )
    raise ValueError(f"judge response contains no JSON: {text[:200]}")


def locomo_judge(
    llm,
    question: str,
    gold_answer: str,
    response: str,
) -> tuple[str, str]:
    prompt = LOCOMO_JUDGE_TEMPLATE.format(
        question=question,
        gold_answer=gold_answer,
        response=response,
    )
    return parse_judge_json(llm.judge(LOCOMO_JUDGE_SYSTEM, prompt))


def judge_with_retries(
    judge_llm,
    question: str,
    answer: str,
    response: str,
    *,
    attempts: int = 3,
) -> tuple[str, str]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        prompt = LOCOMO_JUDGE_TEMPLATE.format(
            question=question,
            gold_answer=answer,
            response=response,
        )
        if attempt > 1:
            prompt += JUDGE_REPAIR_PROMPT
        try:
            return parse_judge_json(
                judge_llm.judge(LOCOMO_JUDGE_SYSTEM, prompt)
            )
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    assert last_error is not None
    raise last_error


def judge_with_metrics(
    judge_llm,
    question: str,
    answer: str,
    response: str,
    *,
    attempts: int = 3,
) -> tuple[str, str, dict[str, int | float | bool]]:
    """Judge one answer while preserving all externally visible model usage."""
    if not callable(getattr(judge_llm, "chat", None)):
        verdict, reasoning = judge_with_retries(
            judge_llm,
            question,
            answer,
            response,
            attempts=attempts,
        )
        return verdict, reasoning, {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "retry_count": 0,
            "latency_s": 0.0,
            "usage_observed": False,
        }

    prompt = LOCOMO_JUDGE_TEMPLATE.format(
        question=question,
        gold_answer=answer,
        response=response,
    )
    prompt_tokens = 0
    completion_tokens = 0
    retry_count = 0
    latency_s = 0.0
    usage_observed = False
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        model_response = judge_llm.chat(
            [
                {"role": "system", "content": LOCOMO_JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        prompt if attempt == 1
                        else prompt + JUDGE_REPAIR_PROMPT
                    ),
                },
            ],
            temperature=(
                JUDGE_RETRY_TEMPERATURE if attempt > 1 else None
            ),
            response_format=True,
            thinking_disabled=True,
            omit_max_tokens=True,
        )
        prompt_tokens += int(model_response.prompt_tokens or 0)
        completion_tokens += int(model_response.completion_tokens or 0)
        retry_count += int(getattr(model_response, "retry_count", 0) or 0)
        latency_s += float(model_response.elapsed_s or 0.0)
        usage_observed = (
            usage_observed
            or bool(getattr(model_response, "usage_observed", False))
            or bool(model_response.prompt_tokens or model_response.completion_tokens)
        )
        try:
            if model_response.error:
                raise RuntimeError(model_response.error)
            verdict, reasoning = parse_judge_json(model_response.content)
            return verdict, reasoning, {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "retry_count": retry_count + attempt - 1,
                "latency_s": latency_s,
                "usage_observed": usage_observed,
            }
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    assert last_error is not None
    raise last_error


def judge_locomo_results(
    qa_results: list[QAResult],
    judge_llm,
    result_dir: Path,
    log,
    concurrency: int = 1,
    checkpoint_interval: int = 10,
    existing_rows: list[dict[str, str]] | None = None,
) -> JudgeReport:
    if checkpoint_interval < 0:
        raise ValueError("judge checkpoint interval must be >= 0")

    def judge_one(result: QAResult) -> dict[str, str]:
        if result.llm_error or result.retrieval_error:
            verdict, reasoning = "ERROR", ""
            judge_error = "skipped because QA or retrieval failed"
        else:
            try:
                verdict, reasoning, metrics = judge_with_metrics(
                    judge_llm,
                    result.question,
                    result.answer,
                    result.response,
                )
                judge_error = ""
            except Exception as exc:
                verdict, reasoning, judge_error = "ERROR", "", str(exc)
                metrics = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "retry_count": 0,
                    "latency_s": 0.0,
                    "usage_observed": False,
                }
                log.error("Judge %s failed: %s", result.question_id, exc)
        if result.llm_error or result.retrieval_error:
            metrics = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "retry_count": 0,
                "latency_s": 0.0,
                "usage_observed": False,
            }
        usage_observed = bool(metrics["usage_observed"])
        prompt_tokens = int(metrics["prompt_tokens"])
        completion_tokens = int(metrics["completion_tokens"])
        return {
            "question_id": result.question_id,
            "question": result.question,
            "answer": result.answer,
            "response": result.response,
            "verdict": verdict,
            "reasoning": reasoning,
            "judge_error": judge_error,
            "judge_prompt_tokens": (
                str(prompt_tokens) if usage_observed else ""
            ),
            "judge_completion_tokens": (
                str(completion_tokens) if usage_observed else ""
            ),
            "judge_total_tokens": (
                str(prompt_tokens + completion_tokens)
                if usage_observed
                else ""
            ),
            "judge_retry_count": str(int(metrics["retry_count"])),
            "judge_latency_ms": f"{float(metrics['latency_s']) * 1000:.1f}",
        }

    existing_by_id: dict[str, dict[str, str]] = {}
    for row in existing_rows or []:
        question_id = str(row.get("question_id") or "").strip()
        verdict = str(row.get("verdict") or "").strip().upper()
        if (
            question_id
            and verdict in {"CORRECT", "WRONG"}
            and not str(row.get("judge_error") or "").strip()
        ):
            if question_id in existing_by_id:
                raise ValueError(
                    "duplicate question_id in Judge resume rows: "
                    f"{question_id}"
                )
            existing_by_id[question_id] = row

    rows: list[dict[str, str] | None] = [None] * len(qa_results)
    expected_ids = {result.question_id for result in qa_results}
    unknown_ids = sorted(set(existing_by_id) - expected_ids)
    if unknown_ids:
        raise ValueError(
            "Judge resume rows contain questions outside the current QA "
            f"selection: {', '.join(unknown_ids)}"
        )
    pending: list[tuple[int, QAResult]] = []
    for index, result in enumerate(qa_results):
        existing = existing_by_id.get(result.question_id)
        if existing is not None:
            if (
                str(existing.get("question") or "") == result.question
                and str(existing.get("answer") or "") == result.answer
                and str(existing.get("response") or "") == result.response
            ):
                rows[index] = existing
                continue
        pending.append((index, result))
    checkpoint_path = result_dir / "judge_results.checkpoint.csv"

    def checkpoint(done: int) -> None:
        if checkpoint_interval <= 0 or done % checkpoint_interval != 0:
            return
        completed = [row for row in rows if row is not None]
        _write_judge_rows(checkpoint_path, completed)
        log.info(
            "  Judge checkpoint: %d/%d -> %s",
            len(completed),
            len(qa_results),
            checkpoint_path,
        )

    if concurrency <= 1:
        progress = tqdm(total=len(pending), desc="Judge", unit="q")
        for done, (index, result) in enumerate(pending, 1):
            rows[index] = judge_one(result)
            progress.update(1)
            checkpoint(done)
        progress.close()
    else:
        progress = tqdm(total=len(pending), desc="Judge", unit="q")
        done = 0
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {
                    pool.submit(judge_one, result): index
                    for index, result in pending
                }
                for future in as_completed(futures):
                    rows[futures[future]] = future.result()
                    done += 1
                    progress.update(1)
                    checkpoint(done)
        finally:
            progress.close()
    final_rows = [row for row in rows if row is not None]

    output_path = result_dir / "judge_results.csv"
    _write_judge_rows(output_path, final_rows)
    if checkpoint_interval > 0:
        _write_judge_rows(checkpoint_path, final_rows)
        log.info("Judge 最终 checkpoint 已保存: %s", checkpoint_path)

    correct = sum(
        1 for row in final_rows if row["verdict"] == "CORRECT"
    )
    wrong = sum(1 for row in final_rows if row["verdict"] == "WRONG")
    errors = sum(1 for row in final_rows if row["judge_error"])
    graded = correct + wrong
    return JudgeReport(
        rows=final_rows,
        correct=correct,
        wrong=wrong,
        errors=errors,
        graded=graded,
        accuracy=correct / graded if graded else 0.0,
    )
