"""Generate separate concurrency experiments from EchoMem's own complete config."""

import argparse
import copy
import json
from pathlib import Path


def configure(source: dict, level: int, *, auto_commit_threshold=None) -> dict:
    if level not in (16, 32, 64, 128):
        raise ValueError("supported concurrency levels: 16,32,64,128")
    result = copy.deepcopy(source)
    if auto_commit_threshold is not None:
        if auto_commit_threshold < 1:
            raise ValueError("auto_commit_threshold must be positive")
        result.setdefault("session", {})["auto_commit_threshold"] = auto_commit_threshold
    embedding = result.get("model", {}).get("embedding", {})
    if embedding.get("model") != "qwen3.7-text-embedding-flash":
        raise ValueError("configure qwen3.7-text-embedding-flash before generating experiments")
    scheduling = result.setdefault("scheduling", {})
    updates = {
        "http": {"max_workers": 4 * level},
        "retrieval": {"admission_permits": level},
        "fanout": {"executor_workers": 2 * level, "engine_max_inflight": level},
        "tenant": {"concurrency": level, "qps": 4 * level},
        "commit": {"queue_max": 4 * level, "tenant_quota": level},
        "llm_gateway": {"llm_max_concurrent": 4 * level,
                        "embed_max_concurrent": 4 * level,
                        "recall_llm_max_concurrent": level,
                        "recall_embed_max_concurrent": level},
    }
    for section, values in updates.items():
        scheduling.setdefault(section, {}).update(values)
    gateway = scheduling["llm_gateway"]
    for kind in ("llm", "embed"):
        # Explicit shares make the generated budget independent of profile defaults.
        gateway.setdefault(f"episode_{kind}_max_concurrent", 3)
        gateway.setdefault(f"workers_{kind}_share", 2)
        gateway[f"provider_budget_{kind}"] = sum(gateway[key] for key in (
            f"{kind}_max_concurrent", f"recall_{kind}_max_concurrent",
            f"episode_{kind}_max_concurrent", f"workers_{kind}_share"))
    recall = result.setdefault("recall", {})
    recall["max_inflight"] = level
    for lane in ("engine", "intent_llm", "query_embedding", "rerank"):
        recall.setdefault("concurrency", {}).setdefault(lane, {}).update(
            max_concurrent=level, queue_capacity=4 * level, max_queued_per_tenant=level)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--auto-commit-threshold", type=int,
                        help="Explicit session threshold for isolated long Commit experiments")
    args = parser.parse_args()
    source = json.loads(args.config.read_text())
    documents = {level: configure(source, level, auto_commit_threshold=args.auto_commit_threshold)
                 for level in (16, 32, 64, 128)}
    args.out_dir.mkdir(parents=True, exist_ok=False)
    for level, document in documents.items():
        path = args.out_dir / f"config-{level}.json"
        with path.open("x", encoding="utf-8") as handle:
            path.chmod(0o600)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print("Generated 16/32/64/128 configs; validate with the target EchoMem before starting.")


if __name__ == "__main__":
    main()
