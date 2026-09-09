from performance.targets.echomem.acceptance.readiness import recall_admission_evidence


def test_target_env_overrides_file_without_exporting_other_values():
    result = recall_admission_evidence({'recall': {'max_inflight': 128}},
                                      ['SECRET=private', 'ECHOMEM_RECALL_MAX_INFLIGHT=16'])
    assert result['declared_max_inflight'] == 16
    assert result['source'] == 'target_container_environment'
    assert 'private' not in str(result)
    assert result['runtime_verified'] is False


def test_missing_limit_is_unknown_not_stage_limit():
    result = recall_admission_evidence({'recall': {'concurrency': {'engine': {'max_concurrent': 128}}}}, [])
    assert result['status'] == 'UNPINNED'
    assert result['declared_max_inflight'] is None


def test_bad_environment_value_is_not_exported():
    result = recall_admission_evidence({}, ['ECHOMEM_RECALL_MAX_INFLIGHT=private-error'])
    assert result['status'] == 'INVALID'
    assert 'private-error' not in str(result)


def test_zero_is_disabled_not_zero_capacity():
    assert recall_admission_evidence({'recall': {'max_inflight': 0}}, [])['status'] == 'CAP_DISABLED'
