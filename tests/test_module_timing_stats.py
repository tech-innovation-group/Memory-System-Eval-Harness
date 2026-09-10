import json
import unittest

from scripts.collect_commit_diagnostics import collect
from scripts.module_timing_stats import TimingStats, from_diagnostics


class ModuleTimingsTests(unittest.TestCase):
    def test_whole_stream_survives_detail_cap(self):
        lines = (json.dumps({'event': 'recall_stage_completed', 'stage': 'llm',
                             'duration_ms': i, 'queue_wait_ms': 2, 'trace_id': 'PRIVATE_TRACE'}) for i in range(10005))
        result = collect(lines)
        self.assertEqual(len(result['samples']), 10000)
        stat = result['module_timings'][0]
        self.assertEqual(stat['duration']['observations'], 10005)
        self.assertEqual(stat['duration']['max_ms'], 10004)
        self.assertEqual(stat['distinct_traces'], 1)
        self.assertNotIn('PRIVATE_TRACE', json.dumps(result))

    def test_nested_macro_values_and_absent_queue(self):
        stats = TimingStats()
        stats.add({'event': 'atomic_pipeline_completed', 'macro_stage_timings_ms': {'extraction': 123}})
        row = stats.export()[0]
        self.assertEqual(row['stage'], 'extraction')
        self.assertEqual(row['duration']['p95_ms'], 123)
        self.assertIsNone(row['queue_wait']['p95_ms'])

    def test_invalid_numbers_and_prefix_scope(self):
        stats = TimingStats()
        for value in (True, -1, float('nan'), float('inf')):
            stats.add({'event': 'recall_stage_completed', 'stage': 'llm', 'duration_ms': value})
        self.assertEqual(stats.export(), [])
        result = from_diagnostics({'samples': [{'event': 'recall_stage_completed', 'evidence': {'stage': 'rule'}, 'duration_ms': 1}]})
        self.assertEqual(result['scope'], 'retained_prefix_only')
        self.assertEqual(result['groups'][0]['stage'], 'rule')
