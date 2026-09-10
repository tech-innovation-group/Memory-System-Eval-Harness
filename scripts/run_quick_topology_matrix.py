"""Bounded, real HTTP topology comparison; called inside a prepared runner."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import html
import json
import threading
import time

from performance.targets.echomem.acceptance.preflight import run_preflight
from performance.targets.echomem.probes._client import EchoMemHTTP, load_tenant_specs
from performance.targets.echomem.probes.concurrency_topology import _commit_call, _search_call, _summary, _InflightCounter

FACT = 'My project review is on September 18 at 10 AM in meeting room Cedar. '
QUERIES = [
    {'id': 'room-1', 'query_type': 'recall', 'query': 'Which room is my project review meeting in?', 'aliases': ['Cedar']},
    {'id': 'room-2', 'query_type': 'recall', 'query': 'Where should I go for my project review?', 'aliases': ['Cedar']},
    {'id': 'room-3', 'query_type': 'recall', 'query': 'Remind me of the room booked for my project review.', 'aliases': ['Cedar']},
]


def plans():
    return [('baseline', 4, 4, 1, 1)] + [
        (name, level, users, sessions, width)
        for level in (16, 64)
        for name, users, sessions, width in (
            ('users', level, 1, 1), ('sessions', 4, level // 4, 1),
            ('session-concurrent', 4, 1, level // 4))
    ] + [('mixed', n, 4, 1, n // 4) for n in (4, 16, 64)]


def save(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def report(out, state):
    save(out / 'quick-matrix.json', state)
    sections = []
    for scene in state['scenes']:
        s, c = scene['search'], scene['commit']
        values = [('Search P95 (ms)', s['p95_ms']), ('Healthy Search', f"{s['search_quality_ok']}/{s['offered']}"),
                  ('Commit completed', f"{c['commit_completed']}/{c['offered']}"),
                  ('Commit timeout', c['commit_timed_out'])]
        labels = ''.join(f'<div><b>{html.escape(str(v))}</b><br>{k}</div>' for k, v in values)
        denominator = max(1, s['offered'])
        chart = ''.join(f'<p>{label}: {value}<span style="display:block;height:12px;background:{color};width:{value/denominator*100:.2f}%"></span></p>'
                        for label, value, color in [('Healthy recall', s['search_quality_ok'], '#23765b'),
                                                   ('Degraded', s['search_degraded'], '#bc6b18'),
                                                   ('Non-healthy', s['offered']-s['search_quality_ok'], '#b63848')])
        sections.append(f'<section><h2>{scene["name"]} / {scene["level"]}</h2><div class="stats">{labels}</div>{chart}'
                        f'<p>Users {scene["users"]}; sessions/user {scene["sessions"]}; configured slots {scene["level"]}; '
                        f'observed HTTP peak {scene["http_peak"]}; elapsed {scene["elapsed_s"]:.1f}s.</p>'
                        f'<details><summary>Per-user statistics and errors</summary><pre>{html.escape(json.dumps(scene,ensure_ascii=False,indent=2))}</pre></details></section>')
    page = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>Topology comparison</title><style>body{font:16px/1.6 system-ui;margin:24px auto;padding:0 20px;max-width:1100px;color:#202923;background:#fafbfb}section{border-top:1px solid #ccd5cf;padding:24px 0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}b{font-size:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:600px){.stats{grid-template-columns:1fr 1fr}}</style>'
    page += f'<h1>四类拓扑短测对比</h1><p>{html.escape(state["status"])} · {len(state["scenes"])}/10 groups · updated {time.strftime("%H:%M:%S")}</p>'
    page += '<p>Real HTTP / real models. Short-run observations, not maximum capacity. HTTP202 is acceptance, not completion. Timeout is not server failure. Degraded overlaps non-healthy; bars are not additive.</p>'
    page += ''.join(sections)
    page += '<p>Search: 10s warmup + 45s measurement. Mixed: separate Search/Commit workers, 60s admission window; each Commit observed up to90s. New Session per Commit avoids task reuse. All users have Search and Commit offers; short/long groups are reported separately in raw evidence.</p>'
    (out / 'report.html').write_text(page)


def run(out, base):
    out = Path(out)
    state = {'status': 'PREFLIGHT', 'scenes': [], 'seed': []}
    report(out, state)
    preflight = run_preflight(out / 'config.json', timeout_s=40, retry_attempts=1, required_kinds=('llm', 'embedding'))
    # Public artifact retains status only; provider credentials remain in config/env.
    save(out / 'quick-preflight.json', {'ok': bool(preflight.get('ok'))})
    if not preflight.get('ok'):
        state['status'] = 'BLOCKED_MODEL_PREFLIGHT'; report(out, state); return
    tenants = load_tenant_specs(out / 'tenants.json')
    if len(tenants) < 64:
        state['status'] = 'BLOCKED_REQUIRES_64_IDENTITIES'; report(out, state); return
    clients = [EchoMemHTTP(base, t.auth_key, timeout_s=20, tenant_id=t.tenant_id,
                          user_id=t.user_id, account_id=t.account_id, agent_id=t.agent_id) for t in tenants]
    def seed(i):
        session, _ = clients[i].open_session(tenants[i].tenant_id, 'quick-seed')
        commit = _commit_call(clients[i], tenants[i].tenant_id, session, FACT, 90)()
        attempts = []
        for delay in (0, 2, 5):
            time.sleep(delay)
            search = _search_call(clients[i], tenants[i].tenant_id, session, QUERIES[0], 20, 'seed')()
            attempts.append(search)
            if search.get('recall_hit'):
                break
        return {'user_index': i, 'commit': commit, 'search': search, 'visibility_attempts': attempts}
    state['status'] = 'SEEDING'; report(out, state)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for row in pool.map(seed, range(64)):
            state['seed'].append(row)
            report(out, state)
    state['seed_quality'] = {
        'total': len(state['seed']),
        'completed': sum(r['commit'].get('terminal_state') == 'completed' for r in state['seed']),
        'hits': sum(bool(r['search'].get('recall_hit')) for r in state['seed']),
        'healthy': sum(bool(r['search'].get('quality_ok')) for r in state['seed']),
    }
    state['quality_warning'] = 'Seed misses/degradation retained; endpoint latency is not a healthy-recall baseline.'
    if any(r['commit'].get('terminal_state') == 'timeout' for r in state['seed']):
        state['status'] = 'BLOCKED_SEED_PENDING'; report(out, state); return
    # Retain degradation in every observation, even when the fact exists.
    for name, level, users, sessions, width in plans():
        state['status'] = f'RUNNING {name}/{level}'; report(out, state)
        session_ids = [[clients[i].open_session(tenants[i].tenant_id, f'quick-{name}-{j}')[0]
                        for j in range(sessions)] for i in range(users)]
        counter = _InflightCounter()
        barrier = threading.Barrier(level + 1)
        rows, lock = [], threading.Lock()
        warmup = 0 if name == 'mixed' else 10
        duration = 60 if name == 'mixed' else 45
        timing = {}
        def worker(slot):
            barrier.wait()
            iteration = 0
            while time.monotonic() < timing['end']:
                begun = time.monotonic()
                if name == 'mixed':
                    op = 'search' if slot < level // 2 else 'commit'
                    actor = (slot % (level // 2) + iteration) % users
                    session = session_ids[actor][0]
                else:
                    op = 'search'; actor = slot // (sessions * width)
                    session = session_ids[actor][(slot // width) % sessions]
                if op == 'commit':
                    session, _ = clients[actor].open_session(tenants[actor].tenant_id, 'quick-independent-commit')
                    size = 512 if actor < 2 else 4096
                    content = (FACT * (size // len(FACT) + 1))[:size]
                    row = _commit_call(clients[actor], tenants[actor].tenant_id, session, content, 90, inflight=counter)()
                    row['content_chars'] = size
                else:
                    row = _search_call(clients[actor], tenants[actor].tenant_id, session, QUERIES[iteration % len(QUERIES)], 20, 'small', inflight=counter)()
                row['user_index'] = actor
                row['start_s'] = begun - timing['start']
                row['finish_s'] = time.monotonic() - timing['start']
                if begun >= timing['measure']:
                    with lock: rows.append(row)
                iteration += 1
        started = time.monotonic()
        timing.update(start=started, measure=started + warmup, end=started + warmup + duration)
        with ThreadPoolExecutor(max_workers=level) as pool:
            futures = [pool.submit(worker, slot) for slot in range(level)]
            barrier.wait()
            for future in futures: future.result()
        elapsed = time.monotonic() - started
        scene = {'name': name, 'level': level, 'users': users, 'sessions': sessions,
                 'session_width': width, 'http_peak': counter.peak, 'elapsed_s': elapsed,
                 'measurement_s': duration, 'drain_s': max(0, elapsed - warmup - duration)}
        for op in ('search', 'commit'):
            subset = [r for r in rows if r['operation'] == op]
            scene[op] = _summary(subset, max(elapsed - warmup, .001))
        scene['per_user'] = {str(i): {op: _summary([r for r in rows if r['user_index'] == i and r['operation'] == op], max(elapsed-warmup, .001)) for op in ('search','commit')} for i in range(users)}
        save(out / f'{name}-{level}-samples.json', rows)
        state['scenes'].append(scene); report(out, state)
        print('SCENE_DONE', name, level, 'search', scene['search']['offered'], 'commit', scene['commit']['offered'], flush=True)
        if scene['commit']['commit_timed_out']:
            state['status'] = 'BLOCKED_UNRESOLVED_BACKLOG'; report(out, state); return
    state['status'] = 'FINISHED'; report(out, state)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True,
                        help='Prepared private run directory containing config.json and tenants.json')
    parser.add_argument('--base-url', required=True, help='Explicit authorized EchoMem HTTP endpoint')
    args = parser.parse_args()
    run(args.out, args.base_url)
