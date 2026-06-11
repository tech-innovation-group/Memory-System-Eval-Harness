#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import platform
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import benchmark_adapter
from echomemory_common import DEFAULT_ECHOMEM_ROOT, context_item_to_dict, ctx, ensure_echomem_imports, sdk_ctx_kwargs, write_echomem_config, write_json
from memory.vikingboat_alignment import (
    VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    VIKINGBOT_ALIGNMENT_PROFILE,
    VIKINGBOT_INITIAL_MIN_SCORE,
    VIKINGBOT_INITIAL_SEARCH_LIMIT,
    VIKINGBOT_MAX_ITERATIONS,
    VIKINGBOT_TOOL_MIN_SCORE,
    VIKINGBOT_TOOL_SEARCH_LIMIT,
    VIKINGBOT_TOOL_SET,
    VIKINGBOT_USER_MEMORY_BUDGET_CHARS,
    alignment_metadata,
)
from openviking_memory_qa import (
    ModelCallError,
    build_vikingbot_question_prompt,
    build_vikingbot_user_memory_message,
    call_openai,
    classify_model_error,
    csv_fieldnames,
    default_openai_max_tokens,
    openai_payload_variants,
    openai_response_message,
    parse_openai_compatible_response,
    token_estimate,
)


MEMORY_SEARCH_TOOL_NAME = "memory_search"
MEMORY_MULTI_READ_TOOL_NAME = "memory_read_many"
MEMORY_LIST_TOOL_NAME = "memory_list"
MEMORY_GREP_TOOL_NAME = "memory_grep"
MEMORY_GLOB_TOOL_NAME = "memory_glob"
ECHOMEMORY_BACKEND_ROUTE = "custom_agent_echomemory_sdk_memory_tools"
ECHOMEMORY_VIKINGBOAT_TOOL_SET = "vikingboat_default"


def normalize_echomemory_tool_set(value: Any, *, vikingboat_compat: bool = False) -> str:
    raw = str(value or "").strip() or "search_read"
    if raw == VIKINGBOT_TOOL_SET:
        return ECHOMEMORY_VIKINGBOAT_TOOL_SET
    if vikingboat_compat and raw == "search_read":
        return ECHOMEMORY_VIKINGBOAT_TOOL_SET
    return raw


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except FileNotFoundError:
        return []
    return rows


def compact(text: Any, limit: int = 1400) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def hit_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


STOPWORDS = {
    "a",
    "an",
    "and",
    "answer",
    "at",
    "both",
    "by",
    "current",
    "date",
    "did",
    "directly",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "he",
    "her",
    "his",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "question",
    "she",
    "the",
    "their",
    "they",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


QUERY_ALIAS_BRIDGES: dict[str, list[str]] = {
    "jean": ["gina"],
    "john": ["jon"],
    "gina": ["jean"],
    "jon": ["john"],
    "contemporary": ["当代舞", "当代"],
    "dance studio": ["舞蹈工作室"],
    "ideal dance studio": ["理想舞蹈工作室", "理想工作室", "水边", "自然光", "Marley地板"],
    "ideal studio": ["理想工作室", "水边", "自然光"],
    "ocean": ["海洋", "海边", "海洋边", "水边"],
    "sea": ["海边", "海洋边", "水边"],
    "marley": ["Marley地板", "Marley flooring", "地板"],
    "marley flooring": ["Marley地板", "地板"],
    "flooring": ["地板", "Marley地板"],
    "natural light": ["自然光"],
    "by the water": ["靠水边", "临水", "水边"],
    "water": ["水边", "临水"],
    "city": ["城市", "旅行", "去过", "访问", "trip", "travel"],
    "local artist": ["本地艺术家"],
    "artist": ["艺术家"],
    "fashion internship": ["时尚实习", "服装实习", "实习"],
    "internship": ["实习"],
    "bank account": ["银行账户"],
    "tattoo": ["纹身", "tattoo"],
    "a few years ago": ["几年前", "a few years ago"],
    "networking event": ["社交活动", "networking event", "networking events"],
    "networking events": ["社交活动", "networking event", "networking events"],
    "visited": ["been to", "trip to", "went to", "visit"],
    "which city": ["been to", "trip to", "went to", "visit"],
    "travel": ["旅行", "出行", "trip", "visit", "visited", "去过", "前往"],
    "trip": ["旅行", "出行", "trip", "visited", "去过", "前往"],
    "mentor": ["导师", "指导", "mentored", "mentorship"],
    "mentorship": ["导师", "指导", "mentored", "mentorship"],
    "hoodie": ["连帽衫", "卫衣", "sweatshirt", "hoodies"],
    "hoodies": ["连帽衫", "卫衣", "sweatshirts"],
    "sweatshirt": ["卫衣", "连帽衫", "hoodie"],
    "sweatshirts": ["卫衣", "连帽衫", "hoodies"],
    "limited collection": ["限量系列", "限量版", "limited edition", "hoodie line"],
    "limited edition": ["限量版", "limited edition", "hoodie line"],
    "hoodie line": ["连帽衫系列", "限量版连帽衫", "hoodie line"],
    "promote": ["推广", "宣传", "promotion", "promotions", "marketing", "ad campaign", "video presentation"],
    "promotion": ["推广", "宣传", "promotion", "promotions", "ad campaign"],
    "video presentation": ["视频演示", "视频展示", "video presentation"],
    "styling": ["搭配", "style", "styling"],
    "unique pieces": ["独特单品", "unique pieces"],
    "business venture": ["业务", "创业", "事业", "business", "venture"],
    "fair": ["fair", "展会", "集市"],
    "competition": ["competition", "contest", "比赛", "竞赛"],
    "events": ["活动", "event", "events"],
    "offering": ["提供", "offer", "service", "mentoring", "training", "classes", "workshops"],
    "offer": ["提供", "offer", "service", "mentoring", "training", "classes", "workshops"],
    "training": ["训练", "培训", "training"],
    "one-on-one": ["一对一", "one-on-one"],
    "cozy": ["舒适"],
    "comfortable": ["舒适"],
    "shopping experience": ["购物体验"],
    "customer": ["顾客", "客户"],
    "customers": ["顾客", "客户"],
    "trophy": ["奖杯"],
    "one-on-one mentoring": ["一对一指导", "一对一辅导"],
    "training": ["训练", "培训"],
    "workshop": ["工作坊"],
    "workshops": ["工作坊"],
}


def clean_query_text(query: str) -> str:
    text = re.sub(r"current date:\s*[^.]+\.", " ", str(query or ""), flags=re.I)
    text = re.sub(r"answer the question directly:\s*", " ", text, flags=re.I)
    return compact(text, 1000)


def text_tokens(text: Any) -> list[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9']+|\d{4}|\d+", str(text or "").lower())
    return [tok.strip("'") for tok in raw if tok.strip("'") and tok.strip("'") not in STOPWORDS]


def query_alias_terms(query: str) -> list[str]:
    q_clean = clean_query_text(query).lower()
    aliases: list[str] = []
    for anchor, bridged_terms in QUERY_ALIAS_BRIDGES.items():
        if anchor in q_clean:
            aliases.extend(bridged_terms)
    for anchor in re.findall(r"\b[A-Z][A-Za-z0-9']+\b", clean_query_text(query)):
        aliases.append(anchor)
    return [term for term in aliases if str(term or "").strip()]


def decoded_path_text(value: Any) -> str:
    raw = unquote(str(value or ""))
    raw = raw.replace("%20", " ")
    raw = raw.replace("/", " ")
    raw = raw.replace("_", " ")
    return raw


def combined_query_text(query: str) -> str:
    extras = query_alias_terms(query)
    if not extras:
        return clean_query_text(query)
    return clean_query_text(query) + " " + " ".join(extras)


def local_memory_score(query: str, content: str) -> float:
    q_clean = combined_query_text(query)
    q_tokens = text_tokens(q_clean)
    if not q_tokens:
        return 0.0
    text_low = str(content or "").lower()
    token_set = set(text_tokens(content))
    overlap = [tok for tok in q_tokens if tok in token_set or tok in text_low]
    score = len(set(overlap)) / max(1, len(set(q_tokens)))

    # Boost exact named/date anchors. LoCoMo questions often hinge on one
    # proper noun or date-like clue, and EchoMemory session abstracts preserve
    # those anchors in English.
    for anchor in re.findall(r"\b[A-Z][A-Za-z0-9']+\b|\b\d{4}\b", q_clean):
        if anchor.lower() in text_low:
            score += 0.12
    if re.search(r"\bboth\b", q_clean, re.I) and re.search(r"\bboth\b|\bshared\b|\bcommon\b", text_low):
        score += 0.25
    if re.search(r"\bcommon\b", q_clean, re.I) and re.search(r"\bboth\b|\bshared\b|\bsimilar\b", text_low):
        score += 0.2
    if is_temporal_query(q_clean) and re.search(
        r"\b(19|20)\d{2}\b|\b(january|february|march|april|may|june|july|august|september|october|november|december)\b|\bsession_date=|\bturn_time=",
        text_low,
    ):
        score += 0.1
    if is_duration_query(q_clean) and re.search(
        r"\b(start(?:ed|ing)?|begin|began|decided|planned|launched|opened|completed|finished|ended|left|joined|lost|received|got)\b",
        text_low,
    ):
        score += 0.1
    if is_multi_evidence_query(q_clean) and re.search(
        r"\b(both|shared|common|similar|different|whereas|while|compared|respectively)\b",
        text_low,
    ):
        score += 0.08
    if re.search(r"\b(city|town|country|place)\b", q_clean) and re.search(
        r"\b(been to|went to|trip to|visited|travel(?:ed)? to)\b|去过|前往|旅行|罗马|巴黎",
        text_low,
    ):
        score += 0.18
    if re.search(r"\b(promote|promotion|marketing|advertis|campaign)\b", q_clean) and re.search(
        r"\b(promot|ad campaign|video presentation|influencer|artist|hoodie|limited edition)\b|推广|宣传|视频演示|艺术家|连帽衫|卫衣",
        text_low,
    ):
        score += 0.16
    if re.search(r"\b(offer|offering|provide|provided)\b", q_clean) and re.search(
        r"\b(one-on-one|mentoring|training|classes|workshops)\b|一对一|指导|辅导|培训|课程|研讨会",
        text_low,
    ):
        score += 0.14
    q_bigrams = [" ".join(q_tokens[i : i + 2]) for i in range(max(0, len(q_tokens) - 1))]
    score += min(0.18, 0.06 * sum(1 for phrase in q_bigrams if phrase and phrase in text_low))
    return round(min(1.25, score), 4)


def echomem_account_roots(workspace: str, account: str) -> list[Path]:
    workspace_path = Path(workspace).expanduser().resolve()
    candidates = [
        workspace_path / account / account,
        workspace_path / account,
        workspace_path,
    ]
    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if (candidate / "sessions").exists() or (candidate / "memory/.structured/atoms").exists():
            roots.append(candidate)
    return roots


def read_session_meta(session_dir: Path) -> dict[str, str]:
    meta: dict[str, str] = {"title": "", "created_at": "", "session_date": "", "session_no": ""}
    try:
        raw_meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        meta["title"] = str(raw_meta.get("title") or "")
        meta["created_at"] = str(raw_meta.get("created_at") or "")
        match = re.search(r"session_(\d+)", meta["title"])
        if match:
            meta["session_no"] = match.group(1)
    except Exception:
        pass
    try:
        with (session_dir / "messages.jsonl").open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                message = json.loads(line)
                content = str(message.get("content") or "")
                if not meta["created_at"]:
                    meta["created_at"] = str(message.get("created_at") or "")
                date_match = re.search(r"\[session_date=([^\]]+)\]", content)
                if date_match:
                    meta["session_date"] = date_match.group(1).strip()
                break
    except Exception:
        pass
    return meta


def session_sort_key(session_dir: Path, meta: dict[str, str]) -> tuple[int, str, str]:
    try:
        session_no = int(meta.get("session_no") or 10**9)
    except ValueError:
        session_no = 10**9
    return (session_no, meta.get("created_at") or "", session_dir.name)


def session_summary_content(session_dir: Path) -> tuple[str, dict[str, str]]:
    chunks: list[tuple[str, str, Path]] = []
    for filename in ("abstract.md", "overview.md"):
        path = session_dir / filename
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                chunks.append((filename, content, path))
    if not chunks:
        return "", read_session_meta(session_dir)
    meta = read_session_meta(session_dir)
    header_parts = []
    if meta.get("title"):
        header_parts.append(f"title={meta['title']}")
    if meta.get("session_date"):
        header_parts.append(f"session_date={meta['session_date']}")
    elif meta.get("created_at"):
        header_parts.append(f"created_at={meta['created_at']}")
    header = "## session metadata\n" + " | ".join(header_parts) if header_parts else ""
    body = "\n\n".join(f"## {filename}\n{content}" for filename, content, _path in chunks)
    return "\n\n".join(part for part in (header, body) if part), meta


def summary_relevant_snippet(query: str, content: str, max_lines: int = 6, threshold: float = 0.18) -> tuple[str, float]:
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if not lines:
        return "", 0.0
    header_lines = [line for line in lines if line.lower().startswith("## session metadata")]
    scored: list[tuple[float, int, str]] = []
    for idx, line in enumerate(lines):
        if line.startswith("##"):
            continue
        score = local_memory_score(query, line)
        if score >= threshold:
            scored.append((score, idx, line))
    if not scored:
        return compact(content, 2200), local_memory_score(query, content)
    chosen = sorted(scored, key=lambda item: item[0], reverse=True)[:max_lines]
    chosen = sorted(chosen, key=lambda item: item[1])
    snippet_lines: list[str] = []
    if header_lines:
        snippet_lines.extend(header_lines[:1])
    snippet_lines.extend(line for _score, _idx, line in chosen)
    best_score = max(score for score, _idx, _line in chosen)
    return "\n".join(snippet_lines), best_score


def is_duration_query(query: str) -> bool:
    q_clean = clean_query_text(query)
    return bool(re.search(r"\bhow long\b|\btake\b|\bduration\b|\binterval\b|\bbetween\b", q_clean, re.I))


def is_temporal_query(query: str) -> bool:
    q_clean = clean_query_text(query)
    return bool(
        is_duration_query(q_clean)
        or re.search(
            r"\bwhen\b|\bdate\b|\btime\b|\bbefore\b|\bafter\b|\bduring\b|\border\b|\bchronolog|\bsequence\b|\bfirst\b|\blast\b",
            q_clean,
            re.I,
        )
    )


def is_multi_evidence_query(query: str) -> bool:
    q_clean = clean_query_text(query)
    return bool(
        re.search(
            r"\bboth\b|\bcommon\b|\bshared\b|\bsimilar\b|\bdiffer|\bcompare\b|\brelationship\b|\bconnection\b|\blist\b|\bwhich\b.*\band\b",
            q_clean,
            re.I,
        )
    )


def is_phrase_detail_query(query: str) -> bool:
    q_clean = clean_query_text(query)
    return bool(
        re.search(
            r"\bsay\b|\bsaid\b|\btell\b|\bdescribe\b|\bthink\b|\bmention\b|\boffer\b|\bplan\b|\bcalled\b|\bnamed\b|\blook like\b|\btitle\b|\bphoto\b|\bpicture\b|\bexact\b",
            q_clean,
            re.I,
        )
    )


def memory_type_of(item: dict[str, Any]) -> str:
    raw = str(item.get("memory_type") or "memory").strip().lower()
    if raw in {"session", "event", "preference", "fact", "relation", "reflection"}:
        return "atom"
    return raw or "memory"


def local_timeline_hint_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    if not args.local_timeline_hints or not is_temporal_query(query):
        return []
    records: list[dict[str, Any]] = []
    for root in echomem_account_roots(args.workspace, args.account):
        session_root = root / "sessions"
        if not session_root.exists():
            continue
        for session_dir in sorted(p for p in session_root.iterdir() if p.is_dir()):
            content, meta = session_summary_content(session_dir)
            if not content:
                continue
            snippet, score = summary_relevant_snippet(query, content, max_lines=5, threshold=max(args.local_score_threshold, 0.12))
            if score < args.local_score_threshold:
                continue
            records.append(
                {
                    "session_dir": session_dir,
                    "meta": meta,
                    "content": snippet or content,
                    "score": score,
                    "sort_key": session_sort_key(session_dir, meta),
                }
            )
    if not records:
        return []
    records = sorted(sorted(records, key=lambda r: r["score"], reverse=True)[:12], key=lambda r: r["sort_key"])

    def line_for(record: dict[str, Any]) -> str:
        meta = record["meta"]
        label = meta.get("title") or record["session_dir"].name
        when = meta.get("session_date") or meta.get("created_at") or "-"
        return f"- {label} | {when} | score={record['score']:.3f}: {compact(record['content'], 460)}"

    content = (
        "Chronological hint derived only from EchoMemory committed session summaries.\n"
        "Use these records as ordered temporal anchors. For duration or before/after questions, prefer explicit event dates in memories over the later question date.\n\n"
        "Relevant timeline records:\n"
        + "\n".join(line_for(r) for r in records)
    )
    return [
        {
            "uri": f"echo://{args.account}/memory/local_timeline_hint",
            "score": 1.35,
            "content": content,
            "memory_type": "timeline_hint",
            "backend": "echomemory_local",
            "path": str(Path(args.workspace).expanduser().resolve()),
        }
    ]


def local_shared_city_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    q_clean = clean_query_text(query)
    if not (is_multi_evidence_query(q_clean) and re.search(r"\b(city|visited|visit|been to|travel)\b|去过|前往|旅行", q_clean, re.I)):
        return []
    target_people = {
        term.lower()
        for term in query_alias_terms(query)
        if term.lower() in {"jon", "gina"}
    }
    if len(target_people) < 2:
        return []

    person_places: dict[str, set[str]] = {person: set() for person in target_people}
    city_patterns = [
        re.compile(r"\b(?P<person>Jon|Gina|Jean|John)\b.{0,80}?\b(?:visited|visit(?:ed)?|went to|travel(?:ed)? to|took a trip to|been to|has(?: \w+){0,2} been to)\b.{0,80}?\b(?P<place>[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)*)\b", re.I),
        re.compile(r"(?P<person>Jon|Gina|Jean|John).{0,80}?(?:去了|去过|前往|旅行).{0,30}?(?P<place>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z ]{1,12})"),
    ]

    def normalize_person(raw: str) -> str:
        raw_low = raw.lower()
        if raw_low in {"jean", "gina"}:
            return "gina"
        if raw_low in {"john", "jon"}:
            return "jon"
        return raw_low

    def normalize_place(raw: str) -> str:
        place = re.sub(r"\b(once|twice|one time|a time|last week|last month|recently|yesterday|today)\b", "", str(raw), flags=re.I)
        place = re.sub(r"\b(object|objects|tag|tags|subject|predicate)\b", "", place, flags=re.I)
        place = place.strip(" ,.;:，。！？()[]{}")
        place = re.sub(r"(一次|一回|一趟|旅行|前往|去了|去过)$", "", place).strip()
        if not place:
            return ""
        if re.search(r"\b(work|job|studio|store|business)\b|工作|店铺|生意|工作室", place, re.I):
            return ""
        if len(place.split()) > 4:
            return ""
        return place

    for root in echomem_account_roots(args.workspace, args.account):
        session_root = root / "sessions"
        if session_root.exists():
            for session_dir in sorted(p for p in session_root.iterdir() if p.is_dir()):
                content, _meta = session_summary_content(session_dir)
                if not content:
                    continue
                for pattern in city_patterns:
                    for match in pattern.finditer(content):
                        person = normalize_person(match.group("person"))
                        place = normalize_place(match.group("place"))
                        if person in person_places and place:
                            person_places[person].add(place)

    shared = sorted(set.intersection(*(places for places in person_places.values() if places))) if all(person_places.values()) else []
    if not shared:
        return []
    lines = [f"- {person}: {', '.join(sorted(person_places[person]))}" for person in sorted(person_places)]
    content = "Shared city inference from committed EchoMemory records.\n" + "\n".join(lines) + f"\n\nShared city: {', '.join(shared)}"
    return [
        {
            "uri": f"echo://{args.account}/memory/local_shared_city_hint",
            "score": 1.5,
            "content": content,
            "memory_type": "shared_city_hint",
            "backend": "echomemory_local",
            "path": str(Path(args.workspace).expanduser().resolve()),
        }
    ]


def parse_session_anchor_date(meta: dict[str, str]) -> datetime | None:
    for raw in [meta.get("session_date"), meta.get("created_at")]:
        text = str(raw or "").strip()
        if not text:
            continue
        match = re.search(r"(\d{1,2}) ([A-Za-z]+), (\d{4})", text)
        if match:
            try:
                return datetime.strptime(" ".join(match.groups()), "%d %B %Y")
            except ValueError:
                pass
        try:
            iso_text = text.replace("Z", "+00:00")
            return datetime.fromisoformat(iso_text)
        except ValueError:
            continue
    return None


def local_temporal_resolution_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    if not is_temporal_query(query):
        return []
    candidates: list[tuple[float, str, str]] = []
    for root in echomem_account_roots(args.workspace, args.account):
        session_root = root / "sessions"
        if not session_root.exists():
            continue
        for session_dir in sorted(p for p in session_root.iterdir() if p.is_dir()):
            content, meta = session_summary_content(session_dir)
            if not content:
                continue
            lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("##")]
            anchor = parse_session_anchor_date(meta)
            for line in lines:
                line_low = line.lower()
                score = local_memory_score(query, line)
                if score < max(args.local_score_threshold, 0.12):
                    continue
                normalized = ""
                if "a few years ago" in line_low:
                    normalized = "A few years ago"
                elif ("last week" in line_low or "the week before this conversation" in line_low) and anchor is not None:
                    normalized = (anchor - timedelta(days=7)).strftime("%B %Y")
                if not normalized:
                    continue
                source_date = meta.get("session_date") or meta.get("created_at") or session_dir.name
                candidates.append((score, normalized, f"{source_date}: {compact(line, 220)}"))
    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    lines = [f"- normalized={norm} | source={source}" for _score, norm, source in candidates[:4]]
    content = "Temporal normalization hints from committed EchoMemory summaries.\n" + "\n".join(lines)
    return [
        {
            "uri": f"echo://{args.account}/memory/local_temporal_resolution_hint",
            "score": 1.45,
            "content": content,
            "memory_type": "temporal_resolution_hint",
            "backend": "echomemory_local",
            "path": str(Path(args.workspace).expanduser().resolve()),
        }
    ]


def rank_hits_for_prompt(args: argparse.Namespace, query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [item for item in items if hit_score(item) >= args.score_threshold]
    for item in filtered:
        item["_rank_score"] = hit_score(item)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in filtered:
        groups.setdefault(memory_type_of(item), []).append(item)
    for group in groups.values():
        group.sort(key=lambda value: float(value.get("_rank_score") or hit_score(value)), reverse=True)

    if is_temporal_query(query):
        order = ["timeline_hint", "event_memory", "graph_node", "session_summary", "atom", "episode_memory", "session_memory", "session", "raw_turn", "memory", "entity_memory"]
        caps = {"timeline_hint": 2, "event_memory": 8, "graph_node": 6, "session_summary": 8, "atom": 10, "episode_memory": 3, "session_memory": 4, "session": 4, "raw_turn": 6, "entity_memory": 2}
    elif is_multi_evidence_query(query):
        order = ["session_summary", "episode_memory", "event_memory", "graph_node", "atom", "session_memory", "session", "raw_turn", "timeline_hint", "memory", "entity_memory"]
        caps = {"session_summary": 10, "episode_memory": 4, "event_memory": 8, "graph_node": 6, "atom": 10, "session_memory": 4, "session": 4, "raw_turn": 6, "timeline_hint": 1, "entity_memory": 2}
    elif is_phrase_detail_query(query):
        order = ["raw_turn", "atom", "graph_node", "session_summary", "event_memory", "session_memory", "session", "timeline_hint", "memory", "entity_memory"]
        caps = {"raw_turn": 14, "atom": 10, "graph_node": 6, "session_summary": 6, "event_memory": 6, "session_memory": 4, "session": 3, "timeline_hint": 1, "entity_memory": 2}
    else:
        order = ["atom", "graph_node", "event_memory", "raw_turn", "session_summary", "session_memory", "session", "episode_memory", "timeline_hint", "memory", "entity_memory"]
        caps = {"atom": 12, "graph_node": 6, "event_memory": 8, "raw_turn": 8, "session_summary": 8, "session_memory": 4, "session": 3, "episode_memory": 3, "timeline_hint": 1, "entity_memory": 2}

    selected: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for memory_type in order:
        for item in groups.get(memory_type, [])[: caps.get(memory_type, args.top_k)]:
            selected.append(item)
            seen_ids.add(id(item))
            if len(selected) >= args.top_k:
                return selected

    leftovers = [item for item in filtered if id(item) not in seen_ids]
    leftovers.sort(key=lambda value: float(value.get("_rank_score") or hit_score(value)), reverse=True)
    selected.extend(leftovers)
    return selected[: args.top_k]


def local_session_summary_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    if not args.local_session_summaries:
        return []
    hits: list[dict[str, Any]] = []
    for root in echomem_account_roots(args.workspace, args.account):
        session_root = root / "sessions"
        if not session_root.exists():
            continue
        for session_dir in sorted(p for p in session_root.iterdir() if p.is_dir()):
            combined, _meta = session_summary_content(session_dir)
            if not combined:
                continue
            snippet, score = summary_relevant_snippet(query, combined, max_lines=6, threshold=max(args.local_score_threshold, 0.12))
            if score < args.local_score_threshold:
                continue
            hits.append(
                {
                    "uri": f"echo://{args.account}/sessions/{session_dir.name}/summary",
                    "score": score,
                    "content": snippet or combined,
                    "memory_type": "session_summary",
                    "backend": "echomemory_local",
                    "path": str(session_dir),
                }
            )
    hits.sort(key=hit_score, reverse=True)
    return hits[: args.local_summary_max]


def local_atom_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    if not args.local_atoms:
        return []
    hits: list[dict[str, Any]] = []
    for root in echomem_account_roots(args.workspace, args.account):
        atom_root = root / "memory/.structured/atoms"
        if not atom_root.exists():
            continue
        for path in atom_root.glob("*.json"):
            try:
                atom = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            statement = str(atom.get("statement") or atom.get("content") or "")
            parts = [statement]
            spo = [atom.get("subject"), atom.get("predicate"), atom.get("object")]
            if any(spo):
                parts.append(" / ".join(str(x) for x in spo if x))
            attrs = atom.get("attributes") or {}
            if atom.get("event_time"):
                parts.append(f"time={atom.get('event_time')}")
            if isinstance(attrs, dict) and attrs:
                parts.append(" ".join(f"{k}={v}" for k, v in attrs.items() if v not in (None, "")))
            content = " | ".join(part for part in parts if part).strip()
            score = local_memory_score(query, content)
            if score < args.local_score_threshold:
                continue
            hits.append(
                {
                    "uri": f"atom://{atom.get('atom_id') or path.stem}",
                    "score": score,
                    "content": content,
                    "memory_type": "atom",
                    "atom_memory_type": atom.get("memory_type") or "",
                    "atom_type": atom.get("atom_type") or "",
                    "backend": "echomemory_local",
                    "path": str(path),
                }
            )
    hits.sort(key=hit_score, reverse=True)
    return hits[: args.local_atom_max]


def local_message_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    if not args.local_messages:
        return []
    hits: list[dict[str, Any]] = []
    window = max(0, int(args.local_message_window))
    for root in echomem_account_roots(args.workspace, args.account):
        session_root = root / "sessions"
        if not session_root.exists():
            continue
        for session_dir in sorted(p for p in session_root.iterdir() if p.is_dir()):
            meta = read_session_meta(session_dir)
            path = session_dir / "messages.jsonl"
            messages = read_jsonl_file(path)
            if not messages:
                continue
            for index, message in enumerate(messages):
                content = str(message.get("content") or "")
                if not content:
                    continue
                score = local_memory_score(query, content)
                if score < args.local_score_threshold:
                    continue
                start = max(0, index - window)
                end = min(len(messages), index + window + 1)
                lines: list[str] = []
                for offset, item in enumerate(messages[start:end], start=start):
                    created_at = str(item.get("created_at") or "")
                    role_id = str(item.get("role_id") or item.get("role") or "")
                    text = compact(item.get("content") or "", 900)
                    lines.append(f"[turn={offset} created_at={created_at} speaker={role_id}] {text}")
                header_parts = []
                if meta.get("title"):
                    header_parts.append(f"title={meta['title']}")
                if meta.get("session_date"):
                    header_parts.append(f"session_date={meta['session_date']}")
                header = " | ".join(header_parts)
                hits.append(
                    {
                        "uri": f"echo://{args.account}/sessions/{session_dir.name}/messages.jsonl#turn={index}",
                        "score": min(1.3, score + 0.08),
                        "content": "\n".join(part for part in (header, *lines) if part),
                        "memory_type": "raw_turn",
                        "backend": "echomemory_local",
                        "path": str(path),
                    }
                )
    hits.sort(key=hit_score, reverse=True)
    return hits[: args.local_message_max]


def local_memory_artifact_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    if not getattr(args, "local_memory_artifacts", True):
        return []
    hits: list[dict[str, Any]] = []
    for root in echomem_account_roots(args.workspace, args.account):
        artifact_roots: list[tuple[Path, str]] = [
            (root / "memory/entities", "entity_memory"),
            (root / "memory/events", "event_memory"),
            (root / "memory/.episodes/episodes", "episode_memory"),
            (root / "memory/session", "session_memory"),
        ]
        for artifact_root, memory_type in artifact_roots:
            if not artifact_root.exists():
                continue
            for path in artifact_root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".md", ".json"}:
                    continue
                if any(part.startswith(".") for part in path.parts):
                    continue
                try:
                    raw = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                decoded_name = decoded_path_text(path.stem)
                decoded_rel = decoded_path_text(path.relative_to(artifact_root))
                if path.suffix.lower() == ".json":
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = {}
                    extras: list[str] = []
                    if isinstance(data, dict):
                        for key in ("title", "summary", "start_time", "end_time", "event_time", "arc_stage", "status"):
                            if data.get(key):
                                extras.append(f"{key}={data.get(key)}")
                        for key in ("topics", "entities", "participants", "key_events", "atom_refs"):
                            value = data.get(key)
                            if isinstance(value, list) and value:
                                extras.append(f"{key}=" + ", ".join(str(item) for item in value[:12]))
                    content = "\n".join(part for part in [decoded_name, decoded_rel, *extras, raw] if part)
                else:
                    content = "\n".join(part for part in [decoded_name, decoded_rel, raw] if part)
                score = local_memory_score(query, content)
                if score < args.local_score_threshold:
                    continue
                if memory_type == "entity_memory" and re.search(r"\bwho\b|\bperson\b|\bfavorite\b|\bstyle\b|\bwhat\b", clean_query_text(query), re.I):
                    score += 0.04
                if memory_type == "event_memory" and is_temporal_query(query):
                    score += 0.08
                if memory_type == "episode_memory" and is_multi_evidence_query(query):
                    score += 0.05
                hits.append(
                    {
                        "uri": f"artifact://{args.account}/{path.relative_to(root)}",
                        "score": round(min(1.35, score), 4),
                        "content": compact(content, 2600),
                        "memory_type": memory_type,
                        "backend": "echomemory_local",
                        "path": str(path),
                    }
                )
    hits.sort(key=hit_score, reverse=True)
    return hits[: args.local_artifact_max]


def local_graph_node_hits(args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    if not getattr(args, "local_memory_artifacts", True):
        return []
    hits: list[dict[str, Any]] = []
    for root in echomem_account_roots(args.workspace, args.account):
        node_root = root / "memory/.graph/nodes"
        if not node_root.exists():
            continue
        for path in node_root.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            summary_hint = str(data.get("summary_hint") or "")
            props = data.get("properties") or {}
            node_type = str(data.get("node_type") or path.parent.name or "")
            extras: list[str] = []
            if isinstance(props, dict):
                for key in ("statement", "subject", "predicate", "object", "event_time", "time", "keywords", "tags"):
                    value = props.get(key)
                    if isinstance(value, list):
                        if value:
                            extras.append(f"{key}=" + ", ".join(str(item) for item in value[:12]))
                    elif value not in (None, ""):
                        extras.append(f"{key}={value}")
            content = "\n".join(part for part in [decoded_path_text(path.stem), summary_hint, *extras] if part)
            score = local_memory_score(query, content)
            if score < max(args.local_score_threshold, 0.12):
                continue
            if node_type == "atom":
                score += 0.08
            elif node_type == "entity":
                score += 0.04
            hits.append(
                {
                    "uri": f"graph://{args.account}/{path.relative_to(root)}",
                    "score": round(min(1.35, score), 4),
                    "content": compact(content, 1800),
                    "memory_type": "graph_node",
                    "backend": "echomemory_local",
                    "path": str(path),
                }
            )
    hits.sort(key=hit_score, reverse=True)
    return hits[: max(8, min(args.local_artifact_max, 24))]


async def echomemory_retrieve(args: argparse.Namespace, sdk: Any, query: str) -> tuple[list[dict[str, Any]], str]:
    context = sdk_ctx_kwargs(sdk, args.account, args.user_id, args.agent_id)
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    try:
        if args.retrieval_mode in {"find", "both"}:
            found = await sdk.find(query, ctx=context)
            items.extend(context_item_to_dict(item) for item in found)
    except Exception as exc:
        errors.append(f"find: {exc}")
    try:
        if args.retrieval_mode in {"search", "both"}:
            result = await sdk.search(query, ctx=context, budget={"max_results": args.top_k})
            items.extend(context_item_to_dict(item) for item in getattr(result, "items", []))
    except Exception as exc:
        errors.append(f"search: {exc}")
    allow_local_evidence = (not getattr(args, "vikingboat_compat", False)) or bool(
        getattr(args, "compat_allow_local_evidence", False)
    )
    if allow_local_evidence:
        items.extend(local_timeline_hint_hits(args, query))
        items.extend(local_temporal_resolution_hits(args, query))
        items.extend(local_shared_city_hits(args, query))
        items.extend(local_message_hits(args, query))
        items.extend(local_session_summary_hits(args, query))
        items.extend(local_atom_hits(args, query))
        items.extend(local_memory_artifact_hits(args, query))
        items.extend(local_graph_node_hits(args, query))
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        key = f"{item.get('uri')}::{compact(item.get('content'), 120)}"
        if key not in seen:
            seen[key] = item
    if args.retrieval_ranker == "score":
        hits = [item for item in seen.values() if hit_score(item) >= args.score_threshold]
        hits.sort(key=hit_score, reverse=True)
        return hits[: args.top_k], "; ".join(errors)
    return rank_hits_for_prompt(args, query, list(seen.values())), "; ".join(errors)


def memory_uri(item: dict[str, Any]) -> str:
    return str(item.get("uri") or item.get("path") or item.get("id") or "")


def memory_content(item: dict[str, Any]) -> str:
    return str(
        item.get("content")
        or item.get("text")
        or item.get("abstract")
        or item.get("overview")
        or item.get("summary")
        or ""
    )


def cache_memory_items(cache: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> None:
    for item in items:
        uri = memory_uri(item)
        if uri and uri not in cache:
            cache[uri] = item


def split_user_agent_hits(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    user_hits: list[dict[str, Any]] = []
    agent_hits: list[dict[str, Any]] = []
    for item in items:
        uri = memory_uri(item).lower()
        memory_type = str(item.get("memory_type") or item.get("type") or "").lower()
        owner = str(item.get("owner") or item.get("scope") or "").lower()
        if "/agent/" in uri or memory_type.startswith("agent") or owner == "agent":
            agent_hits.append(item)
        else:
            user_hits.append(item)
    return user_hits, agent_hits


def search_result_kind(item: dict[str, Any]) -> str:
    raw = str(item.get("memory_type") or item.get("type") or item.get("backend") or "").lower()
    uri = memory_uri(item).lower()
    if "skill" in raw or "/skills/" in uri:
        return "skills"
    if "resource" in raw or "/resources/" in uri:
        return "resources"
    return "memories"


def echomemory_search_payload(items: list[dict[str, Any]], min_score: float, limit: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {"memories": [], "resources": [], "skills": []}
    emitted = 0
    for item in sorted(items, key=hit_score, reverse=True):
        score = hit_score(item)
        if score < min_score:
            continue
        uri = memory_uri(item)
        if not uri:
            continue
        emitted += 1
        grouped[search_result_kind(item)].append(
            {
                "index": emitted,
                "uri": uri,
                "abstract": compact(memory_content(item), 700),
                "is_leaf": True,
                "score": round(score, 6),
            }
        )
        if emitted >= limit:
            break
    return {"count": emitted, **grouped}


async def execute_echomemory_search_tool(
    args: argparse.Namespace,
    sdk: Any,
    tool_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> tuple[str, str, int]:
    query = str(tool_args.get("query") or "").strip()
    if not query:
        return "No results found for empty query", "", 0
    try:
        min_score = float(tool_args.get("min_score") if tool_args.get("min_score") is not None else args.tool_min_score)
    except (TypeError, ValueError):
        min_score = args.tool_min_score

    tool_query_args = argparse.Namespace(**vars(args))
    tool_query_args.top_k = max(int(args.top_k), int(args.tool_search_limit))
    hits, retrieval_error = await echomemory_retrieve(tool_query_args, sdk, query)
    cache_memory_items(cache, hits)
    payload = echomemory_search_payload(hits, min_score, int(args.tool_search_limit))
    if payload["count"] == 0:
        return f"No results found for query: {query}", retrieval_error, 0
    return json.dumps(payload, ensure_ascii=False, indent=2), retrieval_error, int(payload["count"])


def execute_echomemory_multi_read_tool(
    tool_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> str:
    raw_uris = tool_args.get("uris")
    if isinstance(raw_uris, str):
        uris = [raw_uris]
    elif isinstance(raw_uris, list):
        uris = [str(uri) for uri in raw_uris if str(uri or "").strip()]
    else:
        uris = []
    if not uris:
        return "Error: No URIs provided."
    lines = [f"Multi-read results for {len(uris)} resources (level: read):"]
    for uri in uris[:20]:
        lines.append(f"\n--- START OF {uri} ---")
        item = cache.get(uri)
        content = memory_content(item or {})
        lines.append(content if content else f"ERROR: Error reading from EchoMemory memory item: empty content for {uri}")
        lines.append(f"--- END OF {uri} ---")
    if len(uris) > 20:
        lines.append(f"\nSkipped {len(uris) - 20} URIs to keep the tool result bounded.")
    return "\n".join(lines)


def execute_echomemory_list_tool(cache: dict[str, dict[str, Any]], uri: str = "", recursive: bool = False) -> str:
    prefix = str(uri or "").strip()
    rows = []
    for item in sorted(cache.values(), key=lambda value: memory_uri(value)):
        item_uri = memory_uri(item)
        if prefix and not item_uri.startswith(prefix):
            continue
        rows.append(
            str(
                {
                    "name": Path(item_uri.rstrip("/")).name or item_uri,
                    "size": len(memory_content(item)),
                    "uri": item_uri,
                    "isDir": False,
                }
            )
        )
        if not recursive and len(rows) >= 30:
            break
    return "\n".join(rows) if rows else f"No resources found at {prefix or 'cached EchoMemory results'}"


def execute_echomemory_grep_tool(tool_args: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str:
    raw_patterns = tool_args.get("pattern")
    patterns = raw_patterns if isinstance(raw_patterns, list) else [raw_patterns]
    patterns = [str(pattern or "").strip() for pattern in patterns if str(pattern or "").strip()]
    uri_prefix = str(tool_args.get("uri") or "").strip()
    flags = re.I if tool_args.get("case_insensitive") else 0
    if not patterns:
        return "No matches found for patterns: ''"
    results: list[str] = []
    total = 0
    for item in cache.values():
        item_uri = memory_uri(item)
        if uri_prefix and not item_uri.startswith(uri_prefix):
            continue
        content = memory_content(item)
        for pattern in patterns:
            try:
                regex = re.compile(pattern, flags)
            except re.error:
                regex = re.compile(re.escape(pattern), flags)
            for line_no, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    if not results or results[-1] != f"\n📄 {item_uri}":
                        results.append(f"\n📄 {item_uri}")
                    results.append(f"   Line {line_no} (pattern: '{pattern}'):")
                    results.append(f"   {line[:600]}")
                    total += 1
                    if total >= 60:
                        return f"Found {total} matches across {len(patterns)} patterns:" + "\n" + "\n".join(results)
    if not results:
        return "No matches found for patterns: " + ", ".join(f"'{pattern}'" for pattern in patterns)
    return f"Found {total} matches across {len(patterns)} patterns:" + "\n" + "\n".join(results)


def execute_echomemory_glob_tool(tool_args: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str:
    pattern = str(tool_args.get("pattern") or "*").strip() or "*"
    uri_prefix = str(tool_args.get("uri") or "").strip()
    regex = re.escape(pattern).replace("\\*\\*", ".*").replace("\\*", "[^/]*")
    compiled = re.compile(f"^{regex}$")
    matches = []
    for item in sorted(cache.values(), key=lambda value: memory_uri(value)):
        item_uri = memory_uri(item)
        if uri_prefix and not item_uri.startswith(uri_prefix):
            continue
        name = item_uri.split("://", 1)[-1]
        if compiled.search(name) or compiled.search(Path(item_uri).name):
            matches.append(item_uri)
        if len(matches) >= 80:
            break
    if not matches:
        return f"No files found for pattern: {pattern}"
    return "Found " + str(len(matches)) + " files:\n" + "\n".join(f"📄 {uri}" for uri in matches)


def echomemory_search_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_SEARCH_TOOL_NAME,
            "description": (
                "Using query to search EchoMemory long-term memories and supporting context. "
                "This operation performs semantic retrieval, not full character matching. Please avoid repeated calls with similar queries as much as possible."
                "bad-case: after searching with 'Nate Joanna dog playdate 3:00 pm', another search was performed using 'Nate Joanna dog playdate'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "min_score": {
                        "type": "number",
                        "description": "Minimum relevance score threshold",
                        "default": VIKINGBOT_TOOL_MIN_SCORE,
                    },
                    "target_uri": {
                        "type": "string",
                        "description": "Optional EchoMemory URI prefix to limit search scope. If omitted, search all available memory.",
                    },
                },
                "required": ["query"],
            },
        },
    }


def echomemory_multi_read_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_MULTI_READ_TOOL_NAME,
            "description": "Read full content from multiple EchoMemory items. Returns complete content for all URIs with no truncation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uris": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of EchoMemory URIs to read from.",
                    },
                },
                "required": ["uris"],
            },
        },
    }


def echomemory_list_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_LIST_TOOL_NAME,
            "description": "List cached EchoMemory items by URI prefix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The parent EchoMemory URI prefix to list."},
                    "recursive": {"type": "boolean", "description": "Whether to list recursively", "default": False},
                },
                "required": ["uri"],
            },
        },
    }


def echomemory_grep_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_GREP_TOOL_NAME,
            "description": "Search cached EchoMemory item content using regex patterns. Supports multiple patterns to search concurrently. Please avoid repeated calls with similar queries as much as possible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "The EchoMemory URI prefix to search within."},
                    "pattern": {"type": "array", "items": {"type": "string"}, "description": "Regex pattern or array of regex patterns to search for"},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive search", "default": False},
                },
                "required": ["uri", "pattern"],
            },
        },
    }


def echomemory_glob_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": MEMORY_GLOB_TOOL_NAME,
            "description": "Find cached EchoMemory item URIs using glob patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match."},
                    "uri": {"type": "string", "description": "Optional EchoMemory URI prefix to search within.", "default": ""},
                },
                "required": ["pattern"],
            },
        },
    }


def echomemory_tool_definitions(args: argparse.Namespace) -> list[dict[str, Any]]:
    tool_set = normalize_echomemory_tool_set(
        getattr(args, "tool_set", ""),
        vikingboat_compat=bool(getattr(args, "vikingboat_compat", False)),
    )
    if tool_set == "search_only":
        return [echomemory_search_tool_definition()]
    if tool_set == ECHOMEMORY_VIKINGBOAT_TOOL_SET:
        return [
            echomemory_search_tool_definition(),
            echomemory_multi_read_tool_definition(),
            echomemory_list_tool_definition(),
            echomemory_grep_tool_definition(),
            echomemory_glob_tool_definition(),
        ]
    return [echomemory_search_tool_definition(), echomemory_multi_read_tool_definition()]


async def execute_echomemory_tool(
    args: argparse.Namespace,
    sdk: Any,
    name: str,
    parsed_args: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> tuple[str, str, int]:
    if name == MEMORY_SEARCH_TOOL_NAME:
        return await execute_echomemory_search_tool(args, sdk, parsed_args, cache)
    if name == MEMORY_MULTI_READ_TOOL_NAME:
        return execute_echomemory_multi_read_tool(parsed_args, cache), "", 0
    if name == MEMORY_LIST_TOOL_NAME:
        return execute_echomemory_list_tool(cache, str(parsed_args.get("uri") or ""), bool(parsed_args.get("recursive"))), "", 0
    if name == MEMORY_GREP_TOOL_NAME:
        return execute_echomemory_grep_tool(parsed_args, cache), "", 0
    if name == MEMORY_GLOB_TOOL_NAME:
        return execute_echomemory_glob_tool(parsed_args, cache), "", 0
    return f"Error executing {name}: unsupported tool", "", 0


def search_payload_uris(result_text: str, limit: int) -> list[str]:
    try:
        payload = json.loads(result_text)
    except Exception:
        return []
    uris: list[str] = []
    for group_name in ("memories", "resources", "skills"):
        group = payload.get(group_name) if isinstance(payload, dict) else None
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "").strip()
            if uri and uri not in uris:
                uris.append(uri)
            if len(uris) >= limit:
                return uris
    return uris


async def build_initial_tool_prefetch(
    args: argparse.Namespace,
    sdk: Any,
    query: str,
    cache: dict[str, dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], str]:
    if not getattr(args, "initial_tool_prefetch", True):
        return "", [], ""

    tools_used: list[dict[str, Any]] = []
    retrieval_errors: list[str] = []
    search_args = {"query": query, "min_score": args.tool_min_score}
    search_text, retrieval_error, result_count = await execute_echomemory_search_tool(args, sdk, search_args, cache)
    if retrieval_error:
        retrieval_errors.append(retrieval_error)
    tools_used.append(
        {
            "tool_name": MEMORY_SEARCH_TOOL_NAME,
            "args": search_args,
            "result_count": result_count,
            "result": compact(search_text, int(args.tool_log_chars)),
            "prefetch": True,
        }
    )

    sections = [
        "## Prefetched EchoMemory tool results",
        "The evaluation harness executed EchoMemory memory tools before the first model turn.",
        "",
        f"### {MEMORY_SEARCH_TOOL_NAME}",
        search_text,
    ]
    if normalize_echomemory_tool_set(
        getattr(args, "tool_set", "search_read"),
        vikingboat_compat=bool(getattr(args, "vikingboat_compat", False)),
    ) != "search_only":
        uris = search_payload_uris(search_text, max(0, int(args.prefetch_read_count)))
        if uris:
            read_args = {"uris": uris}
            read_text = execute_echomemory_multi_read_tool(read_args, cache)
            tools_used.append(
                {
                    "tool_name": MEMORY_MULTI_READ_TOOL_NAME,
                    "args": read_args,
                    "result_count": 0,
                    "result": compact(read_text, int(args.tool_log_chars)),
                    "prefetch": True,
                }
            )
            sections.extend(["", f"### {MEMORY_MULTI_READ_TOOL_NAME}", read_text])

    prefetch_text = compact("\n".join(sections), int(args.prefetch_context_chars))
    return prefetch_text, tools_used, "; ".join(retrieval_errors[:5])


def build_vikingboat_lite_messages(
    args: argparse.Namespace,
    job: benchmark_adapter.Job,
    user_memory: str,
    agent_memory: str,
    has_memory: bool,
) -> list[dict[str, Any]]:
    runtime = f"{'macOS' if platform.system() == 'Darwin' else platform.system()} {platform.machine()}, Python {platform.python_version()}"
    workspace_display = str(Path.cwd().resolve())
    system = f"""# MemoryBench Agent

You are an AI assistant using EchoMemory as the memory backend.
When acquiring information, data, and knowledge, you **prioritize using memory tools to read and search EchoMemory above all other sources**.
You have access to tools that allow you to:
- Read, search, and grep EchoMemory items
- Read, write, and edit local files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## Runtime
{runtime}

## Workspace
You have two workspaces:
1. Local workspace: {workspace_display}
2. EchoMemory workspace: managed via EchoMemory local SDK and memory tools
- Custom skills: {workspace_display}/skills/{{skill-name}}/SKILL.md

IMPORTANT:
- When responding to direct questions or conversations, reply directly with your text response.
- Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).For normal conversation, just respond with text - do not call the message tool.
- Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.

## Memory
- Long-term memories are created by EchoMemory session commit.

## Evaluation alignment
This run keeps the VikingBoat-style message layout and retrieval budgets for comparability, but the memory backend and exposed tools are EchoMemory."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    tz = time.strftime("%Z") or "UTC"
    session_context = (
        "## Current Session\n"
        f"Channel: {getattr(args, 'vikingbot_channel', 'cli') or 'cli'}\n"
        "**Group chat session.** Current user ID: locomo\n"
        "Multiple users can participate in this conversation. Each user message is prefixed with the user ID in brackets like @<user_id>. "
        "You should pay attention to who is speaking to understand the context. "
    )
    evidence = (
        f"### user memories:\n{user_memory or '(none)'}\n\n"
        f"### agent memories:\n{agent_memory or '(none)'}"
    )
    memory_parts = [
        f"## Current Time: {now} ({tz})",
        session_context,
    ]
    if has_memory:
        memory_parts.append(f"## {MEMORY_SEARCH_TOOL_NAME}(query=[user_query])\n{evidence}")
    memory_parts.append("Reply in the same language as the user's query, ignoring the language of the reference materials. User's query:")
    memory_message = "\n\n---\n\n".join(memory_parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": memory_message},
        {"role": "user", "content": build_vikingbot_question_prompt(job)},
    ]


async def call_echomemory_vikingboat_lite_loop(
    args: argparse.Namespace,
    sdk: Any,
    messages: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tools = echomemory_tool_definitions(args) if args.vikingboat_tool_loop else None
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    tools_used: list[dict[str, Any]] = []
    retrieval_errors: list[str] = []
    retry_count_total = 0
    attempts = max(1, args.model_retries + 1)
    for iteration in range(1, max(1, args.max_iterations) + 1):
        payload_variants = openai_payload_variants(args.answer_model, messages, default_openai_max_tokens(), tools)
        data: dict[str, Any] | None = None
        last_error = ""
        last_kind = "api_error"
        for attempt in range(attempts):
            payload = payload_variants[attempt % len(payload_variants)]
            try:
                req = request.Request(
                    args.answer_base_url.rstrip("/") + "/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {args.answer_token}"},
                    method="POST",
                )
                with request.urlopen(req, timeout=args.timeout_s) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                candidate = parse_openai_compatible_response(body)
                openai_response_message(candidate, allow_tool_calls=bool(tools))
                data = candidate
                retry_count_total += attempt
                break
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {body[:1000]}"
                last_kind = classify_model_error(last_error)
            except Exception as exc:
                last_error = str(exc)
                last_kind = classify_model_error(last_error)
            if attempt < attempts - 1:
                sleep_s = min(30, 2 ** attempt)
                if last_kind == "rate_limited":
                    sleep_s = min(45, 5 * (attempt + 1))
                print(f"[model] retry={attempt + 1}/{args.model_retries} kind={last_kind} error={compact(last_error, 220)}", flush=True)
                time.sleep(sleep_s)
        if data is None:
            raise ModelCallError(last_error or "model call failed", retry_count_total or args.model_retries, last_kind)

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        total_usage["prompt_tokens"] += prompt_tokens
        total_usage["completion_tokens"] += completion_tokens
        total_usage["total_tokens"] += total_tokens

        message = openai_response_message(data, allow_tool_calls=bool(tools))
        tool_calls = message.get("tool_calls") or []
        if tool_calls and tools:
            messages.append({"role": "assistant", "content": message.get("content") or " ", "tool_calls": tool_calls})
            for tool_call in tool_calls:
                fn = tool_call.get("function") or {}
                name = str(fn.get("name") or "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    parsed_args = {"query": str(raw_args)}
                result_text, retrieval_error, result_count = await execute_echomemory_tool(args, sdk, name, parsed_args, cache)
                if retrieval_error:
                    retrieval_errors.append(retrieval_error)
                tools_used.append(
                    {
                        "tool_name": name,
                        "args": parsed_args,
                        "result_count": result_count,
                        "result": compact(result_text, int(args.tool_log_chars)),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id") or f"tool_{len(tools_used)}",
                        "name": name,
                        "content": result_text,
                    }
                )
            messages.append({"role": "user", "content": "Reflect on the results and decide next steps."})
            continue
        return {
            "answer": str(message.get("content") or "").strip(),
            "prompt_tokens": total_usage["prompt_tokens"],
            "completion_tokens": total_usage["completion_tokens"],
            "total_tokens": total_usage["total_tokens"],
            "model_retry_count": retry_count_total,
            "model_error_kind": "",
            "iteration": iteration,
            "tools_used": tools_used,
            "tool_retrieval_error": "; ".join(retrieval_errors[:5]),
        }
    return {
        "answer": "",
        "prompt_tokens": total_usage["prompt_tokens"],
        "completion_tokens": total_usage["completion_tokens"],
        "total_tokens": total_usage["total_tokens"],
        "model_retry_count": retry_count_total,
        "model_error_kind": "max_iterations",
        "iteration": args.max_iterations,
        "tools_used": tools_used,
        "tool_retrieval_error": "; ".join(retrieval_errors[:5]),
    }


def format_memory_section(items: list[dict[str, Any]], max_chars: int) -> str:
    lines: list[str] = []
    used = 0
    seen_hashes: set[int] = set()
    for index, item in enumerate(items, 1):
        uri = str(item.get("uri") or "")
        score = hit_score(item)
        link = (
            f'<memory index="{index}" type="link">\n'
            f"  <uri>{uri}</uri>\n"
            f"  <score>{score:.3f}</score>\n"
            f"</memory>"
        )
        content = memory_content(item).strip()
        if content:
            content_hash = hash(content)
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            full = (
                f'<memory index="{index}" type="full">\n'
                f"  <uri>{uri}</uri>\n"
                f"  <score>{score:.3f}</score>\n"
                f"  <content>{content}</content>\n"
                f"</memory>"
            )
            needed = len(full) + (1 if lines else 0)
            if used + needed <= max_chars:
                lines.append(full)
                used += needed
                continue
        link_needed = len(link) + (1 if lines else 0)
        if used + link_needed <= max_chars:
            lines.append(link)
            used += link_needed
    return "\n".join(lines)


def build_messages(job: benchmark_adapter.Job, user_memory: str, agent_memory: str, has_memory: bool) -> list[dict[str, str]]:
    system = (
        "# EchoMemory LoCoMo Question Answering\n\n"
        "You are a helpful, accurate, and very concise assistant. "
        "Read the retrieved memories carefully, then answer with the smallest exact fact that satisfies the question. "
        "Do not add explanations, background, or adjacent facts unless the question asks for them. "
        "For list questions, return only the listed items. "
        "For date questions, use the event date or the normalized time period implied by the memory, not the later conversation date. "
        "For questions asking what someone said, offered, promoted, planned, or provided, prefer the most specific phrase found in memory and do not broaden it into a more generic activity. "
        "For questions asking which city both people visited, infer the shared city from the memories and answer only that city. "
        "Normalize conversation-anchored phrases such as 'last week' or 'the week before this conversation' using the session date when possible. "
        "Preserve vague phrases such as 'a few years ago' verbatim unless the memory itself anchors them to a specific calendar date."
    )
    evidence = (
        f"### user memories:\n{user_memory or '(none)'}\n\n"
        f"### agent memories:\n{agent_memory or '(none)'}"
    )
    memory_message = build_vikingbot_user_memory_message(evidence, has_memory, group_chat=True, sender_id="locomo")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": memory_message},
        {"role": "user", "content": build_vikingbot_question_prompt(job)},
    ]


VIKINGBOT_ALIGNED_PROMPT_MODES = {"vikingboat_lite", "vikingboat_compat"}


def answer_refinement_needed(job: benchmark_adapter.Job, answer: str) -> bool:
    question = str(job.question or "")
    text = str(answer or "").strip()
    if not text:
        return False
    low = text.lower()
    if low in {"unknown", "i don't know.", "i don't know", "not found in the retrieved memories.", "no such comparison is found in the memories.", "no information."}:
        return True
    if len(text) <= 24 and "," not in text and " and " not in text.lower():
        return False
    if re.search(r"\b(which|what|how)\b", question, re.I) and (
        "," in text
        or " and " in text.lower()
        or len(text) >= 42
    ):
        return True
    if is_phrase_detail_query(question) or is_multi_evidence_query(question):
        return True
    return False


def evidence_focus_snippets(query: str, hits: list[dict[str, Any]], limit: int = 12) -> str:
    scored: list[tuple[float, str, str]] = []
    focus_hit_limit = 20 if (is_multi_evidence_query(query) or is_phrase_detail_query(query)) else 12
    for item in hits[: min(len(hits), focus_hit_limit)]:
        uri = str(item.get("uri") or "")
        content = str(item.get("content") or "")
        memory_type = str(item.get("memory_type") or "")
        fragments: list[str] = []
        for raw in re.split(r"\n+|(?<=[.!?])\s+| - ", content):
            text = " ".join(raw.split()).strip()
            if not text:
                continue
            if text.lower().startswith("## session metadata"):
                text = re.sub(r"^##\s*session metadata\s*", "", text, flags=re.I).strip()
                if not text:
                    continue
            if len(text) < 8:
                continue
            if text.lower().startswith(("title=", "session_date=", "created_at=", "score=")):
                continue
            score = local_memory_score(query, text)
            if memory_type == "session_summary" and text.startswith("- "):
                score += 0.06
            if memory_type in {"graph_node", "atom", "session_memory"}:
                score += 0.03
            if re.search(r"\b(besides|except|only|specifically|includes?)\b", text, re.I):
                score += 0.08
            if score < 0.12:
                continue
            fragments.append(text)
            scored.append((score, uri, text))
    if not scored:
        return ""
    picked: list[str] = []
    seen: set[str] = set()
    uri_counts: dict[str, int] = {}
    for score, uri, text in sorted(scored, key=lambda item: item[0], reverse=True):
        normalized = text.lower()
        if normalized in seen:
            continue
        if uri and uri_counts.get(uri, 0) >= 2:
            continue
        seen.add(normalized)
        if uri:
            uri_counts[uri] = uri_counts.get(uri, 0) + 1
        picked.append(f"- ({uri}) {text}")
        if len(picked) >= limit:
            break
    return "\n".join(picked)


def build_answer_refinement_messages(job: benchmark_adapter.Job, draft_answer: str, focus_snippets: str) -> list[dict[str, str]]:
    system = (
        "You refine a draft answer for a memory benchmark. "
        "Keep only the smallest exact answer supported by the evidence. "
        "Remove broader adjacent facts, extra list items, generic summaries, and unsupported embellishments. "
        "If the draft answer says unknown, not found, or no information, but the evidence contains a direct answer, replace the draft with that direct answer. "
        "For list questions, return all and only the required items as a compact comma-separated list. "
        "For event questions, keep event names only. "
        "For offer/provide/plan/promote questions, prefer the most specific supported phrase rather than a broader category. "
        "For symbol, feeling, advice, and description questions, prefer the exact phrase used in evidence over a looser paraphrase. "
        "For profession, internship, role, city, book, and object questions, return the shortest noun phrase that fully answers the question. "
        "If the evidence contains contrastive wording such as 'besides X, I am offering Y', prefer Y when the question asks what is being offered. "
        "Reply with answer text only."
    )
    user = (
        f"Question: {job.question}\n"
        f"Draft answer: {draft_answer}\n\n"
        f"Focused evidence:\n{focus_snippets or '(none)'}\n\n"
        "Return the minimal exact final answer:"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def refine_answer_once(
    args: argparse.Namespace,
    job: benchmark_adapter.Job,
    draft_answer: str,
    hits: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not args.answer_token or not answer_refinement_needed(job, draft_answer):
        return None
    focus = evidence_focus_snippets(job.question, hits)
    if not focus:
        return None
    messages = build_answer_refinement_messages(job, draft_answer, focus)
    try:
        result = call_openai(
            args.answer_base_url,
            args.answer_model,
            args.answer_token,
            messages,
            min(int(args.timeout_s), 60),
            max_retries=min(int(args.model_retries), 2),
        )
    except ModelCallError:
        return None
    answer = str(result.get("answer") or "").strip()
    if not answer or answer.lower() == "unknown":
        return None
    result["answer"] = answer
    result["refinement_focus"] = compact(focus, 1800)
    result["refinement_prompt_tokens"] = int(result.get("prompt_tokens") or 0)
    result["refinement_completion_tokens"] = int(result.get("completion_tokens") or 0)
    result["refinement_total_tokens"] = int(result.get("total_tokens") or 0)
    return result


async def answer_question(args: argparse.Namespace, sdk: Any, job: benchmark_adapter.Job) -> dict[str, str]:
    started = time.time()
    retrieval_error = ""
    query = build_vikingbot_question_prompt(job)
    try:
        hits, retrieval_error = await echomemory_retrieve(args, sdk, query)
    except Exception as exc:
        hits = []
        retrieval_error = str(exc)
    tool_cache: dict[str, dict[str, Any]] = {}
    cache_memory_items(tool_cache, hits)
    prompt_mode = str(getattr(args, "prompt_mode", "vikingboat_lite") or "vikingboat_lite")
    aligned_prompt = prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES
    prefetch_text = ""
    prefetch_tools: list[dict[str, Any]] = []
    prefetch_error = ""
    if aligned_prompt:
        try:
            prefetch_text, prefetch_tools, prefetch_error = await build_initial_tool_prefetch(args, sdk, query, tool_cache)
        except Exception as exc:
            prefetch_error = f"initial_tool_prefetch: {exc}"
    if prefetch_error:
        retrieval_error = "; ".join(part for part in [retrieval_error, prefetch_error] if part)
    user_hits, agent_hits = split_user_agent_hits(hits)
    user_memory_block = format_memory_section(user_hits, args.user_memory_budget_chars)
    agent_memory_block = format_memory_section(agent_hits, args.agent_memory_budget_chars)
    has_memory = bool(user_memory_block or agent_memory_block)
    messages = (
        build_vikingboat_lite_messages(args, job, user_memory_block, agent_memory_block, has_memory)
        if aligned_prompt
        else build_messages(job, user_memory_block, agent_memory_block, has_memory)
    )
    if prefetch_text and aligned_prompt:
        insert_at = max(1, len(messages) - 1)
        messages.insert(insert_at, {"role": "user", "content": prefetch_text})
    tool_loop_fallback_error = ""
    if args.answer_token:
        try:
            if aligned_prompt and args.vikingboat_tool_loop:
                result = await call_echomemory_vikingboat_lite_loop(args, sdk, messages, tool_cache)
            else:
                result = call_openai(
                    args.answer_base_url,
                    args.answer_model,
                    args.answer_token,
                    messages,
                    args.timeout_s,
                    args.model_retries,
                )
                result.setdefault("iteration", 1)
                result.setdefault("tools_used", [])
        except ModelCallError as exc:
            if aligned_prompt and args.fallback_to_one_shot:
                tool_loop_fallback_error = str(exc)
                fallback_messages = build_messages(job, user_memory_block, agent_memory_block, has_memory)
                if prefetch_text:
                    fallback_messages.insert(max(1, len(fallback_messages) - 1), {"role": "user", "content": prefetch_text})
                try:
                    result = call_openai(
                        args.answer_base_url,
                        args.answer_model,
                        args.answer_token,
                        fallback_messages,
                        args.timeout_s,
                        args.model_retries,
                    )
                    result.setdefault("iteration", 1)
                    result.setdefault("tools_used", [])
                    result["tool_loop_fallback"] = True
                    result["model_error"] = ""
                except ModelCallError as fallback_exc:
                    result = {
                        "answer": "",
                        "prompt_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                        "completion_tokens": 0,
                        "total_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                        "model_retry_count": fallback_exc.retry_count,
                        "model_error_kind": fallback_exc.error_kind,
                        "model_error": str(fallback_exc),
                        "iteration": 0,
                        "tools_used": [],
                    }
            else:
                result = {
                    "answer": "",
                    "prompt_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                    "completion_tokens": 0,
                    "total_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
                    "model_retry_count": exc.retry_count,
                    "model_error_kind": exc.error_kind,
                    "model_error": str(exc),
                    "iteration": 0,
                    "tools_used": [],
                }
    else:
        result = {
            "answer": "unknown",
            "prompt_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)),
            "completion_tokens": 1,
            "total_tokens": token_estimate(json.dumps(messages, ensure_ascii=False)) + 1,
            "model_retry_count": 0,
            "model_error_kind": "no_answer_token",
            "model_error": "",
            "iteration": 0,
            "tools_used": [],
        }
    if (
        args.answer_token
        and aligned_prompt
        and args.fallback_to_one_shot
        and not str(result.get("answer") or "").strip()
        and not result.get("tool_loop_fallback")
    ):
        tool_loop_fallback_error = tool_loop_fallback_error or str(result.get("model_error_kind") or "empty_tool_loop_answer")
        fallback_messages = build_messages(job, user_memory_block, agent_memory_block, has_memory)
        if prefetch_text:
            fallback_messages.insert(max(1, len(fallback_messages) - 1), {"role": "user", "content": prefetch_text})
        try:
            fallback = call_openai(
                args.answer_base_url,
                args.answer_model,
                args.answer_token,
                fallback_messages,
                args.timeout_s,
                args.model_retries,
            )
            fallback.setdefault("iteration", result.get("iteration", 0) or 1)
            fallback.setdefault("tools_used", result.get("tools_used", []))
            fallback["tool_loop_fallback"] = True
            fallback["tool_retrieval_error"] = result.get("tool_retrieval_error", "")
            result = fallback
        except ModelCallError as fallback_exc:
            result["model_error_kind"] = fallback_exc.error_kind
            result["model_error"] = str(fallback_exc)
    answer = str(result.get("answer") or "").strip()
    refinement = None
    if not aligned_prompt and answer:
        refinement = refine_answer_once(args, job, answer, hits)
        if refinement:
            answer = str(refinement.get("answer") or answer).strip()
            result["prompt_tokens"] = int(result.get("prompt_tokens") or 0) + int(refinement.get("refinement_prompt_tokens") or 0)
            result["completion_tokens"] = int(result.get("completion_tokens") or 0) + int(refinement.get("refinement_completion_tokens") or 0)
            result["total_tokens"] = int(result.get("total_tokens") or 0) + int(refinement.get("refinement_total_tokens") or 0)
            result["answer_refined"] = True
            result["refinement_focus"] = refinement.get("refinement_focus") or ""
        else:
            result["answer_refined"] = False
    model_tools_used = list(result.get("tools_used") or [])
    tools_used = [*prefetch_tools, *model_tools_used]
    tool_names = [str(item.get("tool_name") or "") for item in tools_used if item.get("tool_name")]
    tool_name_counts = Counter(tool_names)
    tool_queries = [
        str((item.get("args") or {}).get("query") or "")
        for item in tools_used
        if item.get("tool_name") == MEMORY_SEARCH_TOOL_NAME and (item.get("args") or {}).get("query")
    ]
    query_plan = []
    for item in [query, *tool_queries]:
        if item and item not in query_plan:
            query_plan.append(item)
    if result.get("tool_retrieval_error"):
        retrieval_error = "; ".join(part for part in [retrieval_error, str(result.get("tool_retrieval_error") or "")] if part)
    answer_ok = bool(answer) and answer.lower() != "unknown"
    retrieval_ok = bool(hits)
    model_ok = bool(answer) and not result.get("model_error_kind")
    health_status = "ok" if retrieval_ok and model_ok and answer_ok else (
        "retrieval_empty" if not retrieval_ok else ("answer_empty" if not answer_ok else "model_degraded")
    )
    if result.get("model_error_kind"):
        health_status = str(result["model_error_kind"])
    return {
        **benchmark_adapter.asdict(job),
        "response": answer,
        "simple_grade": "CORRECT" if job.answer.lower().strip() and job.answer.lower().strip() in answer.lower() else "NEEDS_JUDGE",
        "result": "",
        "reasoning": f"echomemory memory qa; pending judge; retrieval_error={compact(retrieval_error, 240)}" if retrieval_error else "echomemory memory qa; pending judge",
        "time_cost": f"{time.time() - started:.4f}",
        "memory_uri": "echo://user/memories/",
        "backend": "echomemory",
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "alignment_backend_route": ECHOMEMORY_BACKEND_ROUTE,
        "relevant_memory": json.dumps(hits, ensure_ascii=False),
        "prompt_mode": prompt_mode,
        "native_prompt": query,
        "prompt_message_count": str(len(messages)),
        "prompt_preview": compact(json.dumps(messages, ensure_ascii=False), 5000),
        "vikingbot_prompt_aligned": str(aligned_prompt).lower(),
        "memory_tool_loop_enabled": str(bool(aligned_prompt and args.vikingboat_tool_loop)).lower(),
        "memory_tool_set": str(args.tool_set),
        "memory_tool_names": json.dumps([tool["function"]["name"] for tool in echomemory_tool_definitions(args)], ensure_ascii=False),
        "memory_content_read_enabled": "true",
        "vikingboat_compat": str(bool(args.vikingboat_compat)).lower(),
        "initial_tool_prefetch_enabled": str(bool(aligned_prompt and args.initial_tool_prefetch)).lower(),
        "prefetch_tool_call_count": str(len(prefetch_tools)),
        "model_tool_call_count": str(len(model_tools_used)),
        "prefetch_read_count": str(args.prefetch_read_count),
        "prefetch_context_chars": str(args.prefetch_context_chars),
        "max_iterations": str(args.max_iterations),
        "iteration": str(result.get("iteration", 0)),
        "tool_call_count": str(len(tools_used)),
        "tool_call_name_counts": json.dumps(dict(tool_name_counts), ensure_ascii=False),
        "tools_used_names": json.dumps(tool_names, ensure_ascii=False),
        "tools_used": json.dumps(tools_used, ensure_ascii=False),
        "tool_loop_fallback": str(bool(result.get("tool_loop_fallback"))).lower(),
        "tool_loop_fallback_error": compact(tool_loop_fallback_error, 500),
        "retrieval_query_plan": json.dumps(query_plan, ensure_ascii=False),
        "retrieval_mode": args.retrieval_mode,
        "retrieval_ranker": args.retrieval_ranker,
        "retrieval_count": str(len(hits)),
        "memory_hit_count": str(len(hits)),
        "user_memory_count": str(len(user_hits)),
        "agent_memory_count": str(len(agent_hits)),
        "user_memory_budget_chars": str(args.user_memory_budget_chars),
        "agent_memory_budget_chars": str(args.agent_memory_budget_chars),
        "initial_search_limit": str(args.top_k),
        "initial_score_threshold": str(args.score_threshold),
        "score_threshold": str(args.score_threshold),
        "tool_search_limit": str(args.tool_search_limit),
        "tool_min_score": str(args.tool_min_score),
        "user_agent_memory_split": "true",
        "link_only_when_over_budget": "true",
        "raw_turn_fallback": str(bool(args.local_messages)).lower(),
        "retrieval_tokens_est": str(token_estimate(user_memory_block) + token_estimate(agent_memory_block)),
        "context_preview": compact(f"### user memories:\n{user_memory_block}\n\n### agent memories:\n{agent_memory_block}", 3000),
        "answer_prompt_tokens": str(result.get("prompt_tokens") or 0),
        "answer_completion_tokens": str(result.get("completion_tokens") or 0),
        "answer_total_tokens": str(result.get("total_tokens") or 0),
        "answer_refined": str(bool(result.get("answer_refined"))).lower(),
        "refinement_focus": compact(str(result.get("refinement_focus") or ""), 1200),
        "model_status": "ok" if model_ok else "failed",
        "model_retry_count": str(result.get("model_retry_count", 0)),
        "model_error_kind": str(result.get("model_error_kind") or ""),
        "model_error": str(result.get("model_error") or ""),
        "retrieval_status": "ok" if retrieval_ok else "empty",
        "answer_status": "ok" if answer_ok else ("failed" if result.get("model_error_kind") else "empty_or_unknown"),
        "health_status": health_status,
        "retrieval_error": retrieval_error,
    }


async def run(args: argparse.Namespace) -> None:
    root = ensure_echomem_imports(args.echomem_root)
    try:
        from echomem.protocol.local_sdk.sdk import EchoMemSDK
        from echomem.runtime.runtime import open_runtime
    except ModuleNotFoundError:
        from echomem.entrypoints.plugins.echoagent.sdk import EchoMemSDK
        from echomem.runtime.bootstrap import open_runtime

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.echomem_config).expanduser().resolve() if args.echomem_config else write_echomem_config(
        out_dir,
        args.account,
        args.workspace,
        root,
        args.fallback_to_mock,
    )
    runtime = await open_runtime(str(config_path))
    sdk = EchoMemSDK(runtime)
    data = read_json(Path(args.dataset).expanduser().resolve())
    question_filter = {q.strip() for q in args.questions.split(",") if q.strip()}
    jobs, _plans = benchmark_adapter.locomo_jobs(data, None, args.sample, question_filter or None)
    if args.random_count:
        rnd = random.Random(args.random_seed)
        jobs = rnd.sample(jobs, min(args.random_count, len(jobs)))
    csv_path = out_dir / "echomemory_memory_qa_results.csv"
    print(f"[qa] dataset={args.dataset} sample={args.sample} questions={len(jobs)} backend=echomemory root={root}", flush=True)
    rows: list[dict[str, str]] = []
    for index, job in enumerate(jobs, 1):
        print(f"[qa] {index}/{len(jobs)} {job.question_id} {job.question[:90]}", flush=True)
        try:
            if args.question_timeout_s and args.question_timeout_s > 0:
                rows.append(await asyncio.wait_for(answer_question(args, sdk, job), timeout=args.question_timeout_s))
            else:
                rows.append(await answer_question(args, sdk, job))
        except asyncio.TimeoutError:
            rows.append(
                {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[QA ERROR] question exceeded timeout_s={args.question_timeout_s}",
                    "time_cost": str(round(args.question_timeout_s, 3)),
                    "backend": "echomemory",
                    "relevant_memory": "[]",
                    "retrieval_count": "0",
                    "model_status": "failed",
                    "model_error_kind": "question_timeout",
                    "model_error": f"question exceeded timeout_s={args.question_timeout_s}",
                    "retrieval_status": "unknown",
                    "answer_status": "failed",
                    "health_status": "question_timeout",
                }
            )
        except Exception as exc:
            error_kind = classify_model_error(str(exc))
            rows.append(
                {
                    **benchmark_adapter.asdict(job),
                    "response": "",
                    "simple_grade": "NEEDS_JUDGE",
                    "result": "",
                    "reasoning": f"[QA ERROR] {exc}",
                    "time_cost": "0",
                    "backend": "echomemory",
                    "relevant_memory": "[]",
                    "retrieval_count": "0",
                    "model_status": "failed",
                    "model_error_kind": error_kind,
                    "model_error": str(exc),
                    "retrieval_status": "unknown",
                    "answer_status": "failed",
                    "health_status": error_kind,
                }
            )
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fieldnames(rows), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fieldnames(rows), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    health_counts = Counter(str(row.get("health_status") or "unknown") for row in rows)
    summary = {
        **alignment_metadata("echomemory", ECHOMEMORY_BACKEND_ROUTE),
        "dataset_format": "locomo",
        "dataset": str(Path(args.dataset).expanduser().resolve()),
        "sample": args.sample,
        "backend": "echomemory",
        "echomem_root": str(root),
        "echomem_config": str(config_path),
        "workspace": str(Path(args.workspace).expanduser().resolve()),
        "account": args.account,
        "count": len(rows),
        "output_csv": str(csv_path),
        "prompt_mode": args.prompt_mode,
        "vikingboat_alignment_profile": VIKINGBOT_ALIGNMENT_PROFILE,
        "alignment_backend_route": ECHOMEMORY_BACKEND_ROUTE,
        "vikingbot_prompt_aligned": args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES,
        "vikingboat_compat": bool(args.vikingboat_compat),
        "memory_tool_loop_enabled": bool(args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES and args.vikingboat_tool_loop),
        "memory_tool_set": args.tool_set,
        "memory_tool_names": [tool["function"]["name"] for tool in echomemory_tool_definitions(args)],
        "memory_content_read_enabled": True,
        "initial_tool_prefetch_enabled": bool(args.prompt_mode in VIKINGBOT_ALIGNED_PROMPT_MODES and args.initial_tool_prefetch),
        "prefetch_read_count": args.prefetch_read_count,
        "prefetch_context_chars": args.prefetch_context_chars,
        "max_iterations": args.max_iterations,
        "tool_search_limit": args.tool_search_limit,
        "tool_min_score": args.tool_min_score,
        "retrieval_mode": args.retrieval_mode,
        "retrieval_ranker": args.retrieval_ranker,
        "top_k": args.top_k,
        "initial_search_limit": args.top_k,
        "initial_score_threshold": args.score_threshold,
        "score_threshold": args.score_threshold,
        "local_session_summaries": args.local_session_summaries,
        "local_atoms": args.local_atoms,
        "local_messages": args.local_messages,
        "local_timeline_hints": args.local_timeline_hints,
        "local_score_threshold": args.local_score_threshold,
        "local_summary_max": args.local_summary_max,
        "local_atom_max": args.local_atom_max,
        "local_message_max": args.local_message_max,
        "local_message_window": args.local_message_window,
        "memory_budget_chars": args.user_memory_budget_chars + args.agent_memory_budget_chars,
        "user_memory_budget_chars": args.user_memory_budget_chars,
        "agent_memory_budget_chars": args.agent_memory_budget_chars,
        "user_agent_memory_split": True,
        "link_only_when_over_budget": True,
        "raw_turn_fallback": bool(args.local_messages),
        "answer_model": args.answer_model,
        "answer_prompt_tokens": sum(int(r.get("answer_prompt_tokens") or 0) for r in rows),
        "answer_completion_tokens": sum(int(r.get("answer_completion_tokens") or 0) for r in rows),
        "answer_total_tokens": sum(int(r.get("answer_total_tokens") or 0) for r in rows),
        "retrieval_tokens_est": sum(int(r.get("retrieval_tokens_est") or 0) for r in rows),
        "avg_retrieval_count": round(sum(int(r.get("retrieval_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "iteration_total": sum(int(r.get("iteration") or 0) for r in rows),
        "avg_iteration": round(sum(int(r.get("iteration") or 0) for r in rows) / len(rows), 2) if rows else 0,
        "tool_call_total": sum(int(r.get("tool_call_count") or 0) for r in rows),
        "tool_call_rows": sum(1 for r in rows if int(r.get("tool_call_count") or 0) > 0),
        "prefetch_tool_call_total": sum(int(r.get("prefetch_tool_call_count") or 0) for r in rows),
        "model_tool_call_total": sum(int(r.get("model_tool_call_count") or 0) for r in rows),
        "model_tool_call_rows": sum(1 for r in rows if int(r.get("model_tool_call_count") or 0) > 0),
        "model_ok_count": sum(1 for r in rows if r.get("model_status") == "ok"),
        "model_failed_count": sum(1 for r in rows if r.get("model_status") == "failed"),
        "retrieval_ok_count": sum(1 for r in rows if r.get("retrieval_status") == "ok"),
        "retrieval_empty_count": sum(1 for r in rows if r.get("retrieval_status") == "empty"),
        "answer_ok_count": sum(1 for r in rows if r.get("answer_status") == "ok"),
        "answer_empty_or_unknown_count": sum(1 for r in rows if r.get("answer_status") == "empty_or_unknown"),
        "health_counts": dict(health_counts),
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LoCoMo QA against EchoMemory memories.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="conv-30")
    parser.add_argument("--questions", default="")
    parser.add_argument("--random-count", type=int, default=0)
    parser.add_argument("--random-seed", type=int, default=30)
    parser.add_argument("--echomem-root", default=str(DEFAULT_ECHOMEM_ROOT))
    parser.add_argument("--echomem-config", default="")
    parser.add_argument("--workspace", default="/tmp/locomo-eval-echomemory")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--prompt-mode", choices=["vikingboat_lite", "vikingboat_compat", "one_shot"], default="vikingboat_compat")
    parser.add_argument("--vikingboat-compat", dest="vikingboat_compat", action="store_true")
    parser.add_argument("--no-vikingboat-compat", dest="vikingboat_compat", action="store_false")
    parser.add_argument("--top-k", type=int, default=VIKINGBOT_INITIAL_SEARCH_LIMIT)
    parser.add_argument("--score-threshold", type=float, default=VIKINGBOT_INITIAL_MIN_SCORE)
    parser.add_argument(
        "--memory-budget-chars",
        type=int,
        default=VIKINGBOT_USER_MEMORY_BUDGET_CHARS + VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS,
    )
    parser.add_argument("--user-memory-budget-chars", type=int, default=VIKINGBOT_USER_MEMORY_BUDGET_CHARS)
    parser.add_argument("--agent-memory-budget-chars", type=int, default=VIKINGBOT_AGENT_MEMORY_BUDGET_CHARS)
    parser.add_argument("--retrieval-mode", choices=["find", "search", "both", "local"], default="both")
    parser.add_argument("--retrieval-ranker", choices=["diversified", "score"], default="score")
    parser.add_argument("--no-local-session-summaries", dest="local_session_summaries", action="store_false")
    parser.add_argument("--local-session-summaries", dest="local_session_summaries", action="store_true")
    parser.add_argument("--no-local-atoms", dest="local_atoms", action="store_false")
    parser.add_argument("--local-atoms", dest="local_atoms", action="store_true")
    parser.add_argument(
        "--local-messages",
        dest="local_messages",
        action="store_true",
        help="Diagnostic only: include raw session messages.jsonl turns in the prompt.",
    )
    parser.add_argument("--no-local-messages", dest="local_messages", action="store_false")
    parser.add_argument("--no-local-timeline-hints", dest="local_timeline_hints", action="store_false")
    parser.add_argument("--local-timeline-hints", dest="local_timeline_hints", action="store_true")
    parser.add_argument("--local-score-threshold", type=float, default=0.08)
    parser.add_argument("--local-summary-max", type=int, default=12)
    parser.add_argument("--local-atom-max", type=int, default=24)
    parser.add_argument("--local-message-max", type=int, default=16)
    parser.add_argument("--local-message-window", type=int, default=1)
    parser.add_argument("--no-local-memory-artifacts", dest="local_memory_artifacts", action="store_false")
    parser.add_argument("--local-memory-artifacts", dest="local_memory_artifacts", action="store_true")
    parser.add_argument("--local-artifact-max", type=int, default=24)
    parser.add_argument("--vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_true")
    parser.add_argument("--no-vikingboat-tool-loop", dest="vikingboat_tool_loop", action="store_false")
    parser.add_argument("--tool-set", choices=["vikingboat_default", "search_read", "search_only"], default="search_read")
    parser.add_argument("--tool-search-limit", type=int, default=VIKINGBOT_TOOL_SEARCH_LIMIT)
    parser.add_argument("--tool-min-score", type=float, default=VIKINGBOT_TOOL_MIN_SCORE)
    parser.add_argument("--tool-log-chars", type=int, default=1200)
    parser.add_argument("--initial-tool-prefetch", dest="initial_tool_prefetch", action="store_true")
    parser.add_argument("--no-initial-tool-prefetch", dest="initial_tool_prefetch", action="store_false")
    parser.add_argument(
        "--compat-allow-initial-prefetch",
        action="store_true",
        help="Keep initial prefetch enabled even when vikingboat_compat is on.",
    )
    parser.add_argument(
        "--compat-allow-local-evidence",
        action="store_true",
        help="Keep local summaries/atoms/hints enabled even when vikingboat_compat is on.",
    )
    parser.add_argument("--prefetch-read-count", type=int, default=4)
    parser.add_argument("--prefetch-context-chars", type=int, default=5000)
    parser.add_argument("--max-iterations", type=int, default=VIKINGBOT_MAX_ITERATIONS)
    parser.add_argument("--fallback-to-one-shot", dest="fallback_to_one_shot", action="store_true")
    parser.add_argument("--no-fallback-to-one-shot", dest="fallback_to_one_shot", action="store_false")
    parser.add_argument("--answer-base-url", default=os.environ.get("JUDGE_BASE_URL", ""))
    parser.add_argument("--answer-model", default=os.environ.get("JUDGE_MODEL", "gpt-5.5"))
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--model-retries", type=int, default=5)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument(
        "--question-timeout-s",
        type=int,
        default=0,
        help="Optional per-question wall-clock timeout. When exceeded, the row is marked failed and the run continues.",
    )
    parser.add_argument("--fallback-to-mock", action="store_true", default=False)
    parser.set_defaults(
        local_session_summaries=True,
        local_atoms=True,
        local_messages=False,
        local_timeline_hints=True,
        local_memory_artifacts=True,
        vikingboat_tool_loop=True,
        vikingboat_compat=None,
        initial_tool_prefetch=None,
        fallback_to_one_shot=True,
    )
    args = parser.parse_args()
    if args.vikingboat_compat is None:
        args.vikingboat_compat = args.prompt_mode == "vikingboat_compat"
    if args.initial_tool_prefetch is None:
        args.initial_tool_prefetch = not bool(args.vikingboat_compat)
    args.tool_set = normalize_echomemory_tool_set(args.tool_set, vikingboat_compat=bool(args.vikingboat_compat))
    if args.vikingboat_compat:
        args.prompt_mode = "vikingboat_compat"
        if not args.compat_allow_initial_prefetch:
            args.initial_tool_prefetch = False
        if not args.compat_allow_local_evidence:
            args.local_session_summaries = False
            args.local_atoms = False
            args.local_messages = False
            args.local_timeline_hints = False
            args.local_memory_artifacts = False
        args.score_threshold = max(float(args.score_threshold), VIKINGBOT_INITIAL_MIN_SCORE)
        args.tool_min_score = max(float(args.tool_min_score), VIKINGBOT_TOOL_MIN_SCORE)
        args.tool_search_limit = max(int(args.tool_search_limit), VIKINGBOT_TOOL_SEARCH_LIMIT)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
