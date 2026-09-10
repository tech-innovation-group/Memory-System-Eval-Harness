import ast
import pytest
from scripts.patch_feishu_extended_stress import patch_source


SOURCE = '''unchanged_before = 1
def run_stress_job(job_id):
    result_dir = RESULTS_DIR / job_id
    provision_command = ["--count", str(STRESS_TENANT_COUNT)]
    volumes = {str(STRESS_HARNESS_ROOT): {"bind": "/work/harness", "mode": "ro"}}
    update_job(message="创建 32 个独立压测租户", progress={"total": STRESS_TENANT_COUNT})
    profile = {"profiles": [{"name": "4U8G-Feishu", "base_url": base_url}]}
    command = ["python", "-m", "performance.targets.echomem.observation_run"]
unchanged_after = 2
'''


def test_bot_patch_is_scoped_and_idempotent():
    patched = patch_source(SOURCE)
    ast.parse(patched)
    assert patched.startswith('unchanged_before = 1\n')
    assert patched.endswith('unchanged_after = 2\n')
    assert 'tenant_count = max(64, STRESS_TENANT_COUNT)' in patched
    assert '"extended_load_tests": True' in patched
    assert 'ECHOMEM_MCP_PORT' in patched
    assert patch_source(patched) == patched


def test_unknown_bot_layout_is_not_silently_patched():
    with pytest.raises(ValueError):
        patch_source(SOURCE.replace('"name": "4U8G-Feishu",', '"name": "different",'))


def test_bot_can_pin_a_deployment_without_overwriting_shared_checkout():
    patched = patch_source(SOURCE, '/opt/harness-verified')
    assert "harness_root = Path('/opt/harness-verified')" in patched
    assert 'str(harness_root):' in patched
    with pytest.raises(ValueError):
        patch_source(SOURCE, 'relative')
