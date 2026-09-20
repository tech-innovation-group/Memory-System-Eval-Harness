import unittest

from scripts.run_quick_topology_matrix import QUERIES, plans
from scripts.build_quick_topology_report import jain, render
from performance.targets.echomem.acceptance.semantic_corpus import assess_retrieval


class QuickTopologyTests(unittest.TestCase):
    def test_plan_has_exactly_ten_distinct_cases(self):
        cases = plans()
        self.assertEqual(len(cases), 10)
        self.assertEqual(len({(name, level) for name, level, *_ in cases}), 10)
        for name, level, users, sessions, width in cases:
            self.assertEqual(level, users * sessions * width)
            self.assertGreaterEqual(users, 4)

    def test_queries_are_valid_even_for_empty_responses(self):
        for query in QUERIES:
            quality = assess_retrieval({}, query)
            self.assertFalse(quality['matched_expected_fact'])
            self.assertFalse(quality['quality_ok'])

    def test_fairness_does_not_invent_missing_or_zero_results(self):
        self.assertIsNone(jain([0, 0]))
        self.assertIsNone(jain([None, 1]))
        self.assertIsNone(jain([1]))
        self.assertEqual(jain([2, 2]), 1)
        self.assertEqual(jain([0, 2]), .5)

    def test_empty_run_is_not_success_and_escapes_metadata(self):
        page = render({'status': 'BLOCKED<script>', 'scenes': []}, {'image': '<unsafe>'})
        self.assertIn('0/10', page)
        self.assertIn('BLOCKED&lt;script&gt;', page)
        self.assertNotIn('<unsafe>', page)
