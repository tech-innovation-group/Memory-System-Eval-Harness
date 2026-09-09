from performance.targets.echomem.acceptance.capacity_seed import CapacityActor, semantic_checks
from performance.targets.echomem.acceptance.semantic_corpus import build_corpus
from performance.targets.echomem.probes._client import HttpResult
import pytest


def test_seed_timeout_preserves_type_without_raw_error():
    class Client:
        agent_id = 'test'

        def request(self, *args, **kwargs):
            return HttpResult('POST', '/api/retrieval/search', None, 10,
                              error='private upstream message', transport_error_type='timeout')

    actor = CapacityActor(0, 0, Client(), build_corpus('unit-test'))
    rows = list(semantic_checks(actor, 1))
    assert rows[0]['transport_error_type'] == 'timeout'
    assert rows[0]['request_timeout_s'] == 60
    assert rows[0]['success'] is False
    assert rows[0]['query'] == actor.corpus['recall_queries'][0]['query']
    assert rows[0]['expected_aliases'] == actor.corpus['recall_queries'][0]['aliases']
    assert 'private upstream message' not in str(rows)


def test_configured_seed_timeout_reaches_http_client():
    class Client:
        agent_id = 'test'

        def request(self, *args, **kwargs):
            assert kwargs['timeout_s'] == 30
            return HttpResult('POST', '/api/retrieval/search', 200, 13,
                              payload={'items': []})

    rows = list(semantic_checks(CapacityActor(0, 0, Client(), build_corpus('test')), 1,
                                search_timeout_s=30))
    assert rows[0]['request_timeout_s'] == 30
    assert rows[0]['success'] is False


@pytest.mark.parametrize('timeout', [True, 0, -1, float('inf'), float('nan'), '30'])
def test_invalid_timeout_rejected_before_seed_io(timeout):
    from performance.targets.echomem.acceptance.capacity_seed import seed_actor
    with pytest.raises(ValueError, match='finite and positive'):
        seed_actor(CapacityActor(0, 0, None, {}), search_timeout_s=timeout)
