#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


QUESTION_KEYS = ("question", "query", "input", "prompt", "question_text")
ANSWER_KEYS = ("answer", "gold_answer", "target", "output", "reference", "label")
TIME_KEYS = ("query_time", "question_time", "question_date", "time", "timestamp", "date", "datetime")
ID_KEYS = ("_id", "id", "uid", "uuid", "sample_id", "question_id", "qid")
EVENT_KEYS = (
    "events",
    "event",
    "memories",
    "memory",
    "messages",
    "conversation",
    "history",
    "sessions",
    "context",
    "contexts",
    "passages",
    "documents",
    "tools",
    "tool",
)
EVENT_TEXT_KEYS = (
    "time",
    "timestamp",
    "date",
    "role",
    "speaker",
    "user",
    "title",
    "name",
    "description",
    "task",
    "content",
    "text",
    "message",
    "event",
    "sentence",
    "sentences",
    "paragraph",
    "paragraphs",
)


@dataclass
class Job:
    dataset_format: str
    sample_id: str
    question_id: str
    question: str
    answer: str
    category: str
    query_time: str
    injection_events: int
    injection_tokens_est: int
    context_preview: str
    response: str = ""
    simple_grade: str = "NEEDS_JUDGE"
    reasoning: str = "adapter dry-run only; no model call"
    time_cost: str = "0"
    original_sample_id: str = ""
    question_index: str = ""
    memory_users: str = ""
    native_question_id: str = ""


def simple_grade(expected: str, answer: str) -> str:
    expected_norm = re.sub(r"\s+", " ", expected.lower()).strip()
    answer_norm = re.sub(r"\s+", " ", answer.lower()).strip()
    if not answer_norm:
        return "NEEDS_JUDGE"
    if "unknown" in answer_norm and expected_norm not in {"unknown", "not mentioned", "not specified"}:
        return "NEEDS_JUDGE"
    if expected_norm and expected_norm in answer_norm:
        return "MATCH"
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", expected_norm)
    if years and all(year in answer_norm for year in years):
        return "MATCH"
    return "NEEDS_JUDGE"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def chenmo_markdown_items(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    turns = [
        {"role": role, "content": content}
        for role, content in re.findall(r'\("(user|assistant)"\s*,\s*"(.*?)"\)', text, re.S)
    ]
    events = [
        {
            "time": "",
            "role": item["role"],
            "text": f"{item['role']}: {item['content']}",
        }
        for item in turns
        if item.get("content")
    ]
    items: list[dict[str, Any]] = []
    category = "ChenMo"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        section = re.match(r"^###\s+(.+?)\s*$", line)
        if section:
            category = section.group(1).strip()
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        qid, question, answer = cells[:3]
        if not re.match(r"^[A-Z]+\d+$", qid):
            continue
        items.append(
            {
                "sample_id": "chenmo",
                "question_id": qid,
                "question": question,
                "answer": answer,
                "category": category,
                "events": events,
            }
        )
    return items


def read_dataset(path: Path) -> Any:
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        rows = []
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                text = line.strip()
                if text:
                    rows.append(json.loads(text))
        return rows
    if path.suffix.lower() in {".md", ".markdown"}:
        return {"items": chenmo_markdown_items(path), "format": "chenmo"}
    return read_json(path)


def first_non_ws(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                return ""
            stripped = chunk.lstrip()
            if stripped:
                return stripped[0]


def iter_json_array_objects(path: Path, offset: int = 0, limit: int | None = None):
    decoder = json.JSONDecoder()
    index = -1
    buf = ""
    in_array = False
    max_index = None if limit is None else offset + max(0, limit)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk and not buf:
                break
            buf += chunk
            pos = 0
            if not in_array:
                while pos < len(buf) and buf[pos].isspace():
                    pos += 1
                if pos < len(buf) and buf[pos] == "[":
                    in_array = True
                    pos += 1
                else:
                    if chunk:
                        buf = buf[max(0, len(buf) - 32):]
                        continue
                    break
            while True:
                while pos < len(buf) and (buf[pos].isspace() or buf[pos] == ","):
                    pos += 1
                if pos < len(buf) and buf[pos] == "]":
                    return
                if pos >= len(buf):
                    break
                try:
                    item, end = decoder.raw_decode(buf, pos)
                except json.JSONDecodeError:
                    break
                index += 1
                if index >= offset:
                    yield index, item
                    if max_index is not None and index + 1 >= max_index:
                        return
                pos = end
            buf = buf[pos:]
            if not chunk:
                break


def iter_payload_from_path(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        for index, item in enumerate(chenmo_markdown_items(path)):
            yield index, item
        return
    if suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for index, line in enumerate(f):
                text = line.strip()
                if text:
                    yield index, json.loads(text)
        return
    if suffix == ".json" and first_non_ws(path) == "[":
        yield from iter_json_array_objects(path)
        return
    for index, item in enumerate(list_payload(read_dataset(path))):
        yield index, item


def count_payload_items_from_path(path: Path) -> int | None:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return len(chenmo_markdown_items(path))
    if suffix not in {".json", ".jsonl", ".ndjson"}:
        return None
    try:
        return sum(1 for _index, _item in iter_payload_from_path(path))
    except Exception:
        return None


def compact(text: Any, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def token_estimate(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4) if text else 0


def pick(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    for key, value in lowered.items():
        if any(marker in key for marker in keys) and value not in (None, ""):
            return value
    return ""


def list_payload(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "examples", "items", "questions", "samples", "instances"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def event_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for key in EVENT_TEXT_KEYS:
            if value.get(key) not in (None, ""):
                item = value[key]
                if isinstance(item, list):
                    item = " ".join(compact(part, 500) for part in item)
                elif isinstance(item, dict):
                    item = compact(json.dumps(item, ensure_ascii=False, sort_keys=True), 900)
                parts.append(f"{key}: {item}")
        if parts:
            return " | ".join(parts)
    return compact(json.dumps(value, ensure_ascii=False, sort_keys=True), 600)


def collect_events(value: Any) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    if isinstance(value, str):
        return [{"time": "", "text": value}]
    if isinstance(value, list):
        for item in value:
            events.extend(collect_events(item))
        return events
    if not isinstance(value, dict):
        return []

    direct_text = event_text(value)
    if direct_text and direct_text != "{}":
        event_time = compact(pick(value, TIME_KEYS), 80)
        lowered_keys = {str(x).lower() for x in value}
        if any(k in lowered_keys for k in EVENT_TEXT_KEYS):
            events.append({"time": event_time, "text": direct_text})

    for key, child in value.items():
        lower = str(key).lower()
        if lower in EVENT_KEYS or any(marker in lower for marker in EVENT_KEYS):
            events.extend(collect_events(child))
    return events


def collect_locomo_conversation_events(conversation: dict[str, Any]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    keys = [key for key, value in conversation.items() if re.fullmatch(r"session_\d+", str(key)) and isinstance(value, list)]
    keys.sort(key=lambda key: int(str(key).split("_")[1]))
    for key in keys:
        session_time = compact(conversation.get(f"{key}_date_time", ""), 80)
        for message in conversation.get(key) or []:
            if not isinstance(message, dict):
                continue
            speaker = message.get("speaker") or message.get("role") or ""
            dia_id = message.get("dia_id") or key
            parts = []
            if message.get("text"):
                parts.append(str(message["text"]))
            if message.get("blip_caption"):
                parts.append(f"image: {message['blip_caption']}")
            if message.get("query"):
                parts.append(f"query: {message['query']}")
            if not parts:
                continue
            prefix = f"{speaker} {dia_id}:".strip()
            events.append({"time": session_time, "text": compact(f"{prefix} {' '.join(parts)}", 900)})
    return events


def parse_locomo_datetime(date_str: str) -> datetime | None:
    value = str(date_str or "").strip()
    if not value:
        return None
    for fmt in ("%I:%M %p on %d %B, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    if " on " in value:
        try:
            return datetime.strptime(value.split(" on ", 1)[1].strip(), "%d %B, %Y")
        except ValueError:
            return None
    return None


def get_locomo_sample_question_time(sample: dict[str, Any]) -> str:
    conversation = sample.get("conversation") or {}
    session_keys = [
        key for key in conversation
        if str(key).startswith("session_") and not str(key).endswith("_date_time")
    ]
    if not session_keys:
        return ""

    def session_no(key: Any) -> int:
        try:
            return int(str(key).replace("session_", ""))
        except ValueError:
            return 0

    for session_key in sorted(session_keys, key=session_no, reverse=True):
        if not conversation.get(session_key):
            continue
        dt = parse_locomo_datetime(str(conversation.get(f"{session_key}_date_time") or ""))
        if dt:
            return dt.strftime("%Y-%m-%d")
    return ""


def parse_longmemeval_datetime(date_str: str) -> datetime | None:
    value = str(date_str or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y/%m/%d (%a) %H:%M")
    except ValueError:
        return None


def locomo_memory_users(sample: dict[str, Any]) -> list[str]:
    conv = sample.get("conversation") or {}
    users: list[str] = []
    for key in ("speaker_a", "speaker_b"):
        value = str(conv.get(key) or "").strip()
        if value and value not in users:
            users.append(value)
    return users


def infer_format(path: Path, data: Any) -> str:
    name = path.name.lower()
    if "chenmo" in name or path.suffix.lower() in {".md", ".markdown"}:
        return "chenmo"
    if isinstance(data, list) and data and isinstance(data[0], dict) and "qa" in data[0] and "conversation" in data[0]:
        return "locomo"
    if "hotpot" in name:
        return "hotpotqa"
    if "proagent" in name:
        return "proagentbench"
    if "tau2" in name or "tau-bench" in name or "tau_bench" in name:
        return "tau2bench"
    if "longmem" in name:
        return "longmemeval"
    if "evolving" in name or "event" in name:
        return "evolvingevents"
    return "generic"


def locomo_jobs(
    data: list[dict[str, Any]],
    limit: int | None,
    sample_filter: str = "all",
    question_filter: set[str] | None = None,
) -> tuple[list[Job], list[dict[str, Any]]]:
    jobs: list[Job] = []
    plans: list[dict[str, Any]] = []
    for sample_index, sample in enumerate(data):
        sample_id = str(sample.get("sample_id") or f"sample_{sample_index}")
        if sample_filter not in ("", "all") and sample_filter not in {str(sample_index), sample_id}:
            continue
        matching_qas: list[tuple[int, dict[str, Any], str, str]] = []
        for q_index, qa in enumerate(sample.get("qa") or []):
            if str(qa.get("category", "")) == "5":
                continue
            question_id = f"{sample_id}_qa{q_index}"
            native_question_id = f"sample_{sample_index}_qa{q_index}"
            if question_filter and question_id not in question_filter and native_question_id not in question_filter:
                continue
            matching_qas.append((q_index, qa, question_id, native_question_id))
        if question_filter and not matching_qas:
            continue
        conv = sample.get("conversation") or {}
        events = collect_locomo_conversation_events(conv) or collect_events(conv)
        sample_question_time = get_locomo_sample_question_time(sample)
        speakers = locomo_memory_users(sample)
        memory_users = json.dumps(speakers, ensure_ascii=False)
        plans.append({
            "sample_id": sample_id,
            "event_count": len(events),
            "events": events,
            "preview_events": events[:20],
            "memory_users": speakers,
            "question_time": sample_question_time,
        })
        for q_index, qa, question_id, native_question_id in matching_qas:
            context_text = "\n".join(e["text"] for e in events[:12])
            jobs.append(
                Job(
                    dataset_format="locomo",
                    sample_id=sample_id,
                    question_id=question_id,
                    question=str(qa.get("question") or ""),
                    answer=str(qa.get("answer") or ""),
                    category=str(qa.get("category") or ""),
                    query_time=str(qa.get("question_time") or sample_question_time or ""),
                    injection_events=len(events),
                    injection_tokens_est=token_estimate(context_text),
                    context_preview=compact(context_text),
                    original_sample_id=sample_id,
                    question_index=str(q_index),
                    memory_users=memory_users,
                    native_question_id=native_question_id,
                )
            )
            if limit and len(jobs) >= limit:
                return jobs, plans
    return jobs, plans


def collect_longmemeval_events(item: dict[str, Any]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    sessions = item.get("haystack_sessions") or item.get("sessions") or item.get("conversation") or []
    dates = item.get("haystack_dates") or []
    session_ids = item.get("haystack_session_ids") or []
    if not isinstance(sessions, list):
        return collect_events(sessions)

    for session_index, session in enumerate(sessions):
        session_time = compact(dates[session_index] if session_index < len(dates) else "", 80)
        session_id = compact(session_ids[session_index] if session_index < len(session_ids) else f"session_{session_index}", 80)
        if isinstance(session, dict):
            messages = session.get("messages") or session.get("conversation") or session.get("turns") or []
        else:
            messages = session
        if not isinstance(messages, list):
            messages = [messages]
        for message_index, message in enumerate(messages):
            if isinstance(message, dict):
                role = str(message.get("role") or message.get("speaker") or message.get("user") or "message")
                content = str(message.get("content") or message.get("text") or message.get("message") or "")
            else:
                role = "message"
                content = str(message)
            content = compact(content, 1200)
            if not content:
                continue
            text = f"{session_id} turn_{message_index} {role}: {content}"
            events.append({"time": session_time, "text": compact(text, 1400)})
    return events


def collect_longmemeval_session_batches(item: dict[str, Any]) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    sessions = item.get("haystack_sessions") or item.get("sessions") or item.get("conversation") or []
    dates = item.get("haystack_dates") or []
    session_ids = item.get("haystack_session_ids") or []
    if not isinstance(sessions, list):
        return batches

    for session_index, session in enumerate(sessions):
        session_time = compact(dates[session_index] if session_index < len(dates) else "", 80)
        session_id = compact(session_ids[session_index] if session_index < len(session_ids) else f"session_{session_index}", 120)
        session_dt = parse_longmemeval_datetime(session_time)
        messages = session.get("messages") or session.get("conversation") or session.get("turns") or [] if isinstance(session, dict) else session
        if not isinstance(messages, list):
            messages = [messages]
        rows: list[dict[str, Any]] = []
        for message_index, message in enumerate(messages):
            if isinstance(message, dict):
                role = str(message.get("role") or message.get("speaker") or message.get("user") or "user").strip() or "user"
                content = str(message.get("content") or message.get("text") or message.get("message") or "").strip()
            else:
                role = "user"
                content = str(message).strip()
            if not content:
                continue
            rows.append(
                {
                    "role": role,
                    "content": content,
                    "parts": [{"type": "text", "text": content}],
                    "speaker": role,
                    "dia_id": f"{session_id}:{message_index}",
                    "created_at": (
                        (session_dt.replace(second=0, microsecond=0)).isoformat()
                        if session_dt is not None
                        else None
                    ),
                }
            )
            if session_dt is not None:
                rows[-1]["created_at"] = session_dt.replace(second=0, microsecond=0).isoformat()
                session_dt = session_dt.replace(second=0, microsecond=0) + timedelta(seconds=1)
        if rows:
            batches.append(
                {
                    "session_key": session_id or f"session_{session_index}",
                    "date_time": session_time,
                    "messages": rows,
                }
            )
    return batches


def collect_longmemeval_documents(item: dict[str, Any]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    sessions = item.get("haystack_sessions") or item.get("sessions") or item.get("conversation") or []
    dates = item.get("haystack_dates") or []
    session_ids = item.get("haystack_session_ids") or []
    if not isinstance(sessions, list):
        return []

    for session_index, session in enumerate(sessions):
        session_time = compact(dates[session_index] if session_index < len(dates) else "", 80)
        session_id = compact(session_ids[session_index] if session_index < len(session_ids) else f"session_{session_index}", 120)
        messages = session.get("messages") or session.get("conversation") or session.get("turns") or [] if isinstance(session, dict) else session
        if not isinstance(messages, list):
            messages = [messages]
        lines = [
            f"source_dataset: LongMemEval",
            f"session_id: {session_id}",
            f"time: {session_time or '-'}",
            "",
            "Conversation turns:",
        ]
        for message_index, message in enumerate(messages):
            if isinstance(message, dict):
                role = str(message.get("role") or message.get("speaker") or message.get("user") or "message")
                content = str(message.get("content") or message.get("text") or message.get("message") or "")
            else:
                role = "message"
                content = str(message)
            content = compact(content, 2200)
            if content:
                lines.append(f"turn_{message_index} {role}: {content}")
        if len(lines) > 5:
            documents.append({
                "doc_id": session_id or f"session_{session_index}",
                "title": session_id or f"session_{session_index}",
                "time": session_time,
                "text": "\n".join(lines),
            })
    return documents


def collect_evolvingevents_events(item: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    raw_events = item.get("events") or item.get("timeline") or []
    if not isinstance(raw_events, list):
        return rows
    for raw in raw_events:
        if isinstance(raw, dict):
            event_time = str(raw.get("timestamp") or raw.get("time") or raw.get("date") or "").strip()
            text = str(raw.get("event") or raw.get("text") or raw.get("description") or raw.get("content") or "").strip()
        else:
            event_time = ""
            text = str(raw).strip()
        if text:
            rows.append({"time": event_time, "text": text})
    return rows


def longmemeval_jobs(data: Any, limit: int | None, sample_filter: str = "all") -> tuple[list[Job], list[dict[str, Any]]]:
    jobs: list[Job] = []
    plans: list[dict[str, Any]] = []
    for index, raw in enumerate(list_payload(data)):
        built = longmemeval_job_plan(raw, index, sample_filter)
        if built is None:
            continue
        job, plan = built
        jobs.append(job)
        plans.append(plan)
        if limit and len(jobs) >= limit:
            break
    return jobs, plans


def longmemeval_job_plan(raw: Any, index: int, sample_filter: str = "all") -> tuple[Job, dict[str, Any]] | None:
    item = raw if isinstance(raw, dict) else {"input": raw}
    sample_id = str(item.get("question_id") or item.get("sample_id") or item.get("id") or f"longmemeval_{index}")
    if sample_filter not in ("", "all") and sample_filter not in {str(index), sample_id}:
        return None
    question = str(item.get("question") or item.get("query") or "")
    answer = str(item.get("answer") or item.get("gold_answer") or item.get("target") or "")
    category = str(item.get("question_type") or item.get("category") or "longmemeval")
    query_time = str(item.get("question_date") or item.get("query_time") or item.get("question_time") or "")
    events = collect_longmemeval_events(item)
    if not events:
        events = collect_events(item)
    memory_documents = collect_longmemeval_documents(item)
    context_text = "\n".join(e["text"] for e in events[:12])
    plan = {
        "sample_id": sample_id,
        "event_count": len(events),
        "events": events,
        "preview_events": events[:20],
        "memory_documents": memory_documents,
        "session_batches": collect_longmemeval_session_batches(item),
    }
    job = Job(
        dataset_format="longmemeval",
        sample_id=sample_id,
        question_id=sample_id,
        question=question,
        answer=answer,
        category=category,
        query_time=query_time,
        injection_events=len(events),
        injection_tokens_est=token_estimate(context_text),
        context_preview=compact(context_text),
    )
    return job, plan


def _hotpot_support_title_set(supporting_facts: Any) -> set[str]:
    titles: set[str] = set()
    if isinstance(supporting_facts, dict):
        raw_titles = supporting_facts.get("title") or supporting_facts.get("titles") or []
        if isinstance(raw_titles, list):
            titles.update(str(title).strip() for title in raw_titles if str(title).strip())
        return titles
    if not isinstance(supporting_facts, list):
        return titles
    for item in supporting_facts:
        if isinstance(item, (list, tuple)) and item:
            title = str(item[0]).strip()
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("document") or "").strip()
        else:
            title = ""
        if title:
            titles.add(title)
    return titles


def _hotpot_context_pairs(context: Any) -> list[tuple[str, list[str]]]:
    pairs: list[tuple[str, list[str]]] = []
    if isinstance(context, dict):
        titles = context.get("title") or context.get("titles") or []
        sentences = context.get("sentences") or context.get("sentence") or []
        if isinstance(titles, list) and isinstance(sentences, list):
            for index, title in enumerate(titles):
                raw_sentences = sentences[index] if index < len(sentences) else []
                if isinstance(raw_sentences, str):
                    sent_list = [raw_sentences]
                elif isinstance(raw_sentences, list):
                    sent_list = [str(sentence) for sentence in raw_sentences if str(sentence).strip()]
                else:
                    sent_list = [compact(raw_sentences, 500)] if raw_sentences else []
                pairs.append((str(title), sent_list))
        return pairs
    if not isinstance(context, list):
        return pairs
    for item in context:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or item.get("document") or "")
            raw_sentences = item.get("sentences") or item.get("sentence") or item.get("text") or item.get("content") or []
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            title = str(item[0])
            raw_sentences = item[1]
        else:
            title = ""
            raw_sentences = item
        if isinstance(raw_sentences, str):
            sent_list = [raw_sentences]
        elif isinstance(raw_sentences, list):
            sent_list = [str(sentence) for sentence in raw_sentences if str(sentence).strip()]
        else:
            sent_list = [compact(raw_sentences, 500)] if raw_sentences else []
        if title or sent_list:
            pairs.append((title, sent_list))
    return pairs


def collect_hotpotqa_events(item: dict[str, Any]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for index, (title, sentences) in enumerate(_hotpot_context_pairs(item.get("context")), 1):
        body = " ".join(compact(sentence, 900) for sentence in sentences if str(sentence).strip())
        if not body and not title:
            continue
        prefix = f"{title}:".strip()
        events.append({"time": "", "text": compact(f"document_{index} {prefix} {body}", 1600)})
    return events


def collect_hotpotqa_documents(item: dict[str, Any]) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for index, (title, sentences) in enumerate(_hotpot_context_pairs(item.get("context")), 1):
        body = " ".join(compact(sentence, 1400) for sentence in sentences if str(sentence).strip())
        if not body and not title:
            continue
        doc_title = title or f"document_{index}"
        documents.append({
            "doc_id": f"document_{index}_{doc_title}",
            "title": doc_title,
            "time": "",
            "text": "\n".join([
                "source_dataset: HotpotQA",
                f"title: {doc_title}",
                "",
                body,
            ]).strip(),
        })
    return documents


def hotpotqa_jobs(data: Any, limit: int | None, sample_filter: str = "all") -> tuple[list[Job], list[dict[str, Any]]]:
    jobs: list[Job] = []
    plans: list[dict[str, Any]] = []
    for index, raw in enumerate(list_payload(data)):
        built = hotpotqa_job_plan(raw, index, sample_filter)
        if built is None:
            continue
        job, plan = built
        jobs.append(job)
        plans.append(plan)
        if limit and len(jobs) >= limit:
            break
    return jobs, plans


def hotpotqa_job_plan(raw: Any, index: int, sample_filter: str = "all") -> tuple[Job, dict[str, Any]] | None:
    item = raw if isinstance(raw, dict) else {"input": raw}
    sample_id = str(pick(item, ID_KEYS) or f"hotpotqa_{index}")
    if sample_filter not in ("", "all") and sample_filter not in {str(index), sample_id}:
        return None
    events = collect_hotpotqa_events(item) or collect_events(item)
    memory_documents = collect_hotpotqa_documents(item)
    context_text = "\n".join(e["text"] for e in events[:12])
    category_parts = [str(item.get("type") or item.get("category") or "hotpotqa")]
    if item.get("level"):
        category_parts.append(str(item.get("level")))
    category = "/".join(part for part in category_parts if part)
    plan = {
        "sample_id": sample_id,
        "event_count": len(events),
        "events": events,
        "preview_events": events[:20],
        "memory_documents": memory_documents,
        "supporting_facts": item.get("supporting_facts") or [],
        "type": str(item.get("type") or item.get("category") or "hotpotqa"),
        "level": str(item.get("level") or "").strip(),
        "has_answer": bool(str(item.get("answer") or item.get("gold_answer") or "").strip()),
    }
    job = Job(
        dataset_format="hotpotqa",
        sample_id=sample_id,
        question_id=sample_id,
        question=str(item.get("question") or item.get("query") or ""),
        answer=str(item.get("answer") or item.get("gold_answer") or ""),
        category=category,
        query_time=str(pick(item, TIME_KEYS) or ""),
        injection_events=len(events),
        injection_tokens_est=token_estimate(context_text),
        context_preview=compact(context_text),
        native_question_id=sample_id,
    )
    return job, plan


def generic_jobs(fmt: str, data: Any, limit: int | None, sample_filter: str = "all") -> tuple[list[Job], list[dict[str, Any]]]:
    jobs: list[Job] = []
    plans: list[dict[str, Any]] = []
    for index, item in enumerate(list_payload(data)):
        built = generic_job_plan(fmt, item, index, sample_filter)
        if built is None:
            continue
        job, plan = built
        jobs.append(job)
        plans.append(plan)
        if limit and len(jobs) >= limit:
            break
    return jobs, plans


def generic_job_plan(fmt: str, raw: Any, index: int, sample_filter: str = "all") -> tuple[Job, dict[str, Any]] | None:
    item = raw if isinstance(raw, dict) else {"input": raw}
    sample_id = str(pick(item, ID_KEYS) or f"{fmt}_{index}")
    if sample_filter not in ("", "all") and sample_filter not in {str(index), sample_id}:
        return None
    question = str(pick(item, QUESTION_KEYS) or "")
    answer = str(pick(item, ANSWER_KEYS) or "")
    query_time = str(pick(item, TIME_KEYS) or "")
    if fmt == "evolvingevents":
        events = collect_evolvingevents_events(item)
    else:
        events = collect_events(item)
    if not events:
        for key, value in item.items():
            if str(key).lower() not in QUESTION_KEYS + ANSWER_KEYS:
                if isinstance(value, (str, list, dict)):
                    events.extend(collect_events(value))
    context_text = "\n".join(e["text"] for e in events[:12])
    plan = {"sample_id": sample_id, "event_count": len(events), "events": events, "preview_events": events[:20]}
    job = Job(
        dataset_format=fmt,
        sample_id=sample_id,
        question_id=str(pick(item, ("question_id", "qid", "id")) or f"{sample_id}_q0"),
        question=question,
        answer=answer,
        category=str(item.get("category") or item.get("type") or fmt),
        query_time=query_time,
        injection_events=len(events),
        injection_tokens_est=token_estimate(context_text),
        context_preview=compact(context_text),
    )
    return job, plan


def write_csv(path: Path, jobs: list[Job]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(jobs[0]).keys()) if jobs else [field.name for field in Job.__dataclass_fields__.values()])
        writer.writeheader()
        for job in jobs:
            writer.writerow(asdict(job))


def event_content(dataset_format: str, sample_id: str, namespace: str, events: list[dict[str, str]]) -> str:
    lines = [
        f"dataset_format: {dataset_format}",
        f"sample_id: {sample_id}",
        f"namespace: {namespace}",
        "",
        "Memory events:",
    ]
    for index, event in enumerate(events, 1):
        prefix = f"{index}."
        if event.get("time"):
            prefix += f" [{event['time']}]"
        lines.append(f"{prefix} {event.get('text') or ''}")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize memory benchmark datasets and emit dry-run jobs for backend adapters.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--format", default="auto", choices=["auto", "locomo", "longmemeval", "evolvingevents", "hotpotqa", "proagentbench", "tau2bench", "chenmo", "generic"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--memory-mode", default="read_only_recommended")
    parser.add_argument("--namespace", default="")
    parser.add_argument("--mode", default="dry-run", choices=["dry-run"])
    parser.add_argument("--timeout-s", type=int, default=180)
    args = parser.parse_args()

    dataset_path = Path(args.dataset).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data = read_dataset(dataset_path) if dataset_path.suffix.lower() in {".json", ".jsonl", ".ndjson", ".md", ".markdown"} else []
    fmt = infer_format(dataset_path, data) if args.format == "auto" else args.format
    limit = args.count or None
    if fmt == "locomo":
        jobs, plans = locomo_jobs(data, limit)
    elif fmt == "longmemeval":
        jobs, plans = longmemeval_jobs(data, limit)
    elif fmt == "hotpotqa":
        jobs, plans = hotpotqa_jobs(data, limit)
    else:
        jobs, plans = generic_jobs(fmt, data, limit)

    output_csv = out_dir / "benchmark_adapter_results.csv"
    namespace = args.namespace or f"{fmt}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    write_csv(output_csv, jobs)
    injection_plan = {
        "mode": args.mode,
        "dataset": str(dataset_path),
        "dataset_format": fmt,
        "namespace": namespace,
        "memory_safety_mode": args.memory_mode,
        "jobs": len(jobs),
        "samples": len(plans),
        "total_injection_events": sum(item["event_count"] for item in plans),
        "pollution_guard": {
            "write_to_memory_backend": False,
            "guard_reason": "dry-run planner only; use OpenViking or EchoMemory adapter tasks for real import and QA",
            "requires_isolated_workspace_for_real_injection": True,
            "requires_isolated_graph_or_collection_for_real_injection": True,
            "recommended_namespace": namespace,
        },
        "samples_preview": plans[:20],
    }
    (out_dir / "injection_plan.json").write_text(json.dumps(injection_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "count": len(jobs),
        "correct": 0,
        "wrong": 0,
        "accuracy": None,
        "graded": 0,
        "status": "NEEDS_BACKEND_ADAPTER",
        "dataset_format": fmt,
        "output_csv": str(output_csv),
        "injection_plan": str(out_dir / "injection_plan.json"),
        "guard_reason": "dry-run planner only",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
