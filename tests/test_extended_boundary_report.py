import json

from scripts.build_extended_boundary_report import build


def test_rule_diagnostic_is_not_reported_as_http_success(tmp_path):
    (tmp_path / 'rule-cost.json').write_text(json.dumps({
        'scope': 'isolated regex CPU diagnostic, not HTTP or model benchmark',
        'asset_sha256': 'asset-fingerprint', 'pattern_timeout_s': .3,
        'samples': [{'rule_index': 11, 'chars': 4096, 'status': 'TIME_LIMIT',
                     'wall_ms': 300, 'cpu_ms': 290, 'matched': None}],
    }))
    report = build(tmp_path)
    objective = report['profiles'][-1]['objectives'][0]
    assert objective['status'] == 'PARTIAL'
    assert '非HTTP性能数据' in report['overview']['rows'][-1][-1]
    assert 'TIME_LIMIT是诊断器主动中断' in objective['reason']
    assert objective['observed']['timings'][0]['matched'] is None
    assert objective['observed']['asset_sha256'] == 'asset-fingerprint'


def test_no_rule_evidence_means_no_rule_claim(tmp_path):
    assert all(p['name'] != '长Search规则诊断（本机隔离执行）'
               for p in build(tmp_path)['profiles'])
