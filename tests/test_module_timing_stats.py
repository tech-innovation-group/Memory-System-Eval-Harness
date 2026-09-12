import json
import unittest
from unittest.mock import patch

from scripts.collect_commit_diagnostics import collect
from scripts.module_timing_stats import TimingStats, from_diagnostics
from performance.targets.echomem.probes.failure_evidence import reference
from performance.targets.echomem.probes._client import EchoMemHTTP


class ModuleTimingsTests(unittest.TestCase):
    def test_transport_timeout_preserves_sent_request_identity(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError()) as transport:
            result = EchoMemHTTP('http://test.invalid').search('s', 'query', 1)
        self.assertIsNone(result.status_code)
        self.assertTrue(result.request_id)
        self.assertEqual(transport.call_args.args[0].get_header('X-request-id'), result.request_id)

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

    def test_scene_correlation_uses_request_identity_not_log_order(self):
        scenes = {'users-64': [{'request_ref': reference('slow')}],
                  'users-16': [{'request_ref': reference('fast')}]}
        lines = [json.dumps({'event': 'recall_stage_completed', 'stage': 'llm',
                            'request_id': key, 'duration_ms': ms})
                 for key, ms in [('slow', 2000), ('unmatched', 9999), ('fast', 10)]]
        result = collect(lines, scenes)['scene_timings']
        self.assertEqual(result['users-64']['groups'][0]['duration']['p95_ms'], 2000)
        self.assertEqual(result['users-16']['groups'][0]['duration']['p95_ms'], 10)
        self.assertEqual(result['users-64']['matched_requests'], 1)

    def test_matrix_and_rule_diagnostics_keep_cpu_separate_and_payload_private(self):
        scenes = {'users-64': [{'request_ref': reference('private-request'), 'elapsed_ms': 2000}]}
        lines = [json.dumps({'event': 'prototype_multiply_completed', 'level': 'DEBUG',
                            'request_id': 'private-request', 'duration_ms': 1800,
                            'caller_thread_cpu_ms': 15, 'matrix_rows': 20000,
                            'matrix_dimensions': 1024, 'query_vector': ['secret-vector']}),
                 json.dumps({'event': 'rule_pattern_started', 'level': 'DEBUG',
                             'request_id': 'private-request', 'rule_index': 11, 'input_chars': 65536}),
                 json.dumps({'event': 'rule_pattern_completed', 'level': 'DEBUG',
                             'request_id': 'private-request', 'rule_index': 11,
                             'input_chars': 4096, 'duration_ms': 250})]
        result = collect(lines, scenes)
        groups = result['scene_timings']['users-64']['groups']
        matrix = next(g for g in groups if g['event'] == 'prototype_multiply_completed')
        self.assertEqual(matrix['duration']['p95_ms'], 1800)
        self.assertEqual(matrix['caller_thread_cpu']['p95_ms'], 15)
        self.assertIsNone(matrix['queue_wait']['p95_ms'])
        self.assertEqual(next(g for g in groups if g['event'] == 'rule_pattern_completed')['stage'], 'rule_11')
        self.assertEqual(len(result['samples']), 3)
        self.assertNotIn('secret-vector', json.dumps(result))
        self.assertNotIn('private-request', json.dumps(result))
