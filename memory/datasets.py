from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import benchmark_adapter
except ModuleNotFoundError:
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import benchmark_adapter


DEFAULT_SCAN_LIMIT_BYTES = 96 * 1024 * 1024


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
        return benchmark_adapter.read_dataset(path)
    return read_json(path)


def _candidate_format(path: Path, candidates: list[dict[str, Any]] | None = None) -> str | None:
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        resolved = path.expanduser()
    for candidate in candidates or []:
        try:
            candidate_path = Path(str(candidate.get("path") or "")).expanduser().resolve()
        except Exception:
            continue
        if candidate_path == resolved:
            return str(candidate.get("format") or "")
    return None


def looks_like_locomo_data(data: Any) -> bool:
    if not isinstance(data, list) or not data:
        return False
    sample = data[0]
    return isinstance(sample, dict) and isinstance(sample.get("qa"), list) and isinstance(sample.get("conversation"), dict)


def infer_dataset_format(path: Path, data: Any | None = None, candidates: list[dict[str, Any]] | None = None) -> str:
    candidate_format = _candidate_format(path, candidates)
    if candidate_format:
        return candidate_format
    if data is None and path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
        try:
            data = read_dataset(path)
        except Exception:
            data = None
    if looks_like_locomo_data(data):
        return "locomo"
    name = path.name.lower()
    if "chenmo" in name or path.suffix.lower() in {".md", ".markdown"}:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if "陈默" in text and "推理问题集" in text:
                return "chenmo"
        except Exception:
            pass
    if "longmem" in name:
        return "longmemeval"
    if "hotpot" in name:
        return "hotpotqa"
    if "proagent" in name:
        return "proagentbench"
    if "tau2" in name or "tau-bench" in name or "tau_bench" in name:
        return "tau2bench"
    if "evolving" in name or "event" in name:
        return "evolvingevents"
    return "generic"


def chenmo_overview_from_markdown(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    turns_match = re.search(r'"turns"\s*:\s*\[(.*?)\n\s*\]', text, re.S)
    turns_text = turns_match.group(1) if turns_match else ""
    turns = len(re.findall(r'\("(?:user|assistant)"\s*,', turns_text))
    question_rows = re.findall(r"^\|\s*([A-Z]+\d+)\s*\|", text, re.M)
    categories: dict[str, int] = {}
    current = ""
    section_re = re.compile(r"^###\s+(.+?)\s*$")
    for line in text.splitlines():
        m = section_re.match(line)
        if m:
            current = m.group(1).strip()
            continue
        q = re.match(r"^\|\s*([A-Z]+\d+)\s*\|", line)
        if q and current:
            categories[current] = categories.get(current, 0) + 1
    return {
        "path": str(path),
        "samples": 1,
        "questions": len(question_rows),
        "categories": categories,
        "sample_rows": [
            {
                "index": 0,
                "sample_id": "chenmo",
                "questions": len(question_rows),
                "sessions": 1,
                "events": turns,
            }
        ],
        "memory_events_total": turns,
    }


def locomo_overview_from_data(path: Path, data: Any) -> dict[str, Any]:
    categories: dict[str, int] = {}
    samples = []
    question_total = 0
    for idx, sample in enumerate(data if isinstance(data, list) else []):
        qa = [q for q in sample.get("qa", []) if str(q.get("category", "")) != "5"]
        question_total += len(qa)
        for q in qa:
            cat = str(q.get("category", ""))
            categories[cat] = categories.get(cat, 0) + 1
        conv = sample.get("conversation", {})
        samples.append(
            {
                "index": idx,
                "sample_id": sample.get("sample_id", f"sample_{idx}"),
                "speakers": [conv.get("speaker_a", ""), conv.get("speaker_b", "")],
                "questions": len(qa),
                "sessions": len([k for k in conv if str(k).startswith("session_") and not str(k).endswith("_date_time")]),
            }
        )
    return {
        "path": str(path),
        "samples": len(data) if isinstance(data, list) else 0,
        "questions": question_total,
        "categories": categories,
        "sample_rows": samples,
    }


def _generic_items_overview(items: list[Any], fmt: str) -> dict[str, Any]:
    rows = []
    categories: dict[str, int] = {}
    for index, raw in enumerate(items):
        item = raw if isinstance(raw, dict) else {"input": raw}
        if fmt == "longmemeval":
            sample_id = str(item.get("question_id") or item.get("sample_id") or item.get("id") or f"longmemeval_{index}")
            category = str(item.get("question_type") or item.get("category") or "longmemeval")
            events = benchmark_adapter.collect_longmemeval_events(item)
            questions = 1 if (item.get("question") or item.get("query")) else 0
        elif fmt == "hotpotqa":
            sample_id = str(benchmark_adapter.pick(item, benchmark_adapter.ID_KEYS) or f"hotpotqa_{index}")
            category = "/".join(str(value) for value in [item.get("type") or item.get("category") or "hotpotqa", item.get("level") or ""] if value)
            events = benchmark_adapter.collect_hotpotqa_events(item)
            questions = 1 if item.get("question") else 0
        else:
            sample_id = str(benchmark_adapter.pick(item, benchmark_adapter.ID_KEYS) or f"{fmt}_{index}")
            category = str(item.get("category") or item.get("type") or fmt)
            events = benchmark_adapter.collect_events(item)
            questions = 1 if benchmark_adapter.pick(item, benchmark_adapter.QUESTION_KEYS) else 0
        if category:
            categories[category] = categories.get(category, 0) + 1
        rows.append({"index": index, "sample_id": sample_id, "questions": questions, "events": len(events), "sessions": len(events)})
    return {
        "samples": len(items),
        "questions": sum(row["questions"] for row in rows),
        "categories": categories,
        "sample_rows": rows,
        "memory_events_total": sum(int(row.get("events") or 0) for row in rows),
        "preview_events_per_sample": 20,
    }


def generic_data_overview(path: Path, loaded: Any | None = None, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
        data = loaded if loaded is not None else read_dataset(path)
        fmt = infer_dataset_format(path, data, candidates)
        if isinstance(data, list):
            return _generic_items_overview(data, fmt)
        if isinstance(data, dict):
            for key in ["data", "examples", "items", "questions"]:
                if isinstance(data.get(key), list):
                    return _generic_items_overview(data[key], fmt)
            return {"samples": len(data), "questions": len(data), "categories": {}, "sample_rows": []}
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            count = sum(1 for _ in csv.DictReader(f))
        return {"samples": count, "questions": count, "categories": {}, "sample_rows": []}
    return {"samples": 0, "questions": 0, "categories": {}, "sample_rows": []}


def dataset_overview(
    path: Path,
    candidates: list[dict[str, Any]] | None = None,
    scan_limit_bytes: int = DEFAULT_SCAN_LIMIT_BYTES,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cache = cache if cache is not None else {}
    try:
        stat = path.stat()
        cache_key = str(path.resolve())
        cached = cache.get(cache_key)
        if cached and cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("size") == stat.st_size:
            return dict(cached["data"])
        if stat.st_size > scan_limit_bytes:
            fmt = infer_dataset_format(path, None, candidates)
            overview = {
                "path": str(path),
                "samples": "?",
                "questions": "?",
                "categories": {},
                "sample_rows": [],
                "format": fmt,
                "runner_status": "large_dataset_lazy",
                "runner_note": f"文件较大（{round(stat.st_size / 1024 / 1024, 1)} MB）；页面概览不会全量扫描，正式任务会完整读取 memory events。可直接用 count=100 跑抽样测试。",
            }
            cache[cache_key] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "data": dict(overview)}
            return overview
    except Exception:
        stat = None
        cache_key = str(path.resolve())

    data = read_dataset(path) if path.suffix.lower() in {".json", ".jsonl", ".ndjson"} else None
    fmt = infer_dataset_format(path, data, candidates)
    if fmt == "chenmo":
        overview = chenmo_overview_from_markdown(path)
    else:
        overview = locomo_overview_from_data(path, data) if fmt == "locomo" and looks_like_locomo_data(data) else generic_data_overview(path, data, candidates)
    overview["format"] = fmt
    overview["runner_status"] = "ready"
    overview["runner_note"] = (
        "已完成 LoCoMo 结构校验；请选择要导入的 conversation，确认记忆空间目录，然后点击“导入所选对话”。"
        if fmt == "locomo"
        else "已完成 ChenMo 结构校验；EchoMemory 正式结果需要使用 version_0.0.6 重新运行后展示。"
        if fmt == "chenmo"
        else "已完成结构校验；页面会按数据集格式启用可用评测入口。"
    )
    if stat is not None:
        cache[cache_key] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "data": dict(overview)}
    return overview


def context_pack_preview(path: Path, limit: int = 8, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data = read_dataset(path) if path.suffix.lower() in {".json", ".jsonl", ".ndjson"} else []
    fmt = infer_dataset_format(path, data, candidates)
    if fmt == "locomo" and looks_like_locomo_data(data):
        jobs, plans = benchmark_adapter.locomo_jobs(data, limit)
    elif fmt == "longmemeval":
        jobs, plans = benchmark_adapter.longmemeval_jobs(data, limit)
    elif fmt == "hotpotqa":
        jobs, plans = benchmark_adapter.hotpotqa_jobs(data, limit)
    else:
        jobs, plans = benchmark_adapter.generic_jobs(fmt, data, limit)
    by_sample: dict[str, dict[str, Any]] = {}
    for plan in plans:
        sample_id = str(plan.get("sample_id") or "")
        by_sample[sample_id] = {
            "sample_id": sample_id,
            "event_count": plan.get("event_count", 0),
            "events": (plan.get("events") or [])[:8],
            "questions": [],
        }
    for job in jobs:
        by_sample.setdefault(
            job.sample_id,
            {"sample_id": job.sample_id, "event_count": job.injection_events, "events": [], "questions": []},
        )
        by_sample[job.sample_id]["questions"].append(
            {
                "question_id": job.question_id,
                "question": job.question,
                "answer": job.answer,
                "category": job.category,
                "query_time": job.query_time,
                "context_preview": job.context_preview,
            }
        )
    return {
        "dataset": str(path),
        "format": fmt,
        "jobs": len(jobs),
        "samples": len(plans),
        "total_events": sum(int(plan.get("event_count") or 0) for plan in plans),
        "pollution_guard": {
            "external_memory_write": False,
            "write_to_openviking": False,
            "storage": "browser preview only; task run writes local run directory only",
        },
        "sample_packs": list(by_sample.values())[:limit],
    }


def locomo_questions(path: Path, sample_filter: str = "all") -> dict[str, Any]:
    data = read_dataset(path)
    if not looks_like_locomo_data(data):
        return {"questions": []}
    rows = []
    for sample_index, sample in enumerate(data):
        sample_id = str(sample.get("sample_id") or f"sample_{sample_index}")
        if sample_filter not in ("", "all") and sample_filter not in {str(sample_index), sample_id}:
            continue
        for q_index, qa in enumerate(sample.get("qa") or []):
            if str(qa.get("category", "")) == "5":
                continue
            rows.append(
                {
                    "sample_index": sample_index,
                    "sample_id": sample_id,
                    "question_index": q_index,
                    "question_id": f"{sample_id}_qa{q_index}",
                    "question": str(qa.get("question") or ""),
                    "answer": str(qa.get("answer") or ""),
                    "category": str(qa.get("category") or ""),
                    "question_time": str(qa.get("question_time") or ""),
                    "evidence": qa.get("evidence") or [],
                }
            )
    return {"path": str(path), "sample": sample_filter, "count": len(rows), "questions": rows}


def benchmark_questions(path: Path, sample_filter: str = "all", limit: int = 2000, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    fmt = infer_dataset_format(path, candidates=candidates)
    data = read_dataset(path)
    if fmt == "generic":
        fmt = infer_dataset_format(path, data, candidates)
    if fmt == "locomo" and looks_like_locomo_data(data):
        return locomo_questions(path, sample_filter)
    if fmt == "longmemeval":
        jobs, _plans = benchmark_adapter.longmemeval_jobs(data, limit, sample_filter)
    elif fmt == "hotpotqa":
        jobs, _plans = benchmark_adapter.hotpotqa_jobs(data, limit, sample_filter)
    else:
        jobs, _plans = benchmark_adapter.generic_jobs(fmt, data, limit, sample_filter)
    rows = []
    for index, job in enumerate(jobs):
        rows.append(
            {
                "sample_index": index,
                "sample_id": job.sample_id,
                "question_index": 0,
                "question_id": job.question_id,
                "question": job.question,
                "answer": job.answer,
                "category": job.category,
                "question_time": job.query_time,
                "evidence": [],
            }
        )
    return {"path": str(path), "sample": sample_filter, "format": fmt, "count": len(rows), "questions": rows}


def iter_json_array_objects(path: Path, offset: int, limit: int):
    decoder = json.JSONDecoder()
    index = -1
    buf = ""
    in_array = False
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
                    if index + 1 >= offset + limit:
                        return
                pos = end
            buf = buf[pos:]
            if not chunk:
                break


def benchmark_questions_page(
    path: Path,
    offset: int = 0,
    limit: int = 100,
    query: str = "",
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    offset = max(0, int(offset or 0))
    limit = max(1, min(500, int(limit or 100)))
    fmt = infer_dataset_format(path, candidates=candidates)
    query_text = query.strip().lower()
    rows = []
    next_offset: int | None = None
    if fmt == "longmemeval" and path.suffix.lower() == ".json":
        matched = 0
        for index, item in iter_json_array_objects(path, offset, limit + 1):
            raw = item if isinstance(item, dict) else {"input": item}
            sample_id = str(raw.get("question_id") or raw.get("sample_id") or raw.get("id") or f"longmemeval_{index}")
            question = str(raw.get("question") or raw.get("query") or "")
            answer = str(raw.get("answer") or raw.get("gold_answer") or raw.get("target") or "")
            category = str(raw.get("question_type") or raw.get("category") or "longmemeval")
            question_time = str(raw.get("question_date") or raw.get("query_time") or raw.get("question_time") or "")
            haystack = " ".join([sample_id, question, answer, category, question_time]).lower()
            if query_text and query_text not in haystack:
                continue
            if matched >= limit:
                next_offset = index
                break
            rows.append(
                {
                    "sample_index": index,
                    "sample_id": sample_id,
                    "question_index": 0,
                    "question_id": sample_id,
                    "question": question,
                    "answer": answer,
                    "category": category,
                    "question_time": question_time,
                    "evidence": raw.get("answer_session_ids") or [],
                }
            )
            matched += 1
        return {
            "path": str(path),
            "format": fmt,
            "offset": offset,
            "limit": limit,
            "next_offset": next_offset,
            "count": len(rows),
            "query": query,
            "questions": rows,
        }

    data = benchmark_questions(path, "all", offset + limit, candidates)
    all_rows = data.get("questions") or []
    if query_text:
        all_rows = [
            row
            for row in all_rows
            if query_text
            in " ".join(
                [
                    str(row.get("question_id") or ""),
                    str(row.get("question") or ""),
                    str(row.get("answer") or ""),
                    str(row.get("category") or ""),
                    str(row.get("question_time") or ""),
                ]
            ).lower()
        ]
    rows = all_rows[offset : offset + limit]
    if offset + limit < len(all_rows):
        next_offset = offset + limit
    return {
        "path": str(path),
        "format": data.get("format") or fmt,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "count": len(rows),
        "query": query,
        "questions": rows,
    }


def csv_wrong_question_ids(csv_path: Path) -> dict[str, Any]:
    if not csv_path.exists():
        raise FileNotFoundError(str(csv_path))
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8", errors="replace")))
    question_ids: list[str] = []
    examples: list[dict[str, str]] = []
    for row in rows:
        grade = (row.get("result") or row.get("simple_grade") or row.get("simple_match") or "").upper()
        if grade != "WRONG":
            continue
        qid = row.get("question_id") or ""
        if not qid or qid in question_ids:
            continue
        question_ids.append(qid)
        if len(examples) < 12:
            examples.append(
                {
                    "question_id": qid,
                    "sample_id": row.get("sample_id", ""),
                    "category": row.get("category", ""),
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                    "response": row.get("response", ""),
                    "reasoning": row.get("reasoning", ""),
                }
            )
    return {"csv": str(csv_path), "count": len(question_ids), "question_ids": question_ids, "examples": examples}


def is_time_question(question: dict[str, Any]) -> bool:
    category = str(question.get("category") or "")
    text = str(question.get("question") or "").lower()
    answer = str(question.get("answer") or "").lower()
    if category == "2":
        return True
    markers = ["when", "what date", "which date", "what day", "how long", "how many months", "how many weeks", "how many days"]
    month_markers = "january february march april may june july august september october november december".split()
    return any(marker in text for marker in markers) or any(month in answer for month in month_markers) or bool(re.search(r"\b20\d{2}\b", answer))


def question_set(path: Path, mode: str, csv_path: Path | None = None, sample: str = "all") -> dict[str, Any]:
    mode = mode or "time"
    if mode == "wrong_csv":
        if csv_path is None:
            raise FileNotFoundError("missing csv")
        result = csv_wrong_question_ids(csv_path)
        result.update({"mode": mode, "runner_hint": "local_agent"})
        return result
    if mode == "time":
        data = locomo_questions(path, sample)
        selected = [row for row in data.get("questions", []) if is_time_question(row)]
        return {
            "mode": mode,
            "path": str(path),
            "sample": sample,
            "count": len(selected),
            "question_ids": [row["question_id"] for row in selected],
            "examples": selected[:12],
            "runner_hint": "local_agent",
        }
    raise ValueError(f"unknown question set mode: {mode}")
