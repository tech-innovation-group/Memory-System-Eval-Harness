"""Aggregate measured log durations, without deriving times from subtraction."""
from collections import defaultdict
import math

from performance.targets.echomem.probes.failure_evidence import public_label, reference

EVENTS = {'recall_stage_completed', 'recall_engine_completed', 'dashscope_rerank_operation',
          'http_request_completed', 'memory_extraction_completed', 'atomic_pipeline_completed',
          'atomic_macro_stage_completed', 'commit_stage_completed'}


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def distribution(values):
    ordered = sorted(values)
    def percentile(q):
        return round(ordered[max(0, math.ceil(len(ordered)*q)-1)], 3) if ordered else None
    return {'observations': len(ordered), 'p50_ms': percentile(.5), 'p95_ms': percentile(.95),
            'p99_ms': percentile(.99), 'mean_ms': round(sum(ordered)/len(ordered), 3) if ordered else None,
            'max_ms': ordered[-1] if ordered else None}


class TimingStats:
    def __init__(self):
        self.groups = defaultdict(lambda: {'duration': [], 'queue': [], 'traces': set()})

    def add(self, row):
        event = row.get('event')
        if event not in EVENTS:
            return
        evidence = row.get('evidence') or {}
        stage = public_label(row.get('stage') or evidence.get('stage')) or event
        engine = public_label(row.get('engine_id') or row.get('engine')) or ''
        trace = evidence.get('trace_ref') or reference(row.get('trace_id'))
        values = [(event, stage, row.get('duration_ms'), row.get('queue_wait_ms'))]
        macro = row.get('macro_stage_timings_ms')
        if event == 'atomic_pipeline_completed' and isinstance(macro, dict):
            values.extend((event + '.macro', public_label(k), v, None) for k,v in macro.items() if public_label(k))
        for source, label, duration, queue in values:
            if not numeric(duration) and not numeric(queue):
                continue
            bucket = self.groups[(source, label, engine)]
            if numeric(duration): bucket['duration'].append(duration)
            if numeric(queue): bucket['queue'].append(queue)
            if trace: bucket['traces'].add(trace)

    def export(self):
        return [{'event': event, 'stage': stage, 'engine': engine,
                 'duration': distribution(bucket['duration']), 'queue_wait': distribution(bucket['queue']),
                 'distinct_traces': len(bucket['traces'])}
                for (event,stage,engine),bucket in sorted(self.groups.items())]


def from_diagnostics(diagnostics):
    if 'module_timings' in diagnostics:
        return {'scope': 'full_input_log_stream', 'groups': diagnostics['module_timings']}
    stats = TimingStats()
    for row in diagnostics.get('samples', []): stats.add(row)
    return {'scope': 'retained_prefix_only', 'groups': stats.export()}
