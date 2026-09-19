import ast
import os
import re
import unittest
from scripts.patch_feishu_m123_routing import HELPERS


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.ns = {'re': re, 'os': os}
        exec(HELPERS, self.ns)

    def test_explicit_stress_commands(self):
        parse = self.ns['parse_stress_command']
        for text in ['压测develop', '压测 develop', '压力测试 develop', '性能测试develop', '帮我压测 develop。', 'stress test develop']:
            self.assertEqual(parse(text), ('develop', None), text)
        for text in ['压测 PR33', '压测pr#33', '压力测试 PR 534']:
            self.assertEqual(parse(text)[0], 'pr')
        self.assertEqual(parse('压测pr#33'), ('pr', 33))

    def test_questions_and_qa_do_not_start_load(self):
        for text in ['测试develop', '压测develop结果怎么样', '不要压测develop', '查询28f328593461', '压测', '压测 develop; rm -rf /']:
            self.assertIsNone(self.ns['parse_stress_command'](text), text)

    def test_only_successfully_completed_stress_jobs_preserve_their_result(self):
        completed = self.ns['completed_stress_execution']
        self.assertTrue(completed({
            'test_type': 'stress', 'status': 'completed', 'exit_code': 0,
        }))
        self.assertFalse(completed({
            'test_type': 'stress', 'status': 'completed', 'exit_code': 2,
        }))
        self.assertFalse(completed({
            'test_type': 'stress', 'status': 'running', 'exit_code': 0,
        }))
        self.assertFalse(completed({
            'test_type': 'full', 'status': 'completed', 'exit_code': 0,
        }))
        self.assertFalse(completed({
            'test_type': 'stress', 'status': 'completed', 'exit_code': None,
        }))

    def test_profile_contract(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        import json
        with tempfile.TemporaryDirectory() as root:
            self.ns.update(RESULTS_DIR=Path(root), DOCKER_RESULTS_DIR=Path(root), json=json,
                           ECHOMEM_HTTP_PORT=18170)
            cmd, env, volumes = self.ns['stress_runner_spec']({'id': 'test'}, 'private-test-key',
                        {'config_path': '/target.json'}, SimpleNamespace(name='job-target'))
            profile=json.loads((Path(root)/'test/stress-profile.json').read_text())['profiles'][0]
            self.assertEqual(profile['m1_concurrency_levels'], [64])
            self.assertEqual(profile['m1_concurrency_tenants'], 64)
            self.assertEqual(profile['m1_tenant_levels'], [64])
            self.assertEqual(profile['m2_tenant_levels'], [2, 64])
            self.assertEqual(profile['m1_seed_repeat_count'], 100)
            self.assertEqual(profile['semantic_seed_repeat_count'], 100)
            self.assertEqual(profile['resource_container'], 'job-target')
            self.assertEqual(profile['service_concurrency_target'], 600)
            self.assertNotIn('private-test-key', json.dumps(profile))
        tune = self.ns.get('apply_m123_service_tuning')
        # The tuning contract is generated in the deployed bot; verify its
        # selected values without touching a real EchoMem instance.
        if tune:
            cfg = tune({})
            self.assertEqual(cfg['scheduling']['http']['max_workers'], 2400)
            self.assertEqual(cfg['scheduling']['retrieval']['admission_permits'], 600)
            self.assertEqual(cfg['scheduling']['fanout'], {
                'executor_workers': 1200,
                'engine_max_inflight': 600,
            })
            self.assertEqual(cfg['scheduling']['llm_gateway']['recall_llm_max_concurrent'], 600)
            self.assertEqual(cfg['recall']['concurrency']['engine'], {
                'max_concurrent': 1200,
                'queue_capacity': 2400,
                'max_queued_per_tenant': 1200,
            })
            self.assertEqual(cfg['recall']['concurrency']['query_embedding']['max_concurrent'], 600)
            self.assertEqual(cfg['commit_pipeline']['queue_max'], 2400)
            self.assertEqual(cfg['commit_pipeline']['tenant_quota'], 600)
            self.assertIn('/opt/echomem-pr-bot/harness', volumes)
            self.assertEqual(cmd, ['python', '/app/scripts/feishu_m123_runner.py'])
            profile_1000 = json.loads((Path(root)/'test-1000/stress-profile.json').read_text())['profiles'][0] if (Path(root)/'test-1000/stress-profile.json').exists() else None
            self.ns['stress_runner_spec']({'id': 'test-1000', 'stress_seed_repeat_count': 1000}, 'private-test-key',
                                          {'config_path': '/target.json'}, SimpleNamespace(name='job-target'))
            profile_1000 = json.loads((Path(root)/'test-1000/stress-profile.json').read_text())['profiles'][0]
            self.assertEqual(profile_1000['m1_seed_repeat_count'], 1000)
            with self.assertRaises(ValueError):
                self.ns['stress_runner_spec']({'id': 'bad', 'stress_seed_repeat_count': 200}, 'private-test-key',
                                              {'config_path': '/target.json'}, SimpleNamespace(name='job-target'))

if __name__ == '__main__':
    unittest.main()
