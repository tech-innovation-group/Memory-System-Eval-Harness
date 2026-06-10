#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request


STOPWORDS = {
    "what", "when", "where", "which", "who", "why", "how", "did", "does", "do",
    "the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "is", "are",
    "was", "were", "has", "have", "had", "both", "with", "from", "their",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(text: Any, limit: int = 1800) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def token_estimate(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4) if text else 0


def locomo_question_time(sample: dict[str, Any]) -> str:
    conversation = sample.get("conversation") or {}
    dates = []
    for key, value in conversation.items():
        if key.endswith("_date_time") and value:
            dates.append(str(value))
    return dates[-1] if dates else ""


def iter_locomo_jobs(data: list[dict[str, Any]], sample_id: str, limit: int, random_seed: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for sample in data:
        sid = str(sample.get("sample_id") or "")
        if sample_id and sid != sample_id:
            continue
        question_time = locomo_question_time(sample)
        for idx, qa in enumerate(sample.get("qa") or [], 1):
            if str(qa.get("category") or "") == "5":
                continue
            jobs.append(
                {
                    "sample_id": sid,
                    "question_id": f"{sid}_qa{idx - 1}",
                    "case_id": f"{sid}_Q{idx}",
                    "qi": idx,
                    "question": str(qa.get("question") or ""),
                    "answer": str(qa.get("answer") or ""),
                    "category": str(qa.get("category") or ""),
                    "evidence": qa.get("evidence") or [],
                    "query_time": question_time,
                }
            )
    if limit and len(jobs) > limit:
        rnd = random.Random(random_seed)
        if sample_id:
            jobs = jobs[:limit]
        else:
            jobs = rnd.sample(jobs, limit)
    return jobs


def load_route_labels(path: Path) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return labels
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = row.get("case_id")
        if case_id:
            labels[str(case_id)] = row
    return labels


def make_memrouter_pipeline(echomem_root: Path, runtime_config: dict[str, Any]):
    sys.path.insert(0, str(echomem_root))
    from echomem.embeddings.base import create_provider
    from echomem.llm_fallback import LLMRouterConfig
    from echomem.pipeline import MemRouterPipeline

    embedding_cfg = runtime_config.get("embedding", {}).get("dense") or runtime_config.get("embedding", {})
    provider = embedding_cfg.get("provider") or "mock"
    if provider == "openai":
        embedder = create_provider(
            "openai",
            model=embedding_cfg.get("model") or "text-embedding-v3",
            api_key=embedding_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=embedding_cfg.get("api_base") or embedding_cfg.get("base_url"),
            output_dimension=embedding_cfg.get("dimension"),
            max_batch_size=runtime_config.get("embedding", {}).get("max_concurrent") or embedding_cfg.get("max_concurrent") or 10,
        )
    else:
        embedder = create_provider("mock", dim=32)

    # Keep the MemRouter fallback deterministic here. The target of this run is
    # template/router behavior plus memory-backed QA, not extra router LLM spend.
    llm_config = LLMRouterConfig(provider="mock", model="mock")
    return MemRouterPipeline.with_defaults(
        embedder=embedder,
        llm_router_config=llm_config,
        enabled_backends=[
            "openviking_memory_backend",
            "graph_memory_backend",
            "streamlined_memory_backend",
        ],
    )


def headers(account: str, user_id: str, agent_id: str, api_key: str = "") -> dict[str, str]:
    out = {
        "Content-Type": "application/json",
        "X-OpenViking-Account": account,
        "X-OpenViking-User": user_id,
        "X-OpenViking-Agent": agent_id,
    }
    if api_key:
        out["X-API-Key"] = api_key
        out["Authorization"] = f"Bearer {api_key}"
    return out


def openviking_find(base_url: str, query: str, account: str, user_id: str, agent_id: str, api_key: str, limit: int) -> list[dict[str, Any]]:
    payload = {
        "query": query,
        "target_uri": "viking://user/memories/",
        "limit": limit,
        "score_threshold": 0,
    }
    req = request.Request(
        base_url.rstrip("/") + "/api/v1/search/find",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers(account, user_id, agent_id, api_key),
        method="POST",
    )
    with request.urlopen(req, timeout=45) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    if raw.get("status") == "error":
        raise RuntimeError(json.dumps(raw, ensure_ascii=False)[:1000])
    result = raw.get("result", raw)
    if isinstance(result, list):
        items = result
    else:
        items = result.get("items") or result.get("results") or result.get("hits") or result.get("memories") or []
        if isinstance(result.get("memories"), list) and isinstance(result.get("resources"), list):
            items = result["memories"] + result["resources"]
    return items[:limit] if isinstance(items, list) else []


def query_terms(query: str) -> list[str]:
    terms = []
    for match in re.finditer(r"[a-z0-9]{3,}", query.lower()):
        token = match.group(0)
        if token not in STOPWORDS and token not in terms:
            terms.append(token)
    return terms


def uri_to_path(workspace: Path, account: str, uri: str) -> Path | None:
    if not uri.startswith("viking://"):
        return None
    rel = uri.removeprefix("viking://").lstrip("/")
    return workspace / "viking" / account / rel


def focused_file_snippet(path: Path, query: str, limit: int = 2200) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    terms = query_terms(query)
    lower = text.lower()
    windows = []
    for term in terms:
        start = lower.find(term)
        if start < 0:
            continue
        windows.append(text[max(0, start - 700): min(len(text), start + 1200)])
    return compact("\n...\n".join(windows) if windows else text, limit)


def lexical_memory_hits(workspace: Path, account: str, user_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    roots = [
        workspace / "viking" / account / "user" / user_id / "memories",
        workspace / "viking" / account / "user" / "default" / "memories",
    ]
    terms = query_terms(query)
    hits: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            if path.name.startswith("."):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            low = text.lower()
            score = sum(1 for term in terms if term in low)
            if not score:
                continue
            rel = path.relative_to(workspace / "viking" / account)
            hits.append(
                {
                    "context_type": "memory",
                    "uri": "viking://" + str(rel).replace(os.sep, "/"),
                    "level": 2,
                    "score": score,
                    "abstract": focused_file_snippet(path, query),
                    "source": "lexical_file_fallback",
                }
            )
    hits.sort(key=lambda item: item.get("score", 0), reverse=True)
    return hits[:limit]


def memory_text(item: dict[str, Any], query: str, workspace: Path, account: str, limit: int = 2200) -> str:
    uri = str(item.get("uri") or item.get("path") or item.get("id") or "")
    path = uri_to_path(workspace, account, uri)
    body = focused_file_snippet(path, query, limit) if path else ""
    if not body:
        body = item.get("content") or item.get("text") or item.get("abstract") or item.get("overview") or ""
    return compact(f"{uri} score={item.get('score', '')}\n{body}", limit)


def call_chat(base_url: str, model: str, token: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    payload = {"model": model, "messages": messages, "temperature": 0}
    req = request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            return {
                "content": str(content).strip(),
                "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
                "total_tokens": usage.get("total_tokens") or 0,
            }
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_exc = RuntimeError(f"HTTP {exc.code}: {body[:600]}")
        except Exception as exc:
            last_exc = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last_exc or "chat call failed"))


def answer_with_evidence(job: dict[str, Any], evidence: str, base_url: str, model: str, token: str, timeout: int) -> dict[str, Any]:
    system = (
        "You answer LoCoMo memory benchmark questions using only the retrieved memory evidence. "
        "Answer concisely. If evidence is insufficient, answer unknown. Do not invent details."
    )
    user = (
        f"Question: {job['question']}\n"
        f"Query time: {job.get('query_time') or '-'}\n\n"
        f"Retrieved memory evidence:\n{evidence or '(none)'}\n\n"
        "Final answer only:"
    )
    if not token:
        return {
            "content": "unknown",
            "prompt_tokens": token_estimate(system + user),
            "completion_tokens": 1,
            "total_tokens": token_estimate(system + user) + 1,
        }
    return call_chat(base_url, model, token, [{"role": "system", "content": system}, {"role": "user", "content": user}], timeout)


def judge_answer(job: dict[str, Any], response: str, evidence: str, base_url: str, model: str, token: str, timeout: int) -> tuple[str, str]:
    if not token:
        return "", "pending judge: no judge token"
    prompt = (
        "You are grading a memory benchmark answer. Reply with JSON only: "
        "{\"result\":\"CORRECT\" or \"WRONG\", \"reasoning\":\"short reason\"}.\n"
        "Mark CORRECT if the response is semantically equivalent to the gold answer.\n\n"
        f"Question: {job['question']}\n"
        f"Gold answer: {job['answer']}\n"
        f"Response: {response}\n"
        f"Retrieved memory: {evidence[:6000]}"
    )
    result = call_chat(base_url, model, token, [{"role": "user", "content": prompt}], timeout)
    content = result["content"]
    try:
        parsed = json.loads(content)
        verdict = str(parsed.get("result") or "").upper()
        if verdict in {"CORRECT", "WRONG"}:
            return verdict, str(parsed.get("reasoning") or "")
    except Exception:
        pass
    upper = content.upper()
    if "CORRECT" in upper and "WRONG" not in upper:
        return "CORRECT", content
    return "WRONG", content


def write_report(out_dir: Path, rows: list[dict[str, str]], summary: dict[str, Any]) -> None:
    pct = "pending" if summary["accuracy"] is None else f"{summary['accuracy'] * 100:.1f}%"
    lines = [
        "# EchoMemory MemRouter LoCoMo 10Q Report",
        "",
        f"- Dataset: `{summary['dataset']}`",
        f"- EchoMemory repo: `{summary['echomem_root']}`",
        f"- EchoMemory commit: `{summary['echomem_commit']}`",
        f"- OpenViking URL: `{summary['openviking_url']}`",
        f"- Workspace: `{summary['workspace']}`",
        f"- Scope: `{summary['scope']}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Questions | {summary['count']} |",
        f"| Judged | {summary['graded']} |",
        f"| Correct | {summary['correct']} |",
        f"| Wrong | {summary['wrong']} |",
        f"| Accuracy | {pct} |",
        f"| Route template hits | {summary['template_hits']} |",
        f"| Route LLM fallback | {summary['llm_fallbacks']} |",
        f"| Answer prompt tokens | {summary['answer_prompt_tokens']} |",
        f"| Answer completion tokens | {summary['answer_completion_tokens']} |",
        f"| Answer total tokens | {summary['answer_total_tokens']} |",
        f"| Retrieval tokens est | {summary['retrieval_tokens_est']} |",
        "",
        "## Cases",
        "",
        "| Case | Cat | Route | Judge | Question | Gold | Response |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| `{}` | {} | `{}` | {} | {} | {} | {} |".format(
                row["case_id"],
                row["category"],
                row["actual_backend"],
                row.get("result") or "PENDING",
                compact(row["question"], 120).replace("|", "\\|"),
                compact(row["answer"], 80).replace("|", "\\|"),
                compact(row["response"], 160).replace("|", "\\|"),
            )
        )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def git_commit(path: Path) -> str:
    import subprocess

    try:
        result = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EchoMemory v0.0.5 + OpenViking memory LoCoMo QA.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--echomem-root", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample", default="conv-30")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--random-seed", type=int, default=30)
    parser.add_argument("--route-labels", default="")
    parser.add_argument("--openviking-url", default="http://127.0.0.1:1933")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--openviking-api-key", default="")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--answer-base-url", default="")
    parser.add_argument("--answer-model", default="")
    parser.add_argument("--answer-token", default=os.environ.get("LOCOMO_ANSWER_TOKEN") or os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--judge-base-url", default="")
    parser.add_argument("--judge-model", default="")
    parser.add_argument("--judge-token", default=os.environ.get("LOCOMO_JUDGE_TOKEN") or os.environ.get("JUDGE_TOKEN") or os.environ.get("OPENAI_API_KEY") or "")
    parser.add_argument("--timeout-s", type=int, default=120)
    args = parser.parse_args()

    dataset = Path(args.dataset).expanduser().resolve()
    echomem_root = Path(args.echomem_root).expanduser().resolve()
    runtime_config_path = Path(args.runtime_config).expanduser().resolve()
    runtime_config = read_json(runtime_config_path)
    workspace = Path(args.workspace or runtime_config.get("storage", {}).get("workspace") or "").expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    answer_base_url = args.answer_base_url or runtime_config.get("vlm", {}).get("api_base") or ""
    answer_model = args.answer_model or runtime_config.get("vlm", {}).get("model") or "gpt-5.5"
    judge_base_url = args.judge_base_url or answer_base_url
    judge_model = args.judge_model or answer_model

    labels = load_route_labels(Path(args.route_labels).expanduser().resolve()) if args.route_labels else {}
    data = read_json(dataset)
    jobs = iter_locomo_jobs(data, args.sample, args.count, args.random_seed)
    pipeline = make_memrouter_pipeline(echomem_root, runtime_config)

    csv_path = out_dir / "echomem_memrouter_locomo10_results.csv"
    rows: list[dict[str, str]] = []
    route_events_path = out_dir / "route_results.jsonl"
    relevant_memory_path = out_dir / "relevant_memory.jsonl"
    started_run = time.time()

    print(f"[start] dataset={dataset} sample={args.sample} questions={len(jobs)}", flush=True)
    print(f"[start] echomem={echomem_root} commit={git_commit(echomem_root)}", flush=True)
    print(f"[start] openviking={args.openviking_url} workspace={workspace}", flush=True)

    for index, job in enumerate(jobs, 1):
        started = time.time()
        user_id = args.user_id or job["sample_id"]
        agent_id = args.agent_id or job["sample_id"]
        route = pipeline.route(job["question"])
        route_raw = route.model_dump()
        actual_backend = route.routes[0].backend_id if route.routes else ""
        route_method = route.route_method
        label = labels.get(job["case_id"], {})
        expected_backend = label.get("expected_backend", "")
        scenario = label.get("scenario", "")

        try:
            hits = openviking_find(args.openviking_url, job["question"], args.account, user_id, agent_id, args.openviking_api_key, args.top_k)
        except Exception as exc:
            hits = []
            print(f"[warn] openviking_find failed for {job['case_id']}: {exc}", flush=True)
        seen = {item.get("uri") for item in hits}
        for item in lexical_memory_hits(workspace, args.account, user_id, job["question"], args.top_k):
            if item.get("uri") not in seen:
                hits.append(item)
                seen.add(item.get("uri"))
        hits = hits[: args.top_k]
        evidence = "\n\n".join(memory_text(item, job["question"], workspace, args.account) for item in hits)
        answer = answer_with_evidence(job, evidence, answer_base_url, answer_model, args.answer_token, args.timeout_s)
        verdict, reasoning = judge_answer(job, answer["content"], evidence, judge_base_url, judge_model, args.judge_token, args.timeout_s)

        row = {
            **{k: str(v) for k, v in job.items() if k != "evidence"},
            "expected_backend": str(expected_backend),
            "scenario": str(scenario),
            "actual_backend": str(actual_backend),
            "route_method": str(route_method),
            "matched_template_id": str(route.routes[0].matched_template_id if route.routes else ""),
            "route_confidence": str(route.routes[0].confidence if route.routes else ""),
            "is_backend_correct": str(bool(expected_backend and actual_backend == expected_backend)),
            "response": answer["content"],
            "result": verdict,
            "reasoning": reasoning,
            "time_cost": f"{time.time() - started:.4f}",
            "memory_uri": "viking://user/memories/",
            "relevant_memory": json.dumps(hits, ensure_ascii=False),
            "retrieval_count": str(len(hits)),
            "retrieval_tokens_est": str(token_estimate(evidence)),
            "injection_tokens_est": str(token_estimate(evidence)),
            "answer_prompt_tokens": str(answer.get("prompt_tokens") or token_estimate(evidence)),
            "answer_completion_tokens": str(answer.get("completion_tokens") or 0),
            "answer_total_tokens": str(answer.get("total_tokens") or 0),
            "eval_engine": "echomem_memrouter_openviking_qa",
        }
        rows.append(row)
        with route_events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"case_id": job["case_id"], "question": job["question"], "route": route_raw}, ensure_ascii=False) + "\n")
        with relevant_memory_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"case_id": job["case_id"], "question": job["question"], "memories": hits}, ensure_ascii=False) + "\n")

        fieldnames = list(dict.fromkeys(k for r in rows for k in r.keys()))
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(
            f"[qa] {index}/{len(jobs)} {job['case_id']} route={actual_backend or '-'} "
            f"judge={verdict or 'PENDING'} time={row['time_cost']}s",
            flush=True,
        )

    correct = sum(1 for row in rows if row.get("result") == "CORRECT")
    wrong = sum(1 for row in rows if row.get("result") == "WRONG")
    graded = correct + wrong
    summary = {
        "count": len(rows),
        "graded": graded,
        "correct": correct,
        "wrong": wrong,
        "accuracy": correct / graded if graded else None,
        "duration_s": round(time.time() - started_run, 3),
        "dataset": str(dataset),
        "echomem_root": str(echomem_root),
        "echomem_commit": git_commit(echomem_root),
        "openviking_url": args.openviking_url,
        "workspace": str(workspace),
        "scope": "EchoMemory MemRouter route -> OpenViking memory retrieval -> gpt-5.5 answer -> model judge",
        "output_csv": str(csv_path),
        "route_results": str(route_events_path),
        "relevant_memory": str(relevant_memory_path),
        "template_hits": sum(1 for row in rows if row.get("route_method") in {"template_embedding", "template_embedding_multi_backend"}),
        "llm_fallbacks": sum(1 for row in rows if row.get("route_method") == "llm_backend_fallback"),
        "answer_prompt_tokens": sum(int(float(row.get("answer_prompt_tokens") or 0)) for row in rows),
        "answer_completion_tokens": sum(int(float(row.get("answer_completion_tokens") or 0)) for row in rows),
        "answer_total_tokens": sum(int(float(row.get("answer_total_tokens") or 0)) for row in rows),
        "retrieval_tokens_est": sum(int(float(row.get("retrieval_tokens_est") or 0)) for row in rows),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(out_dir, rows, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
