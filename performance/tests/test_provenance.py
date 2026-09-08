import json
import subprocess

from performance.targets.echomem.acceptance.provenance import platform_snapshot, render_platform_provenance


def test_archive_has_source_digest_but_no_invented_commit(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "performance").mkdir(parents=True)
    code = root / "performance" / "load.py"
    code.write_text("VALUE = 1\n")
    monkeypatch.setenv("PROVIDER_API_KEY", "secret-not-for-report")
    first = platform_snapshot(root)
    assert first["git_commit"] is None
    assert first["worktree_dirty"] is None
    assert first["python_files"] == 1
    assert len(first["python_source_sha256"]) == 64
    assert "secret-not-for-report" not in json.dumps(first)
    assert str(root) not in json.dumps(first)
    assert platform_snapshot(root)["python_source_sha256"] == first["python_source_sha256"]
    code.write_text("VALUE = 2\n")
    assert platform_snapshot(root)["python_source_sha256"] != first["python_source_sha256"]


def test_git_is_anchored_to_source_not_invoking_directory(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "performance").mkdir(parents=True)
    code = root / "performance" / "load.py"
    code.write_text("VALUE = 1\n")
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q")
    git("add", "performance/load.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    monkeypatch.chdir(tmp_path)
    first = platform_snapshot(root)
    assert first["git_commit"] == git("rev-parse", "HEAD")
    assert first["worktree_dirty"] is False
    code.write_text("VALUE = 2\n")
    assert platform_snapshot(root)["worktree_dirty"] is True
    # A copied deployment inside a different repository cannot borrow its HEAD.
    (root / "deployment" / "performance").mkdir(parents=True)
    (root / "deployment" / "performance" / "load.py").write_text("VALUE = 1\n")
    assert platform_snapshot(root / "deployment")["git_commit"] is None


def test_missing_and_linked_sources_are_explicit(tmp_path):
    assert platform_snapshot(tmp_path)["python_source_sha256"] is None
    (tmp_path / "performance").mkdir()
    (tmp_path / "outside.py").write_text("PRIVATE = 1\n")
    (tmp_path / "performance" / "linked.py").symlink_to(tmp_path / "outside.py")
    assert platform_snapshot(tmp_path)["python_source_sha256"] is None


def test_old_report_does_not_borrow_current_version():
    page = render_platform_provenance(None)
    assert "未采集" in page
    assert "旧数据不会补填当前版本" in page
    assert "<script>" not in render_platform_provenance({"git_commit": "<script>alert(1)</script>"})
