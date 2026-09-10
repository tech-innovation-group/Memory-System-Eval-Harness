"""Patch the deployed bot's stress function only; no credentials or defaults elsewhere change."""
import argparse
import ast
from pathlib import Path


def patch_source(source, harness_root=None):
    if harness_root is not None and not Path(harness_root).is_absolute():
        raise ValueError('harness_root must be absolute')
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_stress_job']
    if len(nodes) != 1:
        raise ValueError('Expected exactly one run_stress_job function')
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    body = ''.join(lines[node.lineno-1:node.end_lineno])
    if 'performance.targets.echomem.observation_run' not in body:
        raise ValueError('Bot does not use the supported observation entrypoint')
    if '# PR33 extended load contract' in body:
        return source
    replacements = [
        ('    result_dir = RESULTS_DIR / job_id',
         '    # PR33 extended load contract\n    tenant_count = max(64, STRESS_TENANT_COUNT)\n'
         + ('    harness_root = STRESS_HARNESS_ROOT\n' if harness_root is None else f'    harness_root = Path({str(harness_root)!r})\n')
         + '    result_dir = RESULTS_DIR / job_id'),
        ('str(STRESS_HARNESS_ROOT):', 'str(harness_root):'),
        ('"--count", str(STRESS_TENANT_COUNT)', '"--count", str(tenant_count)'),
        ('message="创建 32 个独立压测租户"', 'message=f"创建 {tenant_count} 个独立压测租户"'),
        ('"total": STRESS_TENANT_COUNT', '"total": tenant_count'),
        ('"name": "4U8G-Feishu",',
         '"name": "4U8G-Feishu",\n                "extended_load_tests": True,\n'
         '                "payload_boundary": {"mcp_base_url": f"http://127.0.0.1:{ECHOMEM_MCP_PORT}"},')]
    for before, after in replacements:
        if body.count(before) != 1:
            raise ValueError(f'Unsupported bot source: expected one anchor {before!r}')
        body = body.replace(before, after, 1)
    result = ''.join(lines[:node.lineno-1]) + body + ''.join(lines[node.end_lineno:])
    compile(result, '<patched-bot>', 'exec')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--harness-root', type=Path, help='Pin the host bind mount to a verified deployment directory')
    args = parser.parse_args()
    args.output.write_text(patch_source(args.input.read_text(), args.harness_root))
