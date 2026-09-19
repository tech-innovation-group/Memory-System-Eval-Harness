from scripts.build_quick_topology_report import supplemental_section


def test_boundary_supplement_is_escaped_and_not_a_unified_pass():
    result = supplemental_section({'summary': '<unsafe>', 'method': 'independent',
                                  'overview': {'headers': ['case', 'result'],
                                               'rows': [['long commit', 'failed']]}})
    assert '&lt;unsafe&gt;' in result
    assert '<unsafe>' not in result
    assert 'failed' in result
    assert '不与上方并发实验合并分母' in result
    assert '不是完整 M1–M3 验收通过证明' in result
    assert supplemental_section(None) == ''
