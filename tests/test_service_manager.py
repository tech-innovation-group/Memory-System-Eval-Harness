from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shared.service_manager import (
    ManagedService,
    _child_environment,
    _seed_template_embedding_cache,
    _server_address,
    _validate_echomem_resources,
    start_echomem_service,
    stop_echomem_service,
)


class ServiceManagerTests(unittest.TestCase):
    def test_validate_echomem_resources_requires_core_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "core_rules.txt"):
                _validate_echomem_resources(root)

            resource = (
                root
                / "src/echomem/index_engine/engine/atomic_engine/core/resources"
                / "prompts/atomic/core_rules.txt"
            )
            resource.parent.mkdir(parents=True)
            resource.write_text("rules", encoding="utf-8")
            _validate_echomem_resources(root)

    def test_child_environment_prioritizes_current_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(
                "shared.service_manager.os.environ",
                {"PYTHONPATH": "/old/src"},
                clear=False,
            ):
                env = _child_environment(root)
            self.assertEqual(
                f"{root / 'src'}{os.pathsep}/old/src",
                env["PYTHONPATH"],
            )

    def test_parses_local_http_address(self) -> None:
        self.assertEqual(("127.0.0.1", 18094), _server_address("http://127.0.0.1:18094"))

    def test_rejects_https_auto_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "local http"):
            _server_address("https://example.test")

    @unittest.skipUnless(os.name == "posix", "os.killpg is POSIX-only")
    def test_stop_terminates_process_group_and_removes_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "service.pid"
            pid_path.write_text("123\n", encoding="utf-8")
            process = mock.Mock()
            process.pid = 123
            process.poll.return_value = None
            service = ManagedService(process, Path(directory) / "service.log", pid_path)

            with mock.patch("shared.service_manager.os.killpg") as killpg:
                stop_echomem_service(service)

            killpg.assert_called_once_with(123, __import__("signal").SIGTERM)
            process.wait.assert_called_once_with(timeout=15)
            self.assertFalse(pid_path.exists())

    def test_seeds_template_cache_from_config_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "source" / "config.json"
            source = config.parent / "cache/recall/spec_template_embeddings.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"fingerprint":"test"}\n', encoding="utf-8")
            workspace = root / "fresh"

            _seed_template_embedding_cache(workspace, config)

            destination = workspace / "cache/recall/spec_template_embeddings.json"
            self.assertEqual(source.read_text(), destination.read_text())

    def test_startup_exit_removes_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            echomem = root / "echomem"
            workspace = root / "workspace"
            python_bin = echomem / ".venv/bin/python"
            python_bin.parent.mkdir(parents=True)
            python_bin.touch()
            resource = (
                echomem
                / "src/echomem/index_engine/engine/atomic_engine/core/resources"
                / "prompts/atomic/core_rules.txt"
            )
            resource.parent.mkdir(parents=True)
            resource.write_text("rules", encoding="utf-8")
            workspace.mkdir()
            (workspace / "config.json").write_text("{}\n", encoding="utf-8")
            process = mock.Mock()
            process.pid = 321
            process.returncode = 9
            process.poll.return_value = 9

            env = {
                "ECHOMEM_BASE_URL": "http://127.0.0.1:18099",
                "ECHOMEM_ROOT": str(echomem),
                "ECHOMEM_WORKSPACE": str(workspace),
            }
            with (
                mock.patch.dict("os.environ", env, clear=True),
                mock.patch("shared.service_manager._healthy", return_value=False),
                mock.patch("shared.service_manager.subprocess.Popen", return_value=process),
            ):
                with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                    start_echomem_service(project, timeout_s=1)

            self.assertFalse((project / ".runtime/echomem-18099.pid").exists())


if __name__ == "__main__":
    unittest.main()
