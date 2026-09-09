"""Small deterministic fact corpus; answers are fixed before real model seeding."""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from datetime import date, timedelta

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


def assess_retrieval(payload, sample: dict) -> dict:
    body = payload.get("result", payload) if isinstance(payload, dict) else {}
    body = body if isinstance(body, dict) else {}
    items = body.get("items")
    valid = isinstance(items, list)
    items = items if valid else []
    degraded = bool(body.get("degraded_reasons")) or body.get("status") in {"degraded", "failed", "error"}
    text = _normalized(_text(items))
    matched = any(_normalized(alias) in text for alias in sample.get("aliases", []) if alias)
    atomic_items = [item for item in items if isinstance(item, dict) and item.get("engine_id") == "atomic_engine"]
    origin_observed = valid and all(isinstance(item, dict) and item.get("engine_id") for item in items)
    atomic_text = _normalized(_text(atomic_items))
    atomic_matched = any(_normalized(alias) in atomic_text for alias in sample.get("aliases", []) if alias)
    expected = not items if sample["query_type"] == "no_recall" else matched
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
    return {"quality_ok": valid and not degraded and expected, "degraded": degraded,
            "result_structure_valid": valid, "engine_origin_observed": origin_observed,
            "atomic_fact_hit": atomic_matched if origin_observed else None,
            "atomic_item_count": len(atomic_items) if origin_observed else None,
            "matched_expected_fact": matched, "hit_count": len(items),
            "intent_rejected": intent_rejected,
            "search_executed": not intent_rejected,
            "query_type": sample["query_type"], "query_id": sample["id"],
            "fact_id": sample.get("fact_id"), "assertion": "fixed-fact-in-items",
            "degraded_reasons": body.get("degraded_reasons") or [],
            "executed_layers": explain.get("executed_layers", []),
            "final_verdicts": explain.get("final_verdicts", {}),
            "outcome": explain.get("outcome"),
            "engine_results": [{k: e.get(k) for k in ("engine_id", "status", "item_count", "duration_seconds")}
                               for e in explain.get("engine_results", []) if isinstance(e, dict)]}
