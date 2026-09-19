"""Install deterministic M1/M2/M3 routing into the deployed evaluation bot.

Patches narrowly checked anchors; never changes ordinary QA job semantics.
"""
from pathlib import Path
import argparse

HELPERS = '''
def parse_stress_command(text):
    normalized = re.sub(r"\\s+", "", text.lower()).strip("，。！？!?,:：")
    match = re.fullmatch(r"(?:请|帮我)?(?:压测|压力测试|性能测试|stress(?:test)?)(develop|pr#?(\\d+))", normalized)
    if not match:
        return None
    return ("develop", None) if match.group(1) == "develop" else ("pr", int(match.group(2)))


def apply_m123_service_tuning(config):
    """Set service admission/pool knobs high enough for 64/600 client loads."""
    tenant_coordination = config.get("tenant_coordination")
    if isinstance(tenant_coordination, dict):
        # The current EchoMem build rejects this legacy field even when it is
        # present in config.example.json.
        tenant_coordination.pop("pool_size", None)
    scheduling = config.setdefault("scheduling", {})
    scheduling.update({
        "http": {"max_workers": 2400},
        "retrieval": {"admission_permits": 600},
        "commit": {"queue_max": 2400, "tenant_quota": 600, "executor_workers": 600},
        "tenant": {"concurrency": 600, "qps": 2400},
        "llm_gateway": {
            "llm_max_concurrent": 2400, "embed_max_concurrent": 2400,
            "recall_llm_max_concurrent": 600, "recall_embed_max_concurrent": 600,
            "episode_llm_max_concurrent": 600, "episode_embed_max_concurrent": 600,
            "workers_llm_share": 600, "workers_embed_share": 600,
            "provider_budget_llm": 4200, "provider_budget_embed": 4200,
        },
    })
    config.setdefault("control_store", {})["pool_size"] = 500
    return config


def stress_repeat_count(job):
    """Resolve the bounded seed volume for this stress job."""
    raw = job.get("stress_seed_repeat_count") or os.getenv("M123_STRESS_REPEAT_COUNT", "100")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("M123_STRESS_REPEAT_COUNT must be 100 or 1000") from exc
    if value not in {100, 1000}:
        raise ValueError("M123_STRESS_REPEAT_COUNT must be 100 or 1000")
    return value


def stress_runner_spec(job, provisioning_key, prepared, echo_container):
    # The target is the source version prepared for THIS job, never the old
    # manually started target. Private credentials stay in /private tmpfs.
    result_dir = RESULTS_DIR / job["id"]
    result_dir.mkdir(parents=True, exist_ok=True)
    repeat_count = stress_repeat_count(job)
    profile = {"profiles": [{
        "name": f"bot-M1M2M3-64c-{repeat_count}", "base_url": f"http://127.0.0.1:{ECHOMEM_HTTP_PORT}",
        "tenant_config": "/private/tenants.json", "preflight_config": "/target/config.json",
        "resource_container": echo_container.name, "required_concurrency": 64,
        "service_concurrency_target": 600,
        "required_embedding_model": "qwen3.7-text-embedding-flash",
        "m1_topologies": ["concurrency"], "m1_concurrency_levels": [64],
        "m1_concurrency_tenants": 64, "m1_tenant_levels": [64], "m1_load_profile": "search",
        "m1_warmup_s": 10, "m1_duration_s": 60, "m1_seed_workers": 4,
        "m1_seed_timeout_s": 900, "m1_seed_profile": "locomo-single-sentence",
        "m1_seed_dataset": "/app/benchmarks/locomo/data/locomo10.json",
        "m1_seed_sample": "conv-30", "m1_seed_session": "session_1",
        "m1_seed_sentence_id": "D1:19", "m1_seed_repeat_count": repeat_count,
        "m1_seed_question_variant": 0, "m1_seed_validation_queries": 1,
        "semantic_seed_mode": "locomo-single-sentence", "semantic_seed_sample": "conv-30",
        "semantic_seed_session": "session_1", "semantic_seed_sentence_id": "D1:19",
        "semantic_seed_repeat_count": repeat_count, "semantic_seed_question_variant": 0,
        "semantic_seed_workers": 4, "semantic_seed_validation_queries": 1,
        "semantic_seed_identity_cache": "/out/M1/concurrency",
        "m2_tenant_levels": [2, 64],
        "m2m3_search_workers": 64, "require_stage_observability": True
    }]}
    (result_dir / "stress-profile.json").write_text(json.dumps(profile))
    command = ["python", "/app/scripts/feishu_m123_runner.py"]
    environment = {"PYTHONPATH": "/app", "PYTHONUNBUFFERED": "1",
                   "ECHOMEM_PROVISIONING_AUTH_KEY": provisioning_key,
                   "STRESS_BASE_URL": profile["profiles"][0]["base_url"],
                   "STRESS_JOB_ID": job["id"],
                   "STRESS_REPEAT_COUNT": str(repeat_count)}
    volumes = {
        "/opt/echomem-pr-bot/harness": {"bind": "/app", "mode": "ro"},
        str(DOCKER_RESULTS_DIR / job["id"]): {"bind": "/out", "mode": "rw"},
        str(prepared["config_path"]): {"bind": "/target/config.json", "mode": "ro"},
        "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "ro"},
        "/usr/bin/docker": {"bind": "/usr/bin/docker", "mode": "ro"},
    }
    return command, environment, volumes

'''

def patch_source(source):
    if '# M123 deterministic routing' in source:
        raise ValueError('Already patched')
    def replace(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError(f'Expected one anchor: {old[:100]}')
        source = source.replace(old, new, 1)
    replace(
        'ECHOMEM_HEALTH_TIMEOUT_S = int(os.getenv("ECHOMEM_HEALTH_TIMEOUT_S", "300"))',
        'ECHOMEM_HEALTH_TIMEOUT_S = int(os.getenv("ECHOMEM_HEALTH_TIMEOUT_S", "900"))',
    )
    replace('def parse_test_command(text:', '# M123 deterministic routing\n'+HELPERS+'\ndef parse_test_command(text:')
    replace('    retry_of: str = "",\n) -> dict[str, Any]:', '    retry_of: str = "",\n    test_type: str = "full",\n) -> dict[str, Any]:')
    replace('        "test_type": "full",\n        "source_ref": source_ref,', '        "test_type": test_type,\n        "source_ref": source_ref,')
    replace('        retry_of=job_id,', '        retry_of=job_id,\n        test_type=job.get("test_type", "full"),')
    replace('    command = parse_test_command(text)\n', '    stress_command = parse_stress_command(text)\n    command = stress_command or parse_test_command(text)\n')
    replace('    if command is None:\n        command = parse_test_command_with_llm(text)', '    if command is None and re.search(r"压测|压力测试|性能测试|stress", text, re.I):\n        send_feishu_text(chat_id, "压测请使用：压测 develop 或 压测 PR编号；默认执行 M1/M2/M3。查询请使用：状态 任务ID。")\n        return jsonify({"code": 0})\n    if command is None:\n        command = parse_test_command_with_llm(text)')
    replace('                chat_id=chat_id,\n            )\n            code_source', '                chat_id=chat_id,\n                test_type="stress" if stress_command else "full",\n            )\n            code_source')
    replace('f"任务已创建\\nLoCoMo / conv-30\\n{code_source}\\n"', 'f"任务已创建\\n{\'M1/M2/M3 压测 · 64 并发 · 注入量可配置为 100/1000\' if stress_command else \'LoCoMo / conv-30\'}\\n{code_source}\\n"')
    replace('                "服务器单并发排队执行，完成后自动回传准确率和结果文件。",', '                + ("任务排队执行，结果页展示 M1/M2/M3 报告。" if stress_command else "服务器单并发排队执行，完成后自动回传准确率和结果文件。"),')
    replace('        echo_container = client.containers.run(\n            prepared["image"],', '        echo_container = client.containers.run(\n            prepared["image"],\n            **({"nano_cpus": 4000000000, "mem_limit": "8g"} if job.get("test_type") == "stress" else {}),')
    replace('        prepared = prepare_echomem_source(job, secret_values)', '        prepared = prepare_echomem_source(job, secret_values)\n        if job.get("test_type") == "stress":\n            config_path = Path(prepared["config_path"])\n            config = json.loads(config_path.read_text())\n            config.setdefault("runtime", {})["log_level"] = "DEBUG"\n            config.setdefault("logging", {}).update(level="debug", format="json")\n            config = apply_m123_service_tuning(config)\n            config_path.write_text(json.dumps(config))')
    anchor='        eval_container = client.containers.run(\n            IMAGE,'
    replace(anchor, '''        runner_volumes = {str(DOCKER_RESULTS_DIR): {"bind": "/app/results", "mode": "rw"}}
        if job.get("test_type") == "stress":
            command, environment, runner_volumes = stress_runner_spec(job, provisioning_key, prepared, echo_container)
            environment.update({k: v for k, v in echo_environment.items() if k != "ECHOMEM_REGISTRY_MASTER_KEY"})
            environment.update({
                "ANOMALY_LLM_BASE_URL": secret_values["llm_base_url"],
                "ANOMALY_LLM_MODEL": secret_values["llm_model"],
                "ANOMALY_LLM_API_KEY": secret_values["llm_api_key"],
            })
        eval_container = client.containers.run(
            "echomem-m123-runner:20260918" if job.get("test_type") == "stress" else IMAGE,''')
    # Scope replacements to source worker only, not other job runners.
    start=source.index('def run_source_job('); end=source.index('\ndef run_job(', start)
    body=source[start:end]
    body=body.replace('user=f"{RUN_UID}:{RUN_GID}",', 'user="0:0" if job.get("test_type") == "stress" else f"{RUN_UID}:{RUN_GID}",\n            working_dir="/app",\n            tmpfs={"/private": "mode=0700"},')
    old='''volumes={
                str(DOCKER_RESULTS_DIR): {
                    "bind": "/app/results",
                    "mode": "rw",
                }
            },'''
    if body.count(old)!=1: raise ValueError('runner volume anchor')
    body=body.replace(old,'volumes=runner_volumes,')
    body=body.replace('status = "completed" if exit_code == 0 else "failed"','status = "completed" if exit_code == 0 else "failed"\n        if job.get("test_type") == "stress" and not (RESULTS_DIR / job_id / "report.html").is_file():\n            raise RuntimeError("WRONG_ENTRYPOINT: M1/M2/M3 report.html missing")')
    source=source[:start]+body+source[end:]
    replace('    if not job or not job.get("feishu_chat_id"):\n        return\n    try:', '''    if not job or not job.get("feishu_chat_id"):
        return
    if job.get("test_type") == "stress":
        send_feishu_text(str(job["feishu_chat_id"]), f"M1/M2/M3 压测：{job.get('status')}\\n{job.get('message', '')}\\n报告与进度：{job_detail_url(job_id)}")
        return
    try:''')
    replace('    job = {\n        **job,\n        "summary": compact_job(job).get("summary") or {},\n    }', '''    if job.get("test_type") == "stress":
        report = RESULTS_DIR / job_id / "report.html"
        if report.is_file():
            return send_file(report)
        return jsonify({"id": job_id, "test_type": "stress", "metrics": ["M1", "M2", "M3"],
                        "status": job.get("status"), "message": job.get("message"),
                        "progress": job.get("progress"), "report": "尚未生成，准备完成后刷新本页"})
    job = {
        **job,
        "summary": compact_job(job).get("summary") or {},
    }''')
    replace('            fixed_commit_sha=fixed_commit_sha,\n            chat_id=chat_id,\n        )', '            fixed_commit_sha=fixed_commit_sha,\n            chat_id=chat_id,\n            test_type="stress" if payload.get("test_type") == "stress" else "full",\n        )')
    replace('    return send_file(path)\n\n\n@app.get("/jobs/<job_id>/download/<kind>")', '    if "private" in path.name or path.suffix == ".env":\n        abort(403)\n    return send_file(path)\n\n\n@app.get("/jobs/<job_id>/download/<kind>")')
    compile(source,'<patched-bot>','exec')
    return source

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--input',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); a.output.write_text(patch_source(a.input.read_text()))
