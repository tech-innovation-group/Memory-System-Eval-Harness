import { normalizeLocomoAccountConfig } from "./locomo-qa-defaults.js";

export function createWorkbenchController(deps) {
  const {
    $,
    alertUser,
    actions,
    clearTimer,
    copyText,
    currentBenchmark,
    currentRun,
    defaultBenchmarkId,
    delay,
    legacyReferenceUrl,
    localStorageAdapter,
    onDocument,
    openReferenceUrl,
    prefetchLimitForBenchmark,
    qaKind,
    queryAll,
    renderQaConfig,
    renderQaPreview,
    renderAll,
    renderReportExportResult,
    state,
    tasksForBenchmark,
  } = deps;

  const LOCOMO_QA_TEXT_DRAFT_IDS = new Set([
    "wbQaEchomemRoot",
    "wbQaEchomemBaseUrl",
    "wbQaMemoryUserId",
    "wbQaMemoryAgentId",
    "wbQaTopK",
    "wbQaPromptMode",
    "wbQaRetrievalMode",
    "wbQaToolSet",
    "wbQaToolSearchLimit",
    "wbQaMaxIterations",
    "wbQaModelRetries",
    "wbQaQuestionTimeout",
    "wbQaParallelism",
    "wbQaMemoryBudgetChars",
    "wbQaUserMemoryBudgetChars",
    "wbQaAgentMemoryBudgetChars",
    "wbQaPrefetchReadCount",
    "wbQaPrefetchContextChars",
    "wbQaToolLogChars",
  ]);
  const LOCOMO_QA_CHECKBOX_DRAFT_IDS = new Set([
    "wbQaUseTools",
    "wbQaMemoryInjection",
    "wbQaToolLoop",
    "wbQaInitialToolPrefetch",
    "wbQaFallbackToOneShot",
    "wbQaVikingboatCompat",
    "wbQaLocalSessionSummaries",
    "wbQaLocalAtoms",
    "wbQaLocalMessages",
    "wbQaLocalTimelineHints",
    "wbQaLocalMemoryArtifacts",
  ]);
  const WORKBENCH_UI_STATE_KEY = "benchmark-console-v2:ui-state:v2";
  const PERSISTED_FIELD_IDS = new Set([
    "wbAccountSelect",
    "wbBackendSelect",
    "wbUserSimSelect",
    "wbEvalConfigSelect",
    "wbDataPath",
    "wbImportSample",
    "wbQaSample",
    "wbWorkspace",
    "wbImportEchomemRoot",
  ]);
  const VALID_STAGES = new Set(["import", "qa", "judge", "report"]);
  let restoredStartupStage = "import";

  function isValidPersistedLocomoPath(value) {
    const path = String(value || "").trim().toLowerCase();
    if (!path || path.includes("longmemeval")) return false;
    const filename = path.split(/[\\/]/).pop() || "";
    return filename.includes("locomo") && filename.endsWith(".json");
  }

  function parseQuestionIds(rawValue) {
    return String(rawValue || "")
      .split(/[,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function validLocomoQuestionIdsForSample(sample = "all") {
    const normalizedSample = String(sample || "all").trim() || "all";
    return new Set((state.questions || [])
      .filter((row) => normalizedSample === "all" || String(row.sample_id || "").trim() === normalizedSample)
      .map((row) => String(row.question_id || "").trim())
      .filter(Boolean));
  }

  function syncLocomoQuestionIdsInput() {
    const input = $("wbQaQuestionIds");
    if (input) input.value = [...(state.locomoSelectedQuestions || new Set())].join(",");
  }

  function syncLocomoSelectedQaButtonState() {
    const selectedIds = parseQuestionIds($("wbQaQuestionIds")?.value || [...(state.locomoSelectedQuestions || new Set())].join(","));
    const wrongCsvValue = String($("wbQaWrongCsv")?.value || state.locomoWrongCsv || state.locomoQaDraft?.wbQaWrongCsv || "").trim();
    const mode = String($("wbQaMode")?.value || "full").trim() || "full";
    const selectedButton = $("wbRunQaSelected");
    const wrongCsvButton = $("wbRunQaWrongCsv");
    const primaryButton = $("wbRunQaCurrentScope");
    if (selectedButton) {
      selectedButton.disabled = selectedIds.length === 0;
      if (selectedIds.length === 0) selectedButton.title = "请先填写 question ids";
      else selectedButton.removeAttribute("title");
    }
    if (wrongCsvButton) {
      wrongCsvButton.disabled = !wrongCsvValue;
      if (!wrongCsvValue) wrongCsvButton.title = "请先填写错题 CSV 或先选择当前结果";
      else wrongCsvButton.removeAttribute("title");
    }
    if (primaryButton) {
      const primaryDisabled = (mode === "selected" && selectedIds.length === 0)
        || (mode === "wrong_csv" && !wrongCsvValue);
      primaryButton.disabled = primaryDisabled;
      if (mode === "selected" && selectedIds.length === 0) primaryButton.title = "selected 模式需要 question ids";
      else if (mode === "wrong_csv" && !wrongCsvValue) primaryButton.title = "wrong_csv 模式需要错题 CSV";
      else primaryButton.removeAttribute("title");
    }
  }

  function defaultWrongCsvPathForRun(run = currentRun()) {
    const output = String(run?.output_file || "").trim();
    if (!output) return "";
    const slash = Math.max(output.lastIndexOf("/"), output.lastIndexOf("\\"));
    if (slash < 0) return "wrong_questions_brief.csv";
    return `${output.slice(0, slash + 1)}wrong_questions_brief.csv`;
  }

  function currentLocomoDiagnosticsForRun(run = currentRun()) {
    const outputPath = String(run?.output_file || "").trim();
    if (!outputPath) return null;
    const runDetail = run?.run_dir ? (state.runDetails?.[run.run_dir] || null) : null;
    const runSnapshot = run?.run_dir ? (state.runConfigSnapshots?.[run.run_dir] || null) : null;
    const runConfig = runSnapshot?.config || runSnapshot || null;
    const datasetPath = String(
      runDetail?.record?.dataset_path
      || run?.dataset_path
      || runConfig?.data
      || $("wbDataPath")?.value
      || ""
    ).trim();
    const sample = String(
      runDetail?.record?.sample
      || run?.sample
      || runConfig?.sample
      || $("wbQaSample")?.value
      || "all"
    ).trim() || "all";
    const keyed = state.qaDiagnosticsCache?.[[outputPath, datasetPath, sample].join("::")] || null;
    return keyed || state.qaDiagnosticsCache?.[outputPath] || null;
  }

  function clearLocomoTransientState() {
    state.locomoQaGate = null;
    state.locomoJudgePreflight = null;
    state.officialQaGates = state.officialQaGates || {};
    state.officialJudgePreflights = state.officialJudgePreflights || {};
    delete state.officialQaGates[state.activeBenchmark];
    delete state.officialJudgePreflights[state.activeBenchmark];
  }

  function clearOfficialQaGateState(benchmarkId = state.activeBenchmark) {
    state.officialQaGates = state.officialQaGates || {};
    delete state.officialQaGates[benchmarkId];
  }

  function invalidateOfficialQaGateAndRefresh() {
    if (!["hotpotqa", "longmemeval"].includes(String(state.activeBenchmark || "").trim())) return;
    clearOfficialQaGateState(state.activeBenchmark);
    if (typeof renderQaConfig === "function") renderQaConfig();
    renderQaPreview(state.activeBenchmark);
  }

  function resetLocomoRunScopedQaState() {
    state.locomoWrongCsv = "";
    if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") return;
    delete state.locomoQaDraft.wbQaWrongCsv;
  }

  function refreshQuestionPreviewScope({ dataPath, sample } = {}) {
    const benchmark = currentBenchmark();
    state.questionDataPaths = state.questionDataPaths || {};
    state.questionSamples = state.questionSamples || {};
    const nextPath = String((dataPath ?? $("wbDataPath")?.value) || state.questionDataPaths[benchmark.id] || "").trim();
    const nextSample = String((sample ?? $("wbQaSample")?.value) || state.questionSamples[benchmark.id] || "all").trim() || "all";
    state.questionDataPaths[benchmark.id] = nextPath;
    state.questionSamples[benchmark.id] = nextSample;
    state.questionScope = "";
    state.questions = [];
    return actions.ensureQuestions();
  }

  function resetLocomoPendingFilters() {
    state.locomoPendingFilters = {
      category: "",
      query: "",
      min_tokens: "",
      max_tokens: "",
    };
  }

  function resetLocomoRecallFilters() {
    state.locomoRecallFilters = {
      query: "",
    };
  }

  function officialQaDraft() {
    state.officialQaDrafts = state.officialQaDrafts || {};
    const key = String(state.activeBenchmark || "").trim().toLowerCase();
    if (!state.officialQaDrafts[key] || typeof state.officialQaDrafts[key] !== "object") {
      state.officialQaDrafts[key] = {};
    }
    return state.officialQaDrafts[key];
  }

  function ensureLocomoQaDraft() {
    if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") {
      state.locomoQaDraft = {};
    }
    return state.locomoQaDraft;
  }

  function snapshotWorkbenchUiState() {
    const accountValue = String($("wbAccountSelect")?.value || state.selectedAccount || "").trim();
    const dataPathValue = String($("wbDataPath")?.value || state.questionDataPaths?.locomo || "").trim();
    const importSampleValue = String($("wbImportSample")?.value || "").trim();
    const qaSampleValue = String($("wbQaSample")?.value || "").trim();
    const sampleValue = [importSampleValue, qaSampleValue, String(state.questionSamples?.locomo || "").trim(), String(state.locomoQaDraft?.wbQaSample || "").trim()]
      .find((value) => value && value !== "all")
      || importSampleValue
      || qaSampleValue
      || String(state.questionSamples?.locomo || "").trim()
      || String(state.locomoQaDraft?.wbQaSample || "").trim();
    const workspaceValue = String($("wbWorkspace")?.value || state.locomoQaDraft?.wbWorkspace || "").trim();
    const echomemRootValue = String(
      $("wbImportEchomemRoot")?.value
      || $("wbQaEchomemRoot")?.value
      || state.locomoQaDraft?.wbQaEchomemRoot
      || ""
    ).trim();
    const latestLocomoImportRun = (Array.isArray(state.runs) ? state.runs : []).find((run) => {
      const kind = String(run?.kind || "").trim().toLowerCase();
      const account = String(run?.account || "").trim();
      const sample = String(run?.sample || "").trim();
      return kind.includes("import")
        && (!accountValue || !account || account === accountValue)
        && (!sampleValue || sampleValue === "all" || !sample || sample === sampleValue);
    }) || null;
    return {
      selectedAccount: accountValue,
      activeBenchmark: String(state.activeBenchmark || "").trim(),
      activeStage: String(state.activeStage || "").trim(),
      locomo: {
        dataPath: dataPathValue,
        sample: sampleValue,
        workspace: workspaceValue,
        echomemRoot: echomemRootValue,
        lastImportRun: latestLocomoImportRun ? {
          name: latestLocomoImportRun.name || "",
          kind: latestLocomoImportRun.kind || "",
          status: latestLocomoImportRun.status || "",
          run_dir: latestLocomoImportRun.run_dir || "",
          output_file: latestLocomoImportRun.output_file || "",
          sample: latestLocomoImportRun.sample || "",
          account: latestLocomoImportRun.account || "",
        } : null,
      },
    };
  }

  function persistWorkbenchUiState() {
    const storage = localStorageAdapter();
    if (!storage) return;
    try {
      storage.setItem(WORKBENCH_UI_STATE_KEY, JSON.stringify(snapshotWorkbenchUiState()));
    } catch (_) {
      // Ignore quota / privacy failures; persistence is best-effort.
    }
  }

  function schedulePersistWorkbenchUiState() {
    Promise.resolve().then(() => persistWorkbenchUiState());
  }

  function restoreWorkbenchUiState() {
    const storage = localStorageAdapter();
    if (!storage) return null;
    try {
      const raw = storage.getItem(WORKBENCH_UI_STATE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      const restoredBenchmark = String(parsed.activeBenchmark || "").trim();
      const restoredStage = String(parsed.activeStage || "").trim();
      const restoredAccount = String(parsed.selectedAccount || "").trim();
      const locomo = parsed.locomo && typeof parsed.locomo === "object" ? parsed.locomo : {};
      const locomoDraft = ensureLocomoQaDraft();
      if (restoredBenchmark) state.activeBenchmark = restoredBenchmark;
      if (restoredStage) state.activeStage = restoredStage;
      if (restoredAccount) state.selectedAccount = restoredAccount;
      if (isValidPersistedLocomoPath(locomo.dataPath)) {
        state.questionDataPaths = state.questionDataPaths || {};
        state.questionDataPaths.locomo = String(locomo.dataPath || "").trim();
      }
      if (String(locomo.sample || "").trim()) {
        const sample = String(locomo.sample || "").trim();
        state.questionSamples = state.questionSamples || {};
        state.questionSamples.locomo = sample;
        locomoDraft.wbQaSample = sample;
        locomoDraft.wbQaSampleExplicit = true;
      }
      if (String(locomo.workspace || "").trim()) {
        locomoDraft.wbWorkspace = String(locomo.workspace || "").trim();
      }
      if (String(locomo.echomemRoot || "").trim()) {
        locomoDraft.wbQaEchomemRoot = String(locomo.echomemRoot || "").trim();
      }
      state.locomoPersistedImportRun = locomo.lastImportRun && typeof locomo.lastImportRun === "object"
        ? {
            ...locomo.lastImportRun,
            run_dir: String(locomo.lastImportRun.run_dir || "").trim(),
            output_file: String(locomo.lastImportRun.output_file || "").trim(),
            sample: String(locomo.lastImportRun.sample || "").trim(),
            account: String(locomo.lastImportRun.account || "").trim(),
          }
        : null;
      return {
        selectedAccount: restoredAccount,
        activeBenchmark: restoredBenchmark,
        activeStage: restoredStage,
      };
    } catch (_) {
      return null;
    }
  }

  function syncLocomoQaDraftValue(id, value) {
    ensureLocomoQaDraft()[id] = value;
  }

  function syncLocomoQaDraftChecked(id, checked) {
    ensureLocomoQaDraft()[id] = Boolean(checked);
  }

  function invalidateLocomoTransientPreview({ rerender = false } = {}) {
    if (state.activeBenchmark !== "locomo") return;
    clearLocomoTransientState();
    if (rerender) {
      renderQaPreview(state.activeBenchmark);
    }
  }

  function syncOfficialQaDraftValue(id, value) {
    if (state.activeBenchmark === "locomo") return;
    officialQaDraft()[id] = value;
  }

  function syncOfficialQaDraftChecked(id, checked) {
    if (state.activeBenchmark === "locomo") return;
    officialQaDraft()[id] = Boolean(checked);
  }

  function trimLocomoSelectedQuestionsForSample(sample = "all") {
    const validIds = validLocomoQuestionIdsForSample(sample);
    const currentIds = parseQuestionIds($("wbQaQuestionIds")?.value || [...(state.locomoSelectedQuestions || new Set())].join(","));
    state.locomoSelectedQuestions = new Set(currentIds.filter((id) => validIds.has(id)));
    syncLocomoQuestionIdsInput();
    syncLocomoSelectedQaButtonState();
  }

  function loadDiagnosticsQuestionsToSelected(source = "failed") {
    const run = currentRun();
    const diagnostics = currentLocomoDiagnosticsForRun(run);
    const rawIds = source === "missing"
      ? (Array.isArray(diagnostics?.missing_question_ids) ? diagnostics.missing_question_ids : [])
      : (Array.isArray(diagnostics?.retryable_failed_question_ids) ? diagnostics.retryable_failed_question_ids : []);
    const questionIds = parseQuestionIds(rawIds.join(","));
    if (!questionIds.length) {
      throw new Error(source === "missing" ? "当前没有可转成 selected 的缺失题" : "当前没有可转成 selected 的失败题");
    }
    state.locomoSelectedQuestions = new Set(questionIds);
    state.locomoWrongCsv = "";
    if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
    state.locomoQaDraft.wbQaMode = "selected";
    state.locomoQaDraft.wbQaQuestionIds = questionIds.join(",");
    state.locomoQaDraft.wbQaWrongCsv = "";
    clearLocomoTransientState();
    if (typeof renderQaConfig === "function") renderQaConfig();
    syncLocomoQuestionIdsInput();
    syncLocomoSelectedQaButtonState();
    renderQaPreview(state.activeBenchmark);
  }

  function buildLocomoQaConfigPatch() {
    const savedCfg = normalizeLocomoAccountConfig(state?.accountDetails?.config || state?.config?.active_account_config || {});
    const selectedBackend = String(savedCfg.memoryBackend || state?.config?.memoryBackend || "echomemory").trim() || "echomemory";
    const readValue = (id, fallback = "") => {
      const node = $(id);
      return node ? String(node.value || "").trim() : String(fallback || "");
    };
    const readChecked = (id, fallback = false) => {
      const node = $(id);
      return node ? Boolean(node.checked) : Boolean(fallback);
    };
    const workspace = readValue("wbWorkspace", savedCfg.workspace || savedCfg.memoryWorkspace || savedCfg.ovWorkspace || "");
    const echomemBaseUrl = readValue("wbQaEchomemBaseUrl", savedCfg.echomemBaseUrl || "");
    const echomemTransport = echomemBaseUrl
      ? "http"
      : String(savedCfg.echomemTransport || "").trim().toLowerCase();
    const backendConfigs = {...(savedCfg.backendConfigs || {})};
    backendConfigs[selectedBackend] = {
      ...(backendConfigs[selectedBackend] || {}),
      workspace,
      ovWorkspace: workspace,
      memoryWorkspace: workspace,
      workspaceAuto: false,
      workspace_source: "benchmark_console_v2_manual",
      echomemBaseUrl,
      echomemTransport,
    };
    return {
      ...savedCfg,
      workspace,
      ovWorkspace: workspace,
      memoryWorkspace: workspace,
      workspaceAuto: false,
      workspace_source: "benchmark_console_v2_manual",
      backendConfigs,
      echomemRoot: readValue("wbQaEchomemRoot", savedCfg.echomemRoot || ""),
      echomemBaseUrl,
      echomemTransport,
      memoryUserId: readValue("wbQaMemoryUserId", savedCfg.memoryUserId || "default"),
      memoryAgentId: readValue("wbQaMemoryAgentId", savedCfg.memoryAgentId || "default"),
      locomoQaUseTools: readChecked("wbQaUseTools", savedCfg.locomoQaUseTools !== false),
      locomoQaMemoryInjection: readChecked("wbQaMemoryInjection", savedCfg.locomoQaMemoryInjection !== false),
      echomemQaTopK: readValue("wbQaTopK", savedCfg.echomemQaTopK || "30"),
      echomemQaToolSearchLimit: readValue("wbQaToolSearchLimit", savedCfg.echomemQaToolSearchLimit || "20"),
      echomemQaMaxIterations: readValue("wbQaMaxIterations", savedCfg.echomemQaMaxIterations || "50"),
      echomemQaModelRetries: readValue("wbQaModelRetries", savedCfg.echomemQaModelRetries || "5"),
      echomemQaParallelism: readValue("wbQaParallelism", savedCfg.echomemQaParallelism || "5"),
      echomemQaMemoryBudgetChars: readValue("wbQaMemoryBudgetChars", savedCfg.echomemQaMemoryBudgetChars || "6000"),
      echomemQaUserMemoryBudgetChars: readValue("wbQaUserMemoryBudgetChars", savedCfg.echomemQaUserMemoryBudgetChars || "4000"),
      echomemQaAgentMemoryBudgetChars: readValue("wbQaAgentMemoryBudgetChars", savedCfg.echomemQaAgentMemoryBudgetChars || "2000"),
      echomemQaPrefetchReadCount: readValue("wbQaPrefetchReadCount", savedCfg.echomemQaPrefetchReadCount || "4"),
      echomemQaPrefetchContextChars: readValue("wbQaPrefetchContextChars", savedCfg.echomemQaPrefetchContextChars || "5000"),
      echomemQaToolLogChars: readValue("wbQaToolLogChars", savedCfg.echomemQaToolLogChars || "1200"),
      echomemQaQuestionTimeout: readValue("wbQaQuestionTimeout", savedCfg.echomemQaQuestionTimeout || "600"),
      locomoQaQuestionLimit: readValue("wbQaQuestionLimit", savedCfg.locomoQaQuestionLimit || "0"),
      echomemQaRetrievalMode: readValue("wbQaRetrievalMode", savedCfg.echomemQaRetrievalMode || "local"),
      echomemQaPromptMode: readValue("wbQaPromptMode", savedCfg.echomemQaPromptMode || "vikingboat_lite"),
      echomemQaToolSet: readValue("wbQaToolSet", savedCfg.echomemQaToolSet || "vikingbot_native_safe"),
      echomemQaToolLoop: readChecked("wbQaToolLoop", savedCfg.echomemQaToolLoop !== false),
      echomemQaInitialToolPrefetch: readChecked("wbQaInitialToolPrefetch", Boolean(savedCfg.echomemQaInitialToolPrefetch)),
      echomemQaFallbackToOneShot: readChecked("wbQaFallbackToOneShot", savedCfg.echomemQaFallbackToOneShot !== false),
      echomemQaVikingboatCompat: readChecked("wbQaVikingboatCompat", Boolean(savedCfg.echomemQaVikingboatCompat)),
      echomemQaLocalSessionSummaries: readChecked("wbQaLocalSessionSummaries", savedCfg.echomemQaLocalSessionSummaries !== false),
      echomemQaLocalAtoms: readChecked("wbQaLocalAtoms", savedCfg.echomemQaLocalAtoms !== false),
      echomemQaLocalMessages: readChecked("wbQaLocalMessages", Boolean(savedCfg.echomemQaLocalMessages)),
      echomemQaLocalTimelineHints: readChecked("wbQaLocalTimelineHints", savedCfg.echomemQaLocalTimelineHints !== false),
      echomemQaLocalMemoryArtifacts: readChecked("wbQaLocalMemoryArtifacts", savedCfg.echomemQaLocalMemoryArtifacts !== false),
    };
  }

  function syncPrimaryButton() {
    const labels = currentBenchmark().stageLabels || {};
    const button = $("wbRunPrimary");
    const label = labels[state.activeStage]
      || (state.activeStage === "import" ? currentBenchmark().importLabel : currentBenchmark().primaryRunLabel);
    button.dataset.stage = state.activeStage;
    if (button.children.length >= 2) {
      button.lastElementChild.textContent = label;
      return;
    }
    button.textContent = label;
  }

  function setActiveStage(stage) {
    state.activeStage = stage;
    $("wbShell").dataset.stage = stage;
    queryAll(".wb-flow-step").forEach((node) => {
      node.classList.toggle("active", node.dataset.stage === stage);
    });
    queryAll(".wb-stage").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.stagePanel === stage);
    });
    syncPrimaryButton();
    renderAll();
    persistWorkbenchUiState();
  }

  function setActiveBenchmark(benchmarkId) {
    state.activeBenchmark = benchmarkId;
    clearLocomoTransientState();
    $("wbShell").dataset.benchmark = benchmarkId;
    queryAll(".wb-side-item").forEach((node) => {
      node.classList.toggle("active", node.dataset.benchmark === benchmarkId);
    });
    renderAll();
    syncPrimaryButton();
    persistWorkbenchUiState();
  }

  function activeLocomoTaskStage() {
    const task = (tasksForBenchmark("locomo") || []).find((item) => {
      const status = String(item?.status || "").trim().toLowerCase();
      return ["running", "queued", "pending", "stopping"].includes(status);
    });
    if (!task) return { stage: "", signature: "" };
    const kind = String(task.kind || "").trim().toLowerCase();
    const stage = kind === "judge"
      ? "judge"
      : (kind.includes("qa") ? "qa" : (kind.includes("import") ? "import" : ""));
    return {
      stage,
      signature: `${task.id || task.run_dir || kind}:${task.status || ""}`,
    };
  }

  function locomoImportReady() {
    const imported = state.locomoFlowStatus?.artifacts?.imported || {};
    const importStage = (state.locomoFlowStatus?.stages || []).find((item) => item?.id === "import") || null;
    return Number(imported.session_count || imported.sessions?.length || 0) > 0
      || Number(imported.summary_count || imported.summaries?.length || 0) > 0
      || String(importStage?.status || "").trim().toLowerCase() === "ok";
  }

  function locomoQaStage() {
    const run = currentRun();
    if (!run) {
      const importReady = locomoImportReady();
      return {
        stage: importReady ? "qa" : "import",
        signature: `no-qa:${importReady ? "import-ready" : "not-imported"}`,
      };
    }
    const status = String(run.status || "").trim().toLowerCase();
    if (["running", "queued", "pending", "stopping"].includes(status)) {
      return {
        stage: "qa",
        signature: `${run.run_dir || run.id}:qa:${status}`,
      };
    }
    const detail = run.run_dir ? state.runDetails?.[run.run_dir] || null : null;
    const result = run.output_file ? state.resultSummaries?.[run.output_file] || null : null;
    const summary = result?.summary || detail?.record?.summary || run.summary || {};
    const rows = Number(summary.rows || 0);
    const graded = Number(summary.graded || 0);
    const pending = Number(summary.result_counts?.UNSCORED ?? Math.max(0, rows - graded));
    if (rows > 0 && pending <= 0) {
      return {
        stage: "report",
        signature: `${run.run_dir || run.id}:report:${rows}:${pending}`,
      };
    }
    if (rows > 0) {
      return {
        stage: "judge",
        signature: `${run.run_dir || run.id}:judge:${rows}:${pending}`,
      };
    }
    return {
      stage: "qa",
      signature: `${run.run_dir || run.id}:qa:${status || "unknown"}`,
    };
  }

  function reconcileLocomoDatasetAndWorkspace() {
    const records = (state.datasets || []).filter((item) =>
      String(item?.format || item?.dataset_format || "").trim().toLowerCase() === "locomo"
      && item?.exists !== false
      && String(item?.path || "").trim()
    );
    const currentPath = String(state.questionDataPaths?.locomo || "").trim();
    if (!records.some((item) => String(item.path || "").trim() === currentPath)) {
      state.questionDataPaths = state.questionDataPaths || {};
      state.questionDataPaths.locomo = String(records[0]?.path || state.config?.data || "").trim();
      state.questionScope = "";
      state.questions = [];
    }
    const accountConfig = state.accountDetails?.config || {};
    const accountWorkspace = String(
      accountConfig.workspace
      || accountConfig.memoryWorkspace
      || accountConfig.ovWorkspace
      || state.config?.workspace
      || ""
    ).trim();
    if (accountWorkspace) {
      ensureLocomoQaDraft().wbWorkspace = accountWorkspace;
    }
  }

  function reconcileAfterBootstrap() {
    if (state.activeBenchmark !== "locomo") {
      if (!state.stageBootstrapReconciled) {
        state.stageBootstrapReconciled = true;
        setActiveStage(VALID_STAGES.has(restoredStartupStage) ? restoredStartupStage : "import");
      }
      return;
    }
    reconcileLocomoDatasetAndWorkspace();
    const activeTask = activeLocomoTaskStage();
    const resolved = activeTask.stage ? activeTask : locomoQaStage();
    const signature = `${resolved.stage}:${resolved.signature}`;
    if (state.locomoStageSignature === signature) return;
    state.locomoStageSignature = signature;
    setActiveStage(resolved.stage || "import");
  }

  function upsertTask(task) {
    if (!task?.id) return;
    const existingTasks = Array.isArray(state.tasks) ? state.tasks : [];
    const withoutDuplicate = existingTasks.filter((item) => String(item?.id || "").trim() !== String(task.id || "").trim());
    state.tasks = [task, ...withoutDuplicate];
  }

  function removeTaskById(taskId) {
    if (!taskId) return;
    state.tasks = (Array.isArray(state.tasks) ? state.tasks : []).filter((task) =>
      String(task?.id || "").trim() !== String(taskId || "").trim()
    );
  }

  function upsertRunFromTask(task) {
    const runDir = String(task?.run_dir || "").trim();
    if (!runDir) return;
    const existingRuns = Array.isArray(state.runs) ? state.runs : [];
    const withoutDuplicate = existingRuns.filter((run) => String(run?.run_dir || "").trim() !== runDir);
    const optimisticRun = {
      id: String(task?.id || "").trim() || runDir,
      name: task?.name || task?.kind || "-",
      kind: task?.kind || "",
      status: String(task?.status || "queued").trim() || "queued",
      dataset_format: task?.dataset_format || currentBenchmark()?.datasetFormat || "",
      dataset_path: task?.dataset_path || task?.meta?.config?.data || "",
      sample: task?.sample || task?.meta?.config?.sample || "",
      output_file: task?.output_file || "",
      run_dir: runDir,
      account: task?.account || state.selectedAccount || "",
      workspace: task?.workspace || task?.meta?.config?.workspace || "",
      summary: task?.summary || null,
    };
    state.runs = [optimisticRun, ...withoutDuplicate];
  }

  function buildOptimisticQaTask(taskLabel = "QA") {
    const scopedFieldValue = (id, fallback = "") => {
      const scoped = queryAll(`.wb-stage.active #${id}`);
      if (scoped.length === 1) {
        return String(scoped[0]?.value || "").trim();
      }
      return String($(id)?.value || fallback || "").trim();
    };
    const benchmark = currentBenchmark();
    const benchmarkLabel = String(benchmark?.title || "Benchmark").trim();
    const sample = scopedFieldValue("wbQaSample");
    const dataPath = scopedFieldValue("wbDataPath");
    const taskId = `optimistic-${benchmark?.id || "benchmark"}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    return {
      id: taskId,
      name: `${benchmarkLabel} ${taskLabel} 启动中`,
      kind: typeof qaKind === "function" ? qaKind() : "echomemory_qa",
      status: "pending",
      dataset_format: benchmark?.datasetFormat || "",
      dataset_path: dataPath,
      sample,
      account: state.selectedAccount || "",
      meta: {
        config: {
          data: dataPath,
          sample,
          dataset_format: benchmark?.datasetFormat || "",
        },
      },
      progress: {
        current: 0,
        total: 1,
        detail: "正在执行启动前检查并创建任务",
      },
      optimistic: true,
    };
  }

  function runQaActionWithFeedback(actionRunner, errorMessage, taskLabel, { refreshAllRunner, pollLogRunner } = {}) {
    const actionPromise = actionRunner();
    const optimisticTask = buildOptimisticQaTask(taskLabel);
    upsertTask(optimisticTask);
    renderAll();
    actionPromise
      .then((result) => applyActionResult(result, {
        refreshAllRunner,
        pollLogRunner,
        optimisticTaskId: optimisticTask.id,
      }))
      .catch((error) => {
        removeTaskById(optimisticTask.id);
        renderAll();
        alertUser(error.message || errorMessage);
      });
  }

  async function applyActionResult(result, { refreshAllRunner, pollLogRunner, optimisticTaskId = "" } = {}) {
    if (!result) return;
    if (optimisticTaskId) {
      removeTaskById(optimisticTaskId);
    }
    if (result.createdTask?.id) {
      const createdTask = result.createdTask;
      const optimisticTask = {
        ...createdTask,
        status: String(createdTask.status || "queued").trim() || "queued",
      };
      upsertTask(optimisticTask);
    }
    if (result.createdTask?.run_dir) {
      upsertRunFromTask(result.createdTask);
      state.currentRunDirs[state.activeBenchmark] = String(result.createdTask.run_dir || "").trim();
      state.userSelectedRunDirs[state.activeBenchmark] = false;
    }
    renderAll();
    if (result.refresh && refreshAllRunner) {
      await refreshAllRunner();
    }
    if (result.stage) {
      setActiveStage(result.stage);
    }
    if (result.pollLogTarget && pollLogRunner) {
      const task = tasksForBenchmark(state.activeBenchmark)[0];
      if (task) await pollLogRunner(task, result.pollLogTarget);
    }
    if (result.kind === "report-export") {
      renderReportExportResult(result.model);
    }
    const followupRefreshMs = Number(result.followupRefreshMs || 0);
    if (followupRefreshMs > 0 && refreshAllRunner) {
      clearTimer(state.refreshTimer);
      state.refreshTimer = delay(() => {
        refreshAllRunner().catch(() => {});
      }, Math.max(250, followupRefreshMs));
    }
  }

  function bindEvents({ refreshAllRunner, pollLogRunner }) {
    queryAll(".wb-side-item").forEach((node) => {
      node.addEventListener("click", () => {
        setActiveBenchmark(node.dataset.benchmark || defaultBenchmarkId);
        actions.ensureBenchmarkRunDetails(state.activeBenchmark, prefetchLimitForBenchmark(state.activeBenchmark))
          .catch(() => {})
          .finally(() => actions.ensureRunDetail(currentRun()).catch(() => {}).finally(() => {
            if (state.activeBenchmark === "locomo") {
              actions.refreshLocomoDiagnostics().catch(() => {})
                .finally(() => actions.refreshLocomoPendingPreview().catch(() => {}).finally(renderAll));
              return;
            }
            renderAll();
          }));
      });
    });
    queryAll(".wb-flow-step").forEach((node) => {
      node.addEventListener("click", () => {
        const nextStage = node.dataset.stage || "import";
        setActiveStage(nextStage);
        if (nextStage === "judge") {
          actions.preflightJudgeStage()
            .catch(() => {})
            .finally(() => {
              if (state.activeBenchmark === "locomo") {
                return actions.refreshLocomoPendingPreview().catch(() => {}).finally(renderAll);
              }
              renderAll();
              return null;
            });
          return;
        }
        if (state.activeBenchmark === "locomo" && nextStage === "report") {
          renderAll();
        }
      });
    });
    $("wbRefreshAll").addEventListener("click", () => refreshAllRunner().catch((error) => alertUser(error.message || "刷新失败")));
    $("wbOpenLegacy").addEventListener("click", () => {
      if (legacyReferenceUrl) {
        openReferenceUrl(legacyReferenceUrl);
        return;
      }
      alertUser("当前不是独立代理模式；旧系统仅作为业务参考，不参与 V2 运行。");
    });
    $("wbRunPrimary").addEventListener("click", () => {
      actions.runPrimary()
        .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
        .catch((error) => alertUser(error.message || "操作失败"));
    });
    $("wbStopTasks").addEventListener("click", () => {
      actions.stopAllTasks()
        .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
        .catch((error) => alertUser(error.message || "停止失败"));
    });
    onDocument("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button) return;
      const action = button.dataset.action;
      if (action === "run-primary") {
        actions.runPrimary()
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .catch((error) => alertUser(error.message || "操作失败"));
        return;
      }
      if (action === "stop-tasks") {
        actions.stopAllTasks()
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .catch((error) => alertUser(error.message || "停止失败"));
        return;
      }
      if (action === "select-run") {
        state.currentRunDirs[state.activeBenchmark] = button.dataset.runDir || "";
        state.userSelectedRunDirs[state.activeBenchmark] = true;
        clearLocomoTransientState();
        resetLocomoPendingFilters();
        resetLocomoRecallFilters();
        resetLocomoRunScopedQaState();
        actions.ensureRunDetail(currentRun()).catch(() => {}).finally(() => {
          if (state.activeBenchmark === "locomo") {
            if (state.activeStage === "judge") {
              actions.preflightJudgeStage().catch(() => {})
                .finally(() => actions.refreshLocomoPendingPreview().catch(() => {}).finally(renderAll));
              return;
            }
            actions.refreshLocomoDiagnostics({ force: true }).catch(() => {})
              .finally(() => actions.refreshLocomoPendingPreview().catch(() => {}).finally(renderAll));
            return;
          }
          if (state.activeStage === "judge") {
            actions.preflightJudgeStage().catch(() => {}).finally(renderAll);
            return;
          }
          renderAll();
        });
        return;
      }
      if (action === "open-path") {
        actions.openPath(button.dataset.path || "").catch((error) => alertUser(error.message || "打开失败"));
      }
    });
    onDocument("change", (event) => {
      const target = event.target;
      if (!target) return;
      if (target.id && PERSISTED_FIELD_IDS.has(target.id)) {
        schedulePersistWorkbenchUiState();
      }
      if (target.id === "wbDatasetPreset") {
        const panelInput = target.closest(".wb-stage")?.querySelector("#wbDataPath");
        const fallbackInput = $("wbDataPath");
        const input = panelInput || fallbackInput;
        const nextPath = target.value || currentBenchmark().defaultData;
        if (input) input.value = nextPath;
        syncOfficialQaDraftValue("wbDataPath", nextPath);
        clearLocomoTransientState();
        refreshQuestionPreviewScope({
          dataPath: nextPath,
          sample: $("wbQaSample")?.value || "all",
        })
          .then(() => {
            trimLocomoSelectedQuestionsForSample($("wbQaSample")?.value || "all");
            return actions.refreshLocomoDiagnostics({
              sample: $("wbQaSample")?.value || "all",
              datasetPath: nextPath,
            }).catch(() => null);
          })
          .finally(() => {
            if (typeof renderQaConfig === "function") renderQaConfig();
            renderQaPreview(state.activeBenchmark);
          });
      }
      if (target.id === "wbDataPath") {
        const nextPath = String(target.value || "").trim();
        syncOfficialQaDraftValue("wbDataPath", nextPath);
        clearLocomoTransientState();
        refreshQuestionPreviewScope({
          dataPath: nextPath,
          sample: $("wbQaSample")?.value || "all",
        })
          .then(() => {
            trimLocomoSelectedQuestionsForSample($("wbQaSample")?.value || "all");
            return actions.refreshLocomoDiagnostics({
              sample: $("wbQaSample")?.value || "all",
              datasetPath: nextPath,
            }).catch(() => null);
          })
          .finally(() => {
            if (typeof renderQaConfig === "function") renderQaConfig();
            renderQaPreview(state.activeBenchmark);
          });
      }
      if (["wbPendingCategory", "wbPendingSearch", "wbPendingMinTokens", "wbPendingMaxTokens"].includes(target.id)) {
        state.locomoPendingFilters = {
          category: $("wbPendingCategory")?.value || "",
          query: $("wbPendingSearch")?.value?.trim() || "",
          min_tokens: $("wbPendingMinTokens")?.value?.trim() || "",
          max_tokens: $("wbPendingMaxTokens")?.value?.trim() || "",
        };
        actions.refreshLocomoPendingPreview()
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "待判分筛选刷新失败"));
      }
      if (target.id === "wbRecallQuestion") {
        const run = currentRun();
        const diagnostics = currentLocomoDiagnosticsForRun(run);
        const traceRows = Array.isArray(diagnostics?.retrieval_trace_preview) ? diagnostics.retrieval_trace_preview : [];
        const rows = Array.isArray(state.locomoRecallPreview?.rows) ? state.locomoRecallPreview.rows : (traceRows.length ? traceRows : []);
        const selectedOption = target.selectedOptions?.[0] || null;
        const selectedIndex = target.value?.trim() || "";
        const selectedQuestionId = selectedOption?.dataset?.questionId?.trim() || "";
        const matchedRow = rows.find((row) => String(row._row_index ?? "") === selectedIndex)
          || (selectedQuestionId ? rows.find((row) => String(row.question_id || "").trim() === selectedQuestionId) : null)
          || null;
        state.locomoRecallSelection = {
          path: run?.output_file || "",
          questionId: selectedQuestionId,
          index: matchedRow ? String(matchedRow._row_index ?? "") : selectedIndex,
        };
        actions.refreshLocomoRecallDetail()
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "Recall 明细刷新失败"));
      }
      if (target.id === "wbQaSample") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaSample = target.value || "all";
        state.locomoQaDraft.wbQaSampleExplicit = true;
        state.questionSamples = state.questionSamples || {};
        state.questionSamples.locomo = target.value || "all";
        clearLocomoTransientState();
        refreshQuestionPreviewScope({
          dataPath: $("wbDataPath")?.value || "",
          sample: target.value || "all",
        })
          .then(() => {
            trimLocomoSelectedQuestionsForSample(target.value || "all");
            return actions.refreshLocomoDiagnostics({
              sample: target.value || "all",
              datasetPath: $("wbDataPath")?.value || "",
            }).catch(() => null);
          })
          .finally(() => renderQaPreview(state.activeBenchmark));
      }
      if (target.id === "wbImportSample") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaSample = target.value || "all";
        state.locomoQaDraft.wbQaSampleExplicit = true;
        state.questionSamples = state.questionSamples || {};
        state.questionSamples.locomo = target.value || "all";
        clearLocomoTransientState();
        refreshQuestionPreviewScope({
          dataPath: $("wbDataPath")?.value || "",
          sample: target.value || "all",
        })
          .catch(() => null)
          .finally(() => renderAll());
      }
      if (target.id === "wbQaMode") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaMode = target.value || "full";
        if ((target.value || "full") === "wrong_csv") {
          const nextWrongCsv = String($("wbQaWrongCsv")?.value || state.locomoWrongCsv || state.locomoQaDraft.wbQaWrongCsv || "").trim();
          if (!nextWrongCsv) {
            const fallbackWrongCsv = defaultWrongCsvPathForRun();
            state.locomoWrongCsv = fallbackWrongCsv;
            state.locomoQaDraft.wbQaWrongCsv = fallbackWrongCsv;
          }
        }
        clearLocomoTransientState();
        if (typeof renderQaConfig === "function") renderQaConfig();
        renderQaPreview(state.activeBenchmark);
        syncLocomoSelectedQaButtonState();
        return;
      }
      if (target.id === "wbAccountSelect") {
        actions.switchAccount(target.value || "default")
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .catch((error) => alertUser(error.message || "切换账户失败"));
        return;
      }
      if (target.id === "wbBackendSelect") {
        const memoryBackend = target.value === "openviking" ? "openviking" : "echomemory";
        const currentConfig = normalizeLocomoAccountConfig(
          state?.accountDetails?.config || state?.config?.active_account_config || {}
        );
        actions.saveLocomoQaConfig({...currentConfig, memoryBackend})
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "切换记忆后端失败"));
        return;
      }
      // 处理用户模拟器配置选择
      if (target.id === "wbUserSimSelect") {
        const configName = target.value || "";
        state.userSimulatorConfig = configName;
        persistWorkbenchUiState();
        // 更新显示名称
        const nameEl = $("wbUserSimName");
        if (nameEl) nameEl.textContent = configName || "default";
        const hintEl = $("wbUserSimHint");
        if (hintEl) hintEl.textContent = configName ? "已选择" : "使用默认";
        return;
      }
      // 处理评估器配置选择
      if (target.id === "wbEvalConfigSelect") {
        const configName = target.value || "";
        state.evaluatorConfig = configName;
        persistWorkbenchUiState();
        // 更新显示名称
        const nameEl = $("wbEvalConfigName");
        if (nameEl) nameEl.textContent = configName || "default";
        const hintEl = $("wbEvalConfigHint");
        if (hintEl) hintEl.textContent = configName ? "已选择" : "使用默认";
        return;
      }
      if (target.id === "wbQaQuestionCategory") {
        state.locomoQuestionFilters = {
          ...(state.locomoQuestionFilters || {}),
          category: target.value || "all",
        };
        renderQaPreview(state.activeBenchmark);
      }
      if (target.id === "wbQaQuestionSearch") {
        state.locomoQuestionFilters = {
          ...(state.locomoQuestionFilters || {}),
          query: target.value || "",
        };
        renderQaPreview(state.activeBenchmark);
      }
      if (target.matches("#wbQaPreview input[type='checkbox'][data-question-id]")) {
        const questionId = String(target.dataset.questionId || "").trim();
        if (!questionId) return;
        if (!state.locomoSelectedQuestions) state.locomoSelectedQuestions = new Set();
        if (target.checked) state.locomoSelectedQuestions.add(questionId);
        else state.locomoSelectedQuestions.delete(questionId);
        const input = $("wbQaQuestionIds");
        if (input) input.value = [...state.locomoSelectedQuestions].join(",");
        renderQaPreview(state.activeBenchmark);
      }
      if (target.id === "wbQaToggleVisibleQuestions") {
        const rows = (state.questions || []).filter((row) => {
          const sample = String($("wbQaSample")?.value || "all").trim() || "all";
          const filters = state.locomoQuestionFilters || { category: "all", query: "" };
          const query = String(filters.query || "").trim().toLowerCase();
          const category = String(filters.category || "all").trim() || "all";
          if (sample !== "all" && String(row.sample_id || "").trim() !== sample) return false;
          if (category !== "all" && String(row.category || "").trim() !== category) return false;
          if (!query) return true;
          const text = [row.question_id, row.sample_id, row.question, row.answer].join("\n").toLowerCase();
          return text.includes(query);
        }).slice(0, (String($("wbQaSample")?.value || "all") === "all" ? 120 : 240));
        if (!state.locomoSelectedQuestions) state.locomoSelectedQuestions = new Set();
        rows.forEach((row) => {
          const qid = String(row.question_id || "").trim();
          if (!qid) return;
          if (target.checked) state.locomoSelectedQuestions.add(qid);
          else state.locomoSelectedQuestions.delete(qid);
        });
        const input = $("wbQaQuestionIds");
        if (input) input.value = [...state.locomoSelectedQuestions].join(",");
        renderQaPreview(state.activeBenchmark);
      }
      if (target.id === "wbQaQuestionIds") {
        state.locomoSelectedQuestions = new Set(parseQuestionIds(target.value || ""));
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaQuestionIds = target.value || "";
        clearLocomoTransientState();
        syncLocomoSelectedQaButtonState();
        renderQaPreview(state.activeBenchmark);
      }
      if (target.id === "wbQaWrongCsv") {
        state.locomoWrongCsv = String(target.value || "").trim();
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaWrongCsv = target.value || "";
        clearLocomoTransientState();
        syncLocomoSelectedQaButtonState();
        renderQaPreview(state.activeBenchmark);
      }
      if (target.id === "wbImportEchomemRoot") {
        syncLocomoQaDraftValue("wbQaEchomemRoot", target.value || "");
        clearLocomoTransientState();
      }
      if (LOCOMO_QA_TEXT_DRAFT_IDS.has(target.id)) {
        syncLocomoQaDraftValue(target.id, target.value || "");
        invalidateLocomoTransientPreview({ rerender: target.tagName === "SELECT" });
      }
      if (LOCOMO_QA_CHECKBOX_DRAFT_IDS.has(target.id)) {
        syncLocomoQaDraftChecked(target.id, target.checked);
        invalidateLocomoTransientPreview({ rerender: true });
      }
      if (["wbHotpotCount", "wbLongMemEvalCount", "wbQaTopK", "wbQaMaxIterations", "wbQaToolSearchLimit", "wbQaQuestionTimeout", "wbQaParallelism", "wbHotpotQaCheckpointInterval", "wbQaToolSet", "wbQaRetrievalMode", "wbHotpotQaCorpusMode", "wbHotpotQaGlobalImportMode", "wbWorkspace", "wbDataPath", "wbQaMode", "wbQaQuestionIds"].includes(target.id)) {
        syncOfficialQaDraftValue(target.id, target.value || "");
        invalidateOfficialQaGateAndRefresh();
      }
      if (["wbQaUseTools", "wbOfficialEval"].includes(target.id)) {
        syncOfficialQaDraftChecked(target.id, target.checked);
        invalidateOfficialQaGateAndRefresh();
      }
      if (target.id === "wbQaQuestionLimit") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaQuestionLimit = target.value || "";
      }
      if (target.id === "wbWorkspace") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbWorkspace = target.value || "";
        clearLocomoTransientState();
      }
    });
    onDocument("input", (event) => {
      const target = event.target;
      if (!target) return;
      if (target.id && PERSISTED_FIELD_IDS.has(target.id)) {
        schedulePersistWorkbenchUiState();
      }
      if (["wbHotpotCount", "wbLongMemEvalCount", "wbQaTopK", "wbQaMaxIterations", "wbQaToolSearchLimit", "wbQaQuestionTimeout", "wbQaParallelism", "wbHotpotQaCheckpointInterval", "wbQaToolSet", "wbQaRetrievalMode", "wbHotpotQaCorpusMode", "wbHotpotQaGlobalImportMode", "wbWorkspace", "wbDataPath", "wbQaMode", "wbQaQuestionIds"].includes(target.id)) {
        syncOfficialQaDraftValue(target.id, target.value || "");
        invalidateOfficialQaGateAndRefresh();
      }
      if (["wbQaUseTools", "wbOfficialEval"].includes(target.id)) {
        syncOfficialQaDraftChecked(target.id, target.checked);
        invalidateOfficialQaGateAndRefresh();
      }
      if (target.id === "wbQaMode") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaMode = target.value || "full";
      }
      if (target.id === "wbQaQuestionIds") {
        state.locomoSelectedQuestions = new Set(parseQuestionIds(target.value || ""));
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaQuestionIds = target.value || "";
        clearLocomoTransientState();
        syncLocomoSelectedQaButtonState();
      }
      if (target.id === "wbQaQuestionLimit") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaQuestionLimit = target.value || "";
      }
      if (target.id === "wbQaWrongCsv") {
        state.locomoWrongCsv = String(target.value || "").trim();
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbQaWrongCsv = target.value || "";
        syncLocomoSelectedQaButtonState();
      }
      if (target.id === "wbImportEchomemRoot") {
        syncLocomoQaDraftValue("wbQaEchomemRoot", target.value || "");
        clearLocomoTransientState();
      }
      if (LOCOMO_QA_TEXT_DRAFT_IDS.has(target.id)) {
        syncLocomoQaDraftValue(target.id, target.value || "");
        invalidateLocomoTransientPreview();
      }
      if (LOCOMO_QA_CHECKBOX_DRAFT_IDS.has(target.id)) {
        syncLocomoQaDraftChecked(target.id, target.checked);
        invalidateLocomoTransientPreview();
      }
      if (target.id === "wbWorkspace") {
        if (!state.locomoQaDraft || typeof state.locomoQaDraft !== "object") state.locomoQaDraft = {};
        state.locomoQaDraft.wbWorkspace = target.value || "";
        clearLocomoTransientState();
      }
      if (target.id === "wbRecallSearch") {
        state.locomoRecallFilters = {
          query: String(target.value || "").trim(),
        };
        renderQaPreview(state.activeBenchmark);
      }
    });
    onDocument("click", (event) => {
      const clickTarget = event.target instanceof Element ? event.target.closest("[id]") : null;
      const targetId = clickTarget?.id || "";
      if (targetId === "wbRunJudge") {
        actions.runJudge(false)
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .catch((error) => alertUser(error.message || "评分失败"));
      }
      if (targetId === "wbRefreshPendingPreview") {
        actions.refreshLocomoPendingPreview()
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "待判分预览刷新失败"));
      }
      if (targetId === "wbExportPendingCsv") {
        actions.exportLocomoPendingCsv()
          .then((result) => {
            renderAll();
            if (result?.output) return actions.openPath(result.output);
            return null;
          })
          .catch((error) => alertUser(error.message || "导出待判 CSV 失败"));
      }
      if (targetId === "wbRunJudgePending") {
        actions.runJudgePending()
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .catch((error) => alertUser(error.message || "待判分判分失败"));
      }
      if (targetId === "wbRunJudgePreflight" || targetId === "wbRunJudgePreflightAction") {
        actions.preflightJudgeStage()
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "Judge 预检查失败"));
      }
      if (targetId === "wbRunJudgeSmoke") {
        actions.runJudge(true)
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .catch((error) => alertUser(error.message || "评分失败"));
      }
      if (targetId === "wbSaveLocomoQaConfig") {
        actions.saveLocomoQaConfig(buildLocomoQaConfigPatch())
          .then((result) => applyActionResult(result, {refreshAllRunner, pollLogRunner}))
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "保存参数失败"));
      }
      if (targetId === "wbRunQaGate") {
        actions.preflightQaGate()
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "QA 启动检查失败"));
      }
      if (targetId === "wbRunQaCurrentScope") {
        runQaActionWithFeedback(
          () => actions.startQa(),
          `${currentBenchmark().title} 启动失败`,
          "当前模式 QA",
          {refreshAllRunner, pollLogRunner}
        );
      }
      if (targetId === "wbRunQaSelected") {
        event.preventDefault();
        event.stopPropagation();
        const runner = state.activeBenchmark === "locomo"
          ? actions.startLocomoSelectedQa
          : actions.startOfficialSelectedQa;
        runQaActionWithFeedback(
          () => runner(),
          "指定题 QA 启动失败",
          "指定题 QA",
          {refreshAllRunner, pollLogRunner}
        );
      }
      if (targetId === "wbQaSelectVisibleQuestions") {
        const sample = String($("wbQaSample")?.value || "all").trim() || "all";
        const filters = state.locomoQuestionFilters || { category: "all", query: "" };
        const query = String(filters.query || "").trim().toLowerCase();
        const category = String(filters.category || "all").trim() || "all";
        const visibleRows = (state.questions || []).filter((row) => {
          if (sample !== "all" && String(row.sample_id || "").trim() !== sample) return false;
          if (category !== "all" && String(row.category || "").trim() !== category) return false;
          if (!query) return true;
          const text = [row.question_id, row.sample_id, row.question, row.answer].join("\n").toLowerCase();
          return text.includes(query);
        }).slice(0, sample === "all" ? 120 : 240);
        if (!state.locomoSelectedQuestions) state.locomoSelectedQuestions = new Set();
        visibleRows.forEach((row) => {
          const qid = String(row.question_id || "").trim();
          if (qid) state.locomoSelectedQuestions.add(qid);
        });
        const input = $("wbQaQuestionIds");
        if (input) input.value = [...state.locomoSelectedQuestions].join(",");
        renderQaPreview(state.activeBenchmark);
      }
      if (targetId === "wbQaClearSelectedQuestions") {
        state.locomoSelectedQuestions = new Set();
        const input = $("wbQaQuestionIds");
        if (input) input.value = "";
        renderQaPreview(state.activeBenchmark);
      }
      if (targetId === "wbRunQaWrongCsv") {
        event.preventDefault();
        event.stopPropagation();
        const runner = state.activeBenchmark === "hotpotqa"
          ? actions.startHotpotWrongCsvQa
          : state.activeBenchmark === "longmemeval"
            ? actions.startLongMemEvalWrongCsvQa
            : actions.startLocomoWrongCsvQa;
        runQaActionWithFeedback(
          () => runner(),
          "错题 CSV 重跑失败",
          "错题补跑 QA",
          {refreshAllRunner, pollLogRunner}
        );
      }
      if (targetId === "wbRunQaRetryFailed") {
        event.preventDefault();
        event.stopPropagation();
        const runner = state.activeBenchmark === "hotpotqa"
          ? actions.retryHotpotFailedQa
          : state.activeBenchmark === "longmemeval"
            ? actions.retryLongMemEvalFailedQa
            : actions.retryLocomoFailedQa;
        runQaActionWithFeedback(
          () => runner(),
          "失败题重跑失败",
          "失败题补跑 QA",
          {refreshAllRunner, pollLogRunner}
        );
      }
      if (targetId === "wbRunQaRetryMissing") {
        event.preventDefault();
        event.stopPropagation();
        const runner = state.activeBenchmark === "hotpotqa"
          ? actions.retryHotpotMissingQa
          : state.activeBenchmark === "longmemeval"
            ? actions.retryLongMemEvalMissingQa
            : actions.retryLocomoMissingQa;
        runQaActionWithFeedback(
          () => runner(),
          "缺失题补跑失败",
          "缺失题补跑 QA",
          {refreshAllRunner, pollLogRunner}
        );
      }
      if (targetId === "wbRefreshQaDiagnostics") {
        actions.refreshLocomoDiagnostics()
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "刷新 QA 诊断失败"));
      }
      if (targetId === "wbLoadFailedToSelected") {
        try {
          loadDiagnosticsQuestionsToSelected("failed");
        } catch (error) {
          alertUser(error.message || "失败题转 selected 失败");
        }
      }
      if (targetId === "wbLoadMissingToSelected") {
        try {
          loadDiagnosticsQuestionsToSelected("missing");
        } catch (error) {
          alertUser(error.message || "缺失题转 selected 失败");
        }
      }
      if (targetId === "wbRefreshQaCurrentResult") {
        actions.refreshLocomoCurrentResult()
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "刷新当前结果失败"));
      }
      if (targetId === "wbRefreshQaRecallDetail") {
        actions.refreshLocomoRecallDetail({ force: true })
          .then(() => renderAll())
          .catch((error) => alertUser(error.message || "刷新 recall 明细失败"));
      }
      if (targetId === "wbExportReport") {
        actions.exportReport()
          .then((model) => renderReportExportResult(model))
          .catch((error) => alertUser(error.message || "导出失败"));
      }
      if (targetId === "wbCopyImportLog") {
        event.preventDefault();
        event.stopPropagation();
        copyText($("wbImportLogBody").textContent || "")
          .catch((error) => alertUser(error.message || "复制失败"));
      }
      if (targetId === "wbClearImportLog") {
        event.preventDefault();
        event.stopPropagation();
        $("wbImportLogBody").textContent = "";
      }
    });
  }

  function start({ loadBootstrapRunner, refreshAllRunner, pollLogRunner }) {
    bindEvents({ refreshAllRunner, pollLogRunner });
    const restored = restoreWorkbenchUiState() || {};
    restoredStartupStage = VALID_STAGES.has(String(restored.activeStage || "").trim())
      ? String(restored.activeStage || "").trim()
      : "import";
    if (restored.activeBenchmark) {
      setActiveBenchmark(restored.activeBenchmark || defaultBenchmarkId);
    }
    setActiveStage(state.activeBenchmark === "locomo" ? "import" : restoredStartupStage);
    
    // 加载配置列表
    loadPromptConfigs();
    
    loadBootstrapRunner({
      account: restored.selectedAccount || undefined,
    }).then(() => {
      persistWorkbenchUiState();
      const task = tasksForBenchmark(state.activeBenchmark)[0];
      if (task) pollLogRunner(task, "wbImportLogBody");
    }).catch((error) => {
      $("wbImportLogBody").textContent = error.message || "初始化失败";
    });
  }
  
  // 加载用户模拟器和评估器配置列表
  async function loadPromptConfigs() {
    const standaloneApiBase = deps.standaloneApiBase || "";
    
    try {
      // 加载用户模拟器配置列表
      const userSimResponse = await fetch(`${standaloneApiBase}/api/dynamic/user_simulators`);
      if (userSimResponse.ok) {
        const userSimData = await userSimResponse.json();
        populateConfigSelect("wbUserSimSelect", userSimData.simulators || [], "wbUserSimHint");
      }
    } catch (e) {
      const hint = $("wbUserSimHint");
      if (hint) hint.textContent = "加载失败";
    }
    
    try {
      // 加载评估器配置列表
      const evalConfigResponse = await fetch(`${standaloneApiBase}/api/dynamic/evaluator_configs`);
      if (evalConfigResponse.ok) {
        const evalConfigData = await evalConfigResponse.json();
        populateConfigSelect("wbEvalConfigSelect", evalConfigData.evaluator_configs || [], "wbEvalConfigHint");
      }
    } catch (e) {
      const hint = $("wbEvalConfigHint");
      if (hint) hint.textContent = "加载失败";
    }
  }
  
  // 填充配置选择下拉框
  function populateConfigSelect(selectId, configs, hintId) {
    const select = $(selectId);
    const hint = $(hintId);
    if (!select) return;
    
    // 清空现有选项
    select.innerHTML = '<option value="">默认</option>';
    
    // 添加配置选项
    for (const config of configs) {
      const option = document.createElement("option");
      option.value = config.name || "";
      option.textContent = config.description || config.name || "";
      option.title = config.description || "";
      select.appendChild(option);
    }
    
    if (hint) {
      hint.textContent = configs.length > 0 ? `${configs.length} 个配置` : "无配置";
    }
  }
  
  // 获取当前选择的配置
  function getSelectedConfigs() {
    return {
      userSimulator: $("wbUserSimSelect")?.value || "",
      evaluator: $("wbEvalConfigSelect")?.value || "",
    };
  }

  return {
    bindEvents,
    getSelectedConfigs,
    loadPromptConfigs,
    reconcileAfterBootstrap,
    setActiveBenchmark,
    setActiveStage,
    start,
    syncPrimaryButton,
  };
}
