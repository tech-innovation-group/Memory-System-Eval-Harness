"""Capture the executing harness source, never credentials or working paths."""

from datetime import datetime, timezone
import hashlib
from html import escape
from pathlib import Path
import re
import subprocess


def platform_snapshot(root: Path | None = None) -> dict:
    root = (root or Path(__file__).resolve().parents[4]).resolve()
    result = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
              "git_commit": None, "worktree_dirty": None,
              "python_source_sha256": None, "python_files": 0,
              "scope": "performance/**/*.py; excludes dependencies, config and data",
              "issues": []}

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip()

    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
            raise ValueError("not the source repository")
        commit = git("rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise ValueError("invalid revision")
        result["git_commit"] = commit
        result["worktree_dirty"] = bool(git("status", "--porcelain", "--untracked-files=normal", "--", "performance"))
    except (OSError, ValueError, subprocess.SubprocessError):
        result["issues"].append("git_metadata_unavailable")

    digest = hashlib.sha256()
    try:
        paths = sorted((root / "performance").rglob("*.py"))
        if not paths or any(path.is_symlink() for path in paths):
            raise ValueError("missing or linked sources")
        for path in paths:
            data = path.read_bytes()
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(len(data).to_bytes(8, "big") + data)
        result["python_source_sha256"] = digest.hexdigest()
        result["python_files"] = len(paths)
    except (OSError, ValueError):
        result["issues"].append("python_source_snapshot_incomplete")
    return result


def render_platform_provenance(value: dict | None) -> str:
    value = value if isinstance(value, dict) else {}
    def shown(key):
        raw = value.get(key)
        if raw is None:
            return "未采集"
        if isinstance(raw, bool):
            return "是" if raw else "否"
        return escape(str(raw))
    return ("<section><h2>测试平台运行版本</h2>"
            f"<p>启动时 Git 提交：<code>{shown('git_commit')}</code>；"
            f"performance 工作区有改动：{shown('worktree_dirty')}。</p>"
            f"<p>采集时间：{shown('captured_at')}；Python 文件数：{shown('python_files')}。</p>"
            f"<p>Python 源码 SHA256：<code style='overflow-wrap:anywhere'>{shown('python_source_sha256')}</code>。</p>"
            "<p>指纹仅覆盖 performance 下的 Python 源码，不含模型、配置、第三方依赖或数据。"
            "无 Git 元数据的部署包可保留源码指纹，但不能据此猜测 commit；旧数据不会补填当前版本。"
            "EchoMem 服务版本与测试平台版本是两回事。</p></section>")
