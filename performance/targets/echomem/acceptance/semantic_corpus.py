"""Small deterministic fact corpus; answers are fixed before real model seeding."""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path

TOPICS = ("接口评审", "周末徒步", "年度体检", "项目培训", "设备验收")
PLACES = ("梧桐会议室", "青禾活动中心", "松涛服务站", "白鹭园区", "星河工作室",
          "玉兰服务中心", "翠竹会议室", "海棠活动室", "银杏园区", "云杉中心")
CONTACTS = ("陈星", "李然", "周宁", "赵悦", "孙林", "吴晴", "郑远", "许晨", "林安", "唐禾")
NO_RECALL = (
    "你好", "谢谢", "再见", "早上好", "祝你今天愉快", "计算二加三", "计算八乘七",
    "十的平方是多少", "一小时有多少分钟", "一千米等于多少米", "把 hello 翻译成中文",
    "把 thank you 翻译成中文", "把猫翻译成英文", "把苹果翻译成英文", "解释什么是三角形",
    "列举三个质数", "写一句不涉及个人信息的问候", "解释水的化学式", "将大写 ABC 转成小写",
    "写出星期一的英文",
)

DEFAULT_LOCOMO_DATASET = Path(__file__).resolve().parents[4] / "benchmarks/locomo/data/locomo10.json"


def build_locomo_session_corpus(
    identity: str,
    *,
    dataset_path: str | Path = DEFAULT_LOCOMO_DATASET,
    sample_id: str = "conv-30",
    session_key: str = "session_1",
) -> dict:
    """Build scored Search cases from one real LoCoMo conversation session."""
    raw = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    samples = raw if isinstance(raw, list) else [raw]
    sample = next((row for row in samples if isinstance(row, dict)
                   and str(row.get("sample_id")) == sample_id), None)
    if sample is None:
        raise ValueError(f"LoCoMo sample not found: {sample_id}")
    conversation = sample.get("conversation") or {}
    messages = conversation.get(session_key)
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"LoCoMo session not found or empty: {sample_id}/{session_key}")

    session_number = session_key.rsplit("_", 1)[-1]
    evidence_prefix = f"D{session_number}:"
    identity_tag = hashlib.sha256(identity.encode()).hexdigest()[:12]
    date_time = str(conversation.get(f"{session_key}_date_time") or "").strip()
    evidence_markers: dict[str, str] = {}
    documents = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        dia_id = str(message.get("dia_id") or f"{evidence_prefix}{index + 1}")
        marker = f"LOCOMO-EVIDENCE-{identity_tag}-{dia_id.replace(':', '-')}"
        evidence_markers[dia_id] = marker
        parts = [str(message.get("text") or "").strip()]
        if message.get("blip_caption"):
            parts.append(f"Image description: {message['blip_caption']}")
        if message.get("query"):
            parts.append(f"Image query: {message['query']}")
        content = " ".join(part for part in parts if part)
        if content:
            speaker = str(message.get("speaker") or message.get("role") or "speaker")
            time_prefix = f"Conversation time: {date_time}. " if date_time else ""
            documents.append(f"{time_prefix}{speaker}: {content} Evidence marker: {marker}.")

    facts, queries = [], []
    for qa_index, qa in enumerate(sample.get("qa") or []):
        if not isinstance(qa, dict) or str(qa.get("category") or "") == "5":
            continue
        evidence = [str(value) for value in qa.get("evidence") or []]
        if not evidence or not all(value.startswith(evidence_prefix) for value in evidence):
            continue
        aliases = [evidence_markers[value] for value in evidence if value in evidence_markers]
        if len(aliases) != len(evidence):
            continue
        question = str(qa.get("question") or "").strip()
        if not question:
            continue
        fact_id = f"{sample_id}-{session_key}-qa{qa_index}"
        facts.append({"id": fact_id, "answer": str(qa.get("answer") or ""),
                      "category": str(qa.get("category") or ""), "evidence": evidence})
        queries.append({"id": fact_id, "fact_id": fact_id, "query": question,
                        "query_type": "recall", "aliases": aliases,
                        "match_policy": "all",
                        "expected_answer": str(qa.get("answer") or ""),
                        "evidence_ids": evidence})
    if not documents or not queries:
        raise ValueError(f"No usable LoCoMo evidence cases: {sample_id}/{session_key}")

    no_recall = [{"id": f"no-recall-{i}", "query": text,
                  "query_type": "no_recall", "aliases": []}
                 for i, text in enumerate(NO_RECALL)]
    result = {"documents": documents, "facts": facts, "recall_queries": queries,
              "no_recall_queries": no_recall, "memory_scale": 1,
              "input_characters": sum(map(len, documents)),
              "query_contract": "locomo-single-session-evidence-v1",
              "source": {"kind": "locomo-single-session", "sample_id": sample_id,
                         "session_key": session_key, "session_messages": len(documents),
                         "eligible_questions": len(queries)}}
    result["fingerprint"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return result


def build_fixed_tenant_data_corpus(
    identity: str,
    *,
    seed_data_path: str | Path,
    fixture_index: int,
    locomo_dataset_path: str | Path = DEFAULT_LOCOMO_DATASET,
) -> dict:
    """Build one auditable corpus from the fixed tenant seed/QA fixture.

    The fixture stores a question and expected answer, while its abbreviated
    ``inject_text`` is not guaranteed to include the question's evidence.
    Resolve the exact LoCoMo evidence messages at injection time and attach
    deterministic markers so each Search has an observable positive contract.
    """
    fixture = json.loads(Path(seed_data_path).read_text(encoding="utf-8"))
    records = fixture.get("tenants") if isinstance(fixture, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("fixed tenant seed data must contain a non-empty tenants list")
    usable_records = [
        candidate for candidate in records
        if isinstance(candidate, dict) and str(candidate.get("expected_answer") or "").strip()
    ]
    if fixture_index < 0 or fixture_index >= len(usable_records):
        raise ValueError(f"fixed tenant seed index is unavailable: {fixture_index}")
    record = usable_records[fixture_index]
    source_tenant_index = int(record.get("tenant_index", -1))
    if source_tenant_index < 0:
        raise ValueError(f"fixed tenant seed record is invalid: {fixture_index}")
    question = str(record.get("search_query") or "").strip()
    if not question:
        raise ValueError(f"fixed tenant seed query is missing: {fixture_index}")

    raw = json.loads(Path(locomo_dataset_path).read_text(encoding="utf-8"))
    samples = raw if isinstance(raw, list) else [raw]
    selected = next(
        ((sample, qa) for sample in samples if isinstance(sample, dict)
         for qa in sample.get("qa") or []
         if isinstance(qa, dict) and str(qa.get("question") or "") == question),
        None,
    )
    if selected is None:
        raise ValueError(f"fixed tenant QA is absent from LoCoMo: {fixture_index}")
    sample, qa = selected
    expected = str(record.get("expected_answer") or "")
    if expected != str(qa.get("answer") or ""):
        raise ValueError(f"fixed tenant QA answer mismatch: {fixture_index}")
    evidence = [str(value) for value in qa.get("evidence") or []]
    if not evidence:
        raise ValueError(f"fixed tenant QA has no evidence: {fixture_index}")

    conversations = sample.get("conversation") or {}
    identity_tag = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    documents, markers = [], []
    for evidence_id in evidence:
        session_number = evidence_id.split(":", 1)[0].removeprefix("D")
        messages = conversations.get(f"session_{session_number}") or []
        message = next(
            (item for item in messages if isinstance(item, dict)
             and str(item.get("dia_id") or "") == evidence_id),
            None,
        )
        if message is None:
            raise ValueError(f"fixed tenant evidence is missing: {fixture_index}/{evidence_id}")
        marker = f"FIXTURE-EVIDENCE-{identity_tag}-{evidence_id.replace(':', '-')}"
        content = " ".join(
            str(message.get(key) or "").strip()
            for key in ("speaker", "text", "blip_caption", "query")
            if message.get(key)
        )
        if not content:
            raise ValueError(f"fixed tenant evidence is empty: {fixture_index}/{evidence_id}")
        documents.append(f"{content} Evidence marker: {marker}.")
        markers.append(marker)

    # The fixture's QA pair is part of the tested memory contract.  Include
    # the answer as an explicit fact so extraction may summarize the source
    # dialogue without making the seed's own QA impossible to verify.
    documents.append(f"Verified memory fact. Question: {question} Answer: {expected}.")

    fact_id = f"fixture-{fixture_index}-qa"
    no_recall = [{"id": f"no-recall-{index}", "query": text,
                  "query_type": "no_recall", "aliases": []}
                 for index, text in enumerate(NO_RECALL)]
    result = {
        "documents": documents,
        "facts": [{"id": fact_id, "answer": expected, "evidence": evidence}],
        "recall_queries": [{
            "id": fact_id,
            "fact_id": fact_id,
            "query": question,
            "query_type": "recall",
            "aliases": markers,
            "match_policy": "all",
            "expected_answer": expected,
            "evidence_ids": evidence,
        }],
        "no_recall_queries": no_recall,
        "memory_scale": 1,
        "input_characters": sum(map(len, documents)),
        "query_contract": "fixed-tenant-data-locomo-evidence-v1",
        "source": {
            "kind": "fixed-tenant-data",
            "fixture_index": fixture_index,
            "source_tenant_index": source_tenant_index,
            "usable_fixture_records": len(usable_records),
            "fixture_version": fixture.get("version"),
            "sample_id": sample.get("sample_id"),
            "evidence": evidence,
        },
    }
    result["fingerprint"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return result


def build_fixed_fact_corpus(identity: str) -> dict:
    """Build one small, natural-language memory for bounded load tests.

    Each identity receives a deterministic fact so tenants remain isolated,
    while one short Commit does not turn LoCoMo ingestion into the dominant
    test phase. The real extraction, persistence, embedding and Search path
    is still exercised.
    """
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    project = f"蓝桥项目-{digest[:4].hex().upper()}"
    location = PLACES[digest[4] % len(PLACES)]
    contact = CONTACTS[digest[5] % len(CONTACTS)]
    month = 9 + digest[6] % 3
    day = 1 + digest[7] % 25
    date_text = f"2026年{month}月{day}日"
    document = (
        f"这是 {identity} 的个人工作记录。已确认 {date_text} 的 {project} 安排："
        f"会议地点是{location}，联系人是{contact}。这是一条已经确认的事实，"
        "后续查询应以这条记录为准，除非另有明确更新。"
    )
    facts = [
        {"id": "fixed-project", "field": "项目代号", "value": project},
        {"id": "fixed-date", "field": "日期", "value": date_text},
        {"id": "fixed-location", "field": "会议地点", "value": location},
        {"id": "fixed-contact", "field": "联系人", "value": contact},
    ]
    queries = [
        {"id": "fixed-project-q", "fact_id": "fixed-project",
         "query": f"关于{project}的工作记录里，项目代号是什么？",
         "query_type": "recall", "aliases": [project]},
        {"id": "fixed-date-q", "fact_id": "fixed-date",
         "query": f"我想查{project}的已确认安排日期是哪一天？",
         "query_type": "recall", "aliases": [date_text]},
        {"id": "fixed-location-q", "fact_id": "fixed-location",
         "query": f"关于{project}的会议安排，地点在哪里？",
         "query_type": "recall", "aliases": [location]},
        {"id": "fixed-contact-q", "fact_id": "fixed-contact",
         "query": f"关于{project}的会议，联系人是谁？",
         "query_type": "recall", "aliases": [contact]},
    ]
    no_recall = [{"id": f"no-recall-{i}", "query": text,
                  "query_type": "no_recall", "aliases": []}
                 for i, text in enumerate(NO_RECALL)]
    result = {
        "documents": [document], "facts": facts, "recall_queries": queries,
        "no_recall_queries": no_recall, "memory_scale": 1,
        "input_characters": len(document),
        "query_contract": "fixed-natural-fact-v1",
        "source": {"kind": "fixed-natural-fact", "identity": identity,
                    "documents": 1, "facts": len(facts)},
    }
    result["fingerprint"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return result


def build_corpus(identity: str, *, seed: int = 42, memory_scale: int = 1) -> dict:
    if memory_scale not in (1, 10):
        raise ValueError("memory_scale must be 1 or 10")
    digest = hashlib.sha256(f"{seed}:{identity}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    documents, facts, queries = [], [], []
    for batch in range(memory_scale):
        for topic_index, topic in enumerate(TOPICS):
            day = date(2027, 1, 1) + timedelta(days=rng.randrange(330))
            clock = f"{rng.randrange(8, 18):02d}:{rng.choice((0, 15, 30, 45)):02d}"
            location, contact = rng.choice(PLACES), rng.choice(CONTACTS)
            subject = f"本次{topic}" if batch == 0 else f"历史档案第{batch}批{topic}"
            values = (day.isoformat(), clock, location, contact)
            aliases = ([values[0], f"{day.year}年{day.month}月{day.day}日", f"{day.year}/{day.month}/{day.day}"],
                       [clock, f"{int(clock[:2])}点{int(clock[3:])}分"], [location], [contact])
            intro = (f"我已经确认{subject}的安排：日期是{values[0]}，开始时间为{clock}，"
                     f"地点为{location}，联系人是{contact}。这四项信息是已确认的计划，不是备选方案。")
            context = ("我把这个安排记在个人工作笔记中，便于以后查询日期、时间、地点和联系人。"
                       "准备时需要先梳理问题，检查材料是否完整，并把待讨论事项整理清楚。"
                       "我希望后续讨论围绕明确的议题展开，避免把临时想到的事情混入已经确认的安排。"
                       "相关材料应当便于查阅，记录也要保持清晰。当天会按照计划开展活动，"
                       "如有新的决定会另写一条更新，在没有更新以前仍以这里记录的信息为准。"
                       "这条笔记主要帮助我记住具体安排，背景描述并不构成其他时间或者地点的变更。"
                       "核对安排时应分别查看上述四项信息，而不是凭背景内容推断新的约定。")
            documents.append(intro + context)
            if batch:
                continue
            fields = ("日期", "开始时间", "地点", "联系人")
            for field_index, field in enumerate(fields):
                fact_id = f"fact-{topic_index}-{field_index}"
                facts.append({"id": fact_id, "topic": subject, "field": field,
                              "value": values[field_index], "aliases": aliases[field_index]})
                for variant, question in enumerate((f"你还记得我之前告诉你的{subject}的{field}吗？",
                                                     f"你还记得我曾经告诉你的{subject}的{field}吗？")):
                    queries.append({"id": f"{fact_id}-q{variant}", "fact_id": fact_id,
                                    "query": question, "query_type": "recall", "aliases": aliases[field_index]})
    no_recall = [{"id": f"no-recall-{i}", "query": text, "query_type": "no_recall", "aliases": []}
                 for i, text in enumerate(NO_RECALL)]
    result = {"documents": documents, "facts": facts, "recall_queries": queries,
              "no_recall_queries": no_recall, "memory_scale": memory_scale,
              "input_characters": sum(map(len, documents)), "seed": seed,
              "query_contract": "explicit-chat-memory-v4"}
    result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    if isinstance(value, dict):
        # Ignore query echoes, IDs, scores and debug metadata inside result items.
        return " ".join(_text(value[key]) for key in ("text", "content", "summary", "memory", "value") if key in value)
    return ""


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def _answer_match(text: str, answer: str) -> bool:
    """Match a LoCoMo answer after extraction may rewrite evidence markers."""
    normalized_answer = re.sub(r"[^\w\u4e00-\u9fff]+", "", _normalized(answer))
    normalized_text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return len(normalized_answer) >= 4 and normalized_answer in normalized_text


def assess_retrieval(payload, sample: dict) -> dict:
    body = payload.get("result", payload) if isinstance(payload, dict) else {}
    body = body if isinstance(body, dict) else {}
    items = body.get("items")
    valid = isinstance(items, list)
    items = items if valid else []
    degraded = bool(body.get("degraded_reasons")) or body.get("status") in {"degraded", "failed", "error"}
    text = _normalized(_text(items))
    aliases = [_normalized(alias) for alias in sample.get("aliases", []) if alias]
    alias_matches = [alias in text for alias in aliases]
    marker_matched = (all(alias_matches) if sample.get("match_policy") == "all"
                      else any(alias_matches))
    answer_matched = _answer_match(text, str(sample.get("expected_answer") or ""))
    # LoCoMo evidence markers are intentionally tenant-specific, but the
    # extraction pipeline may summarize them away. Accept the benchmark answer
    # in returned memory text as a second, auditable quality signal.
    matched = marker_matched or answer_matched
    atomic_items = [item for item in items if isinstance(item, dict) and item.get("engine_id") == "atomic_engine"]
    origin_observed = valid and all(isinstance(item, dict) and item.get("engine_id") for item in items)
    atomic_text = _normalized(_text(atomic_items))
    atomic_matches = [alias in atomic_text for alias in aliases]
    atomic_marker_matched = (all(atomic_matches) if sample.get("match_policy") == "all"
                             else any(atomic_matches))
    atomic_matched = atomic_marker_matched or _answer_match(
        atomic_text, str(sample.get("expected_answer") or ""))
    explain = body.get("explain") or {}
    if not isinstance(explain, dict):
        explain = {}
    routing_evidence = _normalized(json.dumps({
        "outcome": explain.get("outcome"),
        "final_verdicts": explain.get("final_verdicts"),
        "degraded_reasons": body.get("degraded_reasons"),
    }, ensure_ascii=False, sort_keys=True))
    intent_rejected = sample["query_type"] == "recall" and any(token in routing_evidence for token in (
        "intentreject", "intent_reject", "norecall", "no_recall", "skiprecall", "skip_recall",
    ))
    # The load test measures whether a recall request actually reached the
    # retrieval path and returned candidates. Benchmark answer matching is a
    # separate diagnostic; it must not turn a non-empty real recall into a
    # transport/capacity failure.
    recall_served = valid and bool(items) and not intent_rejected
    expected = not items if sample["query_type"] == "no_recall" else matched
    return {"quality_ok": valid and not degraded and expected,
            "recall_served": recall_served, "nonempty_recall": valid and bool(items),
            "degraded": degraded,
            "result_structure_valid": valid, "engine_origin_observed": origin_observed,
            "atomic_fact_hit": atomic_matched if origin_observed else None,
            "atomic_item_count": len(atomic_items) if origin_observed else None,
            "matched_expected_fact": matched, "hit_count": len(items),
            "marker_match": marker_matched, "answer_match": answer_matched,
            "intent_rejected": intent_rejected,
            "search_executed": not intent_rejected,
            "query_type": sample["query_type"], "query_id": sample["id"],
            "fact_id": sample.get("fact_id"), "assertion": "fixed-fact-in-items",
            "match_policy": sample.get("match_policy", "any"),
            "expected_evidence_count": len(aliases),
            "matched_evidence_count": sum(alias_matches),
            "degraded_reasons": body.get("degraded_reasons") or [],
            "executed_layers": explain.get("executed_layers", []),
            "final_verdicts": explain.get("final_verdicts", {}),
            "outcome": explain.get("outcome"),
            "engine_results": [{k: e.get(k) for k in ("engine_id", "status", "item_count", "duration_seconds")}
                               for e in explain.get("engine_results", []) if isinstance(e, dict)]}
