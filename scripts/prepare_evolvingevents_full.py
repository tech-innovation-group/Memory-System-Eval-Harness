#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_nonempty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def list_payload(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "examples", "chunks", "qa_pairs", "questions", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("Unsupported JSON payload: expected a list or an object containing a list field")


def flatten_id_list(value: Any) -> list[str]:
    rows: list[str] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(flatten_id_list(item))
        return rows
    if isinstance(value, dict):
        nested = (
            value.get("id"),
            value.get("chunk_id"),
            value.get("evidence_id"),
            value.get("ref"),
            value.get("chunk"),
            value.get("qid"),
        )
        text = first_nonempty(*nested)
        if text:
            rows.append(text)
        return rows
    text = first_nonempty(value)
    if text:
        rows.append(text)
    return rows


def chunk_id(chunk: dict[str, Any], index: int) -> str:
    return first_nonempty(
        chunk.get("id"),
        chunk.get("chunk_id"),
        chunk.get("uid"),
        chunk.get("uuid"),
        chunk.get("name"),
        f"chunk_{index}",
    )


def chunk_time(chunk: dict[str, Any]) -> str:
    meta = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    return first_nonempty(
        chunk.get("timestamp"),
        chunk.get("time"),
        chunk.get("date"),
        meta.get("timestamp"),
        meta.get("time"),
        meta.get("date"),
        meta.get("event_time"),
    )


def chunk_text(chunk: dict[str, Any]) -> str:
    meta = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    return first_nonempty(
        chunk.get("event"),
        chunk.get("text"),
        chunk.get("content"),
        chunk.get("description"),
        chunk.get("chunk"),
        chunk.get("body"),
        meta.get("event"),
        meta.get("text"),
        meta.get("content"),
        meta.get("description"),
    )


def build_chunk_index(chunks_payload: Any) -> dict[str, dict[str, str]]:
    rows = {}
    for index, raw in enumerate(list_payload(chunks_payload)):
        if not isinstance(raw, dict):
            continue
        cid = chunk_id(raw, index)
        text = chunk_text(raw)
        if not cid or not text:
            continue
        rows[cid] = {
            "timestamp": chunk_time(raw),
            "event": text,
        }
    return rows


def qa_id(item: dict[str, Any], index: int) -> str:
    return first_nonempty(
        item.get("id"),
        item.get("qa_id"),
        item.get("question_id"),
        item.get("uid"),
        f"ee_full_{index:04d}",
    )


def qa_question(item: dict[str, Any]) -> str:
    return first_nonempty(
        item.get("question"),
        item.get("query"),
        item.get("input"),
        item.get("prompt"),
    )


def qa_answer(item: dict[str, Any]) -> str:
    return first_nonempty(
        item.get("answer"),
        item.get("gold_answer"),
        item.get("target"),
        item.get("output"),
        item.get("label"),
    )


def qa_time(item: dict[str, Any]) -> str:
    return first_nonempty(
        item.get("query_time"),
        item.get("question_time"),
        item.get("time"),
        item.get("timestamp"),
        item.get("date"),
        item.get("current_time"),
    )


def qa_category(item: dict[str, Any]) -> str:
    return first_nonempty(
        item.get("category"),
        item.get("type"),
        item.get("reasoning_type"),
        item.get("task_type"),
        "evolvingevents",
    )


def qa_chunk_refs(item: dict[str, Any]) -> list[str]:
    ref_fields = [
        item.get("chunk_ids"),
        item.get("supporting_chunk_ids"),
        item.get("evidence_chunk_ids"),
        item.get("context_ids"),
        item.get("references"),
        item.get("chunk_refs"),
        item.get("chunks"),
        item.get("evidence"),
    ]
    refs: list[str] = []
    for value in ref_fields:
        refs.extend(flatten_id_list(value))
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            deduped.append(ref)
    return deduped


def qa_inline_events(item: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    raw_events = item.get("events") or item.get("timeline") or item.get("context") or []
    if not isinstance(raw_events, list):
        return rows
    for raw in raw_events:
        if isinstance(raw, dict):
            timestamp = first_nonempty(raw.get("timestamp"), raw.get("time"), raw.get("date"))
            text = first_nonempty(raw.get("event"), raw.get("text"), raw.get("content"), raw.get("description"))
        else:
            timestamp = ""
            text = first_nonempty(raw)
        if text:
            rows.append({"timestamp": timestamp, "event": text})
    return rows


def build_output(chunks_payload: Any, qa_payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    chunk_index = build_chunk_index(chunks_payload)
    output: list[dict[str, Any]] = []
    missing_chunk_refs = 0
    inline_only = 0
    for index, raw in enumerate(list_payload(qa_payload)):
        if not isinstance(raw, dict):
            continue
        question = qa_question(raw)
        answer = qa_answer(raw)
        refs = qa_chunk_refs(raw)
        events = [chunk_index[ref] for ref in refs if ref in chunk_index]
        if refs and not events:
            missing_chunk_refs += 1
        if not events:
            events = qa_inline_events(raw)
            if events:
                inline_only += 1
        row = {
            "id": qa_id(raw, index),
            "events": events,
            "question": question,
            "answer": answer,
            "query_time": qa_time(raw),
            "category": qa_category(raw),
        }
        if row["question"] and row["answer"]:
            output.append(row)
    stats = {
        "chunks_indexed": len(chunk_index),
        "qa_rows_in": len(list_payload(qa_payload)),
        "qa_rows_out": len(output),
        "rows_with_missing_chunk_refs": missing_chunk_refs,
        "rows_using_inline_events_only": inline_only,
    }
    return output, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert upstream EvolvingEvents chunk/QA files into the MemoryBench-compatible dataset/full/evolvingevents.json format."
    )
    parser.add_argument("--chunks", required=True, help="Path to upstream chunks.json")
    parser.add_argument("--qa", required=True, help="Path to upstream qa_pairs.json")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "dataset" / "full" / "evolvingevents.json"),
        help="Output JSON path for the converted full EvolvingEvents dataset",
    )
    parser.add_argument(
        "--stats-out",
        default="",
        help="Optional path to write conversion stats as JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    chunks_path = Path(args.chunks).expanduser().resolve()
    qa_path = Path(args.qa).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    stats_out = Path(args.stats_out).expanduser().resolve() if args.stats_out else None

    payload, stats = build_output(read_json(chunks_path), read_json(qa_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if stats_out:
        stats_out.parent.mkdir(parents=True, exist_ok=True)
        stats_out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out_path), "stats": stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
