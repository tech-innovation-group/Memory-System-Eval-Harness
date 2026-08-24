from __future__ import annotations

import unittest

from shared.import_guard import incomplete_imports, require_complete_imports


class ImportGuardTests(unittest.TestCase):
    def test_rejects_empty_import_set(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no records"):
            require_complete_imports([])

    def test_rejects_timeout_by_default(self) -> None:
        rows = [{"sample_id": "conv-30", "status": "timeout"}]
        with self.assertRaisesRegex(RuntimeError, "conv-30=timeout"):
            require_complete_imports(rows)

    def test_atom_persistence_failure_blocks_qa_with_commit_error(self) -> None:
        rows = [{
            "sample_id": "conv-30",
            "status": "failed",
            "error": "EchoMem commit contains atom_persistence_failed",
        }]
        with self.assertRaisesRegex(
            RuntimeError,
            "atom_persistence_failed.*QA was not started",
        ):
            require_complete_imports(rows)

    def test_allows_explicit_diagnostic_mode(self) -> None:
        rows = [{"sample_id": "conv-30", "status": "timeout"}]
        require_complete_imports(rows, allow_incomplete=True)
        self.assertEqual(rows, incomplete_imports(rows))


if __name__ == "__main__":
    unittest.main()
