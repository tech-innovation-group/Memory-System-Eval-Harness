"""Dataset-agnostic file loading and local dataset resolution."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DATASET_SOURCES: dict[str, dict[str, Any]] = {
    "locomo": {
        "filename": "locomo10.json",
        "urls": [
            "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
        ],
    },
    "hotpotqa": {
        "filename": "hotpot_dev_distractor_v1.json",
        "urls": [
            # 官方源
            "http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json",
            # GitHub 镜像，经 ghfast.top 加速代理（官方源不可达时使用）
            "https://ghfast.top/https://raw.githubusercontent.com/AIRI-Institute/AriGraph/e884b76d7fa5185a3a8a55e5a67393b5a43f5ef2/qa_data/hotpot_dev_distractor_v1.json",
            "https://ghfast.top/https://raw.githubusercontent.com/nju-websoft/KG2RAG/7d626c77b7af30b55aa3f960cde755b9549a0616/data/hotpotqa/hotpot_dev_distractor_v1.json",
            # GitHub 直连镜像（网络可直连 GitHub 时使用）
            "https://raw.githubusercontent.com/AIRI-Institute/AriGraph/e884b76d7fa5185a3a8a55e5a67393b5a43f5ef2/qa_data/hotpot_dev_distractor_v1.json",
            "https://raw.githubusercontent.com/nju-websoft/KG2RAG/7d626c77b7af30b55aa3f960cde755b9549a0616/data/hotpotqa/hotpot_dev_distractor_v1.json",
        ],
    },
    "longmemeval": {
        "filename": "longmemeval_s_cleaned.json",
        "urls": [
            # 官方源 (HuggingFace)
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
            # ModelScope 镜像（国内 CDN 可达）
            "https://modelscope.cn/api/v1/datasets/evalscope/longmemeval-cleaned/repo?Revision=master&FilePath=longmemeval_s_cleaned.json",
            # HuggingFace 国内镜像
            "https://hf-mirror.com/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
        ],
    },
}


@dataclass
class BenchmarkQuestion:
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
    reasoning: str = "evaluation pending"
    time_cost: str = "0"
    original_sample_id: str = ""
    question_index: str = ""
    memory_users: str = ""
    native_question_id: str = ""


def read_dataset(path: str | Path) -> Any:
    source = Path(path)
    if source.suffix.lower() in {".jsonl", ".ndjson"}:
        rows = []
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                text = line.strip()
                if text:
                    rows.append(json.loads(text))
        return rows
    return json.loads(source.read_text(encoding="utf-8"))


def list_payload(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "examples", "items", "questions", "samples", "instances"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


# 下载后校验阈值：小文件（≤ 64MB）做完整 JSON 解析，捕获截断/损坏；
# 更大的文件只做轻量结构检查，避免把整个数据集加载进内存造成内存峰值。
_FULL_JSON_VALIDATION_MAX_BYTES = 64 * 1024 * 1024


def _validate_downloaded_json(path: Path) -> None:
    if path.stat().st_size <= _FULL_JSON_VALIDATION_MAX_BYTES:
        read_dataset(path)
        return
    with path.open("rb") as handle:
        head = handle.read(64).lstrip()
        if not head:
            raise ValueError("download is empty")
        if head[:1] not in (b"[", b"{"):
            raise ValueError("download is not JSON")
        closer = b"]" if head[:1] == b"[" else b"}"
        handle.seek(-8, 2)
        if not handle.read().rstrip().endswith(closer):
            raise ValueError("download appears truncated")


def resolve_dataset_path(benchmark: str, explicit_path: str = "") -> str:
    if explicit_path:
        return explicit_path
    source = DATASET_SOURCES.get(benchmark)
    if not source:
        raise ValueError(f"未知 benchmark: {benchmark}")

    filename = source["filename"]
    configured_path = Path(filename).expanduser()
    local_path = (
        configured_path
        if configured_path.is_absolute()
        else Path(__file__).resolve().parent.parent
        / "benchmarks"
        / benchmark
        / "data"
        / configured_path
    )
    if local_path.exists():
        return str(local_path)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for url in source["urls"]:
        print(f"[dataset] 本地未找到 {local_path}, 尝试下载: {url}")
        temp_path = local_path.with_suffix(local_path.suffix + ".part")
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            if not payload:
                raise RuntimeError("download returned an empty response")
            temp_path.write_bytes(payload)
            _validate_downloaded_json(temp_path)
            temp_path.replace(local_path)
            print(
                f"[dataset] 下载完成: {local_path} "
                f"({len(payload) / 1024 / 1024:.1f} MB)"
            )
            return str(local_path)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    raise RuntimeError(
        f"无法自动获取 {benchmark} 数据集 ({filename})，以下下载源均失败:\n"
        + "\n".join(errors)
        + f"\n请手动下载后保存到: {local_path}\n"
        f"或通过 --dataset 参数指定本地路径。"
    )
