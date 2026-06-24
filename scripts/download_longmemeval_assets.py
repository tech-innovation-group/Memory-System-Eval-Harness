#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
import json
from urllib.error import URLError, HTTPError
from urllib.parse import quote
from urllib.request import urlopen, Request


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = REPO_ROOT / "dataset"
FULL_DIR = DATASET_DIR / "full"
RAW_DIR = DATASET_DIR / "raw"
RAW_ORIGINAL_DIR = RAW_DIR / "longmemeval_original"
RAW_V2_DIR = RAW_DIR / "longmemeval_v2"
DEFAULT_MIRROR_BASE_URL = "https://hf-mirror.com"

V1_FILES = (
    "longmemeval_s_cleaned.json",
    "longmemeval_oracle.json",
    "longmemeval_m_cleaned.json",
)

V2_SENTINEL_FILES = (
    "questions.jsonl",
    "trajectories.jsonl",
)

ORIGINAL_FILE_MAP = {
    ".gitattributes": ".gitattributes",
    "README.md": "README.md",
    "longmemeval_s": "longmemeval_s.json",
    "longmemeval_s.json": "longmemeval_s.json",
    "longmemeval_oracle": "longmemeval_oracle.json",
    "longmemeval_oracle.json": "longmemeval_oracle.json",
    "longmemeval_m": "longmemeval_m.json",
    "longmemeval_m.json": "longmemeval_m.json",
}

ORIGINAL_SENTINEL_FILES = (
    "longmemeval_s.json",
    "longmemeval_oracle.json",
    "longmemeval_m.json",
)


def candidate_v1_sources() -> list[Path]:
    candidates = []
    env_value = (__import__("os").environ.get("LONGMEMEVAL_CLEANED_SOURCE") or "").strip()
    if env_value:
        candidates.append(Path(env_value).expanduser())
    candidates.extend(
        [
            Path("/Users/chx/data/longmemeval-cleaned"),
            DATASET_DIR / "full",
        ]
    )
    seen: set[str] = set()
    unique = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def ensure_dirs() -> None:
    FULL_DIR.mkdir(parents=True, exist_ok=True)
    RAW_ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
    RAW_V2_DIR.mkdir(parents=True, exist_ok=True)


def copy_if_needed(src: Path, dst: Path) -> str:
    if not src.exists():
        return f"missing:{src}"
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return f"ok:{dst}"
    shutil.copy2(src, dst)
    return f"copied:{dst}"


def materialize_v1() -> tuple[bool, list[str]]:
    notes: list[str] = []
    sources = candidate_v1_sources()
    source_dir = next((path for path in sources if path.exists() and path.is_dir()), None)
    if source_dir is None:
        for name in V1_FILES:
            dst = FULL_DIR / name
            if dst.exists():
                notes.append(f"already-present:{dst}")
                continue
            notes.append(f"missing-source:{name}")
        ready = all((FULL_DIR / name).exists() for name in V1_FILES)
        return ready, notes
    for name in V1_FILES:
        notes.append(copy_if_needed(source_dir / name, FULL_DIR / name))
    ready = all((FULL_DIR / name).exists() for name in V1_FILES)
    return ready, notes


def v2_ready(root: Path) -> bool:
    return all((root / name).exists() for name in V2_SENTINEL_FILES)


def original_ready(root: Path) -> bool:
    return all((root / name).exists() for name in ORIGINAL_SENTINEL_FILES)


def summarize_tree(root: Path, max_items: int = 12) -> list[str]:
    if not root.exists():
        return [f"missing:{root}"]
    items = sorted(path.relative_to(root) for path in root.rglob("*"))
    notes = []
    for item in items[:max_items]:
        notes.append(str(item))
    if len(items) > max_items:
        notes.append(f"... ({len(items) - max_items} more)")
    return notes or ["empty"]


def http_get_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": "locomo-eval-web/longmemeval-downloader"})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def download_file(url: str, dst: Path, expected_size: int | None = None) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and expected_size is not None and dst.stat().st_size == expected_size:
        return f"ok:{dst}"
    tmp = dst.with_suffix(dst.suffix + ".part")
    request = Request(url, headers={"User-Agent": "locomo-eval-web/longmemeval-downloader"})
    with urlopen(request, timeout=120) as response, tmp.open("wb") as fout:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            fout.write(chunk)
    if expected_size is not None and tmp.stat().st_size != expected_size:
        tmp.unlink(missing_ok=True)
        return f"size-mismatch:{dst}:{tmp.stat().st_size if tmp.exists() else 'missing'}:{expected_size}"
    tmp.replace(dst)
    return f"downloaded:{dst}"


def try_download_v2(repo_id: str, base_url: str) -> tuple[bool, list[str]]:
    if v2_ready(RAW_V2_DIR):
        return True, [f"already-present:{RAW_V2_DIR}"]
    try:
        tree_url = f"{base_url}/api/datasets/{repo_id}/tree/main?recursive=true"
        tree = http_get_json(tree_url)
        if not isinstance(tree, list):
            return False, [f"unexpected-tree-response:{type(tree).__name__}"]
        notes: list[str] = []
        for item in tree:
            if not isinstance(item, dict) or item.get("type") != "file":
                continue
            rel_path = str(item.get("path") or "").strip()
            if not rel_path:
                continue
            expected_size = item.get("size")
            try:
                expected_size_int = int(expected_size) if expected_size is not None else None
            except Exception:
                expected_size_int = None
            url = f"{base_url}/datasets/{repo_id}/resolve/main/{quote(rel_path, safe='/')}"
            note = download_file(url, RAW_V2_DIR / rel_path, expected_size_int)
            notes.append(note)
        if v2_ready(RAW_V2_DIR):
            return True, notes
        return False, notes + [f"incomplete:{RAW_V2_DIR}"]
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return False, [f"download_failed:{type(exc).__name__}:{exc}"]


def try_download_original(repo_id: str, base_url: str) -> tuple[bool, list[str]]:
    if original_ready(RAW_ORIGINAL_DIR):
        return True, [f"already-present:{RAW_ORIGINAL_DIR}"]
    try:
        tree_url = f"{base_url}/api/datasets/{repo_id}/tree/main?recursive=true"
        tree = http_get_json(tree_url)
        if not isinstance(tree, list):
            return False, [f"unexpected-tree-response:{type(tree).__name__}"]
        notes: list[str] = []
        seen_destinations: set[str] = set()
        for item in tree:
            if not isinstance(item, dict) or item.get("type") != "file":
                continue
            rel_path = str(item.get("path") or "").strip()
            local_name = ORIGINAL_FILE_MAP.get(rel_path)
            if not local_name:
                continue
            if local_name in seen_destinations:
                continue
            seen_destinations.add(local_name)
            expected_size = item.get("size")
            try:
                expected_size_int = int(expected_size) if expected_size is not None else None
            except Exception:
                expected_size_int = None
            url = f"{base_url}/datasets/{repo_id}/resolve/main/{quote(rel_path, safe='/')}"
            note = download_file(url, RAW_ORIGINAL_DIR / local_name, expected_size_int)
            notes.append(note)
        if original_ready(RAW_ORIGINAL_DIR):
            return True, notes
        return False, notes + [f"incomplete:{RAW_ORIGINAL_DIR}"]
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return False, [f"download_failed:{type(exc).__name__}:{exc}"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Stage all locally available LongMemEval assets into locomo-eval-web."
    )
    parser.add_argument(
        "--skip-original-download",
        action="store_true",
        help="Do not attempt the deprecated original LongMemEval split download.",
    )
    parser.add_argument(
        "--skip-v2-download",
        action="store_true",
        help="Only materialize v1 cleaned files; do not attempt LongMemEval-V2 network download.",
    )
    parser.add_argument(
        "--original-repo",
        default="xiaowu0162/longmemeval",
        help="Hugging Face dataset repo id for the deprecated original LongMemEval release.",
    )
    parser.add_argument(
        "--v2-repo",
        default="xiaowu0162/longmemeval-v2",
        help="Hugging Face dataset repo id for LongMemEval-V2.",
    )
    parser.add_argument(
        "--mirror-base-url",
        default=DEFAULT_MIRROR_BASE_URL,
        help="Mirror base URL used for explicit LongMemEval-V2 file downloads.",
    )
    args = parser.parse_args(argv)

    ensure_dirs()

    v1_ready, v1_notes = materialize_v1()
    print("V1 cleaned:")
    for note in v1_notes:
        print(f"  - {note}")

    original_ok = False
    if args.skip_original_download:
        print("Original:")
        print("  - skipped")
    else:
        original_ok, original_notes = try_download_original(args.original_repo, args.mirror_base_url.rstrip("/"))
        print("Original:")
        for note in original_notes:
            print(f"  - {note}")

    v2_ready = False
    if args.skip_v2_download:
        print("V2:")
        print("  - skipped")
    else:
        ok, v2_notes = try_download_v2(args.v2_repo, args.mirror_base_url.rstrip("/"))
        v2_ready = ok
        print("V2:")
        for note in v2_notes:
            print(f"  - {note}")

    print("Summary:")
    print(f"  - v1_ready={v1_ready}")
    print(f"  - original_ready={original_ok if not args.skip_original_download else 'skipped'}")
    print(f"  - v2_ready={v2_ready if not args.skip_v2_download else 'skipped'}")
    print(f"  - full_dir={FULL_DIR}")
    print(f"  - raw_original_dir={RAW_ORIGINAL_DIR}")
    print(f"  - raw_v2_dir={RAW_V2_DIR}")
    print("  - v1_files=" + json.dumps([str(FULL_DIR / name) for name in V1_FILES], ensure_ascii=False))
    print("  - original_files=" + json.dumps([str(RAW_ORIGINAL_DIR / name) for name in ORIGINAL_SENTINEL_FILES], ensure_ascii=False))
    print("  - v2_tree=" + json.dumps(summarize_tree(RAW_V2_DIR), ensure_ascii=False))
    return 0 if v1_ready and (original_ok or args.skip_original_download) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
