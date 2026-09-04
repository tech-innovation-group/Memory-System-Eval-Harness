import tempfile
import unittest
from pathlib import Path

from performance.prepare_docker_env import normalize


class DockerEnvTests(unittest.TestCase):
    def test_normalizes_export_and_plain_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            target = root / "docker.env"
            source.write_text(
                "# comment\nexport A='one'\nB=two\n",
                encoding="utf-8",
            )
            self.assertEqual(2, normalize(source, target))
            self.assertEqual("A=one\nB=two\n", target.read_text(encoding="utf-8"))
