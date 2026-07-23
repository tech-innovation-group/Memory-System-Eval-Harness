export function createEchoAgentLiveActions(deps) {
  const {
    api,
    currentAccountConfig,
    firstValue,
    formReaders,
    state,
    tasksForBenchmark,
    validatePayload,
  } = deps;

  async function startImport() {
    const form = formReaders.readEchoAgentLiveImportForm();
    const config = currentAccountConfig() || {};
    
    const payload = {
      kind: "echoagent_live",
      echoagent_url: firstValue(form.echoagent_url, config.echoagent_url, "http://127.0.0.1:31020"),
      echomem_url: firstValue(form.echomem_url, config.echomem_url, "http://127.0.0.1:8010"),
      username: firstValue(form.username, config.echoagent_username, "test_user"),
      password: firstValue(form.password, config.echoagent_password, "test_password"),
      num_batches: Math.max(1, Number(form.num_batches) || 3),
      queries_per_batch: Math.max(1, Number(form.queries_per_batch) || 5),
      custom_scenario: form.custom_scenario || "",
      scenario_model: firstValue(form.scenario_model, config.echoagent_scenario_model, "deepseek-v4-flash"),
      scenario_base_url: form.scenario_base_url || config.echoagent_scenario_base_url || "",
      scenario_api_key: form.scenario_api_key || config.echoagent_scenario_api_key || "",
      user_simulator_config: form.user_simulator_config || "",
      evaluator_config: form.evaluator_config || "",
    };

    const validation = await validatePayload(payload);
    if (!validation.ok) {
      throw new Error(validation.error || "参数校验失败");
    }

    const task = await api.post("/api/tasks", payload);
    return { task, stage: "qa" };
  }

  async function startQa() {
    // EchoAgent Live 的测试在 import 阶段已经启动
    // 这里直接跳转到结果查看
    const tasks = tasksForBenchmark("echoagent_live");
    const activeTask = tasks.find((t) => t.status === "running" || t.status === "queued");
    if (activeTask) {
      return { task: activeTask, stage: "qa" };
    }
    // 如果没有活跃任务，启动新的测试
    return startImport();
  }

  async function preflightQa() {
    return { ok: true, checks: [] };
  }

  async function preflightJudge() {
    return { ok: true };
  }

  async function runJudge() {
    return { stage: "report" };
  }

  return {
    startImport,
    startQa,
    preflightQa,
    preflightJudge,
    runJudge,
  };
}