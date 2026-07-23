import { normalizeLocomoQaForm } from "./locomo-qa-defaults.js";
import { normalizeLongMemEvalDatasetPath, preferredLongMemEvalDatasetRecord } from "./longmemeval-defaults.js";

export function createFormReaders({ $, queryAll, currentBenchmark, currentWorkspace, state }) {
  function officialQaDraft(benchmarkId = "") {
    state.officialQaDrafts = state.officialQaDrafts || {};
    const key = String(benchmarkId || currentBenchmark()?.id || "").trim().toLowerCase();
    if (!key) return {};
    if (!state.officialQaDrafts[key] || typeof state.officialQaDrafts[key] !== "object") {
      state.officialQaDrafts[key] = {};
    }
    return state.officialQaDrafts[key];
  }

  function scopedNode(id, fallback = null) {
    const stage = state?.activeStage || "";
    const scoped = stage
      ? queryAll(`[data-stage-panel="${stage}"] #${id}`)
      : [];
    if (scoped.length === 1) return scoped[0];
    const activeScoped = queryAll(`.wb-stage.active #${id}`);
    if (activeScoped.length === 1) return activeScoped[0];
    return fallback || $(id);
  }

  function readText(id, fallback = "") {
    const node = scopedNode(id);
    return node ? String(node.value || "").trim() : String(fallback || "");
  }

  function readNumber(id, fallback) {
    const node = scopedNode(id);
    const value = Number(node?.value ?? fallback);
    return Number.isFinite(value) ? value : Number(fallback);
  }

  function readChecked(id, fallback = false) {
    const node = scopedNode(id);
    if (!node) return Boolean(fallback);
    return Boolean(node.checked);
  }

  function readOfficialDraftText(id, fallback = "", benchmarkId = "") {
    const node = scopedNode(id);
    if (node) return String(node.value || "").trim();
    const draft = officialQaDraft(benchmarkId);
    if (Object.prototype.hasOwnProperty.call(draft, id)) {
      return String(draft[id] ?? "").trim();
    }
    return String(fallback || "");
  }

  function readOfficialDraftNumber(id, fallback, benchmarkId = "") {
    const node = scopedNode(id);
    const draft = officialQaDraft(benchmarkId);
    const rawValue = node
      ? node.value
      : (Object.prototype.hasOwnProperty.call(draft, id) ? draft[id] : fallback);
    const value = Number(rawValue ?? fallback);
    return Number.isFinite(value) ? value : Number(fallback);
  }

  function readOfficialDraftChecked(id, fallback = false, benchmarkId = "") {
    const node = scopedNode(id);
    if (node) return Boolean(node.checked);
    const draft = officialQaDraft(benchmarkId);
    if (Object.prototype.hasOwnProperty.call(draft, id)) {
      return Boolean(draft[id]);
    }
    return Boolean(fallback);
  }

  function readWorkspaceValue() {
    return readText("wbWorkspace", currentWorkspace());
  }

  function preferredLocomoSample(fallback = "all") {
    const draftSample = String(state?.locomoQaDraft?.wbQaSample || "").trim();
    const scopedSample = String(state?.questionSamples?.locomo || "").trim();
    return draftSample || scopedSample || String(fallback || "").trim() || "all";
  }

  function readLocomoSampleValue(id, fallback = "all") {
    const node = scopedNode(id);
    const nodeValue = node ? String(node.value || "").trim() : "";
    const explicit = state?.locomoQaDraft?.wbQaSampleExplicit === true;
    const preferred = preferredLocomoSample(fallback);
    if (explicit) return nodeValue || preferred || "all";
    if (nodeValue && nodeValue !== "all") return nodeValue;
    return preferred || nodeValue || "all";
  }

  function readDataPathValue() {
    return readText("wbDataPath", currentBenchmark().defaultData);
  }

  function readBenchmarkDataPathValue(benchmarkId = "") {
    const benchmark = currentBenchmark();
    const rawValue = readDataPathValue();
    if (String(benchmarkId || "").toLowerCase() !== "longmemeval") return rawValue;
    const datasetRecords = (state?.datasets || []).filter((item) => String(item?.format || "").toLowerCase() === "longmemeval");
    const preferred = preferredLongMemEvalDatasetRecord(datasetRecords);
    return normalizeLongMemEvalDatasetPath(rawValue, datasetRecords, preferred?.path || benchmark.defaultData || rawValue);
  }

  function readLocomoDraftText(id, fallback = "") {
    const node = scopedNode(id);
    if (node) return String(node.value || "").trim();
    if (id === "wbQaQuestionIds") {
      const draftValue = String(state?.locomoQaDraft?.wbQaQuestionIds || "").trim();
      if (draftValue) return draftValue;
      const selectedIds = Array.from(state?.locomoSelectedQuestions || [])
        .map((item) => String(item || "").trim())
        .filter(Boolean);
      if (selectedIds.length) return selectedIds.join(",");
    }
    if (id === "wbQaWrongCsv") {
      const draftValue = String(state?.locomoQaDraft?.wbQaWrongCsv || "").trim();
      if (draftValue) return draftValue;
      const currentWrongCsv = String(state?.locomoWrongCsv || "").trim();
      if (currentWrongCsv) return currentWrongCsv;
    }
    return String(fallback || "");
  }

  function readHotpotQaImportForm() {
    return {
      data: readDataPathValue(),
      count: Math.max(1, readNumber("wbImportCount", 10)),
      hotpotqa_corpus_mode: readText("wbHotpotQaCorpusMode", "global_sentence_corpus") || "global_sentence_corpus",
      hotpotqa_global_import_mode: readText("wbHotpotQaGlobalImportMode", "projection") || "projection",
      workspace: readWorkspaceValue(),
    };
  }

  function readHotpotQaQaForm() {
    const benchmarkId = "hotpotqa";
    return {
      data: readOfficialDraftText("wbDataPath", currentBenchmark().defaultData, benchmarkId),
      count: Math.max(1, readOfficialDraftNumber("wbHotpotCount", 10, benchmarkId)),
      mode: readOfficialDraftText("wbQaMode", "full", benchmarkId) || "full",
      question_ids: readOfficialDraftText("wbQaQuestionIds", "", benchmarkId),
      hotpotqa_corpus_mode: readOfficialDraftText("wbHotpotQaCorpusMode", "global_sentence_corpus", benchmarkId) || "global_sentence_corpus",
      hotpotqa_global_import_mode: readOfficialDraftText("wbHotpotQaGlobalImportMode", "projection", benchmarkId) || "projection",
      checkpoint_interval: Math.max(0, readOfficialDraftNumber("wbHotpotQaCheckpointInterval", 5, benchmarkId)),
      top_k: Math.max(1, readOfficialDraftNumber("wbQaTopK", 8, benchmarkId)),
      use_tools: readOfficialDraftChecked("wbQaUseTools", false, benchmarkId),
      official_eval_after: readOfficialDraftChecked("wbOfficialEval", true, benchmarkId),
      tool_search_limit: Math.max(1, readOfficialDraftNumber("wbQaToolSearchLimit", 8, benchmarkId)),
      max_iterations: Math.max(1, readOfficialDraftNumber("wbQaMaxIterations", 8, benchmarkId)),
      retrieval_mode: readOfficialDraftText("wbQaRetrievalMode", "search", benchmarkId) || "search",
      tool_set: readOfficialDraftText("wbQaToolSet", "", benchmarkId),
      question_timeout_s: readOfficialDraftNumber("wbQaQuestionTimeout", 600, benchmarkId),
      workspace: readOfficialDraftText("wbWorkspace", currentWorkspace(), benchmarkId),
    };
  }

  function readLongMemEvalImportForm() {
    return {
      data: readBenchmarkDataPathValue("longmemeval"),
      count: Math.max(1, readNumber("wbImportCount", 10)),
      workspace: readWorkspaceValue(),
    };
  }

  function readLongMemEvalQaForm() {
    const benchmarkId = "longmemeval";
    return {
      data: normalizeLongMemEvalDatasetPath(
        readOfficialDraftText("wbDataPath", currentBenchmark().defaultData, benchmarkId),
        (state?.datasets || []).filter((item) => String(item?.format || "").toLowerCase() === "longmemeval"),
        currentBenchmark().defaultData,
      ),
      count: Math.max(1, readOfficialDraftNumber("wbLongMemEvalCount", 10, benchmarkId)),
      mode: readOfficialDraftText("wbQaMode", "full", benchmarkId) || "full",
      question_ids: readOfficialDraftText("wbQaQuestionIds", "", benchmarkId),
      top_k: Math.max(1, readOfficialDraftNumber("wbQaTopK", 8, benchmarkId)),
      use_tools: readOfficialDraftChecked("wbQaUseTools", false, benchmarkId),
      official_eval_after: readOfficialDraftChecked("wbOfficialEval", true, benchmarkId),
      tool_search_limit: Math.max(1, readOfficialDraftNumber("wbQaToolSearchLimit", 8, benchmarkId)),
      max_iterations: Math.max(1, readOfficialDraftNumber("wbQaMaxIterations", 8, benchmarkId)),
      retrieval_mode: readOfficialDraftText("wbQaRetrievalMode", "search", benchmarkId) || "search",
      tool_set: readOfficialDraftText("wbQaToolSet", "", benchmarkId),
      question_timeout_s: readOfficialDraftNumber("wbQaQuestionTimeout", 600, benchmarkId),
      qa_parallelism: Math.max(1, readOfficialDraftNumber("wbQaParallelism", 10, benchmarkId)),
      workspace: readOfficialDraftText("wbWorkspace", currentWorkspace(), benchmarkId),
    };
  }

  function readLocomoImportForm() {
    return {
      data: readDataPathValue(),
      sample: readLocomoSampleValue("wbImportSample", "all"),
      echomem_root: readText("wbImportEchomemRoot", state?.locomoQaDraft?.wbQaEchomemRoot || ""),
      echomem_base_url: readText("wbQaEchomemBaseUrl", state?.locomoQaDraft?.wbQaEchomemBaseUrl || "") || "",
      workspace: readWorkspaceValue(),
    };
  }

  function readLocomoQaForm() {
    return normalizeLocomoQaForm({
      data: readDataPathValue(),
      sample: readLocomoSampleValue("wbQaSample", "all"),
      mode: readText("wbQaMode", state?.locomoQaDraft?.wbQaMode || "full") || "full",
      echomem_root: readText("wbQaEchomemRoot", ""),
      echomem_base_url: readText("wbQaEchomemBaseUrl", "") || "",
      memory_user_id: readText("wbQaMemoryUserId", "default") || "default",
      memory_agent_id: readText("wbQaMemoryAgentId", "default") || "default",
      question_limit: Math.max(0, readNumber("wbQaQuestionLimit", 0)),
      question_ids: readLocomoDraftText("wbQaQuestionIds", ""),
      wrong_csv: readLocomoDraftText("wbQaWrongCsv", ""),
      top_k: Math.max(1, readNumber("wbQaTopK", 30)),
      use_tools: readChecked("wbQaUseTools", true),
      qa_memory_injection: readChecked("wbQaMemoryInjection", true),
      tool_loop: readChecked("wbQaToolLoop", true),
      tool_search_limit: Math.max(1, readNumber("wbQaToolSearchLimit", 20)),
      max_iterations: Math.max(1, readNumber("wbQaMaxIterations", 50)),
      tool_set: readText("wbQaToolSet", "vikingbot_native_safe") || "vikingbot_native_safe",
      model_retries: Math.max(0, readNumber("wbQaModelRetries", 5)),
      question_timeout_s: Math.max(30, readNumber("wbQaQuestionTimeout", 600)),
      qa_parallelism: Math.max(1, readNumber("wbQaParallelism", 10)),
      memory_budget_chars: Math.max(0, readNumber("wbQaMemoryBudgetChars", 6000)),
      user_memory_budget_chars: Math.max(0, readNumber("wbQaUserMemoryBudgetChars", 4000)),
      agent_memory_budget_chars: Math.max(0, readNumber("wbQaAgentMemoryBudgetChars", 2000)),
      prefetch_read_count: Math.max(0, readNumber("wbQaPrefetchReadCount", 4)),
      prefetch_context_chars: Math.max(0, readNumber("wbQaPrefetchContextChars", 5000)),
      tool_log_chars: Math.max(200, readNumber("wbQaToolLogChars", 1200)),
      workspace: readWorkspaceValue(),
    });
  }

  function readJudgeForm() {
    return {
      data: readDataPathValue(),
    };
  }

  function readEchoAgentLiveImportForm() {
    return {
      echoagent_url: readText("wbEchoAgentUrl", "http://127.0.0.1:31020"),
      echomem_url: readText("wbEchoMemUrl", "http://127.0.0.1:8010"),
      username: readText("wbEchoAgentUsername", "test_user"),
      password: readText("wbEchoAgentPassword", "test_password"),
      num_batches: Math.max(1, readNumber("wbNumBatches", 3)),
      queries_per_batch: Math.max(1, readNumber("wbQueriesPerBatch", 5)),
      custom_scenario: readText("wbCustomScenario", ""),
      scenario_model: readText("wbScenarioModel", "deepseek-v4-flash"),
      scenario_base_url: readText("wbScenarioBaseUrl", ""),
      scenario_api_key: readText("wbScenarioApiKey", ""),
      user_simulator_config: readText("wbUserSimSelect", ""),
      evaluator_config: readText("wbEvalConfigSelect", ""),
    };
  }

  function readQuestionPreviewScope(fallback = {}) {
    const benchmarkId = String(currentBenchmark()?.id || "").trim().toLowerCase();
    const nextSample = benchmarkId === "locomo"
      ? readLocomoSampleValue("wbQaSample", fallback.sample || "all")
      : (readText("wbQaSample", fallback.sample || "all") || fallback.sample || "all");
    return {
      path: readText("wbDataPath", fallback.path || currentBenchmark().defaultData),
      sample: nextSample,
    };
  }

  return {
    readDataPathValue,
    readEchoAgentLiveImportForm,
    readHotpotQaImportForm,
    readHotpotQaQaForm,
    readJudgeForm,
    readLongMemEvalImportForm,
    readLongMemEvalQaForm,
    readLocomoImportForm,
    readLocomoQaForm,
    readQuestionPreviewScope,
    readWorkspaceValue,
  };
}
