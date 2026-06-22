#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class ProbeCase:
    question_id: str
    title: str
    groups: list[list[str]]


DEFAULT_PROBES = {
    "conv-30": [
        ProbeCase("conv-30_qa0", "single evidence date: banker job loss", [["2023-01-19", "19 January"], ["banker"]]),
        ProbeCase("conv-30_qa5", "multi evidence attributes: ideal studio", [["water"], ["natural light"], ["Marley"]]),
        ProbeCase("conv-30_qa29", "multi-hop places: Jon visited cities", [["Paris"], ["Rome"]]),
        ProbeCase("conv-30_qa31", "temporal reasoning: six months to studio opening", [["2023-01-19", "lost his job"], ["2023-06-20", "grand opening", "opening night"]]),
        ProbeCase("conv-30_qa39", "speaker-specific preference: Gina dance style", [["Gina"], ["Contemporary"]]),
        ProbeCase("conv-30_qa40", "speaker-specific preference: Jon dance style", [["Jon"], ["Contemporary"]]),
        ProbeCase("conv-30_qa46", "fine detail: Marley flooring", [["Marley"]]),
        ProbeCase("conv-30_qa78", "wrong-answer regression: mentor and guide reason", [["positivity"], ["determination"]]),
    ]
}


def read_json(path: Path) -> Any:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def compact(text: Any, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def find_sample(data: list[dict[str, Any]], sample_id: str) -> tuple[int, dict[str, Any]]:
    for index, sample in enumerate(data):
        if str(sample.get("sample_id") or f"sample_{index}") == sample_id or str(index) == sample_id:
            return index, sample
    raise ValueError(f"sample not found: {sample_id}")


def session_number(key: str) -> int:
    try:
        return int(str(key).split("_")[1])
    except Exception:
        return 0


def session_keys(sample: dict[str, Any]) -> list[str]:
    conv = sample.get("conversation") or {}
    keys = [key for key, value in conv.items() if re.fullmatch(r"session_\d+", str(key)) and isinstance(value, list)]
    return sorted(keys, key=session_number)


def expected_message_count(sample: dict[str, Any]) -> int:
    conv = sample.get("conversation") or {}
    return sum(len(conv.get(key) or []) for key in session_keys(sample))


def question_map(sample: dict[str, Any], sample_id: str) -> dict[str, dict[str, Any]]:
    rows = {}
    for index, item in enumerate(sample.get("qa") or []):
        qid = f"{sample_id}_qa{index}"
        rows[qid] = {**item, "question_id": qid, "question_index": index}
    return rows


def evidence_text(sample: dict[str, Any], evidence: str) -> str:
    conv = sample.get("conversation") or {}
    try:
        day, offset = str(evidence).split(":")
        session_key = f"session_{int(day[1:])}"
        item = conv[session_key][int(offset) - 1]
    except Exception:
        return ""
    parts = [str(item.get("text") or "")]
    if item.get("blip_caption"):
        parts.append(f"image: {item['blip_caption']}")
    if item.get("query"):
        parts.append(f"query: {item['query']}")
    return " ".join(part for part in parts if part).strip()


def load_import_summary(path: Path | None, runs_dir: Path, sample_id: str) -> dict[str, Any] | None:
    candidates: list[Path] = []
    if path:
        candidates.append(path.expanduser())
    candidates.extend(
        sorted(
            runs_dir.expanduser().glob("openviking_import_*/openviking_import/openviking_import_summary.json"),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
    )
    for candidate in candidates:
        try:
            summary = read_json(candidate)
        except Exception:
            continue
        for record in summary.get("records") or []:
            if str(record.get("sample_id")) == sample_id:
                summary["_path"] = str(candidate)
                return summary
    return None


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def load_jsonl_text(path: Path, max_lines: int = 20000) -> tuple[str, set[str]]:
    texts: list[str] = []
    role_ids: set[str] = set()
    if not path.exists():
        return "", role_ids
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for idx, line in enumerate(handle):
            if idx >= max_lines:
                break
            try:
                item = json.loads(line)
            except Exception:
                continue
            if item.get("role_id"):
                role_ids.add(str(item["role_id"]))
            for part in item.get("parts") or []:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(str(part["text"]))
            if item.get("content"):
                texts.append(str(item["content"]))
    return "\n".join(texts), role_ids


def storage_probe(workspace: Path, account: str, user_id: str, sample_id: str, sample: dict[str, Any], summary: dict[str, Any] | None) -> dict[str, Any]:
    account_root = workspace / "viking" / account
    session_root = account_root / "session"
    memory_root = account_root / "user" / user_id / "memories"
    memory_files = [p for p in memory_root.rglob("*.md") if p.is_file() and not p.name.startswith(".")] if memory_root.exists() else []
    sample_session_dirs = sorted(p for p in session_root.glob(f"locomo-{sample_id}-*") if p.is_dir()) if session_root.exists() else []
    archive_files = [p / "history" / "archive_001" / "messages.jsonl" for p in sample_session_dirs]
    archive_messages = sum(count_jsonl(path) for path in archive_files)
    archive_text = []
    role_ids: set[str] = set()
    for path in archive_files:
        text, ids = load_jsonl_text(path)
        archive_text.append(text)
        role_ids.update(ids)
    other_locomo_sessions = []
    if session_root.exists():
        for path in session_root.glob("locomo-conv-*"):
            if path.is_dir() and f"locomo-{sample_id}-" not in path.name:
                other_locomo_sessions.append(path.name)
    expected = expected_message_count(sample)
    summary_record = None
    if summary:
        for record in summary.get("records") or []:
            if str(record.get("sample_id")) == sample_id:
                summary_record = record
                break
    return {
        "workspace": str(workspace),
        "account": account,
        "user_id": user_id,
        "memory_root": str(memory_root),
        "memory_file_count": len(memory_files),
        "session_root": str(session_root),
        "sample_session_dir_count": len(sample_session_dirs),
        "archive_message_count": archive_messages,
        "dataset_expected_messages": expected,
        "archive_matches_dataset": archive_messages == expected,
        "role_ids": sorted(role_ids),
        "speaker_role_id_ok": {"Jon", "Gina"}.issubset(role_ids) if sample_id == "conv-30" else bool(role_ids),
        "other_locomo_sessions": other_locomo_sessions[:30],
        "possible_cross_sample_pollution": bool(other_locomo_sessions),
        "import_summary_path": summary.get("_path") if summary else "",
        "import_summary_status": summary.get("status") if summary else "",
        "summary_integrity": summary_record.get("integrity") if summary_record else "",
        "summary_expected_messages": summary_record.get("expected_messages") if summary_record else None,
        "summary_submitted_messages": summary_record.get("submitted_messages") if summary_record else None,
        "summary_pending_messages": summary_record.get("pending_message_count_after_commit") if summary_record else None,
        "_archive_text_norm": norm("\n".join(archive_text)),
        "_memory_text_norm": norm("\n".join(p.read_text(encoding="utf-8", errors="replace") for p in memory_files[:2000])),
    }


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


def openviking_search(base_url: str, account: str, user_id: str, agent_id: str, api_key: str, query: str, limit: int, target_uri: str) -> tuple[list[dict[str, Any]], str]:
    payload = {
        "query": query,
        "target_uri": target_uri,
        "limit": limit,
        "score_threshold": 0.1,
    }
    req = Request(
        base_url.rstrip("/") + "/api/v1/search/find",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers(account, user_id, agent_id, api_key),
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        return [], f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:500]}"
    except URLError as exc:
        return [], f"connect_error: {exc}"
    except Exception as exc:
        return [], str(exc)
    if raw.get("status") == "error":
        return [], json.dumps(raw.get("error") or raw, ensure_ascii=False)[:500]
    result = raw.get("result") if isinstance(raw, dict) else raw
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)], ""
    if isinstance(result, dict):
        items = (
            result.get("items")
            or result.get("results")
            or result.get("hits")
            or result.get("memories")
            or []
        )
        if isinstance(result.get("memories"), list) and isinstance(result.get("resources"), list):
            items = result["memories"] + result["resources"]
        return [item for item in items if isinstance(item, dict)], ""
    return [], "unexpected search response"


def uri_to_file(workspace: Path, account: str, uri: str) -> Path | None:
    if not uri.startswith("viking://"):
        return None
    rel = uri.removeprefix("viking://").lstrip("/")
    if rel.startswith(("user/", "agent/", "session/", "resources/")):
        return workspace / "viking" / account / rel
    return workspace / "viking" / account / rel


def hit_text(item: dict[str, Any], workspace: Path, account: str, include_file: bool) -> str:
    parts = [
        str(item.get("uri") or item.get("path") or item.get("id") or ""),
        str(item.get("abstract") or ""),
        str(item.get("content") or item.get("text") or item.get("overview") or item.get("summary") or ""),
    ]
    if include_file:
        path = uri_to_file(workspace, account, str(item.get("uri") or ""))
        if path and path.exists() and path.is_file():
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    return "\n".join(parts)


def matched_groups(text: str, groups: list[list[str]]) -> list[dict[str, Any]]:
    low = norm(text)
    rows = []
    for group in groups:
        matched = [term for term in group if norm(term) and norm(term) in low]
        rows.append({"terms": group, "matched": matched, "ok": bool(matched)})
    return rows


def probe_cases_for(sample_id: str, sample: dict[str, Any]) -> list[ProbeCase]:
    if sample_id in DEFAULT_PROBES:
        return DEFAULT_PROBES[sample_id]
    probes = []
    for index, item in enumerate(sample.get("qa") or []):
        if str(item.get("category")) == "5":
            continue
        answer_terms = [part.strip() for part in re.split(r"[,;/]| and |，|、", str(item.get("answer") or "")) if len(part.strip()) >= 3]
        probes.append(ProbeCase(f"{sample_id}_qa{index}", f"auto probe {index}", [answer_terms[:3] or [str(item.get("answer") or "")]]))
        if len(probes) >= 8:
            break
    return probes


def run_retrieval_probes(args: argparse.Namespace, workspace: Path, sample: dict[str, Any], sample_id: str, storage: dict[str, Any]) -> list[dict[str, Any]]:
    qmap = question_map(sample, sample_id)
    results = []
    for case in probe_cases_for(sample_id, sample):
        qa = qmap.get(case.question_id)
        if not qa:
            continue
        query = str(qa.get("question") or "")
        hits, error = openviking_search(
            args.openviking_url,
            args.account,
            args.user_id,
            args.agent_id,
            args.api_key,
            query,
            args.top_k,
            "viking://user/memories/",
        )
        api_text = "\n".join(hit_text(item, workspace, args.account, include_file=False) for item in hits)
        enriched_text = "\n".join(hit_text(item, workspace, args.account, include_file=True) for item in hits)
        api_groups = matched_groups(api_text, case.groups)
        enriched_groups = matched_groups(enriched_text, case.groups)
        evidence = [str(item) for item in qa.get("evidence") or []]
        evidence_texts = [evidence_text(sample, item) for item in evidence]
        archive_norm = storage.get("_archive_text_norm", "")
        memory_norm = storage.get("_memory_text_norm", "")
        evidence_archive_hits = sum(1 for text in evidence_texts if norm(text) and norm(text) in archive_norm)
        evidence_memory_hits = sum(1 for text in evidence_texts if norm(text) and norm(text) in memory_norm)
        api_ok = all(item["ok"] for item in api_groups)
        enriched_ok = all(item["ok"] for item in enriched_groups)
        if api_ok:
            status = "PASS"
        elif enriched_ok:
            status = "WARN_FILE_ONLY"
        else:
            status = "FAIL"
        results.append(
            {
                "question_id": case.question_id,
                "title": case.title,
                "question": query,
                "gold": qa.get("answer"),
                "category": qa.get("category"),
                "status": status if not error else "ERROR",
                "search_error": error,
                "retrieval_count": len(hits),
                "top_uri": hits[0].get("uri") if hits else "",
                "top_score": hits[0].get("score") if hits else None,
                "expected_groups": case.groups,
                "api_abstract_groups": api_groups,
                "file_enriched_groups": enriched_groups,
                "evidence": evidence,
                "evidence_archive_hits": evidence_archive_hits,
                "evidence_memory_hits": evidence_memory_hits,
                "evidence_total": len(evidence_texts),
                "top_hits": [
                    {
                        "uri": item.get("uri"),
                        "score": item.get("score"),
                        "abstract": compact(item.get("abstract") or item.get("content") or item.get("text") or "", 300),
                    }
                    for item in hits[:5]
                ],
            }
        )
    return results


def render_markdown(report: dict[str, Any]) -> str:
    storage = report["storage"]
    lines = [
        "# LoCoMo Memory Probe Report",
        "",
        f"- sample: `{report['sample_id']}`",
        f"- workspace: `{storage['workspace']}`",
        f"- memory root: `{storage['memory_root']}`",
        f"- OpenViking: `{report['openviking_url']}`",
        "",
        "## Storage",
        "",
        f"- summary: `{storage.get('import_summary_path') or '-'}`",
        f"- integrity: `{storage.get('summary_integrity') or '-'}`",
        f"- dataset messages: `{storage['dataset_expected_messages']}`",
        f"- archived messages: `{storage['archive_message_count']}`",
        f"- memory files: `{storage['memory_file_count']}`",
        f"- role_id preserved: `{storage['speaker_role_id_ok']}` ({', '.join(storage['role_ids']) or '-'})",
        f"- possible cross-sample pollution: `{storage['possible_cross_sample_pollution']}`",
        "",
        "## Retrieval Probes",
        "",
        "| Status | Question | Retrieval | Gold Evidence In Memory | Missing Groups | Top URI |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in report["retrieval_probes"]:
        missing = []
        for group in item["api_abstract_groups"]:
            if not group["ok"]:
                missing.append("/".join(group["terms"]))
        lines.append(
            "| {status} | `{qid}` {q} | {count} | {mem}/{total} | {missing} | `{uri}` |".format(
                status=item["status"],
                qid=item["question_id"],
                q=compact(item["question"], 80).replace("|", "\\|"),
                count=item["retrieval_count"],
                mem=item["evidence_memory_hits"],
                total=item["evidence_total"],
                missing=", ".join(missing) or "-",
                uri=item["top_uri"] or "-",
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `PASS`: API abstract/top hits already contain all expected groups, chat agent and batch QA should both see the key evidence.",
            "- `WARN_FILE_ONLY`: search found files, but API abstract is not enough; batch QA may pass because it reads files, chat context may still miss evidence.",
            "- `FAIL`: top-k search did not surface enough evidence, or commit did not extract the gold evidence into memory files.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe LoCoMo OpenViking memory storage and retrieval health.")
    repo_root = Path(__file__).resolve().parents[1]
    dataset_root = repo_root / "dataset"
    dataset_candidates = [
        dataset_root / "full" / "locomo.json",
        dataset_root / "locomo.json",
        dataset_root / "locomo10.json",
    ]
    dataset_default = next((path for path in dataset_candidates if path.exists()), dataset_candidates[-1])
    workspace_candidates = [
        os.environ.get("LOCOMO_EVAL_WORKSPACE", ""),
        os.environ.get("OPENVIKING_WORKSPACE", ""),
        str(Path.cwd() / "workspace"),
        str(repo_root / "workspace"),
        str(Path.home() / "openviking_workspace_locomo"),
    ]
    workspace_default = next((item for item in workspace_candidates if str(item).strip()), str(repo_root / "workspace"))
    parser.add_argument("--dataset", default=str(dataset_default))
    parser.add_argument("--sample", default="conv-30")
    parser.add_argument("--workspace", default=workspace_default)
    parser.add_argument("--runs-dir", default=os.environ.get("LOCOMO_EVAL_RUNS_DIR") or str(repo_root / "runs"))
    parser.add_argument("--import-summary", default="")
    parser.add_argument("--openviking-url", default="http://127.0.0.1:1933")
    parser.add_argument("--account", default="default")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--agent-id", default="default")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    started = time.time()
    dataset = Path(args.dataset).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve()
    runs_dir = Path(args.runs_dir).expanduser().resolve()
    data = read_json(dataset)
    if not isinstance(data, list):
        raise ValueError("LoCoMo dataset must be a JSON list")
    _, sample = find_sample(data, args.sample)
    sample_id = str(sample.get("sample_id") or args.sample)
    summary = load_import_summary(Path(args.import_summary) if args.import_summary else None, runs_dir, sample_id)
    storage = storage_probe(workspace, args.account, args.user_id, sample_id, sample, summary)
    probes = run_retrieval_probes(args, workspace, sample, sample_id, storage)
    public_storage = {key: value for key, value in storage.items() if not key.startswith("_")}
    report = {
        "status": "PASS" if all(p["status"] == "PASS" for p in probes) and storage["archive_matches_dataset"] and storage["memory_file_count"] > 0 else "NEEDS_ATTENTION",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": round(time.time() - started, 2),
        "dataset": str(dataset),
        "sample_id": sample_id,
        "openviking_url": args.openviking_url,
        "storage": public_storage,
        "retrieval_probes": probes,
        "alignment_findings": [
            "Batch QA is close to VikingBot's single retrieval context: top_k=30, score_threshold=0.1, current date injection, and file-enriched evidence when workspace is supplied.",
            "Chat workbench is not fully aligned: it uses search abstracts only, does not load full memory file content, and does not include VikingBot's current-time/session/profile/user+agent memory split.",
            "The current import preserves role_id in session archives, but memories are written under one user namespace; use a fresh workspace/account per LoCoMo sample to avoid cross-sample contamination.",
            "VikingBot can call openviking_search repeatedly as a tool; the current batch agent is one-shot retrieval plus LLM, so query rewrite failures must be caught by probes.",
        ],
    }
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else runs_dir / f"locomo_memory_probe_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "locomo_memory_probe.json"
    md_path = out_dir / "locomo_memory_probe.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
