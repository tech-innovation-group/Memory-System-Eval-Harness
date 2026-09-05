from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from performance.merge_formal_runs import merge_manifests


class MergeFormalRunsTests(unittest.TestCase):
    def test_supplement_replaces_matching_pr421_and_keeps_pr397(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_dir = root / "base"
            supplement_dir = root / "supplement"
            output_dir = root / "merged"
            base_dir.mkdir()
            supplement_dir.mkdir()

            def run(key: str, submitted: int, output: Path) -> dict:
                return {
                    "scenario_key": key,
                    "scenario": key.split("__", 1)[-1],
                    "status": "completed",
                    "summary": {"metrics": {"search": {"submitted": submitted}}},
                    "output_dir": str(output),
                }

            base = {
                "scenarios": ["pr397__A@1", "pr421__baseline"],
                "repeats": 1,
                "policies": ["server-observe"],
                "runs": [
                    run("pr397__A@1", 3, base_dir / "pr397"),
                    run("pr421__baseline", 0, base_dir / "empty"),
                ],
            }
            supplement = {
                "runs": [run("pr421__baseline", 7, supplement_dir / "real")]
            }
            base_path = base_dir / "suite.json"
            supplement_path = supplement_dir / "suite.json"
            base_path.write_text(json.dumps(base), encoding="utf-8")
            supplement_path.write_text(json.dumps(supplement), encoding="utf-8")

            merged_path = merge_manifests(base_path, supplement_path, output_dir)
            merged = json.loads(merged_path.read_text(encoding="utf-8"))
            by_key = {item["scenario_key"]: item for item in merged["runs"]}
            self.assertEqual(3, by_key["pr397__A@1"]["summary"]["metrics"]["search"]["submitted"])
            self.assertEqual(7, by_key["pr421__baseline"]["summary"]["metrics"]["search"]["submitted"])
            self.assertEqual("complete", merged["finalization"]["coverage_status"])
            self.assertTrue((output_dir / "suite.html").is_file())

    def test_supplement_replaces_legacy_unnamespaced_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_dir = root / "base"
            supplement_dir = root / "supplement"
            output_dir = root / "merged"
            base_dir.mkdir()
            supplement_dir.mkdir()
            base = {
                "scenarios": ["capacity-8"],
                "repeats": 1,
                "policies": ["server-observe"],
                "runs": [
                    {
                        "scenario": "capacity-8",
                        "status": "blocked",
                        "summary": {"metrics": {"search": {"submitted": 0}}},
                    }
                ],
            }
            supplement = {
                "runs": [
                    {
                        "scenario": "capacity-8",
                        "status": "completed",
                        "summary": {"metrics": {"search": {"submitted": 8}}},
                    }
                ]
            }
            base_path = base_dir / "suite.json"
            supplement_path = supplement_dir / "suite.json"
            base_path.write_text(json.dumps(base), encoding="utf-8")
            supplement_path.write_text(json.dumps(supplement), encoding="utf-8")

            merged = json.loads(
                merge_manifests(base_path, supplement_path, output_dir).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(1, len(merged["runs"]))
            self.assertEqual("completed", merged["runs"][0]["status"])
            self.assertEqual(
                "pr421__capacity-8",
                merged["runs"][0]["scenario_key"],
            )


if __name__ == "__main__":
    unittest.main()
