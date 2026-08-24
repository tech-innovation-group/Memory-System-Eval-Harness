"""HotpotQA answer, supporting-fact, and joint metrics."""

from __future__ import annotations

import csv
import json
import re
import string
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm

from shared.qa import QAResult
from shared.text import normalize_answer


EVAL_FIELDS = (
    "question_id",
    "question",
    "answer",
    "response",
    "answer_extracted",
    "answer_em",
    "answer_f1",
    "answer_precision",
    "answer_recall",
    "supporting_facts_em",
    "supporting_facts_f1",
    "supporting_facts_precision",
    "supporting_facts_recall",
    "joint_em",
    "joint_f1",
)


@dataclass
class EvaluationReport:
    rows: list[dict[str, Any]]
    answer_em: float
    answer_f1: float
    supporting_facts_em: float
    supporting_facts_f1: float
    joint_em: float
    joint_f1: float


def answer_metrics(prediction: str, gold: str) -> dict[str, float]:
    normalized_prediction = normalize_answer(prediction)
    normalized_gold = normalize_answer(gold)
    if (
        normalized_prediction in {"yes", "no", "noanswer"}
        and normalized_prediction != normalized_gold
    ) or (
        normalized_gold in {"yes", "no", "noanswer"}
        and normalized_prediction != normalized_gold
    ):
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "em": 0.0}
    prediction_tokens = normalized_prediction.split()
    gold_tokens = normalized_gold.split()
    em = 1.0 if normalized_prediction == normalized_gold else 0.0
    if not prediction_tokens and not gold_tokens:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "em": 1.0}
    if not prediction_tokens or not gold_tokens:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "em": em}
    overlap = sum((Counter(prediction_tokens) & Counter(gold_tokens)).values())
    if overlap == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "em": em}
    precision = overlap / len(prediction_tokens)
    recall = overlap / len(gold_tokens)
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall),
        "em": em,
    }


_ANSWER_LEAD_IN = re.compile(
    r"^\s*(?:"
    r"(?:the\s+)?(?:correct\s+)?answer\s+is|"
    r"it\s+(?:is|s|was)|"
    r"based\s+on\s+(?:the\s+)?(?:retrieved\s+)?documents|"
    r"according\s+to\s+[^,.:;]+[,.:;]|"
    r"i\s+(?:think|believe|would\s+say|found\s+that)|"
    r"yes\s*[,:]|no\s*[,:]"
    r")\s*",
    flags=re.IGNORECASE,
)


def extract_answer(response: str, gold: str) -> str:
    """Extract a concise answer from a verbose LLM response before scoring.

    HotpotQA gold answers are short exact strings, but models often reply in
    full sentences, which exact-match scoring cannot credit. Tiers, in order:
    1. yes/no/noanswer as the response's first word (only when the gold is a
       yes/no-style answer, so a leading "Yes" on a normal question does not
       zero a possibly-correct verbose answer);
    2. gold tokens appearing as a contiguous run inside the response tokens
       (contiguous-run check, not raw substring, so a single-token gold like
       "no" cannot match inside "know");
    3. strip common lead-in phrases and take the first clause;
    4. fall back to the response unchanged.
    """
    normalized_response = normalize_answer(response)
    if not normalized_response:
        return response or ""
    first_word = normalized_response.split()[0]
    if first_word in {"yes", "no", "noanswer"} and normalize_answer(gold) in {
        "yes",
        "no",
        "noanswer",
    }:
        return first_word
    gold_tokens = normalize_answer(gold).split()
    response_tokens = normalized_response.split()
    if gold_tokens and len(response_tokens) >= len(gold_tokens):
        for start in range(len(response_tokens) - len(gold_tokens) + 1):
            if response_tokens[start:start + len(gold_tokens)] == gold_tokens:
                return gold
    body = response
    for _ in range(3):
        stripped = _ANSWER_LEAD_IN.sub("", body).strip()
        if stripped == body:
            break
        body = stripped
    clause = re.split(r"[,.;!?]", body, maxsplit=1)[0].strip()
    if clause:
        return normalize_answer(clause)
    return response


def _normalize_blob(value: Any) -> str:
    text = str(value or "").lower()
    text = "".join(
        character if character not in string.punctuation else " "
        for character in text
    )
    return " ".join(text.split())


def _context_pairs(reference: dict[str, Any]) -> list[tuple[str, list[str]]]:
    context = reference.get("context") or []
    if isinstance(context, dict):
        titles = context.get("title") or []
        sentences = context.get("sentences") or []
        return [
            (
                str(title),
                [str(sentence) for sentence in (
                    sentences[index] if index < len(sentences) else []
                )],
            )
            for index, title in enumerate(titles)
        ]
    pairs: list[tuple[str, list[str]]] = []
    for item in context if isinstance(context, list) else []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            title, sentences = item[0], item[1]
        elif isinstance(item, dict):
            title = item.get("title") or ""
            sentences = item.get("sentences") or item.get("text") or []
        else:
            continue
        if isinstance(sentences, str):
            sentences = [sentences]
        pairs.append((str(title), [str(sentence) for sentence in sentences]))
    return pairs


def _gold_supporting_facts(reference: dict[str, Any]) -> set[tuple[str, int]]:
    facts: set[tuple[str, int]] = set()
    for item in reference.get("supporting_facts") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            facts.add((str(item[0]), int(item[1])))
        except (TypeError, ValueError):
            continue
    return facts


def _explicit_supporting_fact(
    item: dict[str, Any],
) -> tuple[str, int] | None:
    title = str(
        item.get("hotpotqa_title")
        or item.get("title")
        or item.get("document_title")
        or ""
    ).strip()
    sentence_id = item.get("hotpotqa_sent_id")
    if sentence_id is None:
        sentence_id = item.get("sent_id")
    if sentence_id is None:
        sentence_id = item.get("sentence_id")
    text = str(
        item.get("content")
        or item.get("text")
        or item.get("preview")
        or ""
    )
    if not title:
        match = re.search(
            r"^\s*(?:hotpotqa_title|title):\s*(.+?)\s*$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if match:
            title = match.group(1).strip()
    if sentence_id is None:
        match = re.search(
            r"^\s*(?:hotpotqa_sent_id|sent_id):\s*(\d+)\s*$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if match:
            sentence_id = match.group(1)
    if not title or sentence_id is None:
        return None
    try:
        return title, int(sentence_id)
    except (TypeError, ValueError):
        return None


def predict_supporting_facts(
    retrieval_items: list[dict[str, Any]],
    reference: dict[str, Any],
) -> set[tuple[str, int]]:
    explicit = {
        fact
        for item in retrieval_items
        if (fact := _explicit_supporting_fact(item)) is not None
    }
    if explicit:
        return explicit
    evidence = "\n".join(
        str(item.get("content") or item.get("text") or item.get("uri") or "")
        for item in retrieval_items
    )
    normalized_evidence = _normalize_blob(evidence)
    predicted: set[tuple[str, int]] = set()
    for title, sentences in _context_pairs(reference):
        normalized_title = _normalize_blob(title)
        if normalized_title and normalized_title not in normalized_evidence:
            continue
        for sentence_id, sentence in enumerate(sentences):
            normalized_sentence = _normalize_blob(sentence)
            if normalized_sentence and normalized_sentence in normalized_evidence:
                predicted.add((title, sentence_id))
    return predicted


def supporting_fact_metrics(
    predicted: set[tuple[str, int]],
    gold: set[tuple[str, int]],
) -> dict[str, float]:
    true_positive = len(predicted & gold)
    precision = (
        1.0 if not predicted and not gold
        else true_positive / len(predicted) if predicted
        else 0.0
    )
    recall = (
        1.0 if not predicted and not gold
        else true_positive / len(gold) if gold
        else 0.0
    )
    return {
        "em": 1.0 if predicted == gold else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        ),
    }


def load_references(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else []
    return {
        str(row.get("_id") or row.get("id") or row.get("question_id") or index): row
        for index, row in enumerate(rows)
        if isinstance(row, dict)
    }


def evaluate_hotpotqa(
    qa_results: list[QAResult],
    references: dict[str, dict[str, Any]],
    result_dir: Path,
) -> EvaluationReport:
    rows: list[dict[str, Any]] = []
    for result in tqdm(qa_results, desc="评测", unit="q"):
        reference = references.get(result.question_id, {})
        extracted_answer = extract_answer(result.response, result.answer)
        answer = answer_metrics(extracted_answer, result.answer)
        support = supporting_fact_metrics(
            predict_supporting_facts(result.retrieval_items, reference),
            _gold_supporting_facts(reference),
        )
        joint_precision = answer["precision"] * support["precision"]
        joint_recall = answer["recall"] * support["recall"]
        joint_f1 = (
            2 * joint_precision * joint_recall
            / (joint_precision + joint_recall)
            if joint_precision + joint_recall
            else 0.0
        )
        rows.append({
            "question_id": result.question_id,
            "question": result.question,
            "answer": result.answer,
            "response": result.response,
            "answer_extracted": extracted_answer,
            "answer_em": answer["em"],
            "answer_f1": round(answer["f1"], 4),
            "answer_precision": round(answer["precision"], 4),
            "answer_recall": round(answer["recall"], 4),
            "supporting_facts_em": support["em"],
            "supporting_facts_f1": round(support["f1"], 4),
            "supporting_facts_precision": round(support["precision"], 4),
            "supporting_facts_recall": round(support["recall"], 4),
            "joint_em": 1.0 if answer["em"] and support["em"] else 0.0,
            "joint_f1": round(joint_f1, 4),
        })

    output_path = result_dir / "eval_results.csv"
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVAL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    def average(key: str) -> float:
        return sum(float(row[key]) for row in rows) / max(len(rows), 1)

    return EvaluationReport(
        rows=rows,
        answer_em=average("answer_em"),
        answer_f1=average("answer_f1"),
        supporting_facts_em=average("supporting_facts_em"),
        supporting_facts_f1=average("supporting_facts_f1"),
        joint_em=average("joint_em"),
        joint_f1=average("joint_f1"),
    )
