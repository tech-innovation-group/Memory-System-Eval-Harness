const $ = (id) => document.getElementById(id);

const state = {
  config: {},
  dataset: null,
  locomoDataset: null,
  locomoDatasetLoading: false,
  longMemDataset: null,
  longMemQuestions: [],
  selectedLongMemQuestions: new Set(),
  taskId: "",
  taskKind: "",
  logOffsets: {},
  logTimers: {},
  liveTaskTimer: null,
  taskRefreshTimer: null,
  taskDatasetFormats: {},
  outputFile: "",
  outputDatasetFormat: "",
  selectedRunDir: "",
  selectedRunDatasetFormat: "",
  selectedRunRecord: null,
  chatMessages: [],
  questions: [],
  selectedQuestions: new Set(),
  benchmarkQuestions: {},
  selectedBenchmarkQuestions: {},
  filteredQuestions: [],
  datasetRegistry: [],
  lastValidation: null,
  lastReportFile: "",
  lastChatContextData: null,
  chatContextPreviewKey: "",
  chatContextPreviewLoading: false,
  selectedRunSummary: null,
  lastJudgeSummary: null,
  lastQaDiagnostics: null,
  lastQaDiagnosticsInput: "",
  lastJudgeValidation: null,
  importedMemoryStatus: null,
  currentImportTask: null,
  judgeConfirmInput: "",
  lastArchivedMessageCount: 0,
  lastArchiveRecord: null,
  accounts: [],
  accountRecords: [],
  accountConfigCache: {},
  accountBackendReady: false,
  accountStateFile: "",
  currentAccount: "default",
  systemPreflight: null,
  systemPreflightLoading: false,
  echomemorySourceStatus: null,
  chatSendInFlight: false,
  chatArchiveInFlight: false,
  runsLoadedAt: 0,
  recentRuns: [],
  runsLoading: false,
  selectedRunCompareIds: new Set(),
  nativeOpenVikingBaseline: null,
  activeWorkflowKey: "import",
  activeDatasetFormat: "",
  activeDatasetPath: "",
  activeBenchmarkView: "",
  activeBenchmarkFlowStage: "import",
  currentLocomoTask: null,
  locomoQuestionsLoading: false,
  locomoQuestionLoadSeq: 0,
  locomoQaLaunchPending: false,
  locomoQaSubmitInFlight: false,
  locomoQaSubmitPhase: "",
  locomoQaLaunchSource: "",
  currentRunningTask: null,
  taskExecutionProgress: {},
  taskExecutionProgressFetchedAt: {},
  taskExecutionProgressLoading: {},
  taskExecutionProgressOffsets: {},
  taskExecutionStatusFetchedAt: {},
  activeTaskQaPreview: {},
  activeTaskQaPreviewFetchedAt: {},
  activeTaskQaPreviewLoading: {},
  taskStopOverrides: {},
  taskProgressSnapshots: {},
  runningBenchmarkSummaries: {},
  runningBenchmarkSummariesFetchedAt: {},
  runningBenchmarkSummariesLoading: {},
  genericBenchmarkLaunchErrors: {},
  hotpotQaModelReadiness: null,
  hotpotQaModelReadinessLoading: false,
  hotpotQaModelReadinessFetchedAt: 0,
  activeEvidenceScope: null,
  evidenceScopesByOutput: {},
  locomoFlowStatus: null,
  locomoFlowLoading: false,
  uiContract: null,
  importPreviewDatasets: {},
  importPreviewDatasetLoading: {},
  importPreviewDatasetErrors: {},
  bootRequestedView: "",
  bootHydrating: false,
  userNavigatedDuringBoot: false,
  tasksHydrating: false,
};

const DEFAULT_USER_ID = "default";
const DEFAULT_AGENT_ID = "default";
const ARCHIVE_MESSAGE_THRESHOLD = 12;
const ARCHIVE_TOKEN_THRESHOLD = 3000;
const LAST_IMPORT_KEY = "locomoEval.lastOpenVikingImport";
const LAST_DATASET_KEY = "locomoEval.lastDataset";
const LAST_LOCOMO_DATASET_KEY = "locomoEval.lastLocomoDataset";
const LAST_BENCHMARK_DATASET_KEY = "locomoEval.lastBenchmarkDataset";
const CONTEXT_PANEL_KEY = "locomoEval.contextPanelCollapsed";
const ACCOUNT_LIST_KEY = "locomoEval.accountList";
const ACTIVE_ACCOUNT_KEY = "locomoEval.activeAccount";
const ACCOUNT_CONFIG_PREFIX = "locomoEval.accountConfig.";
const CHAT_DRAFT_PREFIX = "locomoEval.chatDraft.";
const UI_REFRESH_VERSION = "20260622qataskstrip01";
const TASK_PROGRESS_TOTAL_HINT_PREFIX = "locomoEval.taskProgressTotal.";
const TASK_STOP_OVERRIDE_TTL_MS = 30 * 1000;
const ACTIVE_TASK_STATUSES = new Set(["queued", "running", "stopping"]);
const TERMINAL_TASK_STATUSES = new Set(["succeeded", "failed", "done", "interrupted", "cancelled", "canceled"]);
const VIKINGBOAT_LITE_TOP_K = 30;
const VIKINGBOAT_LITE_TOOL_SEARCH_LIMIT = 20;
const VIKINGBOAT_LITE_MAX_ITERATIONS = 50;
const RETRIEVAL_COUNT_LABEL = "召回条数";
const TOOL_SEARCH_LABEL = "工具检索";
const MAX_ITERATION_LABEL = "最大迭代";
const UI_ACTION_LOCKS = new Set();

function isTaskRunningStatus(task = {}) {
  const status = String(task?.status || "").toLowerCase();
  return status === "queued" || status === "running";
}

function normalizeSlashes(value = "") {
  return String(value || "").replace(/\\/g, "/");
}

function relativeDatasetPath(path = "") {
  const text = String(path || "").trim();
  if (!text) return "";
  const normalized = normalizeSlashes(text);
  if (normalized === "dataset/locomo10.json") return "./dataset/locomo10.json";
  if (normalized === "dataset/locomo.json") return "./dataset/locomo.json";
  if (normalized === "dataset/full/locomo.json") return "./dataset/full/locomo.json";
  if (normalized === "locomo10.json") return "./dataset/locomo10.json";
  if (normalized === "locomo.json") return "./dataset/locomo.json";
  if (normalized === "full/locomo.json") return "./dataset/full/locomo.json";
  const root = normalizeSlashes(state.config?.root || "");
  const datasetRoot = root ? `${root}/dataset/` : "";
  if (datasetRoot && normalized.startsWith(datasetRoot)) {
    return `./dataset/${normalized.slice(datasetRoot.length)}`;
  }
  if (/\/dataset\//.test(normalized)) {
    return `./dataset/${normalized.split("/dataset/").pop()}`;
  }
  return text;
}

function datasetPathVariants(path = "") {
  const text = String(path || "").trim();
  if (!text) return [];
  const normalized = normalizeSlashes(text);
  const relative = normalizeSlashes(relativeDatasetPath(text));
  const variants = new Set([normalized]);
  if (relative) variants.add(relative);
  if (normalized.startsWith("./")) variants.add(normalized.slice(2));
  if (relative.startsWith("./")) variants.add(relative.slice(2));
  return [...variants].filter(Boolean);
}

function datasetPathMatches(left = "", right = "") {
  const leftVariants = datasetPathVariants(left);
  const rightVariants = datasetPathVariants(right);
  if (!leftVariants.length || !rightVariants.length) return false;
  return leftVariants.some((value) => rightVariants.includes(value));
}

function preferredLocomoDatasetPath() {
  const rows = Array.isArray(state.datasetRegistry) ? state.datasetRegistry.filter((item) => normalizeDatasetFormat(item.format) === "locomo") : [];
  const full = rows.find((item) => item.exists && /(^|\/)full\/locomo\.json$/i.test(normalizeSlashes(item.path || item.resolved_path || "")));
  const bundled = rows.find((item) => item.exists && /(^|\/)locomo10\.json$/i.test(normalizeSlashes(item.path || item.resolved_path || "")));
  const fallback = rows.find((item) => item.exists) || rows[0] || null;
  return relativeDatasetPath(full?.path || full?.resolved_path || bundled?.path || bundled?.resolved_path || fallback?.path || fallback?.resolved_path || state.config?.data || "");
}

function uiActionLocked(key) {
  return UI_ACTION_LOCKS.has(String(key || ""));
}

async function runWithUiActionLock(key, buttonIds, work, busyMessage = "操作正在处理中，请稍候") {
  const lockKey = String(key || "");
  if (!lockKey) return await work();
  if (uiActionLocked(lockKey)) {
    toast(busyMessage);
    return null;
  }
  UI_ACTION_LOCKS.add(lockKey);
  const buttons = (buttonIds || []).map((id) => $(id)).filter(Boolean);
  for (const button of buttons) {
    button.disabled = true;
    button.dataset.actionLocked = lockKey;
    button.setAttribute("aria-disabled", "true");
  }
  try {
    return await work();
  } finally {
    UI_ACTION_LOCKS.delete(lockKey);
    for (const button of buttons) {
      if (button.dataset.actionLocked === lockKey) delete button.dataset.actionLocked;
      button.removeAttribute("aria-disabled");
    }
  }
}

const LOCOMO_ALL_SESSIONS_LABEL = "全部对话";

function currentLocomoSampleScope() {
  const select = $("sample");
  const value = select?.value || "all";
  const optionText = select?.selectedOptions?.[0]?.textContent?.trim() || LOCOMO_ALL_SESSIONS_LABEL;
  if (value === "all") {
    const dataset = currentLocomoDataset();
    return {
      value,
      isAll: true,
      label: LOCOMO_ALL_SESSIONS_LABEL,
      optionText,
      questionCount: Number(dataset?.questions || 0),
    };
  }
  const parts = optionText.split("·").map((part) => part.trim()).filter(Boolean);
  const sampleId = parts.find((part) => /^conv-\d+$/i.test(part))
    || state.questions.find((q) => String(q.sample_index) === String(value) || q.sample_id === value)?.sample_id
    || optionText;
  const optionCountMatch = optionText.match(/·\s*(\d+)\s*题/);
  const optionCount = optionCountMatch ? Number(optionCountMatch[1]) : 0;
  const loadedCount = state.questions.filter((q) => String(q.sample_index) === String(value) || q.sample_id === sampleId || q.sample_id === value).length;
  return {
    value,
    isAll: false,
    label: sampleId,
    optionText,
    questionCount: loadedCount || optionCount || state.questions.length || 0,
  };
}

function currentImportSampleScope() {
  const select = $("importSample");
  const optionText = select?.selectedOptions?.[0]?.textContent?.trim() || LOCOMO_ALL_SESSIONS_LABEL;
  const selected = parseImportSampleSelection(select?.value || "all", optionText);
  const dataset = currentLocomoDataset();
  if (selected.isAll) {
    return {
      value: selected.rawValue,
      baseValue: selected.baseValue,
      smoke: false,
      isAll: true,
      label: LOCOMO_ALL_SESSIONS_LABEL,
      optionText,
      questionCount: Number(dataset?.questions || 0),
    };
  }
  const optionCountMatch = optionText.match(/·\s*(\d+)\s*题/);
  const optionCount = optionCountMatch ? Number(optionCountMatch[1]) : 0;
  return {
    value: selected.rawValue,
    baseValue: selected.baseValue,
    smoke: selected.smoke,
    isAll: false,
    label: selected.smoke ? `${selected.sampleId || optionText} · 单 session 测试` : (selected.sampleId || optionText),
    optionText,
    questionCount: optionCount,
  };
}

const IMPORT_SINGLE_SESSION_SUFFIX = "__single_session_test";

function parseImportSampleSelection(rawValue = $("importSample")?.value || "all", optionText = $("importSample")?.selectedOptions?.[0]?.textContent?.trim() || "") {
  const value = String(rawValue || "all");
  const smoke = value.endsWith(IMPORT_SINGLE_SESSION_SUFFIX);
  const baseValue = smoke ? value.slice(0, -IMPORT_SINGLE_SESSION_SUFFIX.length) : value;
  const parts = String(optionText || "").split("·").map((part) => part.trim()).filter(Boolean);
  const sampleId = baseValue === "all"
    ? ""
    : (
      parts.find((part) => /^conv-\d+$/i.test(part))
      || state.questions.find((q) => String(q.sample_index) === String(baseValue) || q.sample_id === baseValue)?.sample_id
      || ""
    );
  return {
    rawValue: value,
    baseValue: baseValue || "all",
    smoke,
    isAll: baseValue === "all",
    optionText: optionText || "",
    sampleId,
  };
}

function locomoQaSampleOptionLabel(row = {}) {
  const questions = formatInt(row.questions || 0);
  const sessions = Number(row.sessions || 0);
  const sessionText = sessions > 0 ? ` · ${formatInt(sessions)} 段 session` : "";
  return `${row.index} · ${row.sample_id} · ${questions} 题${sessionText}`;
}

function locomoImportSampleOptionLabel(row = {}) {
  const questions = formatInt(row.questions || 0);
  const sessions = Number(row.sessions || 0);
  const events = Number(row.events || 0);
  const scopeText = sessions > 0 ? `${formatInt(sessions)} 段 session` : `${formatInt(events)} 条事件`;
  return `${row.index} · ${row.sample_id} · ${scopeText} · ${questions} 题`;
}

function refreshImportActionLabels() {
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  const datasetFormat = normalizeDatasetFormat(currentLocomoDataset()?.format || "");
  const locomoReady = datasetFormat === "locomo";
  const selection = parseImportSampleSelection();
  const importBusy = isMemoryImportKind(state.currentImportTask?.kind || "") && isTaskActive(state.currentImportTask);
  const sampleName = selection.sampleId || selection.baseValue || "当前 conv";
  const commitButton = $("commitImport");
  if (commitButton) {
    commitButton.disabled = !locomoReady || importBusy;
    commitButton.textContent = selection.smoke ? "运行单 session 测试" : "导入所选对话";
    commitButton.title = importBusy
      ? "导入任务运行中，请稍候"
      : (
        selection.smoke
          ? `只向 ${backendLabel} 写入 ${sampleName} 的 1 段 session，用于快速验证注入链路`
          : `把“导入对话”选择的范围写入 ${backendLabel}`
      );
  }
}

function activeLocomoQaTask() {
  const task = state.currentLocomoTask;
  if (!task?.id) return null;
  if (isTaskTerminal(task)) return null;
  const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || task.dataset_format || "");
  if (!isEvalQaTask(task, format)) return null;
  if (locomoQaTaskAppearsComplete(task, format)) return null;
  return task;
}

function locomoQaTaskConfig(task = {}) {
  return task?.meta?.config || task?.config || {};
}

function locomoQaTaskLaunchMode(task = {}) {
  const config = locomoQaTaskConfig(task);
  return String(config.questions || "").trim() ? "selected" : "full";
}

function locomoQaTaskAppearsComplete(task = {}, format = "") {
  const resolvedFormat = enrichTaskDatasetFormat(task, format || state.taskDatasetFormats[task?.id || ""] || task?.dataset_format || "");
  if (!isEvalQaTask(task, resolvedFormat)) return false;
  const execution = taskExecutionProgress(task, resolvedFormat);
  if (Number(execution?.total_questions || 0) > 0) {
    return Number(execution.answered_questions || 0) >= Number(execution.total_questions || 0);
  }
  const progress = taskWithLiveProgress(task).progress || {};
  const total = Number(progress.total || 0);
  const current = Number(progress.current || 0);
  if (String(progress.unit || "") === "questions" && total > 0) {
    return current >= total;
  }
  return false;
}

function taskDisplayStatusLabel(task = {}, format = "") {
  const resolvedFormat = enrichTaskDatasetFormat(task, format || state.taskDatasetFormats[task?.id || ""] || task?.dataset_format || "");
  if (locomoQaTaskAppearsComplete(task, resolvedFormat)) {
    return task?.summary?.accuracy != null ? "已完成" : "等待判分";
  }
  return taskStatusLabel(task);
}

function currentScopeSelectedQuestionIds(rows = state.questions) {
  return rows.filter((q) => state.selectedQuestions.has(q.question_id)).map((q) => q.question_id);
}

function locomoQaLaunchPendingMessage() {
  if (state.locomoQaLaunchPending) return "问答任务正在启动，请稍候";
  if (state.locomoQaSubmitInFlight) return "问答任务正在提交，请稍候";
  if (activeLocomoQaTask()) return "已有问答任务运行中，请等当前任务结束后再点";
  return "";
}

function locomoQaLaunchGate(preflight = state.systemPreflight, options = {}) {
  const {requirePreflight = false} = options;
  const backend = currentMemoryBackend();
  const qaKind = locomoQaTaskKind();
  const account = currentAccount();
  const workspace = effectiveOpenVikingWorkspace(qaKind);
  const importReady = qaImportReadiness(backend, account, workspace, readCurrentAccountLastImport());
  if (importReady.tone !== "ok") {
    return {
      value: importReady.value,
      detail: importReady.detail,
      tone: importReady.tone === "bad" ? "bad" : "warn",
      blocking: true,
    };
  }
  if (!preflight) {
    return requirePreflight
      ? {
          value: "预检缺失",
          detail: "未能读取系统预检结果。",
          tone: "warn",
          blocking: true,
        }
      : {
          value: "待预检",
          detail: "点击运行时会自动检查模型、记忆目录 和记忆状态。",
          tone: "ok",
          blocking: false,
        };
  }
  if (preflight.backend_adapter?.status === "fail") {
    return {
      value: "记忆后端未就绪",
      detail: preflight.backend_adapter?.message || "请先检查系统配置。",
      tone: "bad",
      blocking: true,
    };
  }
  if (preflight.dataset?.status === "fail") {
    return {
      value: "LoCoMo 数据集不可用",
      detail: preflight.dataset?.message || "请先读取 LoCoMo 数据。",
      tone: "bad",
      blocking: true,
    };
  }
  if (preflight.workspace?.status === "fail") {
    return {
      value: "记忆目录不可用",
      detail: (preflight.workspace?.storage_root || preflight.workspace?.workspace || preflight.workspace?.message || "").trim(),
      tone: "bad",
      blocking: true,
    };
  }
  const answer = preflight.models?.answer || {};
  const echomemoryModels = preflight.models?.echomemory || {};
  const answerTokenReady = Boolean(answer.token_set || (backend === "echomemory" && echomemoryModels.chat_token_set));
  if (!answer.base_url_set) {
    return {
      value: "回答模型地址未配置",
      detail: backend === "echomemory"
        ? "请先在系统配置填写 Agent 模型地址，或确认默认回答模型地址已生效。"
        : "请先在系统配置填写回答模型地址。",
      tone: "warn",
      blocking: true,
    };
  }
  if (!answerTokenReady) {
    return {
      value: "回答模型密钥未配置",
      detail: backend === "echomemory"
        ? "请在系统配置填写 Agent 密钥，或确认 EchoMemory chat 密钥已生效。"
        : "请在系统配置填写密钥，或确认环境变量已生效。",
      tone: "warn",
      blocking: true,
    };
  }
  if (backend === "openviking" && preflight.runtime?.probe?.ok === false) {
    return {
      value: "OpenViking 服务不可用",
      detail: (preflight.runtime?.probe?.error || preflight.runtime?.message || "").trim(),
      tone: "bad",
      blocking: true,
    };
  }
  if (backend === "echomemory") {
    if (!preflight.runtime?.sdk_layout) {
      return {
        value: "未找到 EchoMemory SDK",
        detail: (preflight.runtime?.message || "").trim(),
        tone: "bad",
        blocking: true,
      };
    }
    if (preflight.runtime?.version_ok === false) {
      return {
        value: "EchoMemory 源码版本未对齐",
        detail: (preflight.runtime?.message || "").trim(),
        tone: "bad",
        blocking: true,
      };
    }
  }
  return {
    value: "可启动",
    detail: "模型、记忆目录 和记忆导入状态已就绪。",
    tone: "ok",
    blocking: false,
  };
}

function refreshLocomoQaActionLabels() {
  const backendLabel = memoryBackendLabel(currentMemoryBackend());
  const scope = currentLocomoSampleScope();
  const clickLocked = uiActionLocked("locomoQaLaunch");
  const launchPending = state.locomoQaLaunchPending;
  const submitting = state.locomoQaSubmitInFlight;
  const submitPhase = state.locomoQaSubmitPhase || "submit";
  const launchSource = state.locomoQaLaunchSource || "";
  const submitText = submitPhase === "preflight" ? "预检中..." : "提交中...";
  const submitTitle = submitPhase === "preflight"
    ? "正在检查模型、记忆目录 和记忆导入状态，请稍候"
    : "问答任务正在提交，请稍候";
  const launchText = "启动中...";
  const launchTitle = "正在准备当前问答任务，请稍候";
  const loading = state.locomoQuestionsLoading;
  const busyTask = activeLocomoQaTask();
  const busyTaskMode = busyTask ? locomoQaTaskLaunchMode(busyTask) : "";
  const busyTaskStatus = busyTask ? taskStatusLabel(busyTask) : "";
  const busyTaskText = busyTaskStatus ? `${busyTaskStatus}...` : "运行中...";
  const busyTaskTitle = busyTask
    ? `已有问答任务${busyTaskStatus || "运行中"}，任务 ID：${busyTask.id || "-"}`
    : "";
  const launchGate = !clickLocked && !launchPending && !submitting && !loading && !busyTask
    ? locomoQaLaunchGate()
    : {blocking: false, value: "", detail: "", tone: "ok"};
  const launchBlockedTitle = launchGate.blocking
    ? `当前不可启动：${launchGate.value}${launchGate.detail ? `。${launchGate.detail}` : ""}`
    : "";
  const selectedInScope = currentScopeSelectedQuestionIds().length;
  const disabled = clickLocked || launchPending || submitting || loading || Boolean(busyTask) || launchGate.blocking;
  const runOneBusy = (submitting || launchPending)
    ? launchSource === "selected"
    : busyTaskMode === "selected";
  const baseRunFullText = scope.isAll ? "跑全部 LoCoMo" : "跑当前 conv 全部题";
  const runFullBusy = (submitting || launchPending)
    ? launchSource === "full"
    : busyTaskMode === "full";
  const runOne = $("runOpenVikingQa");
  if (runOne) {
    runOne.disabled = disabled || (!busyTask && selectedInScope === 0);
    runOne.textContent = submitting
      ? (runOneBusy ? submitText : "跑选中题")
      : launchPending
        ? (runOneBusy ? launchText : "跑选中题")
        : busyTask
          ? (runOneBusy ? busyTaskText : "跑选中题")
          : "跑选中题";
    runOne.title = submitting
      ? (runOneBusy ? submitTitle : "另一个问答入口正在提交，请稍候")
      : clickLocked
        ? "点击已受理，正在准备问答任务"
      : launchPending
        ? (runOneBusy ? launchTitle : "另一个问答入口正在启动，请稍候")
      : loading
        ? "题目范围切换中，请稍候"
        : busyTask
          ? (runOneBusy ? busyTaskTitle : "当前已有全量问答任务运行中；请等待它结束后再跑选中题")
          : launchGate.blocking
            ? launchBlockedTitle
          : selectedInScope === 0
            ? "请先勾选至少 1 题；要跑当前范围全量请点右边按钮"
            : (scope.isAll
        ? `${backendLabel} 只运行已勾选题目；${LOCOMO_ALL_SESSIONS_LABEL}模式下必须先勾选`
        : `${backendLabel} 只运行当前范围内已勾选的题目`);
  }
  const runFull = $("runOpenVikingFullQa");
  if (runFull) {
    runFull.disabled = disabled;
    runFull.textContent = submitting
      ? (runFullBusy ? submitText : baseRunFullText)
      : launchPending
        ? (runFullBusy ? launchText : baseRunFullText)
        : busyTask
          ? (runFullBusy ? busyTaskText : baseRunFullText)
          : baseRunFullText;
    runFull.title = submitting
      ? (runFullBusy ? submitTitle : "另一个问答入口正在提交，请稍候")
      : clickLocked
        ? "点击已受理，正在准备问答任务"
      : launchPending
        ? (runFullBusy ? launchTitle : "另一个问答入口正在启动，请稍候")
      : loading
        ? "题目范围切换中，请稍候"
        : busyTask
          ? (runFullBusy ? busyTaskTitle : "当前已有选题问答任务运行中；请等待它结束后再跑全量")
          : launchGate.blocking
            ? launchBlockedTitle
          : (scope.isAll
        ? `${backendLabel} 全量问答测试：${LOCOMO_ALL_SESSIONS_LABEL} / 全部 QA`
        : `${backendLabel} 不看勾选状态，运行当前 conv 的全部题：${scope.label}`);
  }
}

function locomoQuestionsMatchScope(scope = currentLocomoSampleScope()) {
  if (!state.questions.length) return false;
  if (scope.isAll) {
    const dataset = currentLocomoDataset();
    return !dataset?.questions || Number(dataset.questions) === state.questions.length;
  }
  return state.questions.every((q) => String(q.sample_index) === String(scope.value) || q.sample_id === scope.label || q.sample_id === scope.value);
}

function locomoQuestionSelectionKpis(rows = filteredQuestions()) {
  const dataset = currentLocomoDataset();
  const scope = currentLocomoSampleScope();
  const datasetTotal = Number(dataset?.questions || state.questions.length || 0);
  const scopeTotal = scope.isAll
    ? (datasetTotal || state.questions.length || rows.length)
    : Number(scope.questionCount || state.questions.length || rows.length);
  const selected = state.selectedQuestions.size;
  const selectedInScope = rows.filter((q) => state.selectedQuestions.has(q.question_id)).length;
  const rangeValue = scope.isAll
    ? `${rows.length}/${scopeTotal}`
    : `${rows.length}/${scopeTotal} · ${scope.label}${datasetTotal > scopeTotal ? ` / 总${datasetTotal}` : ""}`;
  return [
    ["题目范围", rangeValue],
    ["已选", `${selectedInScope}/${rows.length}`],
    ["运行模式", selected ? "按选择题目" : "当前范围全量"],
  ];
}

function inferSampleIdFromQuestionId(questionId = "") {
  const match = String(questionId || "").trim().match(/^(.+?)_qa\d+$/i);
  return match ? match[1] : "";
}

function inferSingleSampleFromQuestionIds(questionIds = [], examples = []) {
  const ids = Array.isArray(questionIds) ? questionIds : [];
  const fromIds = [...new Set(ids.map((qid) => inferSampleIdFromQuestionId(qid)).filter(Boolean))];
  if (fromIds.length === 1) return fromIds[0];
  const rows = Array.isArray(examples) ? examples : [];
  const fromExamples = [...new Set(rows.map((row) => String(row.sample_id || "")).filter(Boolean))];
  return fromExamples.length === 1 ? fromExamples[0] : "";
}

function resolveLocomoSampleOptionValue(sampleValue = "all", questionIds = [], examples = []) {
  const select = $("sample");
  if (!select) return "all";
  const explicit = String(sampleValue || "").trim() || "all";
  if (explicit !== "all" && [...select.options].some((option) => String(option.value) === explicit)) {
    return explicit;
  }
  const desiredSampleId = explicit !== "all" ? explicit : inferSingleSampleFromQuestionIds(questionIds, examples);
  if (!desiredSampleId || desiredSampleId === "all") return explicit;
  const matchedOption = [...select.options].find((option) => String(option.textContent || "").includes(desiredSampleId));
  if (matchedOption) return String(matchedOption.value || "all");
  const sampleRows = Array.isArray(currentLocomoDataset()?.sample_rows) ? currentLocomoDataset().sample_rows : [];
  const matchedRow = sampleRows.find((row) => String(row.sample_id || "") === desiredSampleId || String(row.index) === desiredSampleId);
  if (matchedRow) return String(matchedRow.index);
  return explicit;
}
const VIEW_NAV_PARENT = {
  workbenchView: "openvikingView",
  datasetView: "openvikingView",
  openvikingView: "openvikingView",
  evalView: "openvikingView",
  judgeView: "openvikingView",
  memoryView: "openvikingView",
  runsView: "openvikingView",
};
const RETIRED_VIEW_FALLBACKS = {
  chenmoView: "openvikingView",
  readmeView: "systemConfigView",
};
const DATASET_FORMAT_VIEWS = {
  locomo: "openvikingView",
  longmemeval: "longMemEvalView",
  evolvingevents: "evolvingEventsView",
  hotpotqa: "hotpotQaView",
  proagentbench: "proAgentBenchView",
  tau2bench: "tauBenchView",
};
const DATASET_FORMAT_ALIASES = {
  longmem: "longmemeval",
  long_mem_eval: "longmemeval",
  longmemevaluation: "longmemeval",
  hotpot: "hotpotqa",
  hotpot_qa: "hotpotqa",
  "hotpot-qa": "hotpotqa",
  proagent: "proagentbench",
  pro_agent_bench: "proagentbench",
  "pro-agent-bench": "proagentbench",
  tau2: "tau2bench",
  tau2_bench: "tau2bench",
  "tau2-bench": "tau2bench",
  tau_bench: "tau2bench",
  "tau-bench": "tau2bench",
  taubench: "tau2bench",
  evolvingevent: "evolvingevents",
  evolving_events: "evolvingevents",
  "evolving-events": "evolvingevents",
};
const STANDALONE_BENCHMARK_FORMATS = new Set(["longmemeval", "evolvingevents", "hotpotqa", "proagentbench", "tau2bench"]);
const DATASET_VIEW_FORMATS = Object.fromEntries(Object.entries(DATASET_FORMAT_VIEWS).map(([format, view]) => [view, format]));
const WORKFLOW_GUIDE_VIEWS = new Set();

function appVersionFromIndexHtml(html) {
  const match = String(html || "").match(/<script[^>]+src=["'][^"']*\/app\.js\?v=([^"']+)/i);
  return match ? decodeURIComponent(match[1]) : "";
}

async function maybeReloadForFreshAppVersion() {
  if (!window.fetch || window.location.protocol === "file:") return;
  try {
    const response = await fetch(`/?version_probe=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) return;
    const serverVersion = appVersionFromIndexHtml(await response.text());
    if (!serverVersion || serverVersion === UI_REFRESH_VERSION) return;
    const reloadKey = `locomoEval.versionReload.${UI_REFRESH_VERSION}.${serverVersion}`;
    if (sessionStorage.getItem(reloadKey) === "1") return;
    sessionStorage.setItem(reloadKey, "1");
    const url = new URL(window.location.href);
    url.searchParams.set("ui_refresh", serverVersion);
    window.location.replace(url.toString());
  } catch {
  }
}
maybeReloadForFreshAppVersion();

setInterval(() => {
  maybeReloadForFreshAppVersion();
}, 30000);

function inferDatasetFormatFromText(...values) {
  const text = values.map((value) => String(value || "")).join(" ").toLowerCase();
  if (!text) return "";
  if (/(longmem|longmemeval)/.test(text)) return "longmemeval";
  if (/(hotpot|hotpotqa)/.test(text)) return "hotpotqa";
  if (/(proagent|pro[-_ ]?agent[-_ ]?bench)/.test(text)) return "proagentbench";
  if (/(tau2|tau[-_ ]?bench|taubench)/.test(text)) return "tau2bench";
  if (/(evolvingevents|evolving[-_ ]?events)/.test(text)) return "evolvingevents";
  if (/(locomo|openviking_qa|echomemory_qa|vikingbot|custom_local_agent_probe)/.test(text)) return "locomo";
  return "";
}

function projectPath(...parts) {
  const root = String(state.config?.root || "").replace(/\/+$/, "");
  return root ? [root, ...parts].join("/") : parts.join("/");
}

function runPath(...parts) {
  const outputDir = String(state.config?.output_dir || "").replace(/\/+$/, "");
  return outputDir ? [outputDir, ...parts].join("/") : projectPath("runs", ...parts);
}

function artifactHref(path) {
  const value = String(path || "");
  const root = String(state.config?.root || "").replace(/\/+$/, "");
  const runsDir = String(state.config?.runs_dir || state.config?.output_dir || "").replace(/\/+$/, "");
  const candidates = [
    runsDir,
    root ? `${root}/runs` : "",
  ].filter(Boolean);
  for (const base of candidates) {
    if (value === base) return "/runs/";
    if (value.startsWith(`${base}/`)) {
      return `/runs/${value.slice(base.length + 1).split("/").map(encodeURIComponent).join("/")}`;
    }
  }
  const marker = "/runs/";
  const index = value.indexOf(marker);
  if (index >= 0) return `/runs/${value.slice(index + marker.length).split("/").map(encodeURIComponent).join("/")}`;
  return "";
}

function readLastImport() {
  const account = safeAccountSlug(currentAccount());
  const scopedKey = `${LAST_IMPORT_KEY}.${account}`;
  try {
    const current = JSON.parse(localStorage.getItem(scopedKey) || "{}");
    if (current && Object.keys(current).length) return current;
    const all = Object.keys(localStorage)
      .filter((key) => key.startsWith(`${LAST_IMPORT_KEY}.`))
      .map((key) => {
        try {
          return JSON.parse(localStorage.getItem(key) || "{}");
        } catch {
          return null;
        }
      })
      .filter((item) => item && item.backend === currentMemoryBackend() && (item.workspace || item.output_file))
      .sort((a, b) => String(b.saved_at || "").localeCompare(String(a.saved_at || "")));
    if (all[0]) return all[0];
    const scoped = localStorage.getItem(scopedKey);
    if (scoped) return JSON.parse(scoped || "{}");
    if (account === "default") return JSON.parse(localStorage.getItem(LAST_IMPORT_KEY) || "{}");
    return {};
  } catch {
    return {};
  }
}

function readScopedLastImport(account = currentAccount()) {
  const normalizedAccount = safeAccountSlug(account);
  try {
    const scopedKey = `${LAST_IMPORT_KEY}.${normalizedAccount}`;
    const scoped = JSON.parse(localStorage.getItem(scopedKey) || "{}");
    if (scoped && Object.keys(scoped).length) return scoped;
    if (normalizedAccount === "default") return JSON.parse(localStorage.getItem(LAST_IMPORT_KEY) || "{}");
    return {};
  } catch {
    return {};
  }
}

function latestMemoryImportTask(tasks = [], options = {}) {
  const preferredKind = locomoImportTaskKind();
  const scoped = currentAccountOnlyEnabled("taskCurrentAccountOnly")
    ? tasks.filter(matchesCurrentAccount)
    : tasks;
  if (options.strictAccount && currentAccountOnlyEnabled("taskCurrentAccountOnly") && !scoped.length) return null;
  const pool = scoped.length ? scoped : tasks;
  const fallbackPool = options.strictAccount ? pool : tasks;
  return pool.find((task) => task.kind === preferredKind && isTaskActive(task))
    || pool.find((task) => isMemoryImportKind(task.kind) && isTaskActive(task))
    || pool.find((task) => task.kind === preferredKind && task.status === "succeeded")
    || pool.find((task) => isMemoryImportKind(task.kind) && task.status === "succeeded")
    || pool.find((task) => task.kind === preferredKind)
    || pool.find((task) => isMemoryImportKind(task.kind))
    || fallbackPool.find((task) => isMemoryImportKind(task.kind));
}

function latestAnyMemoryImportTask(tasks = []) {
  return tasks.find((task) => isMemoryImportKind(task.kind) && isTaskActive(task))
    || tasks.find((task) => isMemoryImportKind(task.kind) && task.status === "succeeded")
    || tasks.find((task) => isMemoryImportKind(task.kind));
}

function latestMemoryImportRun(runs = []) {
  const preferredKind = locomoImportTaskKind();
  const scoped = currentAccountOnlyEnabled("runsCurrentAccountOnly")
    ? runs.filter(matchesCurrentAccount)
    : runs;
  const pool = scoped.length ? scoped : runs;
  return pool.find((run) => run.kind === preferredKind)
    || pool.find((run) => isMemoryImportKind(run.kind || ""))
    || null;
}

async function latestMemoryImportRecord() {
  let runs = Array.isArray(state.recentRuns) ? state.recentRuns : [];
  const runsFresh = state.runsLoadedAt && (Date.now() - state.runsLoadedAt) < 30000 && runs.length;
  if (!runsFresh) {
    const data = await api("/api/runs?include_history=1&limit=80");
    runs = (data.runs || [])
      .filter((run) => !currentAccountOnlyEnabled("runsCurrentAccountOnly") || matchesCurrentAccount(run))
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    state.recentRuns = runs;
    state.runsLoadedAt = Date.now();
  }
  return latestMemoryImportRun(runs);
}

function runCreatedAtMs(run = {}) {
  const created = String(run.created_at || run.updated_at || run.ended_at || "").trim();
  const parsed = created ? Date.parse(created) : NaN;
  return Number.isFinite(parsed) ? parsed : NaN;
}

function isRecentWithinDays(run = {}, days = 3) {
  const createdMs = runCreatedAtMs(run);
  if (!Number.isFinite(createdMs)) return false;
  return createdMs >= (Date.now() - days * 24 * 60 * 60 * 1000);
}

function isRecentLocomoQaRun(run = {}) {
  const kind = String(run.kind || "").trim();
  const format = normalizeDatasetFormat(benchmarkFormatFromRecord(run));
  return (kind === "openviking_qa" || kind === "echomemory_qa")
    && (!format || format === "locomo");
}

function recentLocomoRunSort(a = {}, b = {}) {
  return runCreatedAtMs(b) - runCreatedAtMs(a);
}

async function ensureRecentRunsLoaded(force = false) {
  let runs = Array.isArray(state.recentRuns) ? state.recentRuns : [];
  const runsFresh = !force && state.runsLoadedAt && (Date.now() - state.runsLoadedAt) < 30000 && runs.length;
  if (runsFresh) return runs;
  const data = await api("/api/runs?include_history=1&limit=80");
  runs = (data.runs || [])
    .filter((run) => !currentAccountOnlyEnabled("runsCurrentAccountOnly") || matchesCurrentAccount(run))
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  state.recentRuns = runs;
  state.runsLoadedAt = Date.now();
  return runs;
}

function recentLocomoRunEmptyText(type = "import") {
  return type === "qa"
    ? "近 3 天没有找到 LoCoMo QA 任务。"
    : "近 3 天没有找到记忆导入任务。";
}

function bindRecentLocomoRunCards(importRuns = [], qaRuns = []) {
  const importMap = new Map(importRuns.map((run) => [runCompareKey(run), run]));
  const qaMap = new Map(qaRuns.map((run) => [runCompareKey(run), run]));
  document.querySelectorAll("#recentImportRunsList .run-card").forEach((card) => {
    card.addEventListener("click", async () => {
      const run = importMap.get(card.dataset.runKey || "");
      if (!run) return;
      state.currentImportTask = run;
      renderImportPaths(run);
      renderImportDiagnostics(run);
      updateProgress(run, run.kind || locomoImportTaskKind());
      const logPath = runLogPathFromRecord(run);
      if (logPath) await loadLogPathIntoBox(logPath, "importLogBox").catch(() => false);
      $("importLogBox")?.scrollIntoView({behavior: "smooth", block: "center"});
    });
  });
  document.querySelectorAll("#recentQaRunsList .run-card").forEach((card) => {
    card.addEventListener("click", async () => {
      const run = qaMap.get(card.dataset.runKey || "");
      if (!run) return;
      if (run.output_file) markLocomoOutputFile(run.output_file);
      await refreshResult().catch(() => null);
      showView("evalView", {preserveScroll: true});
    });
  });
}

async function renderRecentLocomoRuns(options = {}) {
  const importList = $("recentImportRunsList");
  const qaList = $("recentQaRunsList");
  const importCount = $("recentImportRunsCount");
  const qaCount = $("recentQaRunsCount");
  if (!importList) return;
  if (options.loading) {
    importList.innerHTML = `<p class="muted-list-note">正在读取近 3 天导入任务...</p>`;
    if (qaList) qaList.innerHTML = `<p class="muted-list-note">正在读取近 3 天 QA 任务...</p>`;
  }
  try {
    const runs = await ensureRecentRunsLoaded(Boolean(options.force));
    const recentRuns = runs.filter((run) => isRecentWithinDays(run, 3));
    const importRuns = recentRuns
      .filter((run) => isMemoryImportKind(run.kind || ""))
      .sort(recentLocomoRunSort);
    const qaRuns = recentRuns
      .filter((run) => isRecentLocomoQaRun(run))
      .sort(recentLocomoRunSort);
    if (importCount) importCount.textContent = `${importRuns.length} 条`;
    if (qaCount) qaCount.textContent = `${qaRuns.length} 条`;
    importList.innerHTML = importRuns.length
      ? importRuns.map(renderRunCard).join("")
      : `<p class="muted-list-note">${recentLocomoRunEmptyText("import")}</p>`;
    if (qaList) {
      qaList.innerHTML = qaRuns.length
        ? qaRuns.map(renderRunCard).join("")
        : `<p class="muted-list-note">${recentLocomoRunEmptyText("qa")}</p>`;
    }
    bindRecentLocomoRunCards(importRuns, qaRuns);
  } catch (error) {
    const message = error?.message || String(error || "读取失败");
    importList.innerHTML = `<p class="muted-list-note bad-text">读取失败：${escapeHtml(message)}</p>`;
    if (qaList) qaList.innerHTML = `<p class="muted-list-note bad-text">读取失败：${escapeHtml(message)}</p>`;
  }
}

async function loadRecentEvalQaRunsForTaskPanel() {
  const data = await api("/api/runs?include_history=1&limit=80");
  const runs = Array.isArray(data.runs) ? data.runs : [];
  return runs
    .filter((run) => !currentAccountOnlyEnabled("taskCurrentAccountOnly") || matchesCurrentAccount(run))
    .filter((run) => isRecentWithinDays(run, 3))
    .filter((run) => isRecentLocomoQaRun(run))
    .sort(recentLocomoRunSort);
}

function renderEvalQaRunFallbackCard(run = {}) {
  const summary = run.summary || {};
  const format = normalizeDatasetFormat(benchmarkFormatFromRecord(run)) || "locomo";
  const account = recordAccount(run) || "default";
  const sampleKeys = summary.samples && typeof summary.samples === "object"
    ? Object.keys(summary.samples).filter(Boolean)
    : [];
  const inferredTitle = runDisplayTitle(run) || "";
  const displayTitle = /^\d+$/.test(inferredTitle) && sampleKeys.length
    ? sampleKeys[0]
    : (inferredTitle || run.name || run.id || "-");
  const acc = summary.accuracy == null ? "待判分" : percent(summary.accuracy);
  const rows = runDatasetMeta(run).rows ?? summary.rows ?? "-";
  const statusLabel = String(run.status || "").toLowerCase() === "succeeded" && summary.accuracy == null
    ? "等待判分"
    : taskStatusLabel(run);
  const durationText = run.duration_s == null ? "" : ` · 用时 ${formatDuration(run.duration_s)}`;
  return `
    <article class="task compact-task" data-run-key="${escapeHtml(runCompareKey(run))}" data-output-file="${escapeHtml(run.output_file || "")}" data-dataset-format="${escapeHtml(format)}">
      <div>
        <strong>${escapeHtml(displayTitle)}</strong>
        <small>QA · ${escapeHtml(statusLabel)} · ${escapeHtml(account)} · ${escapeHtml(displayDatasetFormatForTask(run, format))} · rows ${escapeHtml(rows)} · ${escapeHtml(acc)}${escapeHtml(durationText)}</small>
      </div>
      <code>${escapeHtml(run.output_file || run.run_dir || "")}</code>
    </article>
  `;
}

function saveLastImport(patch = {}) {
  const account = safeAccountSlug(patch.account || currentAccount());
  const current = readScopedLastImport(account);
  const next = {...current, ...patch, account, saved_at: new Date().toISOString()};
  localStorage.setItem(`${LAST_IMPORT_KEY}.${account}`, JSON.stringify(next));
  if (account === "default") localStorage.setItem(LAST_IMPORT_KEY, JSON.stringify(next));
  return next;
}

function readCurrentAccountLastImport() {
  return readScopedLastImport(currentAccount());
}

function chatDraftKey(account = currentAccount()) {
  return `${CHAT_DRAFT_PREFIX}${safeAccountSlug(account)}`;
}

function loadChatDraft(account = currentAccount()) {
  try {
    const raw = localStorage.getItem(chatDraftKey(account));
    const data = raw ? JSON.parse(raw) : [];
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function saveChatDraft(account = currentAccount(), messages = state.chatMessages) {
  try {
    localStorage.setItem(chatDraftKey(account), JSON.stringify(messages || []));
  } catch {}
}

function clearChatDraft(account = currentAccount()) {
  try {
    localStorage.removeItem(chatDraftKey(account));
  } catch {}
}

function normalizeMemoryBackend(value) {
  const backend = String(value || "").trim().toLowerCase();
  return backend === "echomemory" || backend === "echomem" ? "echomemory" : "openviking";
}

function memoryBackendLabel(value) {
  const backend = normalizeMemoryBackend(value);
  if (backend === "echomemory") return "EchoMemory";
  return "OpenViking";
}

function memoryBackendShortLabel(value) {
  const backend = normalizeMemoryBackend(value);
  return backend === "echomemory" ? "EM" : "OV";
}

function importTaskKindForBackend(backend = currentMemoryBackend()) {
  return normalizeMemoryBackend(backend) === "echomemory" ? "echomemory_import" : "openviking_import";
}

function importScriptForBackend(backend = currentMemoryBackend()) {
  return normalizeMemoryBackend(backend) === "echomemory"
    ? "scripts/echomemory_locomo_import.py"
    : "scripts/openviking_locomo_import.py";
}

function genericQaTaskKindForBackend(backend = currentMemoryBackend()) {
  return normalizeMemoryBackend(backend) === "echomemory"
    ? "echomemory_generic_qa"
    : "openviking_generic_qa";
}

function importWriteSurfaceForBackend(backend = currentMemoryBackend()) {
  return normalizeMemoryBackend(backend) === "echomemory"
    ? "写入 EchoMemory 长期记忆"
    : "写入 OpenViking 长期记忆";
}

function workspaceBackendNameHint(workspace = "") {
  const name = String(workspace || "").split("/").pop() || "";
  if (/^echomem_workspace_/i.test(name)) return "echomemory";
  if (/^openviking_workspace_/i.test(name)) return "openviking";
  return "";
}

function compactPath(value = "", head = 34, tail = 34) {
  const text = String(value || "").trim();
  if (!text) return "-";
  if (text.length <= head + tail + 3) return text;
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

function shellQuote(value = "") {
  const text = String(value || "");
  return `'${text.replace(/'/g, "'\"'\"'")}'`;
}

function currentMemoryBackend() {
  return normalizeMemoryBackend($("memoryBackendSelect")?.value || readAccountConfig(currentAccount()).memoryBackend || "openviking");
}

function renderGlobalBackendBadge() {
  const badge = $("globalBackendBadge");
  const label = $("globalBackendLabel");
  if (!badge || !label) return;
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  label.textContent = backendLabel;
  badge.classList.toggle("echomemory", backend === "echomemory");
  badge.classList.toggle("openviking", backend === "openviking");
  badge.title = `当前记忆后端：${backendLabel}；点击到系统配置切换`;
  badge.setAttribute("aria-label", `当前记忆后端：${backendLabel}，点击到系统配置切换`);
}

function backendSupportsAgentWorkbench(backend = currentMemoryBackend()) {
  return ["openviking", "echomemory"].includes(normalizeMemoryBackend(backend));
}

function agentWorkbenchSupportText(backend = currentMemoryBackend()) {
  const normalized = normalizeMemoryBackend(backend);
  if (normalized === "echomemory") {
    return "EchoMemory find/search、上下文组装和手动 commit 可用";
  }
  return backendSupportsAgentWorkbench(backend)
    ? "OpenViking search/find、上下文组装和手动 commit 可用"
    : `${memoryBackendLabel(backend)} 当前只用于 LoCoMo 导入、QA 和报告；人工对话工作台待接入`;
}

function locomoImportTaskKind() {
  return importTaskKindForBackend(currentMemoryBackend());
}

function locomoQaTaskKind() {
  const backend = currentMemoryBackend();
  if (backend === "echomemory") return "echomemory_qa";
  return "openviking_qa";
}

function isMemoryImportKind(kind) {
  return kind === "openviking_import" || kind === "echomemory_import";
}

function isMemoryQaKind(kind) {
  return kind === "openviking_qa" || kind === "echomemory_qa";
}

function activeViewId() {
  return document.body?.dataset?.activeView || document.querySelector(".view-panel.active")?.id || "";
}

function isEvalQaTask(task = {}, format = "") {
  const kind = task.kind || "";
  if (isMemoryImportKind(kind) || kind === "judge" || kind === "stats" || kind === "adapter") return false;
  if (kind === "openviking_qa_retry_failed" || kind === "openviking_qa_retry_missing") return true;
  if (isMemoryQaKind(kind)) return true;
  return isLocomoTaskOutput(kind, task, format) && /qa|eval|agent/i.test(`${kind} ${task.name || ""} ${task.id || ""}`);
}

function taskVisibleInEvalTaskList(task = {}, format = "", viewId = activeViewId()) {
  if (viewId !== "evalView") return true;
  return isEvalQaTask(task, format);
}

function taskVisibleInActiveTaskStrip(task = {}, format = "", viewId = activeViewId()) {
  if (viewId === "evalView") return isEvalQaTask(task, format);
  if (viewId === "judgeView") return (task.kind || "") === "judge";
  return false;
}

function taskVisibleInCurrentTaskPanel(task = {}, format = "", viewId = activeViewId()) {
  const kind = task.kind || "";
  if (viewId === "evalView") return isEvalQaTask(task, format);
  if (viewId === "openvikingView") return isMemoryImportKind(kind);
  if (viewId === "judgeView") return kind === "judge";
  return true;
}

function isTaskActive(task = {}) {
  return ACTIVE_TASK_STATUSES.has(String(task?.status || "").toLowerCase());
}

function syncTrackedTaskSnapshot(task = null) {
  if (!task?.id) return;
  const snapshot = stampTaskSnapshot(task);
  if (state.currentRunningTask?.id === snapshot.id) state.currentRunningTask = snapshot;
  if (state.currentLocomoTask?.id === snapshot.id) state.currentLocomoTask = snapshot;
  if (state.currentImportTask?.id === snapshot.id) state.currentImportTask = snapshot;
}

function syncTrackedTaskSnapshots(tasks = []) {
  for (const task of (tasks || [])) syncTrackedTaskSnapshot(task);
}

function isTaskTerminal(task = {}) {
  return TERMINAL_TASK_STATUSES.has(String(task?.status || "").toLowerCase());
}

function updateStopActionButtons(tasks = []) {
  const pool = (tasks.length ? tasks : [state.currentRunningTask, state.currentLocomoTask, state.currentImportTask])
    .filter((task) => task?.id && isTaskActive(task));
  const hasImport = pool.some((task) => isMemoryImportKind(task.kind || ""));
  const hasJudge = pool.some((task) => (task.kind || "") === "judge");
  const hasEval = pool.some((task) => {
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || task.dataset_format || "");
    return isEvalQaTask(task, format);
  });
  const importButton = $("stopAllTasksImport");
  if (importButton) {
    importButton.disabled = !hasImport;
    importButton.title = hasImport ? "停止当前导入相关任务" : "当前没有运行中的导入任务";
  }
  const evalButton = $("stopAllTasksEval");
  if (evalButton) {
    evalButton.disabled = !hasEval;
    evalButton.title = hasEval ? "停止当前问答相关任务" : "当前没有运行中的问答任务";
  }
  const judgeButton = $("stopAllTasksJudge");
  if (judgeButton) {
    judgeButton.hidden = !hasJudge;
    judgeButton.disabled = !hasJudge;
    judgeButton.title = hasJudge ? "停止当前判分任务" : "当前没有运行中的判分任务";
  }
}

function clearEvalTaskContainers() {
  const list = $("recentTaskList");
  if (list) list.innerHTML = "";
  renderActiveTaskStrip(null);
}

function syncEvalTaskContainersForView(viewId = activeViewId()) {
  if (viewId !== "evalView") {
    clearEvalTaskContainers();
    return;
  }
  const strip = $("activeTaskStrip");
  const currentKind = strip?.dataset.taskKind || "";
  if (currentKind && !taskVisibleInActiveTaskStrip({kind: currentKind}, "", viewId)) {
    renderActiveTaskStrip(null);
  }
}

function configuredWorkspaceForBackend(backend = currentMemoryBackend()) {
  const normalized = normalizeMemoryBackend(backend);
  const cfg = readAccountConfig(currentAccount());
  const ovInput = $("ovWorkspace")?.value.trim() || "";
  const memoryInput = $("memoryWorkspace")?.value.trim() || "";
  if (normalized === "echomemory") {
    return memoryInput || cfg.memoryWorkspace || ovInput || cfg.ovWorkspace || "";
  }
  return ovInput || cfg.ovWorkspace || memoryInput || cfg.memoryWorkspace || "";
}

function effectiveOpenVikingWorkspace(kind, extra = {}) {
  const explicit = String(extra.workspace || "").trim();
  if (explicit) return explicit;
  const backend = normalizeMemoryBackend(
    extra.backend
      || (String(kind || "").startsWith("echomemory_") ? "echomemory" : currentMemoryBackend())
  );
  const inputWorkspace = configuredWorkspaceForBackend(backend);
  const lastImport = readCurrentAccountLastImport();
  if (
    isMemoryQaKind(kind)
    && lastImport.workspace
    && !inputWorkspace
    && normalizeMemoryBackend(lastImport.backend || backend) === backend
  ) {
    return lastImport.workspace;
  }
  return inputWorkspace || lastImport.workspace || "";
}

function vikingbotAlignedQaPayload() {
  return {
    prompt_mode: "vikingbot_aligned",
    top_k: VIKINGBOAT_LITE_TOP_K,
    openviking_tool_loop: true,
    openviking_tool_set: "vikingbot_native_safe",
    tool_search_limit: VIKINGBOAT_LITE_TOOL_SEARCH_LIMIT,
    tool_min_score: 0.35,
    read_openviking_content: true,
    group_chat: false,
    initial_agent_memory: true,
    vikingbot_identity_mode: "sender_session",
    max_iterations: 50,
    query_expansion: false,
    lexical_fallback: false,
    archive_fallback: false,
    read_memory_files: false,
  };
}

function readLastDataset() {
  try {
    return JSON.parse(localStorage.getItem(LAST_DATASET_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveLastDataset(patch = {}) {
  const next = {...readLastDataset(), ...patch, saved_at: new Date().toISOString()};
  localStorage.setItem(LAST_DATASET_KEY, JSON.stringify(next));
  return next;
}

function readLastBenchmarkDataset() {
  try {
    return JSON.parse(localStorage.getItem(LAST_BENCHMARK_DATASET_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveLastBenchmarkDataset(patch = {}) {
  const normalized = normalizeDatasetFormat(patch.format || patch.dataset_format || "");
  if (!normalized || normalized === "locomo") return readLastBenchmarkDataset();
  const path = String(patch.path || "").trim();
  const next = {
    ...readLastBenchmarkDataset(),
    ...patch,
    path,
    format: normalized,
    view: viewForDatasetFormat(normalized, patch.view || ""),
    saved_at: new Date().toISOString(),
  };
  localStorage.setItem(LAST_BENCHMARK_DATASET_KEY, JSON.stringify(next));
  if (path) saveLastDataset({path, format: normalized});
  return next;
}

function readLastLocomoDataset() {
  try {
    const dedicated = JSON.parse(localStorage.getItem(LAST_LOCOMO_DATASET_KEY) || "{}");
    if (dedicated?.path) return dedicated;
  } catch {}
  const legacy = readLastDataset();
  return String(legacy.format || "").toLowerCase() === "locomo" ? legacy : {};
}

function saveLastLocomoDataset(patch = {}) {
  const next = {...readLastLocomoDataset(), ...patch, format: "locomo", saved_at: new Date().toISOString()};
  localStorage.setItem(LAST_LOCOMO_DATASET_KEY, JSON.stringify(next));
  saveLastDataset(next);
  return next;
}

function normalizeDatasetFormat(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return "";
  const compact = raw.replace(/[\s_-]+/g, "");
  return DATASET_FORMAT_ALIASES[raw]
    || DATASET_FORMAT_ALIASES[compact]
    || (DATASET_FORMAT_VIEWS[compact] ? compact : raw);
}

function viewForDatasetFormat(format, fallback = "runsView") {
  return DATASET_FORMAT_VIEWS[normalizeDatasetFormat(format)] || fallback;
}

function setBenchmarkDatasetInput(format = "", path = "") {
  const normalized = normalizeDatasetFormat(format);
  const value = String(path || "").trim();
  if (!normalized || !value) return "";
  if (normalized === "locomo") {
    if ($("data")) $("data").value = value;
    return value;
  }
  if (normalized === "longmemeval") {
    if ($("longMemData")) $("longMemData").value = value;
    return value;
  }
  const key = genericBenchmarkKeyForFormat(normalized);
  if (key) {
    const config = benchmarkConfig(key);
    if ($(config.dataInput)) $(config.dataInput).value = value;
    return value;
  }
  return "";
}

function activeDatasetPathForFormat(format = "") {
  const normalized = normalizeDatasetFormat(format);
  if (!normalized) return "";
  if (normalized === "locomo") return $("data")?.value.trim() || "";
  if (normalized === "longmemeval") return $("longMemData")?.value.trim() || "";
  const key = genericBenchmarkKeyForFormat(normalized);
  if (key) {
    const config = benchmarkConfig(key);
    return $(config.dataInput)?.value.trim() || "";
  }
  const saved = readLastBenchmarkDataset();
  return normalizeDatasetFormat(saved.format) === normalized ? String(saved.path || "") : "";
}

function benchmarkDatasetPathFromRecord(record = {}) {
  const config = record.meta?.config || {};
  const summary = record.summary || {};
  const summaryJson = summary.summary_json || {};
  return String(
    record.data
      || record.dataset
      || record.dataset_path
      || record.input_dataset
      || config.data
      || config.dataset
      || config.dataset_path
      || summary.dataset_path
      || summaryJson.dataset_path
      || ""
  ).trim();
}

function rememberBenchmarkRecord(record = {}, format = "") {
  const normalized = normalizeDatasetFormat(format || benchmarkFormatFromRecord(record));
  if (!normalized || normalized === "locomo") return normalized;
  const view = viewForDatasetFormat(normalized, "");
  const path = benchmarkDatasetPathFromRecord(record) || activeDatasetPathForFormat(normalized);
  rememberActiveDatasetView(view, normalized, path);
  return normalized;
}

function datasetFormatForView(viewId = "") {
  return normalizeDatasetFormat(DATASET_VIEW_FORMATS[viewId] || "");
}

function isStandaloneBenchmarkView(viewId = "") {
  const format = datasetFormatForView(viewId);
  return Boolean(format && format !== "locomo");
}

function rememberActiveDatasetView(viewId = "", format = "", path = "") {
  const normalized = normalizeDatasetFormat(format || datasetFormatForView(viewId));
  if (!normalized) return normalized;
  const previousFormat = state.activeDatasetFormat;
  state.activeDatasetFormat = normalized;
  const targetView = viewForDatasetFormat(normalized, viewId);
  state.activeBenchmarkView = targetView || viewId;
  const selectedPath = String(
    path
      || activeDatasetPathForFormat(normalized)
      || (previousFormat === normalized ? state.activeDatasetPath : "")
      || ""
  ).trim();
  state.activeDatasetPath = selectedPath;
  if (normalized !== "locomo") {
    if (selectedPath) setBenchmarkDatasetInput(normalized, selectedPath);
    saveLastBenchmarkDataset({path: selectedPath, format: normalized, view: state.activeBenchmarkView});
  }
  return normalized;
}

function restoreBenchmarkDatasetForView(viewId = "") {
  const normalized = datasetFormatForView(viewId);
  if (!normalized || normalized === "locomo") return "";
  const saved = readLastBenchmarkDataset();
  const savedFormat = normalizeDatasetFormat(saved.format || "");
  const savedPath = savedFormat === normalized ? String(saved.path || "") : "";
  const existingPath = activeDatasetPathForFormat(normalized);
  const path = savedPath || existingPath;
  if (path) setBenchmarkDatasetInput(normalized, path);
  rememberActiveDatasetView(viewId, normalized, path);
  return path;
}

function preferredBenchmarkFallback(fallback = "runsView") {
  const activeView = document.body?.dataset?.activeView || "";
  if (isStandaloneBenchmarkView(activeView)) return activeView;
  if (isStandaloneBenchmarkView(state.activeBenchmarkView)) return state.activeBenchmarkView;
  return fallback;
}

function nonLocomoTaskFallbackView(fallback = "runsView") {
  return preferredBenchmarkFallback(isStandaloneBenchmarkView(fallback) ? fallback : "runsView");
}

function benchmarkFormatFromRecord(record = {}, fallback = "") {
  const haystack = [
    record.dataset_format,
    record.format,
    record.output_file,
    record.run_dir,
    record.name,
    record.id,
    record.kind,
    record.agent_type,
  ].map((value) => String(value || "")).join(" ");
  const fallbackFormat = normalizeDatasetFormat(fallback);
  const explicit = runDatasetFormat(record)
    || taskDatasetFormat(record, "");
  const inferred = inferDatasetFormatFromText(haystack)
    || genericBenchmarkFormatFromText(haystack);
  return (explicit && explicit !== "generic" ? explicit : "")
    || (fallbackFormat && fallbackFormat !== "generic" ? fallbackFormat : "")
    || (inferred && inferred !== "generic" ? inferred : "")
    || explicit
    || inferred
    || fallbackFormat;
}

function fallbackDatasetFormatForRecord(record = {}, fallback = "") {
  const haystack = [
    record.output_file,
    record.run_dir,
    record.name,
    record.id,
    record.kind,
    record.agent_type,
  ].map((value) => String(value || "")).join(" ");
  const explicit = runDatasetFormat(record);
  const inferred = inferDatasetFormatFromText(haystack)
    || genericBenchmarkFormatFromText(haystack);
  return (explicit && explicit !== "generic" ? explicit : "")
    || inferred
    || (explicit === "generic" ? explicit : "")
    || normalizeDatasetFormat(fallback)
    || (looksNonLocomoArtifact(haystack) ? "generic" : "")
    || (looksLocomoArtifact(haystack) ? "locomo" : "");
}

function taskView(task = {}, fallback = "runsView") {
  const format = benchmarkFormatFromRecord(task, task?.id ? state.taskDatasetFormats[task.id] : "");
  if (isMemoryImportKind(task.kind || "")) return "openvikingView";
  if (task.kind === "judge") return "judgeView";
  if ((task.kind === "openviking_generic_qa" || task.kind === "echomemory_generic_qa") && (!format || format === "generic")) return nonLocomoTaskFallbackView(fallback);
  return viewForDatasetFormat(format, fallback);
}

function benchmarkViewForTask(task = {}, fallback = "runsView") {
  const format = benchmarkFormatFromRecord(task, task?.id ? state.taskDatasetFormats[task.id] : "");
  const view = viewForDatasetFormat(format, "");
  if (view) return view;
  if ((task.kind || "") === "openviking_generic_qa" || (task.kind || "") === "echomemory_generic_qa") return nonLocomoTaskFallbackView(fallback);
  return fallback;
}

function rememberTaskDatasetFormat(taskId, format) {
  const normalized = normalizeDatasetFormat(format);
  if (taskId && normalized) state.taskDatasetFormats[taskId] = normalized;
  return normalized;
}

function enrichTaskDatasetFormat(task = {}, fallback = "") {
  const taskId = task?.id || "";
  const format = taskDatasetFormat(task || {}, fallback || (taskId ? state.taskDatasetFormats[taskId] : ""));
  rememberTaskDatasetFormat(taskId, format);
  return format;
}

function looksNonLocomoArtifact(value) {
  const text = String(value || "").toLowerCase();
  return /(longmem|evolvingevents|hotpot|proagent|tau2|tau-bench|adapter|openviking_generic_qa|generic_qa|generic-qa)/.test(text);
}

function genericBenchmarkFormatFromText(...values) {
  const text = values.map((value) => String(value || "")).join(" ").toLowerCase();
  if (/longmem|longmemeval/.test(text)) return "longmemeval";
  if (/hotpot|hotpotqa/.test(text)) return "hotpotqa";
  if (/proagent|pro[-_ ]?agent[-_ ]?bench/.test(text)) return "proagentbench";
  if (/tau2|tau[-_ ]?bench|taubench/.test(text)) return "tau2bench";
  if (/evolvingevents|evolving[-_ ]?events/.test(text)) return "evolvingevents";
  if (/echomemory_generic_qa.*hotpot|hotpot.*echomemory_generic_qa|openviking_generic_qa.*hotpot|hotpot.*openviking_generic_qa/.test(text)) return "hotpotqa";
  if (/echomemory_generic_qa.*longmem|longmem.*echomemory_generic_qa|openviking_generic_qa.*longmem|longmem.*openviking_generic_qa/.test(text)) return "longmemeval";
  if (/echomemory_generic_qa.*evolving|evolving.*echomemory_generic_qa|openviking_generic_qa.*evolving|evolving.*openviking_generic_qa/.test(text)) return "evolvingevents";
  if (/echomemory_generic_qa.*proagent|proagent.*echomemory_generic_qa|openviking_generic_qa.*proagent|proagent.*openviking_generic_qa/.test(text)) return "proagentbench";
  if (/echomemory_generic_qa.*tau|tau.*echomemory_generic_qa|openviking_generic_qa.*tau|tau.*openviking_generic_qa/.test(text)) return "tau2bench";
  if (/openviking_generic_qa|echomemory_generic_qa|generic_qa|generic-qa/.test(text)) return "generic";
  return "";
}

function looksLocomoArtifact(value) {
  const text = String(value || "").toLowerCase();
  return /(locomo|openviking_qa|echomemory_qa|vikingbot|custom_local_agent_probe)/.test(text);
}

function summaryDatasetFormat(summary = {}) {
  const summaryJson = summary.summary_json || {};
  return normalizeDatasetFormat(summary.dataset_format || summaryJson.dataset_format || summary.format || "");
}

function taskDatasetFormat(task = {}, fallback = "") {
  const summary = task.summary || {};
  const summaryJson = summary.summary_json || {};
  const explicit = normalizeDatasetFormat(
    task.dataset_format
      || task.meta?.config?.dataset_format
      || task.meta?.config?.format
      || task.meta?.config?.adapter_format
      || summary.dataset_format
      || summaryJson.dataset_format
      || ""
  );
  const fallbackFormat = normalizeDatasetFormat(fallback);
  const inferred = genericBenchmarkFormatFromText(task.name, task.id, task.kind, task.output_file, task.run_dir)
    || inferDatasetFormatFromText(task.name, task.id, task.kind, task.output_file, task.run_dir);
  if (explicit && explicit !== "generic") return explicit;
  if (fallbackFormat && fallbackFormat !== "generic") return fallbackFormat;
  if (inferred && inferred !== "generic") return inferred;
  return explicit || inferred || fallbackFormat;
}

function runDatasetFormat(run = {}) {
  const summary = run.summary || {};
  const summaryJson = summary.summary_json || {};
  const explicit = normalizeDatasetFormat(
    run.dataset_format
      || run.meta?.config?.dataset_format
      || run.meta?.config?.format
      || run.meta?.config?.adapter_format
      || summary.dataset_format
      || summaryJson.dataset_format
      || ""
  );
  const inferred = genericBenchmarkFormatFromText(run.name, run.id, run.kind, run.agent_type, run.output_file, run.run_dir)
    || inferDatasetFormatFromText(run.name, run.id, run.kind, run.agent_type, run.output_file, run.run_dir);
  if (explicit && explicit !== "generic") return explicit;
  return inferred || explicit;
}

function displayDatasetFormatForTask(task = {}, format = "") {
  const normalized = normalizeDatasetFormat(format);
  if (normalized) return normalized;
  if (task.kind === "openviking_generic_qa" || task.kind === "echomemory_generic_qa") return "generic";
  if (isMemoryImportKind(task.kind || "") || isMemoryQaKind(task.kind || "")) return "locomo";
  return "-";
}

function taskStageLabel(kind = "", task = {}) {
  const value = String(kind || task.kind || "").trim();
  if (value === "openviking_import" || value === "echomemory_import") return "导入";
  if (value === "openviking_qa_retry_failed" || value === "openviking_qa_retry_missing") return "补跑";
  if (value === "openviking_qa" || value === "echomemory_qa" || value === "openviking_generic_qa" || value === "echomemory_generic_qa" || value === "local_agent") return "QA";
  if (value === "judge") return "判分";
  if (value === "stats") return "统计";
  if (value === "adapter") return "检查";
  const name = String(task.name || task.id || "").toLowerCase();
  if (name.includes("report")) return "报告";
  if (name.includes("judge")) return "判分";
  if (name.includes("import")) return "导入";
  if (name.includes("qa") || name.includes("eval")) return "QA";
  return "任务";
}

function taskStatusLabel(task = {}) {
  const status = String(task.status || "").toLowerCase();
  if (status === "running") return "运行中";
  if (status === "queued") return "排队中";
  if (status === "stopping") return "停止中";
  if (status === "succeeded") return "已完成";
  if (status === "failed") return "失败";
  if (status === "interrupted") return "已中断";
  return task.status || "-";
}

function taskMemoryBackend(task = {}) {
  const raw = [
    task.backend,
    task.memory_backend,
    task.meta?.config?.backend,
    task.meta?.config?.memory_backend,
    task.summary?.backend,
    task.summary?.summary_json?.backend,
    task.kind,
    task.name,
    task.id,
    task.output_file,
    task.run_dir,
  ].map((value) => String(value || "").toLowerCase()).join(" ");
  if (raw.includes("echomemory") || raw.includes("echomem")) return "echomemory";
  if (raw.includes("openviking") || raw.includes("viking")) return "openviking";
  return "";
}

function taskBackendLabel(task = {}) {
  const backend = taskMemoryBackend(task);
  return backend ? memoryBackendLabel(backend) : "";
}

function taskSampleScope(task = {}, format = "") {
  const summary = task.summary || {};
  const summaryJson = summary.summary_json || {};
  const cfg = task.meta?.config || {};
  const sample = cfg.sample || summary.sample || summaryJson.sample || "";
  const rows = summary.rows || summaryJson.count || "";
  const dataset = displayDatasetFormatForTask(task, format);
  const parts = [];
  if (dataset && dataset !== "-") parts.push(dataset);
  if (sample && sample !== "all") parts.push(sample);
  else if (sample === "all") parts.push("全部");
  if (rows) parts.push(`${rows} 行`);
  return parts.join(" · ");
}

function taskProgressLabel(task = {}) {
  const liveTask = taskWithLiveProgress(task);
  const execution = taskExecutionProgress(task, state.taskDatasetFormats[task?.id || ""] || "");
  if (execution?.total_questions) {
    const current = execution.current_question || execution.answered_questions || 0;
    return `第 ${current}/${execution.total_questions} 题`;
  }
  const progress = liveTask.progress || {};
  if (progress.total) {
    const phase = String(progress.phase || "").trim();
    const unit = String(progress.unit || "").trim();
    if (phase.startsWith("commit") && unit === "sessions") return `已归档 ${progress.current}/${progress.total} 个会话`;
    if (isMemoryImportKind(task.kind || "")) {
      if (unit === "sessions") return `已导入 ${progress.current}/${progress.total} 个会话`;
      if (unit === "messages") return `已写入 ${progress.current}/${progress.total} 条消息`;
      if (unit === "questions") return `已处理 ${progress.current}/${progress.total} 题`;
      return `已完成 ${progress.current}/${progress.total}`;
    }
    if (unit === "questions") return `已回答 ${progress.current}/${progress.total} 题`;
    return `已完成 ${progress.current}/${progress.total}`;
  }
  const summary = task.summary || {};
  if (summary.rows != null) return `结果 ${summary.rows} 行`;
  return taskStatusLabel(task);
}

function taskAuthoritativeProgressNote(task = {}, format = "") {
  const resolvedFormat = format || state.taskDatasetFormats[task?.id || ""] || "";
  const execution = taskExecutionProgress(task, resolvedFormat);
  if (!execution?.total_questions) return "";
  const progress = taskWithLiveProgress(task).progress || {};
  const progressScope = progress.total ? `${Number(progress.current || 0)}/${Number(progress.total || 0)}` : "";
  const authoritativeScope = `${execution.current_question || execution.answered_questions || 0}/${execution.total_questions}`;
  return progressScope && progressScope !== authoritativeScope ? `日志权威 ${authoritativeScope}` : "";
}

function taskLiveBenchmarkSummaryLabel(task = {}, format = "") {
  if (!task?.id) return "";
  const normalized = normalizeDatasetFormat(format || taskDatasetFormat(task, state.taskDatasetFormats[task.id] || ""));
  if (!isGenericBenchmarkQaTask(task, normalized)) return "";
  const summary = state.runningBenchmarkSummaries[task.id] || {};
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const avgQaTime = summary.avg_qa_time_s ?? summaryJson.avg_qa_time_s ?? summary.avg_time;
  const avgMemoryInjectionTime = summary.avg_memory_injection_time_s ?? summaryJson.avg_memory_injection_time_s;
  const parts = [];
  if (rows > 0) parts.push(`rows ${formatInt(rows)}`);
  if (avgQaTime != null) parts.push(`QA ${formatSecondsMetric(avgQaTime)}`);
  return parts.join(" · ");
}

function benchmarkQuestionScope(task = {}, format = "") {
  const resolvedFormat = format || state.taskDatasetFormats[task?.id || ""] || "";
  const execution = taskExecutionProgress(task, resolvedFormat);
  if (execution?.total_questions) {
    return {
      current: Number(execution.current_question || execution.answered_questions || 0),
      answered: Number(execution.answered_questions || 0),
      total: Number(execution.total_questions || 0),
    };
  }
  const progress = taskWithLiveProgress(task).progress || {};
  if (String(progress.unit || "") !== "questions") return null;
  const current = Number(progress.current || 0);
  const total = Number(progress.total || 0);
  const answered = String(progress.phase || "") === "qa"
    ? current
    : (current > 0 ? Math.max(0, current - 1) : 0);
  if (!current && !answered && !total) return null;
  return {current, answered, total};
}

function benchmarkProgressDetail(task = {}, format = "") {
  const progress = taskWithLiveProgress(task).progress || {};
  const raw = String(progress.detail || "").trim();
  if (!raw) return "";
  const detail = normalizeVisibleMemoryBackendName(raw).trim();
  const resolvedFormat = format || state.taskDatasetFormats[task?.id || ""] || "";
  if (!isGenericBenchmarkQaTask(task, resolvedFormat)) return detail;
  const scope = benchmarkQuestionScope(task, resolvedFormat);
  const match = detail.match(/^(.+?)\s+question\s+(\d+)\s*\/\s*(\d+)(?:\s*[·: -]\s*(.*))?$/i);
  if (!match) return detail;
  const detailCurrent = Number(match[2] || 0);
  const detailTotal = Number(match[3] || 0);
  const remainder = String(match[4] || "").trim();
  const current = Number(scope?.current || progress.current || 0);
  const total = Number(scope?.total || progress.total || 0);
  const sameCurrent = current > 0 && detailCurrent === current;
  const suspiciousTotal = total > 0 && detailTotal > 0 && total > detailTotal;
  const sameTotal = total > 0 && detailTotal === total;
  if (sameCurrent && (sameTotal || suspiciousTotal || String(progress.unit || "") === "questions")) {
    return remainder;
  }
  if (suspiciousTotal) return remainder;
  return detail;
}

function benchmarkCurrentQuestionLabel(task = {}) {
  const progress = taskWithLiveProgress(task).progress || {};
  const scopeInfo = benchmarkQuestionScope(task) || {};
  const current = Number(scopeInfo.current || progress.current || 0);
  const total = Number(scopeInfo.total || progress.total || 0);
  const scope = current && total ? `${current}/${total}` : "";
  const detail = benchmarkProgressDetail(task);
  const importSession = String(progress.current_import?.session || "").trim();
  const importSample = importSession && importSession.includes("/") ? importSession.split("/").pop() : importSession;
  if (importSample) return [scope, importSample].filter(Boolean).join(" · ");
  if (detail) return scope ? `${scope} · ${detail}` : detail;
  return scope;
}

function benchmarkCurrentImportLabel(task = {}) {
  const progress = taskWithLiveProgress(task).progress || {};
  const currentImport = progress.current_import || {};
  const session = String(currentImport.session || progress.session_label || "").trim();
  const sample = session && session.includes("/") ? session.split("/").pop() : session;
  const index = Number(currentImport.message_index || currentImport.index || 0);
  const total = Number(currentImport.message_total || currentImport.total || 0);
  const step = index && total ? `${index}/${total}` : "";
  const note = String(currentImport.note || (!currentImport.session && progress.indeterminate ? benchmarkProgressDetail(task) : "") || "").trim();
  const parts = [];
  if (sample) parts.push(sample);
  if (step) parts.push(step);
  if (note) parts.push(note);
  return parts.join(" · ");
}

function parseActiveTaskQuestionDetail(detailText = "") {
  const firstLine = String(detailText || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean)[0] || "";
  if (!firstLine) return {questionId: "", question: ""};
  const match = firstLine.match(/^(\S+)\s+(.+)$/);
  if (!match) return {questionId: "", question: firstLine};
  const candidateId = String(match[1] || "").trim();
  const candidateQuestion = String(match[2] || "").trim();
  if (!candidateQuestion) return {questionId: "", question: firstLine};
  if (!/qa\d+|conv-\d+|sample|longmem|evolving|hotpot|tau|proagent/i.test(candidateId)) {
    return {questionId: "", question: firstLine};
  }
  return {questionId: candidateId, question: candidateQuestion};
}

function activeTaskQaPreviewCacheKey(task = {}, questionId = "") {
  const outputFile = String(task.output_file || "").trim();
  if (!outputFile) return "";
  const taskId = String(task.id || "").trim() || outputFile;
  return `${taskId}::${outputFile}::${String(questionId || "").trim()}`;
}

function normalizeActiveTaskQaPreviewRow(row = {}, outputFile = "") {
  if (!row || typeof row !== "object") return null;
  const answer = [
    row.response,
    row.hypothesis,
    row.prediction,
    row.model_answer,
    row.model_response,
  ].map((value) => String(value || "").trim()).find(Boolean) || "";
  const question = String(row.question || "").trim();
  const questionId = String(row.question_id || row.sample_id || "").trim();
  const resultPath = String(outputFile || row.output_file || "").trim();
  if (!question && !answer && !questionId && !resultPath) return null;
  return {questionId, question, answer, resultPath};
}

async function ensureActiveTaskQaPreview(task = {}, questionId = "") {
  const cacheKey = activeTaskQaPreviewCacheKey(task, questionId);
  if (!cacheKey || !task.output_file || !questionId) return cacheKey ? (state.activeTaskQaPreview[cacheKey] || null) : null;
  const now = Date.now();
  const fetchedAt = Number(state.activeTaskQaPreviewFetchedAt[cacheKey] || 0);
  if (state.activeTaskQaPreviewLoading[cacheKey]) return state.activeTaskQaPreview[cacheKey] || null;
  if (fetchedAt && now - fetchedAt < 2500) return state.activeTaskQaPreview[cacheKey] || null;
  state.activeTaskQaPreviewLoading[cacheKey] = true;
  try {
    const data = await api(`/api/question-detail?path=${encodeURIComponent(task.output_file)}&question_id=${encodeURIComponent(questionId)}`);
    const preview = normalizeActiveTaskQaPreviewRow(data?.row || {}, task.output_file);
    if (preview) state.activeTaskQaPreview[cacheKey] = preview;
    state.activeTaskQaPreviewFetchedAt[cacheKey] = Date.now();
    return state.activeTaskQaPreview[cacheKey] || preview || null;
  } catch {
    state.activeTaskQaPreviewFetchedAt[cacheKey] = Date.now();
    return state.activeTaskQaPreview[cacheKey] || null;
  } finally {
    delete state.activeTaskQaPreviewLoading[cacheKey];
  }
}

function parseTaskTimestamp(value) {
  if (value == null || value === "") return 0;
  if (typeof value === "number") return value > 100000000000 ? value / 1000 : value;
  const text = String(value).trim();
  if (!text) return 0;
  const numeric = Number(text);
  if (Number.isFinite(numeric)) return numeric > 100000000000 ? numeric / 1000 : numeric;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function stampTaskSnapshot(task) {
  if (!task || typeof task !== "object") return task;
  const snapshot = {...task, __snapshot_received_at: Date.now() / 1000};
  const taskId = String(snapshot.id || "").trim();
  const status = String(snapshot.status || "").toLowerCase();
  if (taskId) {
    if (snapshot.progress && typeof snapshot.progress === "object") {
      state.taskProgressSnapshots[taskId] = snapshot.progress;
    } else if (status === "running" && state.taskProgressSnapshots[taskId]) {
      snapshot.progress = state.taskProgressSnapshots[taskId];
      snapshot.__progress_fallback = true;
    }
  }
  if (taskId) {
    if (status && status !== "running") {
      delete state.taskStopOverrides[taskId];
    } else {
      const overrideAt = Number(state.taskStopOverrides[taskId] || 0);
      if (overrideAt > 0) {
        if ((Date.now() - overrideAt) <= TASK_STOP_OVERRIDE_TTL_MS) {
          snapshot.status = "stopping";
          snapshot.__stop_override = true;
        } else {
          delete state.taskStopOverrides[taskId];
        }
      }
    }
  }
  return snapshot;
}

function taskSnapshotAgeSeconds(task = {}) {
  const snapshotAt = Number(task.__snapshot_received_at || 0);
  return snapshotAt > 0 ? Math.max(0, Date.now() / 1000 - snapshotAt) : 0;
}

function taskLiveElapsedSeconds(task = {}) {
  const progress = task.progress || {};
  const fallback = task.duration ?? progress.elapsed_seconds ?? 0;
  if (task.status !== "running") return Number(fallback || 0);
  const start = parseTaskTimestamp(task.started_at || task.created_at);
  const live = start ? (Date.now() / 1000 - start) : Number(fallback || 0) + taskSnapshotAgeSeconds(task);
  return Math.max(0, Number(fallback || 0), live);
}

function taskLiveEtaSeconds(task = {}, elapsedSeconds = taskLiveElapsedSeconds(task)) {
  const progress = task.progress || {};
  if (task.status !== "running") return progress.eta_seconds ?? null;
  if (progress.eta_seconds != null) {
    return Math.max(0, Number(progress.eta_seconds || 0) - taskSnapshotAgeSeconds(task));
  }
  const total = Number(progress.total || 0);
  const current = Number(progress.current || 0);
  if (total > 0 && current > 0 && current < total) {
    return Math.max(0, (elapsedSeconds / current) * (total - current));
  }
  return null;
}

const taskProgressTotalHints = Object.create(null);

function readTaskProgressTotalHint(taskId = "") {
  const key = String(taskId || "").trim();
  if (!key || typeof localStorage === "undefined") return 0;
  try {
    return Number(localStorage.getItem(`${TASK_PROGRESS_TOTAL_HINT_PREFIX}${key}`) || 0);
  } catch {
    return 0;
  }
}

function writeTaskProgressTotalHint(taskId = "", total = 0) {
  const key = String(taskId || "").trim();
  const value = Number(total || 0);
  if (!key || value <= 0 || typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(`${TASK_PROGRESS_TOTAL_HINT_PREFIX}${key}`, String(value));
  } catch {}
}

function stabilizeTaskProgress(task = {}, progress = {}) {
  const next = {...(progress || {})};
  const taskId = String(task?.id || "").trim();
  const current = Number(next.current || 0);
  const reportedTotal = Number(next.total || 0);
  const configuredCount = Number(task?.meta?.config?.count || 0);
  let hintedTotal = Number(taskProgressTotalHints[taskId] || 0);
  hintedTotal = Math.max(hintedTotal, readTaskProgressTotalHint(taskId));
  if (configuredCount > 0) hintedTotal = Math.max(hintedTotal, configuredCount);
  if (reportedTotal > hintedTotal) hintedTotal = reportedTotal;
  if (taskId) {
    taskProgressTotalHints[taskId] = hintedTotal;
    writeTaskProgressTotalHint(taskId, hintedTotal);
  }
  const suspiciousShrink = reportedTotal > 0 && hintedTotal > reportedTotal && current >= reportedTotal;
  if (configuredCount === 0 && suspiciousShrink) {
    next.total = hintedTotal;
  } else if (configuredCount > 0) {
    next.total = Math.max(reportedTotal, configuredCount, hintedTotal);
  }
  return next;
}

function taskWithLiveProgress(task = {}) {
  if (!task || typeof task !== "object") return task || {};
  const liveTask = {...task};
  const format = taskDatasetFormat(task, task?.id ? (state.taskDatasetFormats[task.id] || "") : "");
  const taskId = String(task?.id || "").trim();
  const baseProgress = task.progress || (taskId ? state.taskProgressSnapshots[taskId] : null);
  const progress = baseProgress ? stabilizeTaskProgress(task, baseProgress) : null;
  if (taskId && progress) state.taskProgressSnapshots[taskId] = progress;
  const execution = progress
    ? taskExecutionProgress(task, format)
    : null;
  if (task.status === "running") {
    const elapsed = taskLiveElapsedSeconds(task);
    liveTask.duration = elapsed;
    if (progress) {
      if (execution?.total_questions) {
        progress.total = Math.max(Number(progress.total || 0), Number(execution.total_questions || 0));
        progress.current = Math.max(Number(progress.current || 0), Number(execution.current_question || execution.answered_questions || 0));
        if (execution.status_stage) {
          if (/import/i.test(execution.status_stage)) progress.phase = "import";
          else if (/qa/i.test(execution.status_stage)) progress.phase = "qa";
          else if (/judge/i.test(execution.status_stage)) progress.phase = "judge";
        }
        if (execution.status_sample && !progress.current_import?.session) {
          progress.current_import = {
            ...(progress.current_import || {}),
            session: `hotpotqa/${execution.status_sample}`,
          };
        }
      }
      progress.elapsed_seconds = elapsed;
      const eta = taskLiveEtaSeconds(task, elapsed);
      if (eta != null) progress.eta_seconds = eta;
      if (progress.total) {
        progress.pct = Math.max(0, Math.min(100, (Number(progress.current || 0) / Number(progress.total || 1)) * 100));
      }
      liveTask.progress = progress;
    }
  }
  return liveTask;
}

function isGenericBenchmarkQaTask(task = {}, format = "") {
  const normalized = normalizeDatasetFormat(format || taskDatasetFormat(task, ""));
  const kind = String(task?.kind || "").trim();
  return normalized && normalized !== "locomo" && (kind === "openviking_generic_qa" || kind === "echomemory_generic_qa");
}

function parseGenericBenchmarkExecutionProgress(logText = "", task = {}, seed = {}) {
  const lines = String(logText || "").split(/\r?\n/);
  let importIndex = Number(seed.import_index || 0);
  let importTotal = Number(seed.import_total || 0);
  let qaIndex = Number(seed.qa_index || 0);
  let qaTotal = Number(seed.qa_total || 0);
  for (const line of lines) {
    let match = line.match(/\[import\]\s+(\d+)\/(\d+)\s+/);
    if (match) {
      importIndex = Number(match[1] || 0);
      importTotal = Number(match[2] || 0);
    }
    match = line.match(/\[qa\]\s+(\d+)\/(\d+)\s+/);
    if (match) {
      qaIndex = Number(match[1] || 0);
      qaTotal = Number(match[2] || 0);
    }
  }
  const configuredTotal = Number(task?.meta?.config?.count || task?.config?.count || 0);
  const totalQuestions = qaTotal || importTotal || configuredTotal || 0;
  const currentQuestion = Math.max(importIndex, qaIndex, 0);
  const answeredQuestions = Math.max(0, Math.min(qaIndex || 0, totalQuestions || qaIndex || 0));
  if (!totalQuestions && !currentQuestion && !answeredQuestions) return null;
  const pctBase = totalQuestions > 0 ? (Math.max(currentQuestion, answeredQuestions) / totalQuestions) * 100 : 0;
  return {
    current_question: currentQuestion,
    answered_questions: answeredQuestions,
    total_questions: totalQuestions || Math.max(currentQuestion, answeredQuestions),
    import_index: importIndex,
    import_total: importTotal,
    qa_index: qaIndex,
    qa_total: qaTotal,
    pct: Math.max(0, Math.min(100, Number(pctBase.toFixed(1)))),
    source: "log_tail",
  };
}

function genericBenchmarkStatusFile(task = {}) {
  if (task.output_file) return `${dirname(task.output_file)}/generic_qa_status.json`;
  if (task.run_dir) return `${task.run_dir}/generic_qa_status.json`;
  return "";
}

async function loadGenericBenchmarkExecutionStatus(task = {}) {
  const path = genericBenchmarkStatusFile(task);
  if (!path) return null;
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  const payload = JSON.parse(data?.text || "{}");
  if (!payload || typeof payload !== "object") return null;
  const current = Number(payload.job_index || 0);
  const total = Number(payload.job_total || 0);
  if (!current && !total) return null;
  const stage = String(payload.stage || "").trim();
  const answered = /^qa|judge|completed/i.test(stage) ? current : Math.max(0, current - 1);
  const pctBase = total > 0 ? (Math.max(current, answered) / total) * 100 : 0;
  return {
    current_question: current,
    answered_questions: answered,
    total_questions: total || Math.max(current, answered),
    status_stage: stage,
    status_sample: String(payload.sample || payload.question_id || "").trim(),
    source: "generic_qa_status_json",
    pct: Math.max(0, Math.min(100, Number(pctBase.toFixed(1)))),
  };
}

function taskExecutionProgress(task = {}, format = "") {
  if (!task?.id) return null;
  if (!isGenericBenchmarkQaTask(task, format)) return null;
  const cached = state.taskExecutionProgress[task.id] || {};
  const summary = task.summary || {};
  const summaryJson = summary.summary_json || {};
  const summaryRows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const configuredTotal = Number(task?.meta?.config?.count || task?.config?.count || 0);
  const progress = task.progress || {};
  const progressUnit = String(progress.unit || "");
  const progressPhase = String(progress.phase || "");
  const progressCurrent = Number(progress.current || 0);
  const progressTotal = Number(progress.total || 0);
  const totalQuestions = Math.max(
    Number(cached.total_questions || 0),
    Number(cached.status_total_questions || 0),
    progressUnit === "questions" ? progressTotal : 0,
    configuredTotal,
    summaryRows,
    Number(cached.qa_total || 0),
    Number(cached.import_total || 0),
  );
  const progressAnswered = progressUnit === "questions"
    ? (
      progressPhase === "qa"
        ? progressCurrent
        : (task.status === "running" && progressCurrent > 0 ? Math.max(0, progressCurrent - 1) : progressCurrent)
    )
    : 0;
  if (task?.id && totalQuestions > 0) {
    taskProgressTotalHints[task.id] = Math.max(Number(taskProgressTotalHints[task.id] || 0), totalQuestions);
    writeTaskProgressTotalHint(task.id, totalQuestions);
  }
  const answeredQuestions = Math.max(
    Number(cached.answered_questions || 0),
    Number(cached.status_answered_questions || 0),
    summaryRows,
    progressAnswered
  );
  const currentQuestion = Math.max(
    Number(cached.current_question || 0),
    Number(cached.status_current_question || 0),
    progressUnit === "questions" ? progressCurrent : 0,
    answeredQuestions
  );
  if (!totalQuestions && !currentQuestion && !answeredQuestions) return null;
  const pctBase = totalQuestions > 0 ? (Math.max(currentQuestion, answeredQuestions) / totalQuestions) * 100 : 0;
  return {
    ...cached,
    current_question: currentQuestion,
    answered_questions: answeredQuestions,
    total_questions: totalQuestions || Math.max(currentQuestion, answeredQuestions),
    pct: Math.max(0, Math.min(100, Number(pctBase.toFixed(1)))),
  };
}

async function ensureGenericBenchmarkExecutionProgress(task = {}, format = "") {
  if (!task?.id || !isTaskActive(task) || !isGenericBenchmarkQaTask(task, format)) return null;
  const taskId = task.id;
  const logPath = String(task.log_file || "").trim();
  if (!logPath) return null;
  const now = Date.now();
  const lastFetchedAt = Number(state.taskExecutionProgressFetchedAt[taskId] || 0);
  const lastStatusFetchedAt = Number(state.taskExecutionStatusFetchedAt[taskId] || 0);
  if (state.taskExecutionProgressLoading[taskId]) return state.taskExecutionProgress[taskId] || null;
  if (lastFetchedAt && lastStatusFetchedAt && now - Math.min(lastFetchedAt, lastStatusFetchedAt) < 2500) return state.taskExecutionProgress[taskId] || null;
  state.taskExecutionProgressLoading[taskId] = true;
  state.taskExecutionProgressFetchedAt[taskId] = now;
  try {
    const cached = state.taskExecutionProgress[taskId] || null;
    const statusSnapshot = await loadGenericBenchmarkExecutionStatus(task).catch(() => null);
    state.taskExecutionStatusFetchedAt[taskId] = Date.now();
    const previousOffset = Number(state.taskExecutionProgressOffsets[taskId] || 0);
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/log?offset=${previousOffset > 0 ? previousOffset : 0}`);
    if (Number.isFinite(Number(data.offset))) {
      state.taskExecutionProgressOffsets[taskId] = Number(data.offset || 0);
    }
    let parsed = parseGenericBenchmarkExecutionProgress(data.text || "", task, cached || {});
    if (!parsed && !cached) {
      const fallback = await api(`/api/log-tail?path=${encodeURIComponent(logPath)}&limit=240000`);
      parsed = parseGenericBenchmarkExecutionProgress(fallback.text || "", task, cached || {});
    }
    const merged = {
      ...(parsed || cached || {}),
      ...(statusSnapshot ? {
        status_current_question: Number(statusSnapshot.current_question || 0),
        status_answered_questions: Number(statusSnapshot.answered_questions || 0),
        status_total_questions: Number(statusSnapshot.total_questions || 0),
        status_stage: statusSnapshot.status_stage || "",
        status_sample: statusSnapshot.status_sample || "",
      } : {}),
    };
    if (parsed || statusSnapshot) state.taskExecutionProgress[taskId] = merged;
    return state.taskExecutionProgress[taskId] || parsed || null;
  } catch {
    return state.taskExecutionProgress[taskId] || null;
  } finally {
    delete state.taskExecutionProgressLoading[taskId];
  }
}

function taskDisplayTitle(task = {}, format = "") {
  const stage = taskStageLabel(task.kind || "", task);
  const backend = taskBackendLabel(task);
  const scope = taskSampleScope(task, format);
  return [stage, backend, scope].filter(Boolean).join(" · ");
}

function isLocomoRunRecord(run = {}) {
  const kind = String(run.kind || "").trim();
  if (["judge", "stats", "adapter"].includes(kind)) return false;
  const format = runDatasetFormat(run);
  if (format) return format === "locomo";
  const haystack = `${run.name || ""} ${run.id || ""} ${run.kind || ""} ${run.agent_type || ""} ${run.output_file || ""} ${run.run_dir || ""}`;
  if (/(chenmo|陈默)/i.test(haystack)) return false;
  if (looksNonLocomoArtifact(haystack)) return false;
  return looksLocomoArtifact(haystack)
    || run.kind === "openviking_import"
    || run.kind === "echomemory_import"
    || run.kind === "openviking_qa"
    || run.kind === "echomemory_qa"
    || run.agent_type === "native_vikingbot_cli"
    || run.agent_type === "echomemory_memory_qa"
    || run.agent_type === "openviking_memory_qa"
    || run.agent_type === "openviking_commit_import";
}

function isLocomoTaskOutput(kind, task = {}, fallbackFormat = "") {
  const taskKind = task.kind || kind || "";
  if (isMemoryImportKind(taskKind) || taskKind === "adapter") return false;
  const format = taskDatasetFormat(task, fallbackFormat);
  if (format) return format === "locomo";
  if (taskKind === "openviking_generic_qa" || taskKind === "echomemory_generic_qa") return false;
  const haystack = `${task.name || ""} ${task.id || ""} ${taskKind} ${task.output_file || ""} ${task.run_dir || ""}`;
  if (looksNonLocomoArtifact(haystack)) return false;
  return looksLocomoArtifact(haystack) || isMemoryQaKind(taskKind);
}

function markLocomoOutputFile(outputFile) {
  if (!outputFile) return "";
  const previousOutputFile = state.outputFile || "";
  state.outputFile = outputFile;
  state.outputDatasetFormat = "locomo";
  state.selectedRunDatasetFormat = "locomo";
  currentEvidenceScope(outputFile);
  const judgeInput = $("judgeInput");
  if (judgeInput) {
    const activeView = document.querySelector(".view-panel.active")?.id || "";
    const currentValue = judgeInput.value.trim();
    const manualJudgeInput = activeView === "judgeView"
      && currentValue
      && currentValue !== previousOutputFile
      && currentValue !== outputFile;
    if (!manualJudgeInput) judgeInput.value = outputFile;
  }
  renderJudgeReadinessPanel();
  return outputFile;
}

function markDatasetOutputFile(outputFile, format = "") {
  const normalized = normalizeDatasetFormat(format);
  if (normalized === "locomo") return markLocomoOutputFile(outputFile);
  state.outputFile = outputFile || "";
  state.outputDatasetFormat = normalized;
  state.selectedRunDatasetFormat = normalized;
  if (normalized) rememberActiveDatasetView(viewForDatasetFormat(normalized, preferredBenchmarkFallback("runsView")), normalized);
  if (normalized && normalized !== "locomo" && $("judgeInput")) {
    $("judgeInput").value = "";
  }
  if (state.lastValidation && normalized !== "locomo") state.lastValidation = null;
  return outputFile;
}

function clearLocomoResultState() {
  state.outputFile = "";
  state.outputDatasetFormat = "";
  state.selectedRunDir = "";
  state.selectedRunDatasetFormat = "";
  state.selectedRunRecord = null;
  state.selectedRunSummary = null;
  state.lastValidation = null;
  state.lastJudgeValidation = null;
  state.lastJudgeSummary = null;
  state.lastReportFile = "";
  state.judgeConfirmInput = "";
  if ($("judgeInput")) $("judgeInput").value = "";
  updateJudgeAndReportActionButtons({input: "", judgeRunning: false});
}

function currentLocomoResultCsv() {
  const input = ($("judgeInput")?.value || "").trim() || state.outputFile || "";
  if (!input) return "";
  if (state.outputDatasetFormat === "locomo") return input;
  if (state.selectedRunDatasetFormat && state.selectedRunDatasetFormat !== "locomo") return "";
  if (looksNonLocomoArtifact(input)) return "";
  if (looksLocomoArtifact(input)) return input;
  return currentLocomoDataset() ? input : "";
}

function latestLocomoQaRun(runs = []) {
  const scoped = currentAccountOnlyEnabled("runsCurrentAccountOnly")
    ? runs.filter(matchesCurrentAccount)
    : runs;
  const pool = scoped.length ? scoped : runs;
  return pool.find((run) => isRecentLocomoQaRun(run) && run.output_file)
    || pool.find((run) => isLocomoTaskOutput(run.kind || "", run, normalizeDatasetFormat(run.dataset_format || runDatasetFormat(run) || "")) && run.output_file)
    || null;
}

async function ensureCurrentLocomoResultInput(options = {}) {
  const {forceRuns = false} = options;
  const currentTask = state.currentLocomoTask;
  if (currentTask?.output_file) {
    const format = enrichTaskDatasetFormat(currentTask, state.taskDatasetFormats[currentTask.id] || currentTask.dataset_format || "");
    if (isLocomoTaskOutput(currentTask.kind || "", currentTask, format)) {
      return markLocomoOutputFile(currentTask.output_file);
    }
  }
  const current = currentLocomoResultCsv();
  if (current) return current;
  const runs = await ensureRecentRunsLoaded(Boolean(forceRuns)).catch(() => []);
  const latestRun = latestLocomoQaRun(runs);
  if (latestRun?.output_file) {
    return markLocomoOutputFile(latestRun.output_file);
  }
  return "";
}

function readAccountList() {
  try {
    const list = JSON.parse(localStorage.getItem(ACCOUNT_LIST_KEY) || "[]");
    return Array.isArray(list) && list.length ? list : ["default"];
  } catch {
    return ["default"];
  }
}

function saveAccountList(list) {
  const normalized = [...new Set((list || []).map((item) => String(item || "").trim()).filter(Boolean))];
  const finalList = normalized.length ? normalized : ["default"];
  localStorage.setItem(ACCOUNT_LIST_KEY, JSON.stringify(finalList));
  state.accounts = finalList;
  return finalList;
}

function currentAccount() {
  return ($("accountSelect")?.value || $("ovAccount")?.value || state.currentAccount || "default").trim() || "default";
}

function accountConfigKey(account) {
  return `${ACCOUNT_CONFIG_PREFIX}${account || "default"}`;
}

function isLegacyFixedWorkspace(workspace) {
  const value = String(workspace || "").replace(/\/+$/, "");
  const name = value.split("/").pop() || "";
  const retiredMarker = "hi" + "go";
  return /\/openviking_workspace_new0420$/i.test(value) || (/workspace/i.test(name) && name.toLowerCase().includes(retiredMarker));
}

function workspacePrefixForBackend(backend = currentMemoryBackend()) {
  const normalized = normalizeMemoryBackend(backend);
  if (normalized === "echomemory") return "echomem_workspace";
  return "openviking_workspace";
}

function timestampWorkspaceForAccount(account, backend = currentMemoryBackend()) {
  const base = state.config?.home || "~";
  return `${base}/${workspacePrefixForBackend(backend)}_${safeAccountSlug(account)}_${slugTime()}`;
}

function isGeneratedMemoryWorkspace(workspace) {
  const name = String(workspace || "").split("/").pop() || "";
  return /^(openviking|echomem)_workspace_[A-Za-z0-9_.-]+_\d{8}[-_]\d{6}/.test(name);
}

function normalizeAccountWorkspaceConfig(account, config = {}, {forceNew = false} = {}) {
  const next = {...config};
  const workspace = String(next.ovWorkspace || next.memoryWorkspace || "").trim();
  if (forceNew || !workspace || isLegacyFixedWorkspace(workspace)) {
    const generated = timestampWorkspaceForAccount(account, next.memoryBackend || "openviking");
    next.ovWorkspace = generated;
    next.memoryWorkspace = generated;
    next.workspace_source = forceNew ? "generated_timestamp" : (workspace ? "migrated_legacy_fixed_workspace" : "generated_missing_workspace");
  } else {
    next.ovWorkspace = workspace;
    next.memoryWorkspace = workspace;
  }
  return next;
}

function readAccountConfig(account) {
  const accountId = String(account || "default").trim() || "default";
  const cached = (state.accountConfigCache || {})[accountId] || {};
  try {
    const raw = JSON.parse(localStorage.getItem(accountConfigKey(account)) || "{}");
    const normalized = normalizeAccountWorkspaceConfig(accountId, {...raw, ...cached});
    if (JSON.stringify(raw) !== JSON.stringify(normalized)) {
      localStorage.setItem(accountConfigKey(account), JSON.stringify({...normalized, saved_at: new Date().toISOString()}));
    }
    return normalized;
  } catch {
    return normalizeAccountWorkspaceConfig(accountId, cached);
  }
}

function accountRecord(account = currentAccount()) {
  const id = String(account || "default");
  return (state.accountRecords || []).find((item) => String(item.id || "") === id) || null;
}

function cacheAccountConfig(account, patch = {}) {
  const id = String(account || "default").trim() || "default";
  state.accountConfigCache = state.accountConfigCache || {};
  state.accountConfigCache[id] = {
    ...(state.accountConfigCache[id] || {}),
    ...(patch || {}),
  };
  return state.accountConfigCache[id];
}

function saveAccountConfig(account, patch = {}) {
  const key = accountConfigKey(account);
  const next = {
    ...normalizeAccountWorkspaceConfig(account || "default", {...readAccountConfig(account), ...patch}),
    saved_at: new Date().toISOString(),
  };
  cacheAccountConfig(account, next);
  localStorage.setItem(key, JSON.stringify(next));
  return next;
}

function usefulAccountConfig(config = {}) {
  return Object.entries(config).some(([key, value]) => key !== "saved_at" && String(value || "").trim());
}

function mergeBackendAccountState(data = {}) {
  const records = Array.isArray(data.accounts) ? data.accounts : [];
  state.accountRecords = records;
  const ids = records.map((item) => String(item.id || "").trim()).filter(Boolean);
  const backendIds = ids.length ? ids : ["default"];
  const backendSet = new Set(backendIds);
  const previousIds = readAccountList();
  const localOnlyIds = previousIds.filter((account) => !backendSet.has(account) && usefulAccountConfig(readAccountConfig(account)));
  const finalIds = saveAccountList([...backendIds, ...localOnlyIds]);
  records.forEach((record) => {
    if (!record?.id || !record.config) return;
    const local = readAccountConfig(record.id);
    const merged = usefulAccountConfig(local) ? {...local, ...record.config} : {...record.config};
    cacheAccountConfig(record.id, merged);
    saveAccountConfig(record.id, merged);
  });
  state.accountBackendReady = true;
  state.accountStateFile = data.state_file || "";
  const active = String(data.active_account || state.currentAccount || backendIds[0] || "default");
  if (!finalIds.includes(state.currentAccount)) state.currentAccount = active;
  renderAccountIsolationMatrix();
  renderAccountReadiness();
  updateAccountActionState();
  return data;
}

async function loadBackendAccounts(timeoutMs = 0) {
  const data = timeoutMs > 0
    ? await apiWithTimeout("/api/accounts", {}, timeoutMs)
    : await api("/api/accounts");
  return mergeBackendAccountState(data);
}

async function loadAccountConfigFromBackend(account = currentAccount()) {
  const id = String(account || "default").trim() || "default";
  const record = await api(`/api/account-config?account=${encodeURIComponent(id)}`);
  if (record?.config) {
    cacheAccountConfig(id, record.config);
    saveAccountConfig(id, record.config);
  }
  return record;
}

function currentAccountConfigPatch() {
  syncSystemModelFieldsToLegacy();
  const agentToken = $("systemAgentToken")?.value.trim() || "";
  const judgeToken = $("systemJudgeToken")?.value.trim() || $("judgeToken")?.value.trim() || "";
  const memoryToken = $("systemMemoryToken")?.value.trim() || $("ovVlmApiKey")?.value.trim() || "";
  return {
    memoryBackend: $("memoryBackendSelect")?.value || "openviking",
    ovHost: $("ovHost")?.value.trim() || "",
    ovPort: $("ovPort")?.value.trim() || "",
    ovWorkspace: $("ovWorkspace")?.value.trim() || "",
    memoryWorkspace: $("memoryWorkspace")?.value.trim() || "",
    echomemRoot: $("echomemRoot")?.value.trim() || "",
    memoryUserId: $("memoryUserId")?.value.trim() || "default",
    memoryAgentId: $("memoryAgentId")?.value.trim() || "default",
    ovApiKey: $("ovApiKey")?.value.trim() || "",
    judgeBaseUrl: $("systemJudgeBaseUrl")?.value.trim() || $("judgeBaseUrl")?.value.trim() || "",
    judgeModel: $("systemJudgeModel")?.value.trim() || $("judgeModel")?.value.trim() || "",
    agentBaseUrl: $("systemAgentBaseUrl")?.value.trim() || $("judgeBaseUrl")?.value.trim() || "",
    agentModel: $("systemAgentModel")?.value.trim() || $("judgeModel")?.value.trim() || "",
    memoryInjectBaseUrl: $("systemMemoryBaseUrl")?.value.trim() || $("ovVlmBaseUrl")?.value.trim() || "",
    memoryInjectModel: $("systemMemoryModel")?.value.trim() || $("ovVlmModel")?.value.trim() || "",
    agentToken,
    judgeToken,
    memoryInjectToken: memoryToken,
    answerTokenSet: Boolean(agentToken),
    judgeTokenSet: Boolean(judgeToken),
    echomemTokenSet: Boolean(memoryToken),
    echomemEmbeddingTokenSet: Boolean(memoryToken),
    echomemChatTokenSet: Boolean(memoryToken),
    chatTopK: $("chatTopK")?.value || "",
  };
}

function backendSafeAccountConfig(config = {}) {
  return {...(config || {})};
}

async function syncAccountConfigToBackend(account, config) {
  if (!state.accountBackendReady) return null;
  return api("/api/account-config", {
    method: "POST",
    body: JSON.stringify({account, config: backendSafeAccountConfig(config)}),
  }).then((data) => {
    const merged = mergeBackendAccountState(data);
    updateSystemConfigSummary();
    return merged;
  });
}

function syncAccountFields(account) {
  const value = account || "default";
  state.currentAccount = value;
  localStorage.setItem(ACTIVE_ACCOUNT_KEY, value);
  if ($("accountSelect")) $("accountSelect").value = value;
  if ($("accountDisplayName")) $("accountDisplayName").textContent = value;
  if ($("accountDisplayLabel")) $("accountDisplayLabel").textContent = "当前账户";
  if ($("ovAccount")) $("ovAccount").value = value;
  if ($("memoryAccount")) $("memoryAccount").value = value;
  if ($("systemAccountLabel")) $("systemAccountLabel").textContent = value;
  updateAccountActionState();
}

function setAccountActionStatus(text, tone = "") {
  const target = $("accountActionStatus");
  if (!target) return;
  target.textContent = text || "";
  target.classList.toggle("ok", tone === "ok");
  target.classList.toggle("warn", tone === "warn");
  target.classList.toggle("bad", tone === "bad");
}

function updateAccountActionState(text = "", tone = "") {
  const account = currentAccount();
  const deleteButton = $("deleteAccount");
  if (deleteButton) {
    const isDefault = account === "default";
    deleteButton.disabled = isDefault;
    deleteButton.title = isDefault ? "默认账户不能移除" : `移除 ${account} 的页面记录，不删除真实记忆目录`;
  }
  if (text) {
    setAccountActionStatus(text, tone);
    return;
  }
  const record = backendAccountRecord(account);
  const isolation = record?.isolation || {};
  if (account === "default") {
    setAccountActionStatus("", "");
  } else if (!record) {
    setAccountActionStatus("本地账户", "warn");
  } else if (isolation.status === "isolated_workspace") {
    setAccountActionStatus("独立账户", "ok");
  } else if (isolation.status === "shared_workspace") {
    setAccountActionStatus("共享目录", "warn");
  } else {
    setAccountActionStatus("", "");
  }
}

function friendlyUiError(message = "", fallback = "当前状态暂不可用") {
  const raw = String(message || "").trim();
  if (!raw) return "";
  if (/EchoMemSDK|unexpected keyword argument|agent_id|No module named|Traceback/i.test(raw)) {
    return "EchoMemory SDK 适配异常，请在日志中查看详情";
  }
  if (/arrearage|access denied|overdue-payment/i.test(raw)) {
    return "答案模型服务不可用：当前提供商账户欠费或被拒绝";
  }
  if (/无效的令牌|invalid token|incorrect api key|unauthorized|status=401/i.test(raw)) {
    return "模型鉴权失败：当前配置的 token 无效或已失效";
  }
  if (/timeout|timed out/i.test(raw)) return "请求超时，请稍后重试";
  if (/ECONN|Failed to fetch|NetworkError/i.test(raw)) return "后端连接失败，请检查服务状态";
  return raw.length > 96 ? fallback : raw;
}

function errorDetailHtml(message = "") {
  const raw = String(message || "").trim();
  if (!raw) return "";
  return `<details class="error-detail"><summary>${escapeHtml(friendlyUiError(raw))}</summary><code>${escapeHtml(raw)}</code></details>`;
}

function renderAccountSelect(account = "") {
  const list = saveAccountList(readAccountList());
  const active = account || state.currentAccount || list[0] || "default";
  if ($("accountSelect")) {
    $("accountSelect").innerHTML = list.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
    $("accountSelect").value = active;
  }
  if ($("accountDisplayName")) $("accountDisplayName").textContent = active;
  syncAccountFields(active);
}

function accountConfigDefaults() {
  return {
    memoryBackend: "openviking",
    ovHost: state.config.server_host || "127.0.0.1",
    ovPort: state.config.server_port || "19080",
    ovWorkspace: "",
    memoryWorkspace: "",
    echomemRoot: "",
    memoryUserId: "default",
    memoryAgentId: "default",
    ovApiKey: "",
    judgeBaseUrl: state.config.judge_base_url || "https://codexcs.ysaikeji.cn/v1",
    judgeModel: state.config.judge_model || "gpt-5.5",
    agentBaseUrl: state.config.judge_base_url || "https://codexcs.ysaikeji.cn/v1",
    agentModel: state.config.judge_model || "gpt-5.5",
    memoryInjectBaseUrl: state.config.vlm_base_url || "https://dashscope.aliyuncs.com/compatible-mode/v1",
    memoryInjectModel: state.config.vlm_model || "deepseek-v4-flash",
    chatTopK: "30",
  };
}

function setInputValue(id, value = "") {
  const input = $(id);
  if (input) input.value = value || "";
}

function syncSystemModelFieldsToLegacy() {
  if ($("systemJudgeBaseUrl")) setInputValue("judgeBaseUrl", $("systemJudgeBaseUrl").value.trim());
  if ($("systemJudgeModel")) setInputValue("judgeModel", $("systemJudgeModel").value.trim());
  if ($("systemJudgeToken")) setInputValue("judgeToken", $("systemJudgeToken").value.trim());
  if ($("systemMemoryBaseUrl")) setInputValue("ovVlmBaseUrl", $("systemMemoryBaseUrl").value.trim());
  if ($("systemMemoryModel")) setInputValue("ovVlmModel", $("systemMemoryModel").value.trim());
  if ($("systemMemoryToken")) setInputValue("ovVlmApiKey", $("systemMemoryToken").value.trim());
}

function agentModelConfig() {
  const cfg = readAccountConfig(currentAccount());
  return {
    baseUrl: $("systemAgentBaseUrl")?.value.trim() || cfg.agentBaseUrl || cfg.judgeBaseUrl || $("judgeBaseUrl")?.value.trim() || "",
    model: $("systemAgentModel")?.value.trim() || cfg.agentModel || cfg.judgeModel || $("judgeModel")?.value.trim() || "gpt-5.5",
    token: $("systemAgentToken")?.value.trim() || cfg.agentToken || "",
  };
}

function judgeModelConfig() {
  const cfg = readAccountConfig(currentAccount());
  return {
    baseUrl: $("systemJudgeBaseUrl")?.value.trim() || cfg.judgeBaseUrl || $("judgeBaseUrl")?.value.trim() || "",
    model: $("systemJudgeModel")?.value.trim() || cfg.judgeModel || $("judgeModel")?.value.trim() || "gpt-5.5",
    token: $("systemJudgeToken")?.value.trim() || $("judgeToken")?.value.trim() || cfg.judgeToken || "",
  };
}

function memoryInjectModelConfig() {
  const cfg = readAccountConfig(currentAccount());
  return {
    baseUrl: $("systemMemoryBaseUrl")?.value.trim() || cfg.memoryInjectBaseUrl || $("ovVlmBaseUrl")?.value.trim() || "",
    model: $("systemMemoryModel")?.value.trim() || cfg.memoryInjectModel || $("ovVlmModel")?.value.trim() || "deepseek-v4-flash",
    token: $("systemMemoryToken")?.value.trim() || $("ovVlmApiKey")?.value.trim() || cfg.memoryInjectToken || "",
  };
}

function setModelPreflightStatus(role, text, tone = "") {
  const id = role === "judge" ? "systemJudgePreflight" : role === "memory" ? "systemMemoryPreflight" : "systemAgentPreflight";
  const target = $(id);
  if (!target) return;
  target.textContent = text || "";
  target.classList.toggle("ok", tone === "ok");
  target.classList.toggle("bad", tone === "bad");
  target.classList.toggle("warn", tone === "warn");
}

async function testSystemModel(role) {
  const cfg = role === "judge" ? judgeModelConfig() : role === "memory" ? memoryInjectModelConfig() : agentModelConfig();
  const label = role === "judge" ? "判分模型" : role === "memory" ? "记忆注入" : "Agent";
  setModelPreflightStatus(role, "测试中...", "warn");
  const payload = {
    role,
    base_url: cfg.baseUrl,
    model: cfg.model,
    api_key: cfg.token,
    timeout_s: 45,
  };
  const data = await api("/api/model-preflight", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (data.ok) {
    setModelPreflightStatus(role, `通过 · ${data.model || cfg.model} 可用`, "ok");
    toast(`${label} 模型可用`);
  } else {
    const detail = [data.status, data.error].filter(Boolean).join(" · ");
    setModelPreflightStatus(role, `失败 · ${detail || "模型不可用"}`, "bad");
    toast(`${label} 模型预检失败`);
  }
  return data;
}

function applyAccountConfig(account) {
  const cfg = {...accountConfigDefaults(), ...readAccountConfig(account)};
  if (!cfg.judgeBaseUrl) cfg.judgeBaseUrl = accountConfigDefaults().judgeBaseUrl;
  if (!cfg.judgeModel) cfg.judgeModel = accountConfigDefaults().judgeModel;
  if (!cfg.agentBaseUrl) cfg.agentBaseUrl = cfg.judgeBaseUrl;
  if (!cfg.agentModel) cfg.agentModel = cfg.judgeModel;
  syncAccountFields(account);
  if ($("memoryBackendSelect")) $("memoryBackendSelect").value = cfg.memoryBackend || "openviking";
  if ($("ovHost")) $("ovHost").value = cfg.ovHost;
  if ($("ovPort")) $("ovPort").value = cfg.ovPort;
  if ($("ovWorkspace")) $("ovWorkspace").value = cfg.ovWorkspace;
  if ($("ovApiKey")) $("ovApiKey").value = cfg.ovApiKey || "";
  if ($("memoryWorkspace")) $("memoryWorkspace").value = cfg.memoryWorkspace || cfg.ovWorkspace;
  if ($("echomemRoot")) $("echomemRoot").value = cfg.echomemRoot || "";
  if ($("memoryUserId")) $("memoryUserId").value = cfg.memoryUserId || "default";
  if ($("memoryAgentId")) $("memoryAgentId").value = cfg.memoryAgentId || "default";
  const regeneratedWorkspace = maybeRegenerateWorkspaceForBackend(account, cfg.memoryBackend || "openviking");
  if (regeneratedWorkspace) {
    const nextConfig = {...cfg, ovWorkspace: regeneratedWorkspace, memoryWorkspace: regeneratedWorkspace};
    saveAccountConfig(account, nextConfig);
    syncAccountConfigToBackend(account, nextConfig).catch(() => {});
  }
  setInputValue("systemJudgeBaseUrl", cfg.judgeBaseUrl);
  setInputValue("systemJudgeModel", cfg.judgeModel);
  setInputValue("systemJudgeToken", cfg.judgeToken);
  setInputValue("systemAgentBaseUrl", cfg.agentBaseUrl || cfg.judgeBaseUrl);
  setInputValue("systemAgentModel", cfg.agentModel || cfg.judgeModel);
  setInputValue("systemAgentToken", cfg.agentToken);
  setInputValue("systemMemoryBaseUrl", cfg.memoryInjectBaseUrl);
  setInputValue("systemMemoryModel", cfg.memoryInjectModel);
  setInputValue("systemMemoryToken", cfg.memoryInjectToken);
  setInputValue("judgeBaseUrl", cfg.judgeBaseUrl);
  setInputValue("judgeModel", cfg.judgeModel);
  setInputValue("judgeToken", cfg.judgeToken);
  setInputValue("ovVlmBaseUrl", cfg.memoryInjectBaseUrl);
  setInputValue("ovVlmModel", cfg.memoryInjectModel);
  setInputValue("ovVlmApiKey", cfg.memoryInjectToken);
  if ($("chatTopK")) $("chatTopK").value = cfg.chatTopK || "30";
  state.chatMessages = loadChatDraft(account);
  renderChat();
  updateSystemConfigSummary();
  updateWorkspaceMode();
  renderImportPaths();
  runSystemPreflight(true).catch(() => {});
}

function persistCurrentAccountConfig() {
  const account = currentAccount();
  const patch = currentAccountConfigPatch();
  saveAccountConfig(account, patch);
  syncAccountConfigToBackend(account, patch).catch(() => {});
  updateSystemConfigSummary();
  runSystemPreflight(true).catch(() => {});
}

function updateSystemConfigSummary() {
  const account = currentAccount();
  const backend = currentMemoryBackend();
  const workspace = $("ovWorkspace")?.value.trim() || "";
  const storageRoot = storageRootForBackend(workspace, account, backend);
  const echomemRoot = $("echomemRoot")?.value.trim() || readAccountConfig(account).echomemRoot || "";
  const agentCfg = agentModelConfig();
  const judgeCfg = judgeModelConfig();
  const memoryCfg = memoryInjectModelConfig();
  if ($("systemAccountLabel")) $("systemAccountLabel").textContent = account;
  if ($("systemBackendLabel")) $("systemBackendLabel").textContent = memoryBackendLabel(backend);
  renderGlobalBackendBadge();
  if ($("systemOpenVikingLabel")) {
    $("systemOpenVikingLabel").textContent = backend === "echomemory"
      ? "local EchoMemory SDK"
      : `${$("ovHost")?.value.trim() || "-"}:${$("ovPort")?.value.trim() || "-"}`;
  }
  if ($("systemWorkspaceLabel")) $("systemWorkspaceLabel").textContent = workspace || "-";
  if ($("systemAgentLabel")) $("systemAgentLabel").textContent = agentCfg.model || "-";
  renderHotpotQaModelReadiness();
  if ($("systemJudgeLabel")) $("systemJudgeLabel").textContent = judgeCfg.model || "-";
  if ($("systemMemoryModelLabel")) $("systemMemoryModelLabel").textContent = memoryCfg.model || "-";
  if ($("systemAccountStateFile")) $("systemAccountStateFile").textContent = state.accountStateFile || "-";
  renderBackendIsolationSummary();
  renderAccountReadiness();
  renderAccountConfigSnapshot();
  renderAccountIsolationMatrix();
  renderLocomoOverview();
  updateBackendUi();
  refreshEchoMemorySourceCard().catch(() => {});
}

function updateBackendUi() {
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  const shortLabel = memoryBackendShortLabel(backend);
  renderGlobalBackendBadge();
  if ($("importWorkspaceLabel")) {
    $("importWorkspaceLabel").textContent = `记忆目录`;
  }
  if ($("importSampleLabel")) {
    const dataset = currentLocomoDataset();
    const summary = dataset?.samples && dataset?.questions
      ? ` · ${formatInt(dataset.samples)} 个对话样本 · 共 ${formatInt(dataset.questions)} 题`
      : "";
    $("importSampleLabel").textContent = `导入会话${summary}`;
  }
  if ($("backendConnectionSummary")) {
    $("backendConnectionSummary").textContent = backend === "echomemory"
      ? "EchoMemory 本地 SDK 与抽取模型"
      : "OpenViking 服务状态与抽取模型";
  }
  if ($("probeOpenViking")) {
    $("probeOpenViking").textContent = backend === "echomemory" ? "检查 EchoMemory SDK" : "检测 OpenViking 服务";
    $("probeOpenViking").title = backend === "echomemory"
      ? "检查 EchoMemory adapter 是否注册，本地 SDK 由导入任务实际调用"
      : "检测 OpenViking 服务状态";
  }
  if ($("runOpenVikingQa")) {
    $("runOpenVikingQa").title = `${backendLabel} 只运行已勾选题目`;
  }
  if ($("runOpenVikingFullQa")) {
    $("runOpenVikingFullQa").title = `${backendLabel} 运行当前对话范围内的全部题目`;
  }
  refreshLocomoQaActionLabels();
  if ($("ovWorkspace")) {
    $("ovWorkspace").placeholder = "可手填记忆目录，或点右侧自动生成";
  }
  if ($("ovApiKey")) {
    if ($("ovApiKeyLabel")) {
      $("ovApiKeyLabel").textContent = backend === "echomemory"
        ? "EchoMemory 服务 Key（通常可留空）"
        : "OpenViking 服务 API 密钥（本地可留空）";
    }
    $("ovApiKey").placeholder = backend === "echomemory"
      ? "EchoMemory 后端通常读取本地环境变量，可留空"
      : "OpenViking root/api key，本地 dev 模式可留空";
  }
  refreshImportActionLabels();
  renderImportReadinessPanel();
  renderQaReadinessPanel();
  renderJudgeReadinessPanel();
}

function renderAccountConfigSnapshot() {
  const target = $("accountConfigSnapshot");
  if (!target) return;
  const account = currentAccount();
  const cfg = {...accountConfigDefaults(), ...readAccountConfig(account), ...currentAccountConfigPatch()};
  const ovUrl = `${cfg.ovHost || "127.0.0.1"}:${cfg.ovPort || "19080"}`;
  const backend = normalizeMemoryBackend(cfg.memoryBackend);
  const rows = [
    ["当前账户", account],
    ["记忆后端", memoryBackendLabel(backend)],
    ["状态文件", state.accountStateFile || "-"],
    ["接入方式", backend === "echomemory" ? "EchoMemory SDK" : ovUrl],
    ["EchoMemory Root", cfg.echomemRoot || "-"],
    ["记忆目录", cfg.ovWorkspace || "-"],
    ["读取目录", cfg.memoryWorkspace || cfg.ovWorkspace || "-"],
    ["User / Agent", `${cfg.memoryUserId || "default"} / ${cfg.memoryAgentId || "default"}`],
    ["判分地址", cfg.judgeBaseUrl || "-"],
    ["判分模型", cfg.judgeModel || "-"],
    ["召回数量", cfg.chatTopK || "默认"],
  ];
  target.innerHTML = `
    <div class="snapshot-grid">
      ${rows.map(([label, value]) => `
        <article>
          <span>${escapeHtml(label)}</span>
          <code>${escapeHtml(value)}</code>
        </article>
      `).join("")}
    </div>
    <div class="snapshot-note">
      <strong>作用范围</strong>
      <p>每个账户独立保存目录和模型设置。</p>
    </div>
  `;
}

function backendStorageLayout(backend = "openviking") {
  const normalized = normalizeMemoryBackend(backend);
  if (normalized === "echomemory") {
    return {
      layout: "workspace/<account>/<account>",
      writeSurface: "EchoMemory 本地 SDK",
      folders: "sessions / users / agents",
      note: "EchoMemory fork 或图记忆模块应把该目录作为当前账户的干净记忆空间。",
    };
  }
  return {
    layout: "workspace/viking/<account>",
    writeSurface: "OpenViking service",
    folders: "session / user / agent",
    note: "OpenViking commit_session 后长期记忆应落在该账户目录下。",
  };
}

function renderBackendIsolationSummary() {
  const target = $("accountBackendIsolationSummary");
  if (!target) return;
  const account = currentAccount();
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  const workspace = $("ovWorkspace")?.value.trim() || "";
  const storageRoot = storageRootForBackend(workspace, account, backend);
  const layout = backendStorageLayout(backend);
  const generated = workspace && isGeneratedMemoryWorkspace(workspace);
  const tone = workspace ? "ok" : "warn";
  const verifyCommand = storageRoot
    ? `test -d ${shellQuote(storageRoot)} && find ${shellQuote(storageRoot)} -maxdepth 2 -type d | head`
    : "./preflight.sh";
  target.innerHTML = `
    <article class="backend-isolation-hero ${tone}">
      <div>
        <span>${escapeHtml(backendLabel)}</span>
        <strong>${escapeHtml(account)} · ${workspace ? "独立写入" : "待配置目录"}</strong>
        <p>后端切换只影响当前账户。</p>
      </div>
      <div class="backend-isolation-actions">
        ${copyButtonHtml(verifyCommand, "复制验证命令")}
      </div>
    </article>
    <div class="backend-isolation-grid">
      <article>
        <span>接入方式</span>
        <strong>${escapeHtml(layout.writeSurface)}</strong>
        <p>${escapeHtml(layout.note)}</p>
      </article>
      <article>
        <span>目录布局</span>
        <code>${escapeHtml(layout.layout)}</code>
        <p>${escapeHtml(layout.folders)}</p>
      </article>
      <article>
        <span>目录来源</span>
        <strong>${escapeHtml(generated ? "自动生成" : (workspace ? "手动指定" : "未配置"))}</strong>
        <code>${escapeHtml(workspace || "-")}</code>
      </article>
      <article>
        <span>当前存储根</span>
        <strong>${escapeHtml(storageRoot ? "可验证" : "待生成")}</strong>
        <code>${escapeHtml(storageRoot || "-")}</code>
      </article>
    </div>
  `;
  bindCopyButtons("#accountBackendIsolationSummary");
}

function accountIsolationStatus(status, sharedWith = []) {
  if (status === "shared_workspace") {
    const suffix = sharedWith.length ? `：${sharedWith.join(", ")}` : "";
    return {label: `共享目录${suffix}`, tone: "warn"};
  }
  if (status === "missing_workspace") return {label: "未配置目录", tone: "bad"};
  return {label: "独立目录", tone: "ok"};
}

function backendAccountRecord(account) {
  const wanted = String(account || "default");
  return (state.accountRecords || []).find((record) => String(record.id || "") === wanted) || null;
}

function storageRootForBackend(workspace = "", account = "", backend = "openviking") {
  if (!workspace) return "";
  const accountId = account || "default";
  const normalized = normalizeMemoryBackend(backend);
  if (normalized === "echomemory") return `${workspace}/${accountId}/${accountId}`;
  return `${workspace}/viking/${accountId}`;
}

function setAccountCreateExpanded(expanded) {
  const row = $("accountCreateRow");
  const input = $("accountNameInput");
  const button = $("createAccount");
  const label = button?.querySelector(".workspace-action-label");
  const icon = button?.querySelector(".workspace-action-icon");
  if (!row || !input || !button) return;
  row.classList.toggle("expanded", Boolean(expanded));
  input.hidden = !expanded;
  input.setAttribute("aria-hidden", expanded ? "false" : "true");
  if (label) label.textContent = expanded ? "确认创建" : "创建账户";
  if (icon) icon.textContent = expanded ? "✓" : "＋";
  button.title = expanded ? "使用输入的名称创建独立评测账户" : "新建一个独立评测账户";
  if (expanded) {
    requestAnimationFrame(() => input.focus());
  } else {
    input.value = "";
  }
}

function handleCreateAccountClick() {
  const row = $("accountCreateRow");
  if (row && !row.classList.contains("expanded")) {
    setAccountCreateExpanded(true);
    updateAccountActionState("输入名称，创建独立目录。", "muted");
    return;
  }
  createAccount().catch((e) => toast(e.message));
}

function accountDirectoryItems(isolation = {}, workspace = "", account = "", backend = "openviking") {
  const accountId = account || "default";
  const currentBackend = normalizeMemoryBackend(isolation.backend || backend);
  const expectedRoot = storageRootForBackend(workspace, accountId, currentBackend);
  const actualRoot = isolation.storage_root || isolation.viking_root || "";
  const matches = Boolean(workspace && isolation.workspace === workspace && actualRoot === expectedRoot);
  const labels = currentBackend === "echomemory"
    ? ["目录", "账户", "会话", "用户记忆", "Agent 记忆"]
    : ["根目录", "账户", "会话", "用户记忆", "Agent 记忆"];
  return [
    [labels[0], matches ? isolation.workspace_exists : null],
    [labels[1], matches ? isolation.account_root_exists : null],
    [labels[2], matches ? isolation.session_root_exists : null],
    [labels[3], matches ? isolation.user_root_exists : null],
    [labels[4], matches ? isolation.agent_root_exists : null],
  ].map(([label, ready]) => ({label, ready}));
}

function readinessChipHtml(item) {
  const tone = item.ready === true ? "ok" : item.ready === false ? "bad" : "muted";
  const suffix = item.ready === true ? "已准备" : item.ready === false ? "未准备" : "待检查";
  return `<span class="readiness-chip ${tone}">${escapeHtml(item.label)} ${suffix}</span>`;
}

function renderAccountReadiness() {
  return;
}

function renderAccountIsolationMatrix() {
  const target = $("accountIsolationMatrix");
  if (!target) return;
  const backendById = new Map((state.accountRecords || []).map((record) => [String(record.id || ""), record]));
  const accounts = saveAccountList(readAccountList());
  const rows = accounts.map((account) => {
    const cfg = {...accountConfigDefaults(), ...(backendById.get(account)?.config || {}), ...readAccountConfig(account)};
    const workspace = String(cfg.ovWorkspace || cfg.memoryWorkspace || "").trim();
    return {account, cfg, workspace};
  });
  const workspaceCounts = rows.reduce((acc, row) => {
    if (row.workspace) acc[row.workspace] = (acc[row.workspace] || 0) + 1;
    return acc;
  }, {});
  target.innerHTML = `
    <div class="isolation-grid">
      ${rows.map((row) => {
        const backend = backendById.get(row.account) || {};
        const isolation = backend.isolation || {};
        const workspace = row.workspace || isolation.workspace || "";
        const memoryBackend = normalizeMemoryBackend(row.cfg.memoryBackend || isolation.backend || "openviking");
        const storageRoot = isolation.storage_root || storageRootForBackend(workspace, row.account, memoryBackend);
        const sharedWith = rows.filter((other) => other.account !== row.account && other.workspace && other.workspace === workspace).map((other) => other.account);
        const status = !workspace ? "missing_workspace" : workspaceCounts[workspace] > 1 ? "shared_workspace" : "isolated_workspace";
        const badge = accountIsolationStatus(status, sharedWith);
        const readiness = accountDirectoryItems(isolation, workspace, row.account, memoryBackend).map(readinessChipHtml).join("");
        return `
          <article class="isolation-row ${badge.tone}">
            <div class="isolation-row-head">
              <strong>${escapeHtml(row.account)}</strong>
              <span class="isolation-badge ${badge.tone}">${escapeHtml(badge.label)}</span>
            </div>
            <dl>
              <dt>目录</dt>
              <dd><code>${escapeHtml(workspace || "-")}</code></dd>
              <dt>存储目录</dt>
              <dd><code>${escapeHtml(storageRoot || "-")}</code></dd>
              <dt>目录状态</dt>
              <dd><div class="readiness-pills">${readiness}</div></dd>
            </dl>
          </article>
        `;
      }).join("")}
    </div>
    <div class="snapshot-note">
      <strong>怎么验证干净环境</strong>
      <p>每个空间都应显示“独立目录”。如果多个空间共享同一个目录，评测结果可能互相影响。</p>
    </div>
  `;
}

function preflightTone(status) {
  const value = String(status || "").toLowerCase();
  if (value === "ok") return "ok";
  if (value === "fail" || value === "bad") return "bad";
  if (value === "warn" || value === "warning") return "warn";
  if (value === "todo") return "todo";
  return "muted";
}

function preflightLabel(status) {
  const value = String(status || "").toLowerCase();
  if (value === "ok") return "通过";
  if (value === "fail" || value === "bad") return "失败";
  if (value === "warn" || value === "warning") return "需确认";
  if (value === "todo") return "待执行";
  return "待检查";
}

function backendContractLabel(status) {
  const value = String(status || "").toLowerCase();
  if (value === "ok") return "必需契约通过";
  if (value === "warn") return "可运行，建议补齐";
  if (value === "fail" || value === "bad") return "必需契约缺失";
  return "未检查";
}

function preflightCard(title, status, value, details = []) {
  const tone = preflightTone(status);
  const rows = details.filter(Boolean).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `
    <article class="preflight-card ${tone}">
      <div class="preflight-card-head">
        <span>${escapeHtml(title)}</span>
        <em>${escapeHtml(preflightLabel(status))}</em>
      </div>
      <strong>${escapeHtml(value || "-")}</strong>
      ${rows ? `<ul>${rows}</ul>` : ""}
    </article>
  `;
}

function renderPreflightFixes(fixes = []) {
  const rows = Array.isArray(fixes) ? fixes : [];
  if (!rows.length) return "";
  return `
    <article class="preflight-fixes">
      <div class="preflight-fix-head">
        <span>修复清单</span>
        <strong>${rows.some((item) => item.priority === "required") ? "先处理必需项" : "可继续评测"}</strong>
      </div>
      <div class="preflight-fix-list">
        ${rows.map((fix) => {
          const envRows = Object.entries(fix.env || {});
          const envText = envRows.map(([key, value]) => `export ${key}=${value}`).join("\n");
          const envBlock = envRows.length
            ? `
              <div class="preflight-copy-block">
                <div class="preflight-copy-head">
                  <span>env.local</span>
                  ${copyButtonHtml(envText)}
                </div>
                <pre>${escapeHtml(envText)}</pre>
              </div>
            `
            : "";
          const commandBlock = fix.command
            ? `
              <div class="preflight-copy-block">
                <div class="preflight-copy-head">
                  <span>命令</span>
                  ${copyButtonHtml(fix.command)}
                </div>
                <code>${escapeHtml(fix.command)}</code>
              </div>
            `
            : "";
          return `
            <section class="preflight-fix-item ${escapeHtml(fix.priority || "")}">
              <div>
                <span>${escapeHtml(fix.priority === "required" ? "必需" : fix.priority === "ok" ? "就绪" : "建议")}</span>
                <strong>${escapeHtml(fix.title || "-")}</strong>
                <p>${escapeHtml(fix.body || "")}</p>
              </div>
              ${envBlock}
              ${commandBlock}
            </section>
          `;
        }).join("")}
      </div>
    </article>
  `;
}

function renderAuditEvidence(evidence) {
  if (evidence === null || evidence === undefined) return "";
  const rows = Array.isArray(evidence) ? evidence : [evidence];
  if (!rows.length) return "";
  const limited = rows.slice(0, 6).map((item) => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object") {
      if (item.file || item.line || item.preview) {
        return `${item.file || "-"}${item.line ? `:${item.line}` : ""} ${item.preview || ""}`.trim();
      }
      return JSON.stringify(item);
    }
    return String(item);
  });
  const more = rows.length > limited.length ? `<li>另有 ${rows.length - limited.length} 项，详见 API 返回。</li>` : "";
  return `<ul>${limited.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}${more}</ul>`;
}

function renderHandoffAudit(data, targetId = "handoffAuditPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>等待审计</span>
        <strong>外发前运行一次</strong>
        <p>审计默认排除 runs、dist、outputs、external 和 dataset/full。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>正在审计</span>
        <strong>检查当前交付入口</strong>
        <p>不会读取或返回真实 API 密钥。</p>
      </article>
    `;
    return;
  }
  const checks = Array.isArray(data.checks) ? data.checks : [];
  const requiredFailures = checks.filter((item) => item.severity === "required" && item.status === "fail").length;
  const warnings = checks.filter((item) => item.status === "warn").length;
  const summary = data.summary || "";
  target.innerHTML = `
    <article class="handoff-audit-card summary ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>交付审计</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${requiredFailures ? `${requiredFailures} 个必需失败` : warnings ? `${warnings} 个建议确认` : "可以进入外发前确认"}</strong>
      <p>${escapeHtml(data.checked_at || "")} · audited files ${escapeHtml(data.audited_files ?? "-")}</p>
      ${summary ? copyButtonHtml(summary, "复制审计摘要") : ""}
    </article>
    ${checks.map((check) => `
      <article class="handoff-audit-card ${preflightTone(check.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(check.severity === "required" ? "必需" : check.severity === "recommended" ? "建议" : "信息")}</span>
          <em>${escapeHtml(preflightLabel(check.status))}</em>
        </div>
        <strong>${escapeHtml(check.title || check.id || "-")}</strong>
        <p>${escapeHtml(check.detail || "")}</p>
        ${renderAuditEvidence(check.evidence)}
      </article>
    `).join("")}
  `;
  bindCopyButtons(`#${targetId}`);
}

function renderDeliveryBoundaryGate(data, targetId = "deliveryBoundaryPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>等待检查</span>
        <strong>只读交付边界</strong>
        <p>不会导入数据、调用模型、读取 API 密钥或修改 workspace。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>正在检查</span>
        <strong>聚合交付边界证据</strong>
        <p>读取 UI contract、adapter doctor、公开入口和脱敏外发边界。</p>
      </article>
    `;
    return;
  }
  const checks = Array.isArray(data.checks) ? data.checks : [];
  const sidebar = Array.isArray(data.sidebar) ? data.sidebar : [];
  const publicFiles = Array.isArray(data.public_files) ? data.public_files : [];
  const expected = Array.isArray(data.expected_backends) ? data.expected_backends : [];
  const registered = Array.isArray(data.registered_backends) ? data.registered_backends : [];
  target.innerHTML = `
    <article class="handoff-audit-card summary ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>交付边界门禁</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${escapeHtml(data.scope || "OpenViking + EchoMemory")}</strong>
      <p>${escapeHtml(data.checked_at || "")} · Agent ${escapeHtml(data.agent_label || "MemoryBench Agent")}</p>
      ${data.markdown ? copyButtonHtml(data.markdown, "复制边界 Markdown 文本") : ""}
    </article>
    <article class="handoff-audit-card ${preflightTone(data.status)}">
      <span>后端范围</span>
      <strong>${escapeHtml(registered.join(", ") || "-")}</strong>
      <p>期望后端：${escapeHtml(expected.join(", ") || "-")}</p>
    </article>
    <article class="handoff-audit-card ${sidebar.length === 9 ? "ok" : "warn"}">
      <span>侧边栏</span>
      <strong>${escapeHtml(sidebar.length)} 个固定入口</strong>
      <p>${escapeHtml(sidebar.map((item) => item.label).join(" / ") || "-")}</p>
    </article>
    <article class="handoff-audit-card ${publicFiles.length ? "ok" : "warn"}">
      <span>公开入口</span>
      <strong>${escapeHtml(publicFiles.length)} 个 public static files</strong>
      <p>${escapeHtml(publicFiles.join(" / ") || "-")}</p>
    </article>
    ${checks.map((check) => `
      <article class="handoff-audit-card ${preflightTone(check.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(check.id || "gate")}</span>
          <em>${escapeHtml(preflightLabel(check.status))}</em>
        </div>
        <strong>${escapeHtml(check.title || "-")}</strong>
        <p>${escapeHtml(check.detail || "")}</p>
        ${renderAuditEvidence(check.evidence)}
      </article>
    `).join("")}
    ${data.historical_static_policy ? `
      <article class="handoff-audit-card muted">
        <span>历史页面策略</span>
        <strong>不作为公开入口</strong>
        <p>${escapeHtml(data.historical_static_policy)}</p>
      </article>
    ` : ""}
  `;
  bindCopyButtons(`#${targetId}`);
}

function renderHandoffDashboard(data, targetId = "handoffDashboardPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-dashboard-card muted">
        <span>等待检查</span>
        <strong>服务启动后自动读取</strong>
        <p>只返回状态、路径、布尔值和占位符命令，不返回 API 密钥。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-dashboard-card muted">
        <span>正在检查</span>
        <strong>聚合交付状态</strong>
        <p>读取启动门禁、验收矩阵、EchoMemory 契约、小样本核验计划和外发清单。</p>
      </article>
    `;
    return;
  }
  const cards = Array.isArray(data.cards) ? data.cards : [];
  const issues = Array.isArray(data.issues) ? data.issues : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const nextActions = Array.isArray(data.next_actions) ? data.next_actions : [];
  const commands = Array.isArray(data.quick_start) ? data.quick_start : [];
  const doNotShare = Array.isArray(data.do_not_share) ? data.do_not_share : [];
  const dataset = data.dataset || {};
  const workspace = data.workspace || {};
  const recommendation = data.smoke?.recommendation || data.smoke_plan?.recommendation || {};
  const tenQuestionIds = Array.isArray(recommendation.ten_question_ids) ? recommendation.ten_question_ids : [];
  const score = Number.isFinite(Number(data.score)) ? Number(data.score) : 0;
  target.innerHTML = `
    <article class="handoff-dashboard-card hero ${preflightTone(data.status)}">
      <div class="readiness-score">
        <span>${escapeHtml(preflightLabel(data.status))}</span>
        <strong>${escapeHtml(score)}/100</strong>
      </div>
      <div>
        <span>交付驾驶舱</span>
        <strong>${escapeHtml(data.scope || "OpenViking + EchoMemory")} · ${escapeHtml(data.account || currentAccount())}</strong>
        <p>${escapeHtml(data.checked_at || "")} · blockers ${escapeHtml(issues.length)} · warnings ${escapeHtml(warnings.length)}</p>
        ${data.markdown ? copyButtonHtml(data.markdown, "复制公开版驾驶舱") : ""}
      </div>
    </article>
    <article class="handoff-dashboard-card next ${nextActions.length ? preflightTone(data.status) : "ok"}">
      <span>下一步</span>
      <strong>${nextActions.length ? "先处理这些项" : "可以做 LoCoMo 小样本核验"}</strong>
      <ul>${(nextActions.length ? nextActions : ["先导入一个 conversation，再跑 1 题 QA，并判分当前结果。"]).slice(0, 6).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </article>
    <article class="handoff-dashboard-card smoke">
      <span>推荐核验样本</span>
      <strong>${escapeHtml(recommendation.sample_id || "-")} · ${escapeHtml(recommendation.sample_questions ?? "-")} 题</strong>
      <p>1 题：<code>${escapeHtml(recommendation.one_question_id || "-")}</code></p>
      <p>10 题：<code>${escapeHtml(tenQuestionIds.join(",") || "-")}</code></p>
      <div class="smoke-plan-actions">
        ${recommendation.one_question_id ? copyButtonHtml(recommendation.one_question_id, "复制 1 题") : ""}
        ${tenQuestionIds.length ? copyButtonHtml(tenQuestionIds.join(","), "复制 10 题") : ""}
        ${recommendation.sample_id ? `<button class="secondary compact-button" type="button" data-locomo-sample="${escapeHtml(recommendation.sample_id)}" data-locomo-view="evalView">切到推荐 conv</button>` : ""}
        ${tenQuestionIds.length ? `<button class="secondary compact-button" type="button" data-locomo-sample="${escapeHtml(recommendation.sample_id || "all")}" data-locomo-questions="${escapeHtml(tenQuestionIds.join(","))}" data-locomo-view="evalView">加载推荐 10 题</button>` : ""}
        <button class="secondary compact-button" type="button" data-view-jump="datasetView">去数据集</button>
      </div>
    </article>
    <article class="handoff-dashboard-card paths">
      <span>当前路径</span>
      <strong>数据集与记忆空间</strong>
      <dl class="smoke-plan-kv">
        <dt>数据集</dt><dd><code>${escapeHtml(dataset.path || "-")}</code></dd>
        <dt>工作空间</dt><dd><code>${escapeHtml(workspace.workspace || "-")}</code></dd>
        <dt>存储根</dt><dd><code>${escapeHtml(workspace.storage_root || "-")}</code></dd>
      </dl>
    </article>
    ${cards.map((card) => `
      <article class="handoff-dashboard-card gate ${preflightTone(card.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(card.title || card.id || "-")}</span>
          <em>${escapeHtml(preflightLabel(card.status))}</em>
        </div>
        <strong>${card.score === null || card.score === undefined ? escapeHtml(card.detail || "-") : `${escapeHtml(card.score)}/100`}</strong>
        <p>${escapeHtml(card.detail || "")}</p>
        ${card.view ? `<button class="secondary compact-button" type="button" data-view-jump="${escapeHtml(card.view)}">查看</button>` : ""}
      </article>
    `).join("")}
    <article class="handoff-dashboard-card ${issues.length ? "bad" : "ok"}">
      <span>必需问题</span>
      <strong>${issues.length ? `${issues.length} 项需要先修` : "没有 required blocker"}</strong>
      <ul class="handoff-dashboard-list">
        ${(issues.length ? issues : [{title: "交付边界、必需文件和基础契约当前没有 required failure。", action: "继续做小样本核验。"}]).slice(0, 6).map((item) => `
          <li>
            <b>${escapeHtml(item.title || item.source || "-")}</b>
            <span>${escapeHtml(item.action || item.detail || "")}</span>
          </li>
        `).join("")}
      </ul>
    </article>
    ${commands.length ? `
      <article class="handoff-dashboard-card commands">
        <span>启动命令</span>
        <strong>外部测试者按顺序执行</strong>
        <div class="setup-pack-command-list">
          ${commands.map((item) => `
            <div class="setup-pack-command">
              <div>
                <strong>${escapeHtml(item.title || "命令")}</strong>
                <code>${escapeHtml(item.command || "")}</code>
              </div>
              ${item.command ? copyButtonHtml(item.command, "复制") : ""}
            </div>
          `).join("")}
        </div>
      </article>
    ` : ""}
    <article class="handoff-dashboard-card warn">
      <span>不要外发</span>
      <strong>这些内容必须留在本机</strong>
      <ul class="handoff-dashboard-list">
        ${doNotShare.slice(0, 10).map((item) => `<li><b>${escapeHtml(item)}</b><span>不要放进交付目录或截图。</span></li>`).join("") || "<li><b>.env.local / runs / workspaces</b><span>不要外发本机敏感数据。</span></li>"}
      </ul>
    </article>
  `;
  bindCopyButtons(`#${targetId}`);
  bindViewJumpButtons(`#${targetId}`);
  bindLocomoPresetButtons(`#${targetId}`);
}

function hydrateHandoffSections(data = {}) {
  if (data.readiness) renderReadiness(data.readiness, "readinessPanel");
  if (data.readiness) renderReadiness(data.readiness, "readinessReadmePanel");
  if (data.acceptance) renderAcceptanceMatrix(data.acceptance, "acceptanceMatrixPanel");
  if (data.acceptance) renderAcceptanceMatrix(data.acceptance, "acceptanceMatrixReadmePanel");
  if (data.smoke || data.smoke_plan) renderSmokePlan(data.smoke || data.smoke_plan, "smokePlanPanel");
  if (data.smoke || data.smoke_plan) renderSmokePlan(data.smoke || data.smoke_plan, "smokePlanReadmePanel");
  if (data.handoff_package) renderHandoffPackage(data.handoff_package, "handoffPackagePanel");
  if (data.handoff_package) renderHandoffPackage(data.handoff_package, "handoffPackageReadmePanel");
  if (data.setup_pack) renderSetupPack(data.setup_pack, "setupPackPanel");
  if (data.setup_pack) renderSetupPack(data.setup_pack, "setupPackReadmePanel");
  if (data.echomem_contract) renderEchoMemContract(data.echomem_contract, "echomemContractPanel");
  if (data.echomem_contract) renderEchoMemContract(data.echomem_contract, "echomemContractReadmePanel");
  if (data.adapter_doctor) renderAdapterDoctor(data.adapter_doctor, "adapterDoctorPanel");
  if (data.adapter_doctor) renderAdapterDoctor(data.adapter_doctor, "adapterDoctorReadmePanel");
  if (data.audit) renderHandoffAudit(data.audit, "handoffAuditPanel");
  if (data.audit) renderHandoffAudit(data.audit, "handoffAuditReadmePanel");
  if (data.delivery_boundary) renderDeliveryBoundaryGate(data.delivery_boundary, "deliveryBoundaryPanel");
  if (data.delivery_boundary) renderDeliveryBoundaryGate(data.delivery_boundary, "deliveryBoundaryReadmePanel");
}

async function runHandoffDashboard(targetIds = ["handoffDashboardPanel", "handoffDashboardReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderHandoffDashboard({loading: true}, id));
  try {
    const data = await api("/api/handoff-dashboard", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderHandoffDashboard(data, id));
    hydrateHandoffSections(data);
    if (!silent) toast(data.status === "ok" ? "交付驾驶舱已就绪" : "交付驾驶舱需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      score: 0,
      checked_at: new Date().toISOString(),
      account: currentAccount(),
      backend: currentMemoryBackend(),
      scope: "OpenViking + EchoMemory",
      cards: [],
      issues: [{title: "交付驾驶舱请求失败", action: error.message || "检查服务是否启动"}],
      warnings: [],
      next_actions: [error.message || "检查服务是否启动"],
      quick_start: [],
      do_not_share: [".env.local", "judge.conf", "runs/", "workspaces", "真实 API 密钥"],
      markdown: `# LoCoMo Memory Eval Handoff Dashboard\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderHandoffDashboard(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderGithubLaunchKit(data, targetId = "githubLaunchKitReadmePanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="github-launch-card muted">
        <span>等待生成</span>
        <strong>生成 GitHub 发布材料</strong>
        <p>输出 README 首屏、架构图、核验命令、Issue 模板状态和安全边界，不包含真实密钥。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="github-launch-card muted">
        <span>正在生成</span>
        <strong>聚合开源发布材料</strong>
        <p>读取交付驾驶舱、外发清单、Issue 模板和最近报告。</p>
      </article>
    `;
    return;
  }
  const cards = Array.isArray(data.cards) ? data.cards : [];
  const quickstart = data.quickstart || {};
  const commands = Array.isArray(quickstart.commands) ? quickstart.commands : [];
  const issues = Array.isArray(data.issue_templates) ? data.issue_templates : [];
  const safety = Array.isArray(data.safety) ? data.safety : [];
  const echoMem = Array.isArray(data.echo_mem_integration) ? data.echo_mem_integration : [];
  const demo = data.demo_report || {};
  const issueRows = issues.map((item) => {
    const ready = item.exists && item.mentions_scope && item.warns_no_secrets;
    return `
      <li class="${ready ? "ok" : "warn"}">
        <code>${escapeHtml(item.path || "-")}</code>
        <span>${escapeHtml(item.purpose || "")}</span>
      </li>
    `;
  }).join("");
  const commandRows = commands.map((command, index) => `
    <div class="github-launch-command">
      <div>
        <strong>Step ${index + 1}</strong>
        <code>${escapeHtml(command || "")}</code>
      </div>
      ${command ? copyButtonHtml(command, "复制") : ""}
    </div>
  `).join("");
  target.innerHTML = `
    <article class="github-launch-card hero ${preflightTone(data.status)}">
      <div class="readiness-score">
        <span>${escapeHtml(preflightLabel(data.status))}</span>
        <strong>${escapeHtml(data.score ?? "-")}/100</strong>
      </div>
      <div>
        <span>GitHub Launch Kit</span>
        <strong>${escapeHtml(data.readme_intro || "OpenViking + EchoMemory")}</strong>
        <p>${escapeHtml(data.checked_at || "")} · scope ${escapeHtml(data.scope || "OpenViking + EchoMemory")}</p>
        ${data.markdown ? copyButtonHtml(data.markdown, "复制公开版 README") : ""}
      </div>
    </article>
    <article class="github-launch-card commands">
      <span>5 分钟复现</span>
      <strong>${escapeHtml(quickstart.sample || "-")} · ${escapeHtml(quickstart.one_question || "-")}</strong>
      <div class="github-launch-command-list">${commandRows || "<p>暂无命令。</p>"}</div>
    </article>
    <article class="github-launch-card">
      <span>架构图</span>
      <strong>Mermaid 可直接放 README</strong>
      <pre>${escapeHtml(data.architecture_mermaid || "")}</pre>
      ${data.architecture_mermaid ? copyButtonHtml(`\`\`\`mermaid\n${data.architecture_mermaid}\n\`\`\``, "复制架构图") : ""}
    </article>
    <article class="github-launch-card">
      <span>发布门禁</span>
      <strong>核心状态</strong>
      <ul class="github-launch-list">
        ${cards.map((card) => `<li class="${preflightTone(card.status)}"><b>${escapeHtml(card.title || "-")}</b><span>${escapeHtml(card.detail || "")}</span></li>`).join("")}
      </ul>
    </article>
    <article class="github-launch-card">
      <span>Issue 模板</span>
      <strong>${issues.filter((item) => item.exists && item.mentions_scope && item.warns_no_secrets).length}/${issues.length || 0} ready</strong>
      <ul class="github-launch-list">${issueRows || "<li><span>暂无 Issue 模板状态。</span></li>"}</ul>
    </article>
    <article class="github-launch-card">
      <span>EchoMemory 接入</span>
      <strong>外部 fork 需要保持这些契约</strong>
      <ul class="github-launch-list">${echoMem.map((item) => `<li><span>${escapeHtml(item)}</span></li>`).join("")}</ul>
    </article>
    <article class="github-launch-card warn">
      <span>安全边界</span>
      <strong>公开发布前必须检查</strong>
      <ul class="github-launch-list">${safety.map((item) => `<li><span>${escapeHtml(item)}</span></li>`).join("")}</ul>
    </article>
    <article class="github-launch-card ${demo.report_html ? "ok" : "warn"}">
      <span>演示报告</span>
      <strong>${demo.report_html ? "检测到最近 HTML 报告" : "还没有可用演示报告"}</strong>
      <p><code>${escapeHtml(demo.report_html || "先跑 1 组小样本对话核验，再导出评测报告文件（report.html）")}</code></p>
    </article>
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runGithubLaunchKit(targetIds = ["githubLaunchKitReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderGithubLaunchKit({loading: true}, id));
  try {
    const data = await api("/api/github-launch-kit", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderGithubLaunchKit(data, id));
    if (!silent) toast(data.status === "ok" ? "GitHub Launch Kit 已生成" : "GitHub Launch Kit 需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      score: 0,
      checked_at: new Date().toISOString(),
      scope: "OpenViking + EchoMemory",
      readme_intro: "Launch Kit 请求失败",
      cards: [{title: "请求失败", status: "fail", detail: error.message || "检查服务是否启动"}],
      quickstart: {commands: [], sample: "-", one_question: "-"},
      issue_templates: [],
      safety: [".env.local、judge.conf、runs、workspace 和真实 API 密钥 不要外发。"],
      echo_mem_integration: [],
      demo_report: {},
      architecture_mermaid: "",
      markdown: `# GitHub Launch Kit\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderGithubLaunchKit(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderReadiness(data, targetId = "readinessPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="readiness-card muted">
        <span>等待检查</span>
        <strong>读取当前状态后显示</strong>
        <p>只返回状态、路径和布尔值，不返回 API 密钥。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="readiness-card muted">
        <span>正在检查</span>
        <strong>聚合当前环境状态</strong>
        <p>检查交付审计、数据集、模型、运行时和任务状态。</p>
      </article>
    `;
    return;
  }
  const steps = Array.isArray(data.steps) ? data.steps : [];
  const actions = Array.isArray(data.next_actions) ? data.next_actions : [];
  const score = Number.isFinite(Number(data.score)) ? Number(data.score) : 0;
  target.innerHTML = `
    <article class="readiness-card hero ${preflightTone(data.status)}">
      <div class="readiness-score">
        <span>${escapeHtml(preflightLabel(data.status))}</span>
        <strong>${escapeHtml(score)}/100</strong>
      </div>
      <div>
        <span>启动门禁</span>
        <strong>${escapeHtml(memoryBackendLabel(data.backend))} · ${escapeHtml(data.account || currentAccount())}</strong>
        <p>${escapeHtml(data.checked_at || "")}</p>
        ${data.summary ? copyButtonHtml(data.summary, "复制公开版门禁") : ""}
      </div>
    </article>
    <article class="readiness-card next ${actions.length ? preflightTone(data.status) : "ok"}">
      <span>下一步</span>
      <strong>${actions.length ? "按顺序处理" : "可以继续测试"}</strong>
      <ul>${(actions.length ? actions : ["可以继续 LoCoMo 导入、QA、判分或外发前小样本核验。"]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </article>
    ${steps.map((step) => `
      <article class="readiness-card ${preflightTone(step.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(step.title || "-")}</span>
          <em>${escapeHtml(preflightLabel(step.status))}</em>
        </div>
        <strong>${escapeHtml(step.detail || "-")}</strong>
        <p>${escapeHtml(step.action || "")}</p>
      </article>
    `).join("")}
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runReadiness(targetIds = ["readinessPanel", "readinessReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderReadiness({loading: true}, id));
  try {
    const data = await api("/api/readiness", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderReadiness(data, id));
    if (!silent) toast(data.status === "ok" ? "启动门禁通过" : "启动门禁需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      score: 0,
      checked_at: new Date().toISOString(),
      account: currentAccount(),
      backend: currentMemoryBackend(),
      steps: [{
        status: "fail",
        title: "门禁请求失败",
        detail: error.message || "无法读取启动门禁。",
        action: "检查服务是否启动",
      }],
      next_actions: [error.message || "检查服务是否启动"],
      summary: `LoCoMo Memory Eval Readiness\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderReadiness(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function acceptanceEvidenceText(evidence) {
  if (evidence === null || evidence === undefined) return "";
  if (typeof evidence === "string") return evidence;
  if (Array.isArray(evidence)) return evidence.slice(0, 3).map((item) => typeof item === "string" ? item : JSON.stringify(item)).join(" · ");
  if (typeof evidence === "object") {
    const pairs = Object.entries(evidence)
      .filter(([, value]) => value !== null && value !== undefined && value !== "")
      .slice(0, 5)
      .map(([key, value]) => {
        const text = typeof value === "object" ? JSON.stringify(value) : String(value);
        return `${key}: ${text}`;
      });
    return pairs.join(" · ");
  }
  return String(evidence);
}

function renderAcceptanceMatrix(data, targetId = "acceptanceMatrixPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="acceptance-card muted">
        <span>等待检查</span>
        <strong>读取当前交付验收状态</strong>
        <p>只返回状态、路径和布尔值，不返回 API 密钥。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="acceptance-card muted">
        <span>正在检查</span>
        <strong>汇总外部交付验收项</strong>
        <p>检查后端边界、EchoMemory 契约、数据集、模型、安全和报告链路。</p>
      </article>
    `;
    return;
  }
  const items = Array.isArray(data.items) ? data.items : [];
  const blockers = Array.isArray(data.blockers) ? data.blockers : [];
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const nextActions = Array.isArray(data.next_actions) ? data.next_actions : [];
  const score = Number.isFinite(Number(data.score)) ? Number(data.score) : 0;
  target.innerHTML = `
    <article class="acceptance-card hero ${preflightTone(data.status)}">
      <div class="readiness-score">
        <span>${escapeHtml(preflightLabel(data.status))}</span>
        <strong>${escapeHtml(score)}/100</strong>
      </div>
      <div>
        <span>外部验收矩阵</span>
        <strong>${escapeHtml(data.scope || "OpenViking + EchoMemory")} · ${escapeHtml(data.account || currentAccount())}</strong>
        <p>${escapeHtml(data.checked_at || "")} · blockers ${escapeHtml(blockers.length)} · warnings ${escapeHtml(warnings.length)}</p>
        ${data.markdown ? copyButtonHtml(data.markdown, "复制公开版验收") : ""}
      </div>
    </article>
    <article class="acceptance-card next ${nextActions.length ? preflightTone(data.status) : "ok"}">
      <span>下一步</span>
      <strong>${nextActions.length ? "先处理这些项" : "可以交给外部测试者做小样本核验"}</strong>
      <ul>${(nextActions.length ? nextActions : ["进入 LoCoMo 评测，先跑 conv-30 少量 QA 并判分当前结果，再扩展到全量。"]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </article>
    ${items.map((item) => {
      const evidence = acceptanceEvidenceText(item.evidence);
      return `
        <article class="acceptance-card ${preflightTone(item.status)}">
          <div class="preflight-card-head">
            <span>${escapeHtml(item.severity === "required" ? "必需" : item.severity === "recommended" ? "建议" : "信息")}</span>
            <em>${escapeHtml(preflightLabel(item.status))}</em>
          </div>
          <strong>${escapeHtml(item.title || item.id || "-")}</strong>
          <p>${escapeHtml(item.proof || "")}</p>
          ${evidence ? `<code>${escapeHtml(evidence)}</code>` : ""}
          <p>${escapeHtml(item.action || "")}</p>
        </article>
      `;
    }).join("")}
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runAcceptanceMatrix(targetIds = ["acceptanceMatrixPanel", "acceptanceMatrixReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderAcceptanceMatrix({loading: true}, id));
  try {
    const data = await api("/api/acceptance-matrix", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderAcceptanceMatrix(data, id));
    if (!silent) toast(data.status === "ok" ? "外部验收通过" : "外部验收需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      score: 0,
      checked_at: new Date().toISOString(),
      account: currentAccount(),
      backend: currentMemoryBackend(),
      scope: "OpenViking + EchoMemory",
      items: [{
        id: "acceptance_request",
        title: "验收矩阵请求失败",
        status: "fail",
        severity: "required",
        proof: error.message || "无法读取验收矩阵。",
        action: "检查 Web 服务是否启动。",
      }],
      blockers: [{}],
      warnings: [],
      next_actions: [error.message || "检查 Web 服务是否启动。"],
      markdown: `# LoCoMo Memory Eval External Acceptance Matrix\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderAcceptanceMatrix(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function bindViewJumpButtons(rootSelector) {
  document.querySelectorAll(`${rootSelector} [data-view-jump]`).forEach((button) => {
    if (button.dataset.viewJumpBound === "1") return;
    button.dataset.viewJumpBound = "1";
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
}

async function applyLocomoPreset(button) {
  const sampleValue = button.dataset.locomoSample || "all";
  const questionIds = String(button.dataset.locomoQuestions || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const targetView = button.dataset.locomoView || "evalView";
  const path = $("data")?.value?.trim() || "";
  if (!path) {
    showView("datasetView");
    return toast("请先在数据集步骤确认 LoCoMo JSON 路径");
  }
  if (!currentLocomoDataset()) {
    await loadDataset();
  }
  if (questionIds.length) {
    await selectQuestionIds(questionIds, sampleValue);
    showView(targetView);
    toast(`已加载 ${questionIds.length} 题`);
    return;
  }
  const targetSample = resolveLocomoSampleOptionValue(sampleValue);
  const sample = $("sample");
  if (!sample) return;
  if (sample.value !== targetSample || !locomoQuestionsMatchScope()) {
    sample.value = targetSample;
    state.selectedQuestions.clear();
    await loadQuestions();
  }
  showView(targetView);
  const scope = currentLocomoSampleScope();
  toast(`已切到 ${scope.isAll ? LOCOMO_ALL_SESSIONS_LABEL : scope.label}`);
}

function bindLocomoPresetButtons(rootSelector) {
  document.querySelectorAll(`${rootSelector} [data-locomo-sample], ${rootSelector} [data-locomo-questions]`).forEach((button) => {
    if (button.dataset.locomoPresetBound === "1") return;
    button.dataset.locomoPresetBound = "1";
    button.addEventListener("click", () => applyLocomoPreset(button).catch((e) => toast(e.message)));
  });
}

function renderSmokePlan(data, targetId = "smokePlanPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="smoke-plan-card muted">
        <span>等待生成</span>
        <strong>只读测试计划</strong>
        <p>不会导入数据、调用模型、执行判分或返回 API 密钥。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="smoke-plan-card muted">
        <span>正在生成</span>
        <strong>读取当前环境和推荐样本</strong>
        <p>生成页面路线、推荐 QA、预期产物和安全命令。</p>
      </article>
    `;
    return;
  }
  const steps = Array.isArray(data.steps) ? data.steps : [];
  const commands = Array.isArray(data.commands) ? data.commands : [];
  const recommendation = data.recommendation || {};
  const examples = Array.isArray(recommendation.question_examples) ? recommendation.question_examples : [];
  const tenQuestionIds = Array.isArray(recommendation.ten_question_ids) ? recommendation.ten_question_ids : [];
  const dataset = data.dataset || {};
  const workspace = data.workspace || {};
  const score = Number.isFinite(Number(data.score)) ? Number(data.score) : 0;
  target.innerHTML = `
    <article class="smoke-plan-card hero ${preflightTone(data.status)}">
      <div class="readiness-score">
        <span>${escapeHtml(preflightLabel(data.status))}</span>
        <strong>${escapeHtml(score)}/100</strong>
      </div>
      <div>
        <span>小样本核验控制台</span>
        <strong>${escapeHtml(memoryBackendLabel(data.backend))} · ${escapeHtml(data.account || currentAccount())}</strong>
        <p>${escapeHtml(data.checked_at || "")} · 只读计划，不会自动导入、调用模型或执行判分。</p>
        ${data.markdown ? copyButtonHtml(data.markdown, "复制公开版核验计划") : ""}
      </div>
    </article>
    <article class="smoke-plan-card sample">
      <span>推荐样本</span>
      <strong>${escapeHtml(recommendation.sample_id || "-")} · ${escapeHtml(recommendation.sample_questions ?? "-")} 题</strong>
      <p>1 题核验：<code>${escapeHtml(recommendation.one_question_id || "-")}</code></p>
      <p>10 题核验：<code>${escapeHtml(tenQuestionIds.join(",") || "-")}</code></p>
      <div class="smoke-plan-actions">
        ${recommendation.one_question_id ? copyButtonHtml(recommendation.one_question_id, "复制 1 题 ID") : ""}
        ${tenQuestionIds.length ? copyButtonHtml(tenQuestionIds.join(","), "复制 10 题 IDs") : ""}
        ${recommendation.sample_id ? `<button class="secondary compact-button" type="button" data-locomo-sample="${escapeHtml(recommendation.sample_id)}" data-locomo-view="evalView">切到推荐 conv</button>` : ""}
        ${tenQuestionIds.length ? `<button class="secondary compact-button" type="button" data-locomo-sample="${escapeHtml(recommendation.sample_id || "all")}" data-locomo-questions="${escapeHtml(tenQuestionIds.join(","))}" data-locomo-view="evalView">加载推荐 10 题</button>` : ""}
        <button class="secondary compact-button" type="button" data-view-jump="openvikingView">去记忆导入</button>
      </div>
    </article>
    <article class="smoke-plan-card">
      <span>当前路径</span>
      <strong>数据集与记忆空间</strong>
      <dl class="smoke-plan-kv">
        <dt>数据集</dt><dd><code>${escapeHtml(dataset.path || "-")}</code></dd>
        <dt>工作空间</dt><dd><code>${escapeHtml(workspace.workspace || "-")}</code></dd>
        <dt>存储根</dt><dd><code>${escapeHtml(workspace.storage_root || "-")}</code></dd>
      </dl>
    </article>
    ${steps.map((step, index) => `
      <article class="smoke-plan-card step ${preflightTone(step.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(String(index + 1).padStart(2, "0"))} · ${escapeHtml(step.view || "-")}</span>
          <em>${escapeHtml(preflightLabel(step.status))}</em>
        </div>
        <strong>${escapeHtml(step.title || step.id || "-")}</strong>
        <p>${escapeHtml(step.action || "")}</p>
        <p><b>预期：</b>${escapeHtml(step.expected || "")}</p>
        ${step.detail ? `<p>${escapeHtml(step.detail)}</p>` : ""}
        <div class="smoke-plan-actions">
          ${step.view ? `<button class="secondary compact-button" type="button" data-view-jump="${escapeHtml(step.view)}">去执行</button>` : ""}
          ${step.button ? `<span>${escapeHtml(step.button)}</span>` : ""}
        </div>
      </article>
    `).join("")}
    ${examples.length ? `
      <article class="smoke-plan-card examples">
        <span>题目例子</span>
        <strong>推荐 QA 预览</strong>
        <div class="smoke-question-list">
          ${examples.map((item) => `
            <section>
              <strong>${escapeHtml(item.question_id || "-")} · C${escapeHtml(item.category || "-")}</strong>
              <p>${escapeHtml(item.question || "")}</p>
              <small>Gold: ${escapeHtml(item.answer || "")}</small>
            </section>
          `).join("")}
        </div>
      </article>
    ` : ""}
    ${commands.length ? `
      <article class="smoke-plan-card commands">
        <span>命令</span>
        <strong>只读检查命令</strong>
        <div class="setup-pack-command-list">
          ${commands.map((item) => `
            <div class="setup-pack-command">
              <div>
                <strong>${escapeHtml(item.title || "命令")}</strong>
                <code>${escapeHtml(item.command || "")}</code>
              </div>
              ${item.command ? copyButtonHtml(item.command, "复制") : ""}
            </div>
          `).join("")}
        </div>
      </article>
    ` : ""}
  `;
  bindCopyButtons(`#${targetId}`);
  bindViewJumpButtons(`#${targetId}`);
  bindLocomoPresetButtons(`#${targetId}`);
}

async function runSmokePlan(targetIds = ["smokePlanPanel", "smokePlanReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderSmokePlan({loading: true}, id));
  try {
    const data = await api("/api/smoke-plan", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderSmokePlan(data, id));
    if (!silent) toast(data.status === "ok" ? "小样本核验计划已生成" : "小样本核验计划需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      score: 0,
      checked_at: new Date().toISOString(),
      backend: currentMemoryBackend(),
      account: currentAccount(),
      recommendation: {},
      steps: [{
        id: "smoke_plan_request",
        title: "小样本核验计划请求失败",
        status: "fail",
        view: "systemConfigView",
        action: error.message || "无法读取小样本核验计划。",
        expected: "检查 Web 服务是否启动。",
      }],
      commands: [],
      markdown: `# LoCoMo Memory Eval Small-Sample Validation Plan\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderSmokePlan(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

async function runHandoffAudit(targetIds = ["handoffAuditPanel", "handoffAuditReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderHandoffAudit({loading: true}, id));
  try {
    const data = await api("/api/handoff-audit", {method: "POST", body: "{}"});
    targetIds.forEach((id) => renderHandoffAudit(data, id));
    if (!silent) toast(data.status === "ok" ? "交付审计通过" : "交付审计需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      checked_at: new Date().toISOString(),
      audited_files: 0,
      checks: [{
        id: "audit_request",
        title: "审计请求失败",
        status: "fail",
        severity: "required",
        detail: error.message || "无法运行交付审计。",
        evidence: [],
      }],
      summary: `LoCoMo Memory Eval Handoff Audit\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderHandoffAudit(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

async function runDeliveryBoundaryGate(targetIds = ["deliveryBoundaryPanel", "deliveryBoundaryReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderDeliveryBoundaryGate({loading: true}, id));
  try {
    const data = await api("/api/delivery-boundary", {method: "POST", body: "{}"});
    targetIds.forEach((id) => renderDeliveryBoundaryGate(data, id));
    if (!silent) toast(data.status === "ok" ? "交付边界通过" : "交付边界需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      checked_at: new Date().toISOString(),
      scope: "OpenViking + EchoMemory",
      agent_label: "MemoryBench Agent",
      expected_backends: ["echomemory", "openviking"],
      registered_backends: [],
      sidebar: [],
      public_files: [],
      checks: [{
        id: "delivery_boundary_request",
        title: "交付边界请求失败",
        status: "fail",
        detail: error.message || "无法读取交付边界。",
        evidence: [],
      }],
      markdown: `# Delivery Boundary Gate\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderDeliveryBoundaryGate(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderAdapterDoctor(data, targetId = "adapterDoctorPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>等待自检</span>
        <strong>只检查后端边界和契约</strong>
        <p>不会导入数据、检索记忆或返回 API 密钥。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>正在自检</span>
        <strong>检查 OpenViking + EchoMemory</strong>
        <p>验证已注册后端、必需能力和 adapter 方法。</p>
      </article>
    `;
    return;
  }
  const backends = Array.isArray(data.backends) ? data.backends : [];
  const registered = Array.isArray(data.registered_backends) ? data.registered_backends : [];
  const expected = Array.isArray(data.expected_backends) ? data.expected_backends : [];
  const missing = Array.isArray(data.missing_backends) ? data.missing_backends : [];
  const unexpected = Array.isArray(data.unexpected_backends) ? data.unexpected_backends : [];
  target.innerHTML = `
    <article class="handoff-audit-card summary ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>记忆后端自检</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${escapeHtml(data.status === "ok" ? "只注册 OpenViking + EchoMemory" : "后端边界需要确认")}</strong>
      <p>已注册：${escapeHtml(registered.join(", ") || "-")} · 期望：${escapeHtml(expected.join(", ") || "-")}</p>
      ${data.markdown ? copyButtonHtml(data.markdown, "复制自检 Markdown 文本") : ""}
    </article>
    ${missing.length || unexpected.length ? `
      <article class="handoff-audit-card bad">
        <span>边界异常</span>
        <strong>缺失或多余后端</strong>
        <p>缺失：${escapeHtml(missing.join(", ") || "无")} · 多余：${escapeHtml(unexpected.join(", ") || "无")}</p>
      </article>
    ` : ""}
    ${backends.map((backend) => `
      <article class="handoff-audit-card ${preflightTone(backend.contract_status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(backend.id || "-")}</span>
          <em>${escapeHtml(preflightLabel(backend.contract_status))}</em>
        </div>
        <strong>${escapeHtml(backend.name || backend.id || "-")} · ${escapeHtml(backend.status || "-")}</strong>
        <p>能力 ${escapeHtml((backend.capabilities || []).length)} · 缺失必需项：${escapeHtml((backend.missing_required || []).join(", ") || "无")}</p>
        ${(backend.missing_recommended || []).length ? `<p>缺失建议项：${escapeHtml((backend.missing_recommended || []).join(", "))}</p>` : ""}
      </article>
    `).join("")}
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runAdapterDoctor(targetIds = ["adapterDoctorPanel", "adapterDoctorReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderAdapterDoctor({loading: true}, id));
  try {
    const data = await api("/api/adapter-doctor", {method: "POST", body: "{}"});
    targetIds.forEach((id) => renderAdapterDoctor(data, id));
    if (!silent) toast(data.status === "ok" ? "记忆后端自检通过" : "记忆后端自检需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      expected_backends: ["echomemory", "openviking"],
      registered_backends: [],
      missing_backends: [],
      unexpected_backends: [],
      backends: [],
      markdown: `# Memory Backend Adapter Doctor\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderAdapterDoctor(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderAgentAlignment(data, targetId = "agentAlignmentPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>等待检查</span>
        <strong>读取最近 LoCoMo QA 任务</strong>
        <p>不会调用模型、不会读取 API 密钥，只检查已有任务清单、摘要和报告证据。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>正在检查</span>
        <strong>读取 MemoryBench Agent 对齐证据</strong>
        <p>检查提示词、召回条数、工具循环、工具集合、额外兜底和同判分模型对比。</p>
      </article>
    `;
    return;
  }
  const latest = data.latest_backend_run || {};
  const latestRun = latest.run || {};
  const alignment = latest.alignment || {};
  const checks = Array.isArray(alignment.checks) ? alignment.checks : [];
  const defaults = data.default_profile || {};
  const sameJudge = data.same_judge_evidence || {};
  const nextActions = Array.isArray(data.next_actions) ? data.next_actions : [];
  const sameJudgeText = sameJudge.status === "ok"
    ? `对齐结果 ${percent(sameJudge.aligned?.accuracy)} / 原生结果 ${percent(sameJudge.native?.accuracy)} / 差值 ${sameJudge.delta_pp ?? "-"}pp`
    : (sameJudge.detail || "没有同判分模型对比证据");
  target.innerHTML = `
    <article class="handoff-audit-card summary ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>Agent 可比性门禁</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${escapeHtml(alignment.title || "等待可比 LoCoMo 结果")}</strong>
      <p>${escapeHtml(alignment.detail || "需要先生成 LoCoMo QA 结果。")}</p>
      <p>账户 ${escapeHtml(data.account || currentAccount())} · 后端 ${escapeHtml(memoryBackendLabel(data.backend || currentMemoryBackend()))} · 来源 ${escapeHtml(data.backend_source || "当前页面")}</p>
      ${data.markdown ? copyButtonHtml(data.markdown, "复制 Agent 对齐 Markdown 文本") : ""}
    </article>
    <article class="handoff-audit-card ${preflightTone(alignment.status)}">
      <span>最新 ${escapeHtml(memoryBackendLabel(data.backend || currentMemoryBackend()))} LoCoMo QA</span>
      <strong>${escapeHtml(latestRun.id || "-")} · ${escapeHtml(latestRun.rows ?? "-")} 行 · ${escapeHtml(percent(latestRun.accuracy))}</strong>
      <p>${escapeHtml(latestRun.created_at || "")}</p>
      <p><code>${escapeHtml(latestRun.run_dir || latestRun.output_file || "-")}</code></p>
    </article>
    <article class="handoff-audit-card ${sameJudge.status === "ok" ? "ok" : "warn"}">
      <span>同判分模型对比证据</span>
      <strong>${escapeHtml(sameJudgeText)}</strong>
      <p>${escapeHtml(sameJudge.run_dir || "用于确认 MemoryBench Agent 对齐结果与参考链路是否可比。")}</p>
    </article>
    <article class="handoff-audit-card">
      <span>默认可比参数</span>
      <strong>${RETRIEVAL_COUNT_LABEL} ${escapeHtml(defaults.initial_search_limit ?? "-")} · ${TOOL_SEARCH_LABEL} ${escapeHtml(defaults.tool_search_limit ?? "-")} · ${MAX_ITERATION_LABEL} ${escapeHtml(defaults.max_iterations ?? "-")}</strong>
      <p>初始阈值 ${escapeHtml(defaults.initial_score_threshold ?? "-")} / 工具阈值 ${escapeHtml(defaults.tool_min_score ?? "-")} · 工具集合 ${escapeHtml(defaults.tool_set || "-")}</p>
    </article>
    ${checks.map((check) => `
      <article class="handoff-audit-card ${preflightTone(check.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(check.title || check.id || "check")}</span>
          <em>${escapeHtml(preflightLabel(check.status))}</em>
        </div>
        <strong>${escapeHtml(check.status === "ok" ? "已对齐" : "需确认")}</strong>
        <p>${escapeHtml(check.detail || "")}</p>
      </article>
    `).join("")}
    <article class="handoff-audit-card ${nextActions.length ? preflightTone(data.status) : "ok"}">
      <span>下一步</span>
      <strong>${nextActions.length ? "按提示处理" : "可以进入后端差异分析"}</strong>
      <ul class="setup-pack-list">
        ${(nextActions.length ? nextActions : ["继续跑 LoCoMo 小样本核验或全量运行，并导出报告。"]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </article>
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runAgentAlignment(targetIds = ["agentAlignmentPanel", "agentAlignmentReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderAgentAlignment({loading: true}, id));
  try {
    const data = await api("/api/agent-alignment", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderAgentAlignment(data, id));
    if (!silent) toast(data.status === "ok" ? "Agent 可比性通过" : "Agent 可比性需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      backend: currentMemoryBackend(),
      latest_backend_run: {},
      default_profile: {},
      same_judge_evidence: {status: "missing", detail: error.message || "无法读取 Agent 对齐门禁。"},
      next_actions: [error.message || "检查 Web 服务是否启动。"],
      markdown: `# MemoryBench Agent Alignment Gate\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderAgentAlignment(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderAccountIsolationGate(data, targetId = "accountIsolationGatePanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>等待检查</span>
        <strong>读取当前账户 workspace</strong>
        <p>不会调用模型、不会读取 API 密钥，只检查当前账户目录和共享关系。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>正在检查</span>
        <strong>读取账户隔离状态</strong>
        <p>检查当前 account、后端、workspace、storage root 和共享关系。</p>
      </article>
    `;
    return;
  }
  const current = data.current || {};
  const metrics = data.metrics || {};
  const rows = Array.isArray(data.accounts) ? data.accounts : [];
  const checks = Array.isArray(current.checks) ? current.checks : [];
  const nextActions = Array.isArray(data.next_actions) ? data.next_actions : [];
  const badge = accountIsolationStatus(current.status || data.status, current.shared_with || []);
  const statusDetail = [
    `accounts ${metrics.accounts ?? rows.length}`,
    `isolated ${metrics.isolated ?? 0}`,
    `shared ${metrics.shared ?? 0}`,
    `missing ${metrics.missing ?? 0}`,
    `not-created ${metrics.not_created ?? 0}`,
  ].join(" · ");
  target.innerHTML = `
    <article class="handoff-audit-card summary ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>账户隔离门禁</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${escapeHtml(current.id || data.active_account || currentAccount())} · ${escapeHtml(memoryBackendLabel(current.backend || currentMemoryBackend()))}</strong>
      <p>${escapeHtml(statusDetail)}</p>
      ${data.markdown ? copyButtonHtml(data.markdown, "复制账户隔离 Markdown 文本") : ""}
    </article>
    <article class="handoff-audit-card ${escapeHtml(badge.tone || preflightTone(data.status))}">
      <span>当前账户状态</span>
      <strong>${escapeHtml(badge.label || current.status || "-")}</strong>
      <p>${escapeHtml(current.layout || "workspace/<account>")}</p>
      <p><code>${escapeHtml(current.storage_root || current.workspace || "-")}</code></p>
    </article>
    ${checks.map((check) => `
      <article class="handoff-audit-card ${preflightTone(check.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(check.title || check.id || "check")}</span>
          <em>${escapeHtml(preflightLabel(check.status))}</em>
        </div>
        <strong>${escapeHtml(check.status === "ok" ? "可验证" : "需处理")}</strong>
        <p>${escapeHtml(check.detail || "")}</p>
      </article>
    `).join("")}
    <article class="handoff-audit-card">
      <span>账户列表</span>
      <strong>${escapeHtml(rows.length)} 个账户</strong>
      <div class="setup-pack-command-list compact">
        ${rows.slice(0, 8).map((row) => {
          const rowBadge = accountIsolationStatus(row.status, row.shared_with || []);
          return `
            <div class="setup-pack-command">
              <div>
                <strong>${escapeHtml(row.id || "-")} · ${escapeHtml(memoryBackendLabel(row.backend || "openviking"))}</strong>
                <code>${escapeHtml(row.storage_root || row.workspace || "-")}</code>
              </div>
              <span class="mini-status ${escapeHtml(rowBadge.tone || "")}">${escapeHtml(rowBadge.label || row.status || "-")}</span>
            </div>
          `;
        }).join("") || "<p>暂无账户记录。</p>"}
      </div>
    </article>
    <article class="handoff-audit-card ${nextActions.length ? preflightTone(data.status) : "ok"}">
      <span>下一步</span>
      <strong>${nextActions.length ? "按提示处理" : "可以继续 LoCoMo 小样本核验"}</strong>
      <ul class="setup-pack-list">
        ${(nextActions.length ? nextActions : ["账户隔离正常，可以继续导入、QA、判分和报告生成。"]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </article>
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runAccountIsolation(targetIds = ["accountIsolationGatePanel", "accountIsolationReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderAccountIsolationGate({loading: true}, id));
  try {
    const data = await api("/api/account-isolation", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderAccountIsolationGate(data, id));
    if (!silent) toast(data.status === "ok" ? "账户隔离通过" : "账户隔离需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      active_account: currentAccount(),
      current: {id: currentAccount(), backend: currentMemoryBackend(), status: "missing_workspace"},
      accounts: [],
      metrics: {},
      next_actions: [error.message || "检查 Web 服务是否启动。"],
      markdown: `# Account Isolation Gate\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderAccountIsolationGate(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderSetupPack(data, targetId = "setupPackPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="setup-pack-card muted">
        <span>等待生成</span>
        <strong>只生成占位符</strong>
        <p>当前交付边界只包含 OpenViking 和 EchoMemory，不会读取或返回真实 API 密钥。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="setup-pack-card muted">
        <span>正在生成</span>
        <strong>读取当前账户配置</strong>
        <p>生成 env 模板、启动命令、验证命令和安全外发清单。</p>
      </article>
    `;
    return;
  }
  const commands = Array.isArray(data.commands) ? data.commands : [];
  const uiSteps = Array.isArray(data.ui_steps) ? data.ui_steps : [];
  const doNotShare = Array.isArray(data.do_not_share) ? data.do_not_share : [];
  const readiness = data.readiness || {};
  const nextActions = Array.isArray(readiness.next_actions) ? readiness.next_actions : [];
  const envTemplate = data.env_template || "";
  target.innerHTML = `
    <article class="setup-pack-card hero ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>接入配置向导</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${escapeHtml(memoryBackendLabel(data.backend))} · ${escapeHtml(data.account || currentAccount())}</strong>
      <p>门禁 ${escapeHtml(readiness.status || "-")} · ${escapeHtml(readiness.score ?? "-")}/100。模板只包含占位符，不包含真实密钥。</p>
      ${data.summary ? copyButtonHtml(data.summary, "复制公开版接入摘要") : ""}
    </article>
    <article class="setup-pack-card">
      <span>env.local 模板</span>
      <strong>复制后只在本机填写</strong>
      <pre class="setup-pack-block">${escapeHtml(envTemplate || "# 暂无模板")}</pre>
      ${envTemplate ? copyButtonHtml(envTemplate, "复制 env.local") : ""}
    </article>
    <article class="setup-pack-card">
      <span>启动和验证命令</span>
      <strong>按顺序执行</strong>
      <div class="setup-pack-command-list">
        ${commands.map((item) => `
          <div class="setup-pack-command">
            <div>
              <strong>${escapeHtml(item.title || "命令")}</strong>
              <code>${escapeHtml(item.command || "")}</code>
            </div>
            ${item.command ? copyButtonHtml(item.command, "复制") : ""}
          </div>
        `).join("") || "<p>暂无命令。</p>"}
      </div>
    </article>
    <article class="setup-pack-card">
      <span>页面操作</span>
      <strong>推荐小样本核验顺序</strong>
      <ol class="setup-pack-list">
        ${uiSteps.map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>先运行启动门禁，再执行 LoCoMo 小样本核验。</li>"}
      </ol>
    </article>
    <article class="setup-pack-card warn">
      <span>不要外发</span>
      <strong>这些内容留在测试者本机</strong>
      <ul class="setup-pack-list">
        ${doNotShare.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </article>
    <article class="setup-pack-card ${nextActions.length ? preflightTone(readiness.status) : "ok"}">
      <span>下一步</span>
      <strong>${nextActions.length ? "先处理门禁提示" : "可以开始测试"}</strong>
      <ul class="setup-pack-list">
        ${(nextActions.length ? nextActions : ["进入 LoCoMo 评测，读取默认数据后导入一个 conversation 做小样本核验。"]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </article>
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runSetupPack(targetIds = ["setupPackPanel", "setupPackReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderSetupPack({loading: true}, id));
  try {
    const data = await api("/api/setup-pack", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderSetupPack(data, id));
    if (!silent) toast("已生成接入配置模板");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      backend: currentMemoryBackend(),
      account: currentAccount(),
      readiness: {status: "fail", score: 0, next_actions: [error.message || "检查服务是否启动"]},
      env_template: "",
      commands: [],
      ui_steps: [],
      do_not_share: [".env.local", "runs/", "workspaces", "真实 API 密钥"],
      summary: `LoCoMo Memory Eval Connection Guide\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderSetupPack(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderHandoffPackage(data, targetId = "handoffPackagePanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-package-card muted">
        <span>等待生成</span>
        <strong>不打包、不读取密钥</strong>
        <p>用于外发前确认当前交付只包含 OpenViking + EchoMemory。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-package-card muted">
        <span>正在生成</span>
        <strong>整理可外发清单</strong>
        <p>读取交付审计、验收矩阵和必需文件状态。</p>
      </article>
    `;
    return;
  }
  const include = Array.isArray(data.include) ? data.include : [];
  const exclude = Array.isArray(data.exclude) ? data.exclude : [];
  const verify = Array.isArray(data.verify) ? data.verify : [];
  const missing = Array.isArray(data.missing_include) ? data.missing_include : [];
  const readiness = data.readiness || {};
  const acceptance = data.acceptance || {};
  const audit = data.audit || {};
  const includeRows = include.map((item) => `
    <li class="${item.exists ? "ok" : "bad"}">
      <code>${escapeHtml(item.path || "-")}</code>
      <span>${escapeHtml(item.reason || "")}</span>
    </li>
  `).join("");
  const excludeRows = exclude.map((item) => `
    <li class="warn">
      <code>${escapeHtml(item.path || "-")}</code>
      <span>${escapeHtml(item.reason || "")}</span>
    </li>
  `).join("");
  const verifyRows = verify.map((item) => `
    <div class="handoff-package-command">
      <div>
        <strong>${escapeHtml(item.title || "验证")}</strong>
        <code>${escapeHtml(item.command || "")}</code>
      </div>
      ${item.command ? copyButtonHtml(item.command, "复制") : ""}
    </div>
  `).join("");
  target.innerHTML = `
    <article class="handoff-package-card hero ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>外发清单</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${escapeHtml(data.scope || "OpenViking + EchoMemory")} · ${escapeHtml(data.account || currentAccount())}</strong>
      <p>门禁 ${escapeHtml(readiness.status || "-")} ${escapeHtml(readiness.score ?? "-")}/100 · 验收 ${escapeHtml(acceptance.status || "-")} ${escapeHtml(acceptance.score ?? "-")}/100 · 审计必需失败 ${escapeHtml(audit.required_failures ?? 0)}</p>
      ${data.markdown ? copyButtonHtml(data.markdown, "复制公开版外发清单") : ""}
    </article>
    <article class="handoff-package-card ${missing.length ? "bad" : "ok"}">
      <span>可外发</span>
      <strong>${missing.length ? `缺少 ${missing.length} 个必需文件` : "必需源码和模板齐全"}</strong>
      <ul class="handoff-package-list">${includeRows || "<li><span>暂无可外发项。</span></li>"}</ul>
    </article>
    <article class="handoff-package-card warn">
      <span>必须排除</span>
      <strong>历史运行、workspace 和密钥不要带走</strong>
      <ul class="handoff-package-list">${excludeRows}</ul>
    </article>
    <article class="handoff-package-card commands">
      <span>接收后验证</span>
      <strong>按顺序跑这些命令</strong>
      <div class="handoff-package-command-list">${verifyRows || "<p>暂无验证命令。</p>"}</div>
    </article>
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runHandoffPackage(targetIds = ["handoffPackagePanel", "handoffPackageReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderHandoffPackage({loading: true}, id));
  try {
    const data = await api("/api/handoff-package", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    targetIds.forEach((id) => renderHandoffPackage(data, id));
    if (!silent) toast(data.status === "ok" ? "外发清单已就绪" : "外发清单需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      account: currentAccount(),
      backend: currentMemoryBackend(),
      scope: "OpenViking + EchoMemory",
      include: [],
      exclude: [{path: ".env.local / runs / workspaces", reason: "不要外发本机敏感数据。"}],
      verify: [],
      missing_include: [],
      readiness: {status: "fail", score: 0},
      acceptance: {status: "fail", score: 0},
      audit: {status: "fail", required_failures: 1},
      markdown: `# LoCoMo Memory Eval Handoff Checklist\n\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderHandoffPackage(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function renderEchoMemContract(data, targetId = "echomemContractPanel") {
  const target = $(targetId);
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>等待检查</span>
        <strong>不导入、不检索、不调用模型</strong>
        <p>只检查源码布局、脚本契约和本机配置是否满足 EchoMemory 接入要求。</p>
      </article>
    `;
    return;
  }
  if (data.loading) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>正在检查</span>
        <strong>读取 EchoMemory 接入契约</strong>
        <p>只读检查，不会写 workspace，也不会返回 API 密钥。</p>
      </article>
    `;
    return;
  }
  const checks = Array.isArray(data.checks) ? data.checks : [];
  const requiredFailures = Array.isArray(data.required_failures) ? data.required_failures.length : checks.filter((item) => item.severity === "required" && item.status === "fail").length;
  const warnings = Array.isArray(data.warnings) ? data.warnings.length : checks.filter((item) => item.status === "warn").length;
  target.innerHTML = `
    <article class="handoff-audit-card summary ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>EchoMemory 接入契约</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${requiredFailures ? `${requiredFailures} 个必需失败` : warnings ? `${warnings} 个建议确认` : "契约满足，可以进入 LoCoMo 小样本核验"}</strong>
      <p>${escapeHtml(data.checked_at || "")} · ${escapeHtml(data.root || "-")}</p>
      ${data.summary ? copyButtonHtml(data.summary, "复制公开版契约摘要") : ""}
    </article>
    ${checks.map((check) => `
      <article class="handoff-audit-card ${preflightTone(check.status)}">
        <div class="preflight-card-head">
          <span>${escapeHtml(check.severity === "required" ? "必需" : check.severity === "recommended" ? "建议" : "信息")}</span>
          <em>${escapeHtml(preflightLabel(check.status))}</em>
        </div>
        <strong>${escapeHtml(check.title || check.id || "-")}</strong>
        <p>${escapeHtml(check.detail || "")}</p>
        ${renderAuditEvidence(check.evidence)}
      </article>
    `).join("")}
  `;
  bindCopyButtons(`#${targetId}`);
}

async function runEchoMemContract(targetIds = ["echomemContractPanel", "echomemContractReadmePanel"], silent = false) {
  targetIds.forEach((id) => renderEchoMemContract({loading: true}, id));
  try {
    const payload = currentPreflightPayload();
    payload.config = {...(payload.config || {}), memoryBackend: "echomemory"};
    const data = await api("/api/echomem-contract", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    targetIds.forEach((id) => renderEchoMemContract(data, id));
    if (!silent) toast(data.status === "ok" ? "EchoMemory 接入契约通过" : "EchoMemory 接入契约需要确认");
    return data;
  } catch (error) {
    const data = {
      status: "fail",
      checked_at: new Date().toISOString(),
      root: "",
      checks: [{
        id: "contract_request",
        title: "契约检查请求失败",
        status: "fail",
        severity: "required",
        detail: error.message || "无法运行 EchoMemory 接入契约检查。",
        evidence: [],
      }],
      required_failures: [{}],
      warnings: [],
      summary: `EchoMemory Connection Contract\n- Status: fail\n- Error: ${error.message || "unknown"}`,
    };
    targetIds.forEach((id) => renderEchoMemContract(data, id));
    if (!silent) toast(error.message);
    return data;
  }
}

function currentPreflightPayload() {
  const localCfg = readAccountConfig(currentAccount());
  const agentToken = $("systemAgentToken")?.value.trim() || localCfg.agentToken || "";
  const judgeToken = $("systemJudgeToken")?.value.trim() || $("judgeToken")?.value.trim() || localCfg.judgeToken || "";
  const memoryToken = $("systemMemoryToken")?.value.trim() || $("ovVlmApiKey")?.value.trim() || localCfg.memoryInjectToken || "";
  const agentCfg = agentModelConfig();
  return {
    account: currentAccount(),
    dataset: $("data")?.value.trim() || "",
    config: {
      memoryBackend: currentMemoryBackend(),
      ovHost: $("ovHost")?.value.trim() || "",
      ovPort: $("ovPort")?.value.trim() || "",
      ovWorkspace: $("ovWorkspace")?.value.trim() || "",
      memoryWorkspace: $("memoryWorkspace")?.value.trim() || $("ovWorkspace")?.value.trim() || "",
      echomemRoot: $("echomemRoot")?.value.trim() || localCfg.echomemRoot || "",
      echomem_root: $("echomemRoot")?.value.trim() || localCfg.echomemRoot || "",
      memoryUserId: $("memoryUserId")?.value.trim() || localCfg.memoryUserId || "default",
      memoryAgentId: $("memoryAgentId")?.value.trim() || localCfg.memoryAgentId || "default",
      judgeBaseUrl: $("judgeBaseUrl")?.value.trim() || "",
      judgeModel: $("judgeModel")?.value.trim() || "",
      answerBaseUrl: agentCfg.baseUrl,
      answerModel: agentCfg.model,
      answerTokenSet: Boolean(agentToken),
      judgeTokenSet: Boolean(judgeToken),
      echomemTokenSet: Boolean(memoryToken),
      echomemEmbeddingTokenSet: Boolean(memoryToken),
      echomemChatTokenSet: Boolean(memoryToken),
    },
  };
}

function renderSystemPreflight(data = state.systemPreflight) {
  const target = $("systemPreflightPanel");
  if (!target) return;
  if (state.systemPreflightLoading) {
    target.innerHTML = `
      <article class="preflight-card muted wide">
        <span>正在检查</span>
        <strong>读取当前账户配置</strong>
        <p>检查后端、目录、数据集、模型配置和安全项。</p>
      </article>
    `;
    return;
  }
  if (!data) {
    target.innerHTML = `
      <article class="preflight-card muted wide">
        <span>等待检查</span>
        <strong>读取当前配置后显示</strong>
        <p>不会返回 API 密钥，只显示是否已配置。</p>
      </article>
    `;
    return;
  }
  const backendAdapter = data.backend_adapter || data.plugin || {};
  const workspace = data.workspace || {};
  const dataset = data.dataset || {};
  const models = data.models || {};
  const runtime = data.runtime || {};
  const security = data.security || {};
  const datasetLabel = dataset.format === "locomo"
    ? `${dataset.samples ?? 0} conv / ${dataset.questions ?? 0} QA`
    : (dataset.message || dataset.format || "数据集未就绪");
  const modelLabel = [
    models.answer?.model ? `回答 ${models.answer.model}` : "",
    models.judge?.model ? `判分 ${models.judge.model}` : "",
  ].filter(Boolean).join(" · ") || "模型未配置";
  target.innerHTML = `
    <article class="preflight-card summary ${preflightTone(data.status)}">
      <div class="preflight-card-head">
        <span>总体</span>
        <em>${escapeHtml(preflightLabel(data.status))}</em>
      </div>
      <strong>${escapeHtml(memoryBackendLabel(data.backend))} · ${escapeHtml(data.account || currentAccount())}</strong>
      <p>${escapeHtml(data.checked_at || "")}</p>
      ${data.share_summary ? copyButtonHtml(data.share_summary, "复制公开版预检") : ""}
    </article>
    ${preflightCard("记忆后端", backendAdapter.status, memoryBackendLabel(data.backend), [
      backendAdapter.registered ? "后端已接入" : "后端未接入",
      backendAdapter.contract_status ? `契约：${backendContractLabel(backendAdapter.contract_status)}` : "",
      backendAdapter.missing_required_capabilities?.length ? `缺少必需能力：${backendAdapter.missing_required_capabilities.join(", ")}` : "必需能力完整",
      backendAdapter.missing_required_methods?.length ? `缺少必需方法：${backendAdapter.missing_required_methods.join(", ")}` : "必需方法完整",
      backendAdapter.missing_recommended_capabilities?.length ? `建议补齐能力：${backendAdapter.missing_recommended_capabilities.join(", ")}` : "",
      backendAdapter.missing_optional_methods?.length ? `建议补齐方法：${backendAdapter.missing_optional_methods.join(", ")}` : "",
    ])}
    ${preflightCard("目录", workspace.status, workspace.storage_root || workspace.workspace || "-", [
      workspace.workspace_exists ? "目录存在" : "目录不存在",
      workspace.storage_root_exists ? "存储根目录存在" : "存储根目录未创建",
      workspace.layout ? `布局：${workspace.layout}` : "",
    ])}
    ${preflightCard("数据集", dataset.status, datasetLabel, [
      dataset.exists ? "文件存在" : "文件不存在",
      dataset.path || "",
    ])}
    ${preflightCard("模型", models.status, modelLabel, [
      models.answer?.base_url_set ? "回答模型地址已配置" : "回答模型地址未配置",
      models.judge?.base_url_set ? "判分地址已配置" : "判分地址未配置",
      models.echomemory?.embedding_token_set ? "EchoMemory embedding 密钥已配置" : "EchoMemory embedding 密钥未检测到",
      models.echomemory?.chat_token_set ? "EchoMemory chat 密钥已配置" : "EchoMemory chat 密钥未检测到",
      (models.answer?.token_set || models.judge?.token_set || models.echomemory?.embedding_token_set || models.echomemory?.chat_token_set) ? "至少一个密钥来源已设置" : "未检测到环境密钥",
    ])}
    ${preflightCard("运行时", runtime.status, runtime.url || runtime.root || runtime.label || "-", [
      runtime.kind || "",
      runtime.probe?.ok ? "服务探测成功" : (runtime.message || runtime.probe?.error || ""),
      runtime.explicit_root === false && !runtime.default_root ? "EchoMemory root 未显式设置" : "",
      runtime.default_root ? "使用官方默认源码路径" : "",
      runtime.sdk_layout ? "SDK 目录结构可用" : "",
      runtime.source?.required_tag ? `要求版本：${runtime.source.required_tag}` : "",
      runtime.source?.describe ? `当前版本：${runtime.source.describe}` : "",
      runtime.source?.short_commit ? `commit：${runtime.source.short_commit}` : "",
      runtime.version_ok === false ? "版本未对齐" : "",
      runtime.next_action || "",
    ])}
    ${preflightCard("Security", security.status, security.secrets_redacted ? "密钥已脱敏" : "需要检查", [
      security.token_values_returned ? "预检返回了 token 值" : "预检不返回 token 值",
      Array.isArray(security.do_not_share) ? `不要外发：${security.do_not_share.join(", ")}` : "",
    ])}
    ${renderPreflightFixes(data.fixes)}
  `;
}

function renderEchoMemorySourceCard(data = state.echomemorySourceStatus) {
  const target = $("echomemorySourceCard");
  if (!target) return;
  if (!data) {
    target.innerHTML = `
      <article class="handoff-audit-card muted">
        <span>等待读取</span>
        <strong>检查 EchoMemory 本地源码</strong>
        <p>不读取密钥，不调用模型。</p>
      </article>
    `;
    return;
  }
  const runtime = data.runtime || {};
  const source = runtime.source || {};
  const ok = Boolean(runtime.version_ok);
  target.innerHTML = `
    <article class="handoff-audit-card ${preflightTone(ok ? "ok" : "fail")}">
      <span>${ok ? "版本已对齐" : "版本未对齐"}</span>
      <strong>${escapeHtml(source.describe || source.tag || "unknown")} · ${escapeHtml(source.short_commit || "-")}</strong>
      <p>要求：${escapeHtml(source.required_tag || "version_0.0.7")}</p>
      <p>路径：${escapeHtml(runtime.root || "-")}</p>
      <p>${escapeHtml(runtime.message || "")}</p>
    </article>
  `;
}

async function refreshEchoMemorySourceCard() {
  if (!$("echomemorySourceCard")) return null;
  try {
    const data = await api(`/api/system-preflight?backend=echomemory&account=${encodeURIComponent(currentAccount())}`);
    state.echomemorySourceStatus = data;
    renderEchoMemorySourceCard(data);
    return data;
  } catch (error) {
    state.echomemorySourceStatus = {
      runtime: {
        status: "fail",
        version_ok: false,
        message: error.message || "读取 EchoMemory 源码版本失败",
        source: {required_tag: "version_0.0.7"},
      },
    };
    renderEchoMemorySourceCard(state.echomemorySourceStatus);
    return state.echomemorySourceStatus;
  }
}

async function runSystemPreflight(silent = false) {
  const panel = $("systemPreflightPanel");
  if (!panel) return null;
  state.systemPreflightLoading = true;
  renderSystemPreflight();
  try {
    const data = await api("/api/system-preflight", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    state.systemPreflight = data;
    return data;
  } catch (error) {
    state.systemPreflight = {
      status: "fail",
      account: currentAccount(),
      backend: currentMemoryBackend(),
      backend_adapter: {status: "fail", registered: false, missing_capabilities: []},
      workspace: {status: "fail", workspace: "", storage_root: ""},
      dataset: {status: "fail", message: error.message},
      models: {status: "warn"},
      runtime: {status: "fail", message: error.message},
      security: {status: "ok", secrets_redacted: true, token_values_returned: false},
    };
    if (!silent) toast(error.message);
    return state.systemPreflight;
  } finally {
    state.systemPreflightLoading = false;
    renderSystemPreflight();
    renderQaReadinessPanel();
    refreshLocomoQaActionLabels();
    bindCopyButtons("#systemPreflightPanel");
  }
}

function backendStatusLabel(backend) {
  if (backend.status === "active") return "当前可用";
  if (backend.status === "planned") return "规划中";
  if (backend.status === "missing") return "未检测到";
  if (backend.status === "experimental") return "实验中";
  return backend.status || "-";
}

function renderBackendContract(contract = {}) {
  if (!contract || !Object.keys(contract).length) return "";
  const tone = preflightTone(contract.status);
  const missingRequired = [
    ...(contract.missing_required_capabilities || []).map((item) => `能力 ${item}`),
    ...(contract.missing_required_methods || []).map((item) => `方法 ${item}`),
  ];
  const missingOptional = [
    ...(contract.missing_recommended_capabilities || []).map((item) => `能力 ${item}`),
    ...(contract.missing_optional_methods || []).map((item) => `方法 ${item}`),
  ];
  const requiredText = missingRequired.length ? missingRequired.join("、") : "必需能力和方法完整";
  const optionalText = missingOptional.length ? `建议补齐：${missingOptional.join("、")}` : "建议项完整";
  return `
    <div class="backend-contract ${tone}">
      <div>
        <span>后端契约</span>
        <strong>${escapeHtml(backendContractLabel(contract.status))}</strong>
      </div>
      <p>${escapeHtml(requiredText)}</p>
      <small>${escapeHtml(optionalText)}</small>
    </div>
  `;
}

function renderBackendLocalConfig(backend) {
  const info = backend.local_config || null;
  if (!info) return "";
  const configFiles = Array.isArray(info.config_files) ? info.config_files : [];
  const fileRows = configFiles.map((item) => {
    const summary = item.summary || {};
    const extensionPlugins = summary.plugins
      ? Object.entries(summary.plugins).map(([name, cfg]) => {
          const enabled = cfg?.enabled ? "已启用" : "未启用";
          const endpoint = cfg?.endpoint ? ` · ${cfg.endpoint}` : "";
          return `<li><strong>${escapeHtml(name)}</strong><span>${escapeHtml(enabled + endpoint)}</span></li>`;
        }).join("")
      : "";
    const yamlSummary = summary.echofs_base_path || summary.chat_model || summary.embedding_model
      ? `
        <li><strong>租户</strong><span>${escapeHtml(summary.tenant_id || "-")}</span></li>
        <li><strong>数据目录</strong><span>${escapeHtml(summary.echofs_base_path || "-")}</span></li>
        <li><strong>模型</strong><span>对话 ${escapeHtml(summary.chat_model || "-")} · 向量 ${escapeHtml(summary.embedding_model || "-")}</span></li>
      `
      : "";
    return `
      <article>
        <span>${escapeHtml(item.kind || "配置")}</span>
        <code>${escapeHtml(item.path || "-")}</code>
        <ul>${extensionPlugins || yamlSummary || "<li><strong>配置摘要</strong><span>已检测到配置文件</span></li>"}</ul>
      </article>
    `;
  }).join("");
  return `
    <div class="plugin-local-config">
      <div class="plugin-local-head">
        <strong>${info.detected ? "本地配置已检测到" : "未找到本地配置"}</strong>
        <span>${escapeHtml(info.detected ? "只读继承；不启动、不写配置" : "可通过环境变量指定")}</span>
      </div>
      <div class="plugin-path-grid">
        <article><span>代码目录</span><code>${escapeHtml(info.repo || "-")}</code></article>
        <article><span>服务目录</span><code>${escapeHtml(info.backend_dir || "-")}</code></article>
        <article><span>记忆模块</span><code>${escapeHtml(info.echo_memory_dir || "-")}</code></article>
        <article><span>数据目录</span><code>${escapeHtml(info.data_dir || "-")}</code></article>
      </div>
      ${fileRows ? `
        <details class="plugin-local-details">
          <summary>配置文件 ${configFiles.length} 个</summary>
          <div class="plugin-config-files">${fileRows}</div>
        </details>
      ` : ""}
    </div>
  `;
}

function renderBackendCards(backends = []) {
  const target = $("backendCards");
  if (!target) return;
  if (!backends.length) {
    target.innerHTML = `
      <article class="config-plugin-card">
        <span>记忆后端状态</span>
        <strong>暂无结果</strong>
        <p>当前服务没有返回记忆后端信息。</p>
      </article>
    `;
    return;
  }
  target.innerHTML = backends.map((backend) => {
    const active = backend.status === "active" ? " active" : "";
    const contract = backend.contract || {};
    const actions = backend.id === "openviking"
      ? `<button class="secondary" type="button" data-view-jump="openvikingView">配置 OpenViking</button>`
      : "";
    const caps = Array.isArray(backend.capabilities) && backend.capabilities.length
      ? `
        <details class="plugin-capability-details">
          <summary>能力 ${backend.capabilities.length} 项</summary>
          <ul class="plugin-capability-list">${backend.capabilities.map((cap) => `<li><strong>${escapeHtml(cap.name || "-")}</strong><span>${escapeHtml(cap.description || "")}</span></li>`).join("")}</ul>
        </details>
      `
      : `<p>当前没有公开能力说明。</p>`;
    return `
      <article class="config-plugin-card${active}">
        <span>${escapeHtml(backend.name || backend.id || "记忆后端")}</span>
        <strong>${escapeHtml(backendStatusLabel(backend))}</strong>
        <p>${escapeHtml(backend.description || "")}</p>
        <div class="plugin-meta-row">
          <em>范围：${escapeHtml(backend.config_scope === "account" ? "当前账户" : (backend.config_scope || "当前账户"))}</em>
        </div>
        ${renderBackendContract(contract)}
        ${caps}
        ${renderBackendLocalConfig(backend)}
        ${actions}
      </article>
    `;
  }).join("");
  document.querySelectorAll("#backendCards [data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
}

async function loadBackends() {
  try {
    const data = await api("/api/backends");
    renderBackendCards(data.backends || []);
  } catch (error) {
    const target = $("backendCards");
    if (target) {
      target.innerHTML = `
        <article class="config-plugin-card">
          <span>记忆后端状态</span>
          <strong>读取失败</strong>
          <p>${escapeHtml(error.message || "无法读取服务状态。")}</p>
        </article>
      `;
    }
  }
}

async function createAccount() {
  const raw = ($("accountNameInput")?.value || "").trim();
  if (!raw) {
    updateAccountActionState("请输入名称。", "warn");
    $("accountNameInput")?.focus();
    return;
  }
  const previous = currentAccount();
  const account = raw.replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "");
  if (!account) {
    updateAccountActionState("名称仅限字母、数字、_ . -", "warn");
    $("accountNameInput")?.focus();
    return;
  }
  if (readAccountList().includes(account)) {
    renderAccountSelect(account);
    applyAccountConfig(account);
    if ($("accountNameInput")) $("accountNameInput").value = "";
    setAccountCreateExpanded(false);
    updateAccountActionState(`切换：${account}`, "warn");
    return;
  }
  saveAccountList([...readAccountList(), account]);
  renderAccountSelect(account);
  initializeCleanAccountConfig(account, readAccountConfig(previous));
  if ($("accountNameInput")) $("accountNameInput").value = "";
  setAccountCreateExpanded(false);
  state.chatMessages = [];
  state.lastArchivedMessageCount = 0;
  renderChat();
  renderArchiveStatus();
  renderImportPaths();
  refreshImportedMemories().catch(() => {});
  try {
    const data = await api("/api/accounts", {
      method: "POST",
      body: JSON.stringify({account, inherit_from: previous, config: readAccountConfig(account)}),
    });
    mergeBackendAccountState(data);
    applyAccountConfig(account);
    updateAccountActionState(`已创建：${account}`, "ok");
    toast(`已创建：${account}`);
  } catch {
    updateAccountActionState(`本地：${account}`, "warn");
    toast(`已创建本地记录：${account}`);
  }
}

async function deleteCurrentAccount() {
  const account = currentAccount();
  if (account === "default") return toast("默认空间不能移除");
  const nextList = readAccountList().filter((item) => item !== account);
  saveAccountList(nextList);
  localStorage.removeItem(accountConfigKey(account));
  await api("/api/accounts/delete", {
    method: "POST",
    body: JSON.stringify({account}),
  }).then(mergeBackendAccountState).catch(() => {});
  const next = nextList[0] || "default";
  renderAccountSelect(next);
  applyAccountConfig(next);
  refreshImportedMemories().catch(() => {});
  updateAccountActionState(`已移除：${account}`, "warn");
  toast(`已移除空间：${account}`);
}

function datasetTypeLabel(format) {
  const key = String(format || "").toLowerCase();
  if (key === "locomo") return "LoCoMo";
  if (key === "chenmo") return "ChenMo";
  if (key === "longmemeval") return "LongMemEval";
  if (key === "evolvingevents") return "EvolvingEvents";
  if (key === "hotpotqa") return "HotpotQA";
  if (key === "proagentbench") return "proAgentBench";
  if (key === "tau2bench") return "Tau2-bench";
  return format ? String(format) : "数据集";
}

const LOCOMO_CATEGORY_LABELS = {
  "1": "单条记忆事实",
  "2": "时间/日期推理",
  "3": "跨记忆多跳",
  "4": "综合开放回答",
  "5": "排除题",
};

const LOCOMO_CATEGORY_HINTS = {
  "1": "从一条明确记忆中回答人物、地点、偏好等事实。",
  "2": "重点考日期、先后顺序和时间上下文。",
  "3": "需要把多条记忆或多个人物关系串起来。",
  "4": "需要归纳多个证据，答案通常更开放。",
  "5": "LoCoMo 官方统计中通常排除。",
};

const GENERIC_BENCHMARKS = {
  evolvingevents: {
    label: "EvolvingEvents",
    view: "evolvingEventsView",
    adapterFormat: "evolvingevents",
    dataInput: "evolvingEventsData",
    countInput: "evolvingEventsCount",
    kpis: "evolvingEventsKpis",
    status: "evolvingEventsStatus",
    preview: "evolvingEventsPreview",
    result: "evolvingEventsRunResult",
    progressBar: "evolvingEventsProgressBar",
    progressText: "evolvingEventsProgressText",
    logBox: "evolvingEventsLogBox",
    defaultDatasetId: "evolvingevents-sample",
    emptyPathHint: "请填写 EvolvingEvents JSON / JSONL，或点击“使用示例数据”。",
    metricNote: "输出 MemoryBench 记忆问答分数：写入事件上下文、调用当前后端检索、答案模型、判分和报告；官方 EvolvingEvents 指标单独标注，不冒充 SOTA 可比分数。",
    officialEvalAfter: false,
    requiresOfficialRunner: false,
  },
  hotpotqa: {
    label: "HotpotQA",
    view: "hotpotQaView",
    adapterFormat: "hotpotqa",
    dataInput: "hotpotQaData",
    countInput: "hotpotQaCount",
    kpis: "hotpotQaKpis",
    status: "hotpotQaStatus",
    preview: "hotpotQaPreview",
    result: "hotpotQaRunResult",
    progressBar: "hotpotQaProgressBar",
    progressText: "hotpotQaProgressText",
    logBox: "hotpotQaLogBox",
    defaultDatasetId: "hotpotqa-sample",
    preferredDatasetIds: ["hotpotqa-dev-distractor", "hotpotqa-sample"],
    emptyPathHint: "请填写 HotpotQA JSON / JSONL。",
    metricNote: "运行后自动输出 HotpotQA 答案 EM/F1；支持事实 / 联合 F1 指标需要后续生成支持句预测后才可对比官方完整榜。",
    officialEvalAfter: true,
    requiresOfficialRunner: false,
  },
  proagentbench: {
    label: "proAgentBench",
    view: "proAgentBenchView",
    adapterFormat: "proagentbench",
    dataInput: "proAgentBenchData",
    countInput: "proAgentBenchCount",
    kpis: "proAgentBenchKpis",
    status: "proAgentBenchStatus",
    preview: "proAgentBenchPreview",
    result: "proAgentBenchRunResult",
    progressBar: "proAgentBenchProgressBar",
    progressText: "proAgentBenchProgressText",
    logBox: "proAgentBenchLogBox",
    defaultDatasetId: "proagentbench-sample",
    emptyPathHint: "请填写 proAgentBench JSON / JSONL。",
    metricNote: "输出 MemoryBench 任务记忆问答分数：任务上下文写入、OpenViking 检索、答案模型、判分和报告；proAgentBench 原始主动代理指标需要官方 runner 单独标注。",
    officialEvalAfter: false,
    requiresOfficialRunner: false,
  },
  tau2bench: {
    label: "Tau2-bench",
    view: "tauBenchView",
    adapterFormat: "tau2bench",
    dataInput: "tauBenchData",
    countInput: "tauBenchCount",
    kpis: "tauBenchKpis",
    status: "tauBenchStatus",
    preview: "tauBenchPreview",
    result: "tauBenchRunResult",
    progressBar: "tauBenchProgressBar",
    progressText: "tauBenchProgressText",
    logBox: "tauBenchLogBox",
    defaultDatasetId: "tau2bench-sample",
    emptyPathHint: "请填写 Tau2-bench JSON / JSONL。",
    metricNote: "输出 MemoryBench 工具任务记忆问答分数：任务/知识上下文写入、OpenViking 检索、答案模型、判分和报告；Tau2-bench Pass^k/reward 仍以官方工具环境单独标注。",
    officialEvalAfter: false,
    requiresOfficialRunner: false,
  },
};

function benchmarkMetricNote(config) {
  return config.metricNote || "运行后输出 MemoryBench 记忆问答 + 大模型 + 判分准确率；官方原 benchmark 指标在报告中单独标注。";
}

function datasetRunnerNote(format, note = "", fallback = "") {
  const normalized = normalizeDatasetFormat(format);
  const text = String(note || "").trim();
  if (normalized === "locomo" && /只展示\s*LoCoMo|上方流程条|LoCoMo 数据结构|问答|判分|其它数据集|benchmark/i.test(text)) {
    return "数据已读取；请选择要导入的会话，确认目录后点击“导入所选对话”。";
  }
  return text || fallback;
}

function isSampleDatasetPath(path = "", record = {}) {
  const text = `${path || ""} ${record.id || ""} ${record.name || ""}`.toLowerCase();
  return /(^|[/.])[^/]*sample[^/]*\.(jsonl?|ndjson)\b/i.test(String(path || ""))
    || /\bsample\b/.test(text)
    || /\.sample\./.test(text);
}

function datasetRecordForPath(path = "", format = "") {
  const normalized = normalizeDatasetFormat(format);
  return (state.datasetRegistry || []).find((item) => datasetPathMatches(item.path, path) || datasetPathMatches(item.resolved_path, path))
    || (normalized ? (state.datasetRegistry || []).find((item) => normalizeDatasetFormat(item.format) === normalized && (datasetPathMatches(item.path, path) || datasetPathMatches(item.resolved_path, path))) : null)
    || {};
}

function datasetSizeLabel(record = {}) {
  return record.size_mb != null ? `${record.size_mb} MB` : "";
}

function benchmarkPlanLinkHtml(label = "查看正式方案") {
  return `<a class="secondary inline-action" href="/formal-benchmark-plan-20260606.html" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
}

function renderGenericGateNotice(key, gate, data = null) {
  const config = benchmarkConfig(key);
  const target = $(config.result);
  if (!target || gate.ok) return;
  target.innerHTML = `
    <p class="dataset-next-step bad-text"><strong>当前不能作为正式分数：</strong>${escapeHtml(gate.reason)}</p>
    <p class="dataset-next-step">这页会固定停留在 ${escapeHtml(config.label)}；仍可启动小样本核验，完整数据路径会走正式 MemoryBench 记忆问答链路。</p>
    <div class="panel-actions">
      ${benchmarkPlanLinkHtml()}
      <button class="secondary" type="button" data-view-jump="runsView">查看任务/报告</button>
    </div>
  `;
  target.querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
}

function genericBenchmarkRunGate(key, path = genericBenchmarkPath(key), data = null) {
  const config = benchmarkConfig(key);
  const record = (state.datasetRegistry || []).find((item) => datasetPathMatches(item.path, path) || datasetPathMatches(item.resolved_path, path)) || {};
  if (!path) return {ok: false, reason: config.emptyPathHint};
  if (isSampleDatasetPath(path, record)) {
    return {ok: false, reason: `${config.label} 当前选择的是内置 sample，只能做小样本核验，不能作为正式 benchmark 分数。请填完整数据路径。`};
  }
  if (config.requiresOfficialRunner) {
    return {ok: false, reason: `${config.label} 还没有接入官方完整 runner/指标；不能作为官方原榜分数，避免把 MemoryBench 记忆问答当 SOTA 可比分数。`};
  }
  if (data && (!data.questions || data.questions === 0)) {
    return {ok: false, reason: `${config.label} 没有识别到题目，不能启动正式评测。`};
  }
  return {ok: true, reason: ""};
}

function updateGenericRunButton(key, data = null) {
  const config = benchmarkConfig(key);
  const button = document.querySelector(`.generic-run-adapter[data-benchmark="${key}"]`);
  if (!button) return;
  const gate = genericBenchmarkRunGate(key, genericBenchmarkPath(key), data);
  button.disabled = false;
  button.title = gate.ok ? `启动 ${config.label} 正式 MemoryBench 评测或小样本测试` : `${gate.reason}；仍可启动小样本核验。`;
  button.textContent = "开始测试";
  button.dataset.disabledReason = gate.reason || "";
  renderGenericGateNotice(key, gate, data);
}

function updateAllGenericRunButtons() {
  Object.keys(GENERIC_BENCHMARKS).forEach((key) => updateGenericRunButton(key));
}

function benchmarkCount(inputId, fallback = 20) {
  const raw = String($(inputId)?.value ?? "").trim();
  if (raw === "") return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  return value <= 0 ? 0 : Math.floor(value);
}

function benchmarkCountLabel(count) {
  return Number(count) <= 0 ? "全量" : String(count);
}

function benchmarkQuestionState(key) {
  if (!state.benchmarkQuestions[key]) state.benchmarkQuestions[key] = [];
  if (!state.selectedBenchmarkQuestions[key]) state.selectedBenchmarkQuestions[key] = new Set();
  return {
    questions: state.benchmarkQuestions[key],
    selected: state.selectedBenchmarkQuestions[key],
  };
}

function benchmarkQuestionElements(key) {
  const config = benchmarkConfig(key);
  return {
    picker: $(`${config.adapterFormat}QuestionPicker`) || $(`${key}QuestionPicker`) || $(config.preview),
    search: $(`${config.adapterFormat}QuestionSearch`) || $(`${key}QuestionSearch`),
    selectedText: $(`${config.adapterFormat}SelectedText`) || $(`${key}SelectedText`),
  };
}

function filteredBenchmarkQuestions(key) {
  const {questions} = benchmarkQuestionState(key);
  const {search} = benchmarkQuestionElements(key);
  const query = String(search?.value || "").trim().toLowerCase();
  if (!query) return questions;
  return questions.filter((q) => {
    const haystack = [
      q.question_id,
      q.sample_id,
      q.question,
      q.answer,
      q.category,
      q.question_time,
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function renderBenchmarkQuestionSelection(key) {
  const config = benchmarkConfig(key);
  const {selected} = benchmarkQuestionState(key);
  const {picker, selectedText} = benchmarkQuestionElements(key);
  if (!picker) return;
  const rows = filteredBenchmarkQuestions(key);
  const visibleRows = rows.slice(0, 100);
  const hiddenHint = rows.length > visibleRows.length
    ? `<p class="dataset-next-step">仅显示前 100 题；用搜索缩小范围。</p>`
    : "";
  picker.innerHTML = hiddenHint + visibleRows.map((q) => `
    <label class="question-row">
      <input type="checkbox" data-benchmark="${escapeHtml(key)}" data-question-id="${escapeHtml(q.question_id || "")}" ${selected.has(q.question_id) ? "checked" : ""}>
      <span>
        <strong>${escapeHtml(q.question || "(未识别 question 字段)")}</strong>
        <small>${escapeHtml(q.sample_id || "-")} · ${escapeHtml(q.question_id || "-")} · ${escapeHtml(q.category || "-")}</small>
        <em>标准答案：${escapeHtml(q.answer || "-")}</em>
      </span>
    </label>
  `).join("") || `<p>没有加载到可选题目。先校验 ${escapeHtml(config.label)} 数据集，再点“题目预览”。</p>`;
  picker.querySelectorAll("input[type='checkbox']").forEach((box) => {
    box.addEventListener("change", () => {
      const questionId = box.dataset.questionId || "";
      if (!questionId) return;
      if (box.checked) selected.add(questionId);
      else selected.delete(questionId);
      renderBenchmarkSelectionSummary(key);
    });
  });
  renderBenchmarkSelectionSummary(key);
}

function renderBenchmarkSelectionSummary(key) {
  const {questions, selected} = benchmarkQuestionState(key);
  const rows = filteredBenchmarkQuestions(key);
  const {selectedText} = benchmarkQuestionElements(key);
  if (selectedText) {
    selectedText.textContent = selected.size
      ? `已选 ${selected.size} 题；“跑选中题”只跑这些题。`
      : `未勾选时按“测试题数”运行；当前可见 ${rows.length}/${questions.length} 题。`;
  }
}

function selectVisibleBenchmarkQuestions(key) {
  const {selected} = benchmarkQuestionState(key);
  filteredBenchmarkQuestions(key).slice(0, 100).forEach((q) => {
    if (q.question_id) selected.add(q.question_id);
  });
  renderBenchmarkQuestionSelection(key);
}

function clearBenchmarkQuestionSelection(key) {
  benchmarkQuestionState(key).selected.clear();
  renderBenchmarkQuestionSelection(key);
}

function useFullBenchmarkCount(key) {
  const config = benchmarkConfig(key);
  const input = $(config.countInput);
  if (input) input.value = "0";
  clearBenchmarkQuestionSelection(key);
  toast(`${config.label} 已切换为全量运行（count=0，已清空选题）`);
}

function useFormalBenchmarkPreset(key, options = {}) {
  const config = benchmarkConfig(key);
  const input = $(config.dataInput);
  const preferredIds = config.preferredDatasetIds || [config.defaultDatasetId].filter(Boolean);
  const record = preferredIds
    .map((id) => state.datasetRegistry.find((item) => item.id === id && item.exists))
    .find(Boolean)
    || preferredIds.map((id) => state.datasetRegistry.find((item) => item.id === id)).find(Boolean)
    || null;
  if (input && record?.path) input.value = record.path;
  const countInput = $(config.countInput);
  const countValue = options.countValue != null ? String(options.countValue) : "0";
  if (countInput) countInput.value = countValue;
  clearBenchmarkQuestionSelection(key);
  if (config.status) {
    const target = $(config.status);
    if (target) {
      const launchLabel = countValue === "0" ? "正式全量路径" : `正式路径（count=${escapeHtml(countValue)}）`;
      target.innerHTML = `
        <p><strong>${escapeHtml(config.label)} 已切到 ${launchLabel}</strong></p>
        <p class="dataset-next-step">${escapeHtml(record?.path || config.emptyPathHint)}</p>
        <p class="dataset-next-step">题数已设为 ${escapeHtml(countValue)}，选题已清空；现在点击“开始测试”会按正式路径运行。</p>
      `;
    }
  }
  toast(`${config.label} 已切到正式路径（count=${countValue}）`);
}

function datasetCategoryLabel(format, category) {
  const key = String(category || "").trim();
  const normalizedFormat = String(format || "").toLowerCase();
  if (normalizedFormat === "locomo") {
    return LOCOMO_CATEGORY_LABELS[key] || `LoCoMo C${key || "-"}`;
  }
  if (normalizedFormat === "chenmo") return key || "未分类";
  return key ? `类别 ${key}` : "未分类";
}

function datasetCategoryHint(format, category) {
  const key = String(category || "").trim();
  const normalizedFormat = String(format || "").toLowerCase();
  if (normalizedFormat === "locomo") {
    return LOCOMO_CATEGORY_HINTS[key] || "该题型来自数据集原始 category 字段。";
  }
  if (normalizedFormat === "chenmo") return "ChenMo 推理问题章节，用于查看不同推理能力上的通过情况。";
  return "该分类来自数据集原始字段。";
}

function locomoCategoryLabel(category) {
  const key = String(category || "").trim();
  if (!key) return "未分类";
  return `C${key} · ${datasetCategoryLabel("locomo", key)}`;
}

function locomoCategoryBadge(category) {
  const key = String(category || "").trim();
  const label = locomoCategoryLabel(key);
  const hint = datasetCategoryHint("locomo", key);
  return `<span class="category-badge category-${escapeHtml(key || "unknown")}" title="${escapeHtml(hint)}">${escapeHtml(label)}</span>`;
}

function locomoOverviewMetric(label, value, tone = "") {
  const toneClass = tone ? ` ${tone}` : "";
  return `
    <article class="overview-metric${toneClass}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value ?? "-")}</strong>
    </article>
  `;
}

function flowStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  if (value === "running") return "运行中";
  return preflightLabel(value);
}

function flowTone(status) {
  const value = String(status || "").toLowerCase();
  if (value === "running") return "active";
  return preflightTone(value);
}

function flowMetricRows(metrics = {}) {
  const entries = Object.entries(metrics || {})
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 3);
  if (!entries.length) return "";
  return `
    <div class="overview-metrics">
      ${entries.map(([key, value]) => locomoOverviewMetric(key.replace(/_/g, " "), typeof value === "number" && key === "accuracy" ? percent(value) : value, key === "accuracy" && value != null ? "ok" : "")).join("")}
    </div>
  `;
}

function flowEvidenceText(evidence = {}) {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) return "";
  const preferredKeys = ["path", "workspace", "storage_root", "latest_csv", "report_html", "run_dir", "account_path", "memory_root", "error"];
  const pairs = preferredKeys
    .map((key) => [key, evidence[key]])
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 2);
  return pairs.map(([key, value]) => `${key}: ${value}`).join(" · ");
}

function flowPrimaryStage(stages = []) {
  const rows = Array.isArray(stages) ? stages : [];
  return rows.find((stage) => String(stage.status || "").toLowerCase() === "running")
    || rows.find((stage) => ["fail", "bad", "warn", "warning", "todo"].includes(String(stage.status || "").toLowerCase()))
    || rows[rows.length - 1]
    || {};
}

function flowActionLabel(stage = {}) {
  const status = String(stage.status || "").toLowerCase();
  if (status === "running") return "查看进度";
  if (status === "ok") return "查看详情";
  if (status === "todo") return "去执行";
  if (status === "warn" || status === "warning") return "去确认";
  if (status === "fail" || status === "bad") return "去修复";
  return "打开";
}

function flowStatusClass(status = "") {
  const tone = flowTone(status);
  return tone === "active" ? "flow-running" : `flow-${tone}`;
}

function locomoFlowMainView(view = "") {
  const value = String(view || "");
  if (value === "datasetView" || value === "workbenchView") return "openvikingView";
  if (value === "memoryView") return "runsView";
  return value;
}

const LOCOMO_FLOW_CARDS = [
  {key: "openvikingView", view: "openvikingView", title: "记忆导入", detail: "读取数据、导入 conv、检查完整性"},
  {key: "evalView", view: "evalView", title: "问答测试", detail: "选择问答、查看相关记忆"},
  {key: "judgeView", view: "judgeView", title: "判分", detail: "检查结果文件并判分当前结果"},
  {key: "runsView", view: "runsView", title: "导出报告", detail: "选择任务并生成评测报告"},
];

function benchmarkFlowContext(viewId = "") {
  const activeView = String(viewId || document.body?.dataset?.activeView || "");
  const directFormat = datasetFormatForView(activeView);
  if (directFormat && directFormat !== "locomo") {
    return {view: activeView, format: directFormat, label: datasetTypeLabel(directFormat)};
  }
  const activeFormat = normalizeDatasetFormat(state.activeDatasetFormat || "");
  const activeBenchmarkView = state.activeBenchmarkView || viewForDatasetFormat(activeFormat, "");
  if (activeView === "runsView" && activeFormat && activeFormat !== "locomo" && activeBenchmarkView) {
    return {view: activeBenchmarkView, format: activeFormat, label: datasetTypeLabel(activeFormat)};
  }
  return null;
}

function benchmarkFlowCards(context = {}) {
  const targetView = context.view || "runsView";
  const label = context.label || datasetTypeLabel(context.format) || "当前数据集";
  return [
    {key: "import", view: targetView, title: "记忆导入", detail: `${label}：选择数据、配置后端、准备写入记忆`},
    {key: "qa", view: targetView, title: "问答测试", detail: "加载题目、勾选样本、启动真实模型链路"},
    {key: "judge", view: targetView, title: "判分", detail: "自动判分 / 官方指标摘要随任务生成"},
    {key: "report", view: "runsView", title: "导出报告", detail: "进入结果中心查看任务、摘要和报告"},
  ];
}

function normalizeBenchmarkFlowStage(stage = "") {
  const value = String(stage || "").trim();
  return ["import", "qa", "judge", "report"].includes(value) ? value : "";
}

function defaultBenchmarkFlowStage(viewId = "") {
  if (viewId === "evolvingEventsView") return "import";
  if (viewId === "longMemEvalView") return "import";
  if (viewId === "hotpotQaView") return "import";
  return viewId === "runsView" ? "report" : "import";
}

function applyBenchmarkFlowStage(viewId = "", stage = "") {
  const panel = $(viewId);
  if (!panel) return;
  const normalized = normalizeBenchmarkFlowStage(stage) || defaultBenchmarkFlowStage(viewId);
  panel.dataset.activeBenchmarkStage = normalized;
  panel.querySelectorAll(".benchmark-stage-tab").forEach((button) => {
    const active = String(button.dataset.flowKey || "") === normalized;
    button.classList.toggle("active", active);
    button.classList.toggle("is-selected", active);
    button.setAttribute("aria-current", active ? "step" : "false");
  });
}

function renderFlowNavCards(nav, cards = [], activeKey = "") {
  const buttons = [...nav.querySelectorAll(".flow-card")];
  buttons.forEach((button, index) => {
    const card = cards[index];
    if (!card) {
      button.hidden = true;
      return;
    }
    const number = button.querySelector("span");
    const title = button.querySelector("strong");
    const detail = button.querySelector("small");
    if (number) number.textContent = String(index + 1);
    if (title) title.textContent = card.title || "";
    if (detail) detail.textContent = card.detail || "";
    button.dataset.viewJump = card.view || "";
    button.dataset.flowKey = card.key || "";
    button.hidden = false;
    button.disabled = false;
    button.setAttribute("aria-disabled", "false");
    button.classList.remove("flow-ok", "flow-warn", "flow-bad", "flow-todo", "flow-muted", "flow-running");
    const isActive = (card.key || card.view || "") === activeKey || (card.view || "") === activeKey;
    button.classList.toggle("active", isActive);
    button.classList.toggle("is-selected", isActive);
    button.setAttribute("aria-current", isActive ? "step" : "false");
    button.title = isActive ? `当前步骤：${card.title || ""}` : `进入${card.title || ""}`;
  });
}

const LOCOMO_BLOCK_META = {
  import: {title: "记忆导入", view: "openvikingView"},
  qa: {title: "问答测试", view: "evalView"},
  judge: {title: "判分", view: "judgeView"},
  report: {title: "查看报告", view: "runsView"},
};

const LOCOMO_STAGE_BLOCK = {
  dataset: "import",
  import: "import",
  integrity: "import",
  qa: "qa",
  judge: "judge",
  report: "report",
};

function flowStatusRank(status = "") {
  const value = String(status || "").toLowerCase();
  if (value === "fail" || value === "bad") return 5;
  if (value === "running") return 4;
  if (value === "warn" || value === "warning") return 3;
  if (value === "todo") return 2;
  if (value === "ok") return 1;
  return 0;
}

function worstFlowStatus(stages = []) {
  return (Array.isArray(stages) ? stages : [])
    .map((stage) => String(stage.status || "todo").toLowerCase())
    .sort((a, b) => flowStatusRank(b) - flowStatusRank(a))[0] || "todo";
}

function firstActionableStage(stages = []) {
  const rows = Array.isArray(stages) ? stages : [];
  return rows.find((stage) => String(stage.status || "").toLowerCase() === "running")
    || rows.find((stage) => ["fail", "bad", "warn", "warning", "todo"].includes(String(stage.status || "").toLowerCase()))
    || rows[rows.length - 1]
    || {};
}

function compactStageDetail(prefix, stage = {}) {
  if (!stage || !stage.detail) return "";
  return `${prefix}: ${stage.detail}`;
}

function aggregateLocomoFlowStages(stages = []) {
  const raw = Array.isArray(stages) ? stages : [];
  if (!raw.length) return [];
  const byId = Object.fromEntries(raw.map((stage) => [String(stage.id || ""), stage]));
  const grouped = Object.fromEntries(Object.keys(LOCOMO_BLOCK_META).map((key) => [key, []]));
  const systemWarnings = [];
  raw.forEach((stage) => {
    const id = String(stage.id || "");
    const block = LOCOMO_STAGE_BLOCK[id];
    if (block) {
      grouped[block].push(stage);
    } else if (flowStatusRank(stage.status) >= flowStatusRank("warn")) {
      systemWarnings.push(stage);
    }
  });
  if (systemWarnings.length) grouped.import.push(...systemWarnings);
  return Object.entries(LOCOMO_BLOCK_META).map(([block, meta]) => {
    const blockStages = grouped[block] || [];
    const primary = firstActionableStage(blockStages);
    if (block === "import") {
      const details = [
        compactStageDetail("数据集", byId.dataset),
        compactStageDetail("导入", byId.import),
        compactStageDetail("完整性", byId.integrity),
        ...systemWarnings.map((stage) => compactStageDetail(stage.title || stage.id || "系统", stage)),
      ].filter(Boolean);
      return {
        id: "import",
        title: meta.title,
        status: worstFlowStatus(blockStages),
        view: meta.view,
        detail: details.join(" · ") || "读取 LoCoMo JSON，选择 conv，导入后检查完整性。",
        action: primary.action || "读取并导入",
        evidence: Object.assign({}, byId.dataset?.evidence || {}, byId.import?.evidence || {}, byId.integrity?.evidence || {}, primary.evidence || {}),
        metrics: Object.assign({}, byId.dataset?.metrics || {}, byId.import?.metrics || {}, byId.integrity?.metrics || {}),
      };
    }
    const source = primary.id ? primary : (blockStages[0] || {});
    return {
      ...source,
      id: block,
      title: meta.title,
      view: meta.view,
      status: source.status || "todo",
      detail: source.detail || (block === "qa" ? "选择 QA 后运行问答测试。" : block === "judge" ? "QA 完成后运行判分。" : "生成、导出、查看历史报告并做结果对比。"),
      action: source.action || flowActionLabel(source),
    };
  });
}

function locomoBlockCompletion(stages = []) {
  const rows = Array.isArray(stages) ? stages : [];
  const total = rows.length;
  const ok = rows.filter((stage) => String(stage.status || "").toLowerCase() === "ok").length;
  return {ok, total, pct: total ? Math.round((ok / total) * 100) : 0};
}

function flowEvidenceRows(evidence = {}) {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) return [];
  const preferredKeys = [
    "path",
    "workspace",
    "storage_root",
    "account_path",
    "memory_root",
    "latest_csv",
    "report_html",
    "run_dir",
    "output_file",
    "error",
  ];
  const rows = [];
  preferredKeys.forEach((key) => {
    const value = evidence[key];
    if (value === null || value === undefined || value === "" || typeof value === "object") return;
    rows.push([key.replace(/_/g, " "), String(value)]);
  });
  return rows.slice(0, 6);
}

function flowArtifactRows(data = {}) {
  const artifacts = data.artifacts || {};
  const dataset = artifacts.dataset || {};
  const workspace = artifacts.workspace || {};
  const imported = artifacts.imported || {};
  const latestQa = artifacts.latest_qa || {};
  const latestReport = artifacts.latest_report || {};
  return [
    ["数据集", dataset.path],
    ["工作空间", workspace.storage_root || workspace.workspace],
    ["记忆根目录", imported.memory_root || imported.account_path],
    ["QA 结果 CSV", latestQa.output_file],
    ["报告", latestReport.report_html],
  ].filter(([, value]) => value);
}

function flowArtifactRow(label, value) {
  const href = artifactHref(value);
  return `
    <div class="flow-artifact-row">
      <span>${escapeHtml(label)}</span>
      <code>${escapeHtml(value || "-")}</code>
      ${copyButtonHtml(value || "", "复制")}
      ${href ? `<a class="path-link" href="${escapeHtml(href)}" target="_blank" rel="noreferrer">打开</a>` : ""}
    </div>
  `;
}

function flowStageRail(stages = []) {
  const rows = Array.isArray(stages) ? stages : [];
  if (!rows.length) return "";
  return `
    <div class="locomo-stage-rail" aria-label="LoCoMo 阶段状态">
      ${rows.map((stage, index) => `
        <button class="${escapeHtml(flowStatusClass(stage.status))}" type="button" data-view-jump="${escapeHtml(locomoFlowMainView(stage.view || "openvikingView"))}">
          <span>${index + 1}</span>
          <strong>${escapeHtml(stage.title || stage.id || "-")}</strong>
          <em>${escapeHtml(flowStatusLabel(stage.status))}</em>
        </button>
      `).join("")}
    </div>
  `;
}

function syncLocomoFlowNavStatus(stages = []) {
  const nav = $("locomoFlowNav");
  if (!nav) return;
  const priority = {"flow-bad": 5, "flow-running": 4, "flow-warn": 3, "flow-todo": 2, "flow-ok": 1, "flow-muted": 0};
  const byView = {};
  (Array.isArray(stages) ? stages : []).forEach((stage) => {
    const view = locomoFlowMainView(stage.view || "");
    if (!view) return;
    const cls = flowStatusClass(stage.status);
    if (!byView[view] || (priority[cls] || 0) > (priority[byView[view]] || 0)) byView[view] = cls;
  });
  nav.querySelectorAll(".flow-card").forEach((button) => {
    button.classList.remove("flow-ok", "flow-warn", "flow-bad", "flow-todo", "flow-muted", "flow-running");
    const cls = byView[button.dataset.viewJump || ""];
    if (cls) button.classList.add(cls);
  });
}

function renderLocomoFlowStatusPanel(data) {
  const panel = $("locomoOverviewPanel");
  if (!panel) return false;
  const activeView = document.body?.dataset?.activeView || document.querySelector(".view-panel.active")?.id || "";
  if (activeView === "evalView") {
    panel.hidden = true;
    panel.innerHTML = "";
    renderLocomoWorkbenchTrack();
    return false;
  }
  if (!data || state.locomoFlowLoading) {
    panel.innerHTML = `
      <article class="overview-card reference locomo-flow-hero">
        <div class="overview-card-head">
          <span>LoCoMo Flow</span>
          <strong>正在读取真实流程状态</strong>
        </div>
        <p>从记忆导入、问答测试、判分和报告产物恢复状态。</p>
      </article>
    `;
    renderLocomoWorkbenchTrack();
    return true;
  }
  const stages = aggregateLocomoFlowStages(data.stages);
  const completion = locomoBlockCompletion(stages);
  const nextActions = Array.isArray(data.next_actions) ? data.next_actions : [];
  const activeStage = flowPrimaryStage(stages);
  const artifactRows = flowArtifactRows(data);
  panel.innerHTML = `
    <article class="overview-card ${flowTone(data.status)} locomo-flow-hero">
      <div class="overview-card-head">
        <span>LoCoMo Flow</span>
        <strong>${escapeHtml(memoryBackendLabel(data.backend))} · ${escapeHtml(data.account || currentAccount())}</strong>
      </div>
      <div class="flow-progress-line" aria-label="LoCoMo 流程完成度">
        <i style="width: ${Math.max(0, Math.min(100, Number(completion.pct || 0)))}%"></i>
      </div>
      <div class="overview-metrics">
        ${locomoOverviewMetric("完成", `${completion.ok ?? 0}/${completion.total ?? (stages.length || 0)}`)}
        ${locomoOverviewMetric("进度", `${completion.pct ?? 0}%`, data.status === "ok" ? "ok" : "")}
        ${locomoOverviewMetric("状态", flowStatusLabel(data.status), flowTone(data.status))}
      </div>
      <div class="flow-current-step ${escapeHtml(flowStatusClass(activeStage.status))}">
        <span>当前卡点</span>
        <strong>${escapeHtml(activeStage.title || "LoCoMo 流程")}</strong>
        <p>${escapeHtml(activeStage.detail || "所有阶段已完成或等待更新。")}</p>
        <div class="overview-actions">
          <button class="primary compact-button" type="button" data-view-jump="${escapeHtml(locomoFlowMainView(activeStage.view || "openvikingView"))}">${escapeHtml(flowActionLabel(activeStage))}</button>
          <button class="secondary compact-button" type="button" id="refreshLocomoFlowStatus">刷新状态</button>
          ${data.markdown ? copyButtonHtml(data.markdown, "复制状态") : ""}
        </div>
      </div>
      <p>${escapeHtml(data.checked_at || "")} · 状态来自后端文件、任务和报告产物；不会读取或返回 API 密钥。</p>
      ${flowStageRail(stages)}
      ${artifactRows.length ? `
        <div class="flow-artifact-panel">
          <div class="flow-artifact-head">
            <span>关键产物</span>
            <strong>路径可复制，runs 产物可直接打开</strong>
          </div>
          <div class="flow-artifact-list">${artifactRows.map(([label, value]) => flowArtifactRow(label, value)).join("")}</div>
        </div>
      ` : ""}
      <div class="overview-actions">
        <button class="secondary compact-button" type="button" data-view-jump="systemConfigView">系统配置</button>
        <button class="secondary compact-button" type="button" data-view-jump="runsView">结果中心</button>
      </div>
      ${nextActions.length ? `<ol class="locomo-next-actions">${nextActions.slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>` : ""}
    </article>
    ${stages.map((stage) => {
      const evidenceText = flowEvidenceText(stage.evidence || {});
      const evidenceRows = flowEvidenceRows(stage.evidence || {});
      return `
        <article class="overview-card ${flowTone(stage.status)} locomo-stage-card">
          <div class="overview-card-head">
            <span>${escapeHtml(stage.title || stage.id || "-")}</span>
            <strong>${escapeHtml(flowStatusLabel(stage.status))}</strong>
          </div>
          ${flowMetricRows(stage.metrics || {})}
          <p>${escapeHtml(stage.detail || "")}</p>
          ${evidenceText ? `<code>${escapeHtml(evidenceText)}</code>` : ""}
          ${evidenceRows.length ? `
            <details class="locomo-stage-details">
              <summary>查看证据路径</summary>
              <div class="locomo-stage-evidence">
                ${evidenceRows.map(([key, value]) => `
                  <div>
                    <span>${escapeHtml(key)}</span>
                    <code>${escapeHtml(value)}</code>
                    ${copyButtonHtml(value, "复制")}
                  </div>
                `).join("")}
              </div>
            </details>
          ` : ""}
          <div class="overview-actions">
            <button class="secondary compact-button" type="button" data-view-jump="${escapeHtml(locomoFlowMainView(stage.view || "openvikingView"))}">${escapeHtml(flowActionLabel(stage))}</button>
          </div>
        </article>
      `;
    }).join("")}
  `;
  bindCopyButtons("#locomoOverviewPanel");
  bindViewJumpButtons("#locomoOverviewPanel");
  syncLocomoFlowNavStatus(stages);
  $("refreshLocomoFlowStatus")?.addEventListener("click", () => refreshLocomoFlowStatus().catch((e) => toast(e.message)));
  renderLocomoWorkbenchTrack();
  return true;
}

function renderImportStageRail(status = {}) {
  const rail = $("importStageRail");
  if (!rail) return;
  const lastImport = readLastImport();
  const task = state.currentImportTask || {};
  const running = isTaskRunningStatus(task);
  const complete = status.complete || String(lastImport.integrity || "").toLowerCase() === "complete";
  const warned = status.warn || (lastImport.integrity && String(lastImport.integrity).toLowerCase() !== "complete");
  const activeStage = running ? "commit" : (complete ? "smoke" : (lastImport.output_file ? "verify" : "parse"));
  const doneStages = new Set();
  if (currentLocomoDataset()) doneStages.add("parse");
  if (lastImport.workspace || task.id) {
    ["session", "messages"].forEach((stage) => doneStages.add(stage));
  }
  if (lastImport.output_file || task.output_file) doneStages.add("commit");
  if (complete) {
    ["parse", "session", "messages", "commit", "verify", "smoke"].forEach((stage) => doneStages.add(stage));
  }
  rail.querySelectorAll("[data-stage]").forEach((item) => {
    const stage = item.dataset.stage || "";
    item.classList.toggle("done", doneStages.has(stage));
    item.classList.toggle("active", !complete && activeStage === stage);
    item.classList.toggle("warn", warned && stage === "verify");
  });
}

function locomoTrackToneLabel(tone) {
  if (tone === "ok") return "已就绪";
  if (tone === "active") return "运行中";
  if (tone === "warn") return "需确认";
  return "待处理";
}

function locomoTrackCard(card) {
  const tone = card.tone || "todo";
  return `
    <article class="locomo-track-card ${escapeHtml(tone)}">
      <div class="locomo-track-head">
        <span>${escapeHtml(card.step || "")}</span>
        <em>${escapeHtml(card.status || locomoTrackToneLabel(tone))}</em>
      </div>
      <strong>${escapeHtml(card.title || "")}</strong>
      <p>${escapeHtml(card.detail || "")}</p>
      <div class="locomo-track-foot">
        <small>${escapeHtml(card.metric || "")}</small>
        <button class="secondary compact-button" type="button" data-view-jump="${escapeHtml(card.view || "workbenchView")}">${escapeHtml(card.action || "查看")}</button>
      </div>
    </article>
  `;
}

function renderLocomoWorkbenchTrack() {
  const target = $("locomoWorkbenchTrack");
  const summaryTarget = $("locomoWorkbenchSummary");
  if (!target && !summaryTarget) return;
  const dataset = currentLocomoDataset();
  const lastImport = readLastImport();
  const imported = state.importedMemoryStatus || {};
  const outputCsv = currentLocomoResultCsv();
  const reportPath = state.lastReportFile || "";
  const task = state.currentLocomoTask || {};
  const taskProgress = task.progress || {};
  const taskRunning = isTaskActive(task);
  const summary = state.selectedRunSummary || task.summary || {};
  const pending = summary.result_counts?.UNSCORED ?? (summary.rows && summary.graded != null ? Math.max(0, Number(summary.rows) - Number(summary.graded)) : "-");
  const datasetReady = Boolean(dataset);
  const importComplete = String(lastImport.integrity || "").toLowerCase() === "complete" || Number(imported.complete_count || 0) > 0;
  const importRan = Boolean(lastImport.output_file || state.currentImportTask?.output_file || state.currentImportTask?.id);
  const backendLabel = memoryBackendLabel(currentMemoryBackend());
  const qaProgress = taskRunning && taskProgress.total
    ? `${taskProgress.current}/${taskProgress.total} · ${Number(taskProgress.pct || 0).toFixed(1)}%`
    : (taskRunning ? taskStatusLabel(task) : (outputCsv ? "CSV 已生成" : "等待 QA"));
  const judgeReady = outputCsv && summary.accuracy != null;
  const importStatus = importComplete ? "已完成" : (importRan ? "需确认" : (datasetReady ? "可导入" : "待加载"));
  const importDetail = [
    datasetReady ? `数据集 ${dataset.samples ?? "-"} conv / ${dataset.questions ?? "-"} QA` : "先读取 LoCoMo JSON",
    `${backendLabel} · 账户 ${currentAccount()}`,
    importComplete ? "完整性已完成" : (importRan ? "导入已运行，请检查完整性" : "选择 conv 或全量导入"),
  ].join(" · ");
  const importMetric = importComplete
    ? `摘要 ${imported.complete_count ?? "-"} / ${imported.summary_count ?? "-"} · 记忆文件 ${imported.memory_files ?? "-"}`
    : (lastImport.sample_id || lastImport.sample || (datasetReady ? "选择 conv 或全量" : "LoCoMo JSON"));
  const cards = [
    {
      step: "1",
      title: "记忆导入",
      status: importStatus,
      detail: importDetail,
      metric: importMetric,
      tone: importComplete ? "ok" : (importRan ? "warn" : "todo"),
      view: "openvikingView",
      action: importComplete ? "查看导入" : "读取并导入",
    },
    {
      step: "2",
      title: "问答测试",
      status: taskRunning ? taskStatusLabel(task) : (outputCsv ? "已生成" : "待运行"),
      detail: outputCsv || "选择具体 QA、错题或时间题运行记忆问答。",
      metric: qaProgress,
      tone: taskRunning ? "active" : (outputCsv ? "ok" : "todo"),
      view: "evalView",
      action: outputCsv ? "看结果" : "运行 QA",
    },
    {
      step: "3",
      title: "判分",
      status: judgeReady ? "已判分" : (outputCsv ? "待判分" : "等待 QA"),
      detail: judgeReady ? `准确率 ${percent(summary.accuracy)}` : "QA 完成后运行判分；未判分不会显示为 0%。",
      metric: `结果行 ${summary.rows ?? "-"} · 待判 ${pending}`,
      tone: judgeReady ? "ok" : (outputCsv ? "warn" : "todo"),
      view: "judgeView",
      action: judgeReady ? "查看判分" : "判分当前结果",
    },
    {
      step: "4",
      title: "查看报告",
      status: reportPath ? "可打开" : (judgeReady ? "待生成" : "等待判分"),
      detail: reportPath || "生成评测报告，展示配置、Token 用量、证据、上下文和错误归因。",
      metric: reportPath ? "评测报告文件" : "评测报告 + 证据追踪",
      tone: reportPath ? "ok" : (judgeReady ? "warn" : "todo"),
      view: "runsView",
      action: reportPath ? "看报告" : "生成报告",
    },
  ];
  const nextCard = cards.find((card) => card.tone !== "ok") || cards[cards.length - 1];
  if (target) {
    target.innerHTML = cards.map(locomoTrackCard).join("");
    bindViewJumpButtons("#locomoWorkbenchTrack");
  }
  if (summaryTarget) {
    summaryTarget.innerHTML = `
      <article>
        <span>当前账户</span>
        <strong>${escapeHtml(currentAccount())}</strong>
      </article>
      <article>
        <span>记忆后端</span>
        <strong>${escapeHtml(backendLabel)}</strong>
      </article>
      <article class="${escapeHtml(nextCard.tone || "todo")}">
        <span>下一步</span>
        <strong>${escapeHtml(nextCard.title || "LoCoMo 评测")}</strong>
      </article>
      <article>
        <span>Agent 边界</span>
        <strong>MemoryBench Agent 可比</strong>
      </article>
    `;
  }
}

function renderLocomoOverview() {
  const panel = $("locomoOverviewPanel");
  if (!panel) return;
  const activeView = document.body?.dataset?.activeView || document.querySelector(".view-panel.active")?.id || "";
  if (activeView === "evalView") {
    panel.hidden = true;
    panel.innerHTML = "";
    renderLocomoWorkbenchTrack();
    return;
  }
  if (state.locomoFlowStatus || state.locomoFlowLoading) {
    renderLocomoFlowStatusPanel(state.locomoFlowStatus);
    return;
  }
  const dataset = currentLocomoDataset();
  const lastImport = readLastImport();
  const imported = state.importedMemoryStatus || {};
  const outputCsv = currentLocomoResultCsv();
  const reportPath = state.lastReportFile || "";
  const task = state.currentLocomoTask || {};
  const taskProgress = task.progress || {};
  const taskRunning = isTaskActive(task);
  const summary = state.selectedRunSummary || task.summary || {};
  const pending = summary.result_counts?.UNSCORED ?? (summary.rows && summary.graded != null ? Math.max(0, Number(summary.rows) - Number(summary.graded)) : "-");
  const datasetReady = Boolean(dataset);
  const importComplete = String(lastImport.integrity || "").toLowerCase() === "complete" || Number(imported.complete_count || 0) > 0;
  const importTone = importComplete ? "ok" : (lastImport.output_file ? "warn" : "");
  const qaTone = taskRunning ? "active" : (outputCsv ? "ok" : "");
  const runAccuracy = summary.accuracy == null ? "待判分" : percent(summary.accuracy);
  const taskProgressText = taskRunning && taskProgress.total
    ? `${taskProgress.current}/${taskProgress.total} · ${Number(taskProgress.pct || 0).toFixed(1)}%`
    : (taskRunning ? taskStatusLabel(task) : (outputCsv ? "结果已生成" : "等待 QA"));
  panel.innerHTML = `
    <article class="overview-card ${importComplete ? "ok" : importTone}">
      <div class="overview-card-head">
        <span>记忆导入</span>
        <strong>${importComplete ? "导入完成" : (datasetReady ? "可导入" : "等待数据集")}</strong>
      </div>
      <div class="overview-metrics">
        ${locomoOverviewMetric("Conv", dataset?.samples ?? "-")}
        ${locomoOverviewMetric("QA", dataset?.questions ?? "-")}
        ${locomoOverviewMetric("完整性", lastImport.integrity === "complete" ? "完整" : (imported.complete_count ? "完整" : "-"), importTone)}
      </div>
      <p>${escapeHtml(lastImport.workspace || dataset?.path || dataset?.resolved_path || "在记忆导入块填写 LoCoMo JSON，读取后选择 conv 并归档。")}</p>
    </article>
    <article class="overview-card ${importTone}">
      <div class="overview-card-head">
        <span>问答测试</span>
        <strong>${escapeHtml(taskProgressText)}</strong>
      </div>
      <div class="overview-metrics">
        ${locomoOverviewMetric("结果行", summary.rows ?? "-")}
        ${locomoOverviewMetric("Token 估算", summary.total_injection_tokens_est || summary.answer_total_tokens || "-")}
        ${locomoOverviewMetric("证据", outputCsv ? "已记录" : "-")}
      </div>
      <p>${escapeHtml(outputCsv || "选择问答后运行测试，页面会显示进度、答案和相关记忆。")}</p>
    </article>
    <article class="overview-card ${qaTone}">
      <div class="overview-card-head">
        <span>判分</span>
        <strong>${escapeHtml(runAccuracy)}</strong>
      </div>
      <div class="overview-metrics">
        ${locomoOverviewMetric("已判", summary.graded ?? "-")}
        ${locomoOverviewMetric("待判", pending)}
        ${locomoOverviewMetric("错误", summary.wrong ?? "-")}
      </div>
      <p>${escapeHtml(outputCsv ? "QA 完成后运行判分；待判分不会显示为 0%。" : "等待问答测试结果。")}</p>
    </article>
    <article class="overview-card reference">
      <div class="overview-card-head">
        <span>查看报告</span>
        <strong>${escapeHtml(reportPath ? "报告可打开" : "等待报告")}</strong>
      </div>
      <p>${escapeHtml(reportPath || "报告应包含配置快照、模型、Token 用量、耗时、整体打分、证据、上下文和结果对比。")}</p>
      <div class="overview-actions">
        <button class="secondary compact-button" type="button" data-view-jump="runsView">查看报告</button>
        <button class="secondary compact-button" type="button" data-view-jump="systemConfigView">系统配置</button>
      </div>
    </article>
  `;
  panel.querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
  renderLocomoWorkbenchTrack();
  renderImportStageRail({complete: importComplete, warn: Boolean(lastImport.output_file && !importComplete)});
}

async function refreshLocomoFlowStatus(silent = false) {
  state.locomoFlowLoading = true;
  renderLocomoOverview();
  try {
    const data = await api("/api/locomo-flow-status", {
      method: "POST",
      body: JSON.stringify(currentPreflightPayload()),
    });
    state.locomoFlowStatus = data;
    if (!silent) {
      toast(data.status === "ok" ? "LoCoMo 流程状态已刷新" : "LoCoMo 流程还有待处理项");
    }
    return data;
  } catch (error) {
    state.locomoFlowStatus = null;
    if (!silent) toast(error.message || "LoCoMo 流程状态读取失败");
    throw error;
  } finally {
    state.locomoFlowLoading = false;
    renderLocomoOverview();
  }
}

function setContextPanelCollapsed(collapsed) {
  const workbench = $("agentWorkbench");
  const button = $("toggleContextPanel");
  if (!workbench || !button) return;
  workbench.classList.toggle("context-collapsed", Boolean(collapsed));
  button.textContent = collapsed ? "显示证据" : "隐藏证据";
  button.classList.toggle("active", Boolean(collapsed));
  localStorage.setItem(CONTEXT_PANEL_KEY, collapsed ? "1" : "0");
}

function syncContextPanelDefaultForViewport() {
  const button = $("toggleContextPanel");
  if (button) button.hidden = false;
  if (window.innerWidth <= 1100) {
    setContextPanelCollapsed(true);
    return;
  }
  const saved = localStorage.getItem(CONTEXT_PANEL_KEY);
  if (saved === "1" || saved === "0") {
    setContextPanelCollapsed(saved === "1");
    return;
  }
  setContextPanelCollapsed(false);
}

function toggleContextPanel() {
  const workbench = $("agentWorkbench");
  setContextPanelCollapsed(!workbench?.classList.contains("context-collapsed"));
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 2400);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data.error || `${res.status} ${res.statusText}`);
    error.status = res.status;
    error.data = data;
    throw error;
  }
  return data;
}

async function apiWithTimeout(path, options = {}, timeoutMs = 5000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await api(path, {...options, signal: controller.signal});
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`请求超时 ${Math.round(timeoutMs / 1000)}s`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[<>&"]/g, (c) => ({
    "<": "&lt;",
    ">": "&gt;",
    "&": "&amp;",
    '"': "&quot;",
  }[c]));
}

function normalizeVisibleMemoryBackendName(value) {
  return String(value ?? "")
    .replace(/EchoMem\s*\/\s*EchoMemory/g, "EchoMemory")
    .replace(/EchoMem\/EchoMemory/g, "EchoMemory")
    .replace(/\bEchoMem\b/g, "EchoMemory");
}

function percent(n) {
  return n == null || Number.isNaN(n) ? "待判分" : `${Math.round(n * 1000) / 10}%`;
}

function agentTypeForKind(kind) {
  if (kind === "local_agent") return "local_reference_agent";
  if (kind === "openviking_qa") return "memorybench_agent";
  if (kind === "echomemory_qa") return "echomemory_memory_qa";
  if (kind === "openviking_generic_qa") return "openviking_generic_qa";
  if (kind === "echomemory_generic_qa") return "echomemory_generic_qa";
  if (kind === "openviking_qa_retry_failed" || kind === "openviking_qa_retry_missing") return "memorybench_agent";
  if (kind === "openviking_import") return "openviking_commit_import";
  if (kind === "echomemory_import") return "echomemory_commit_import";
  if (kind === "judge") return "judge";
  return kind || "unknown";
}

function agentTypeLabel(value) {
  const labels = {
    memorybench_agent: "MemoryBench Agent",
    local_reference_agent: "MemoryBench 本地基线",
    native_vikingbot_cli: "OpenViking 参考 QA（历史）",
    echomemory_memory_qa: "EchoMemory QA",
    openviking_generic_qa: "OpenViking MemoryBench QA",
    echomemory_generic_qa: "EchoMemory MemoryBench QA",
    openviking_memory_qa: "MemoryBench Agent · OpenViking",
    openviking_commit_import: "记忆导入",
    echomemory_commit_import: "EchoMemory 导入",
    judge: "判分",
  };
  return labels[value] || value || "-";
}

function taskNameForKind(kind, datasetFormat) {
  if (kind === "judge") return "judge";
  if (kind === "adapter") return `${datasetFormat} 预览`;
  if (kind === "openviking_import") return "locomo OpenViking 记忆导入";
  if (kind === "echomemory_import") return "locomo EchoMemory 记忆导入";
  if (kind === "openviking_qa") return `${datasetFormat} MemoryBench Agent · OpenViking QA`;
  if (kind === "echomemory_qa") return `${datasetFormat} EchoMemory QA`;
  if (kind === "openviking_generic_qa") return `${datasetFormat} OpenViking MemoryBench QA`;
  if (kind === "echomemory_generic_qa") return `${datasetFormat} EchoMemory MemoryBench QA`;
  return `${datasetFormat} MemoryBench 本地基线`;
}

function setConnection(ok, text) {
  const el = $("connectionStatus");
  el.textContent = text;
  el.classList.toggle("ok", ok === true);
  el.classList.toggle("bad", ok === false);
}

function renderKpis(target, rows) {
  const el = $(target);
  if (!el) return;
  const visibleRows = (rows || []).filter(([, value]) => value !== undefined && value !== null && value !== "");
  el.innerHTML = visibleRows.map(([label, value]) => `
    <div class="kpi"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
  el.hidden = !visibleRows.length;
}

function renderDatasetCategories(data, targetId = "datasetCategoryPanel") {
  const target = $(targetId);
  if (!target) return;
  const entries = Object.entries(data.categories || {}).sort((a, b) => {
    const left = Number(a[0]);
    const right = Number(b[0]);
    if (Number.isFinite(left) && Number.isFinite(right)) return left - right;
    return String(a[0]).localeCompare(String(b[0]), "zh-Hans-CN");
  });
  if (!entries.length) {
    target.innerHTML = "";
    return;
  }
  const total = entries.reduce((sum, [, value]) => sum + Number(value || 0), 0);
  const isLocomo = String(data.format || "").toLowerCase() === "locomo";
  target.innerHTML = `
    <div class="category-panel-head">
      <strong>${escapeHtml(isLocomo ? "题型分布" : "分类分布")}</strong>
      <span>${escapeHtml(total ? `${formatInt(total)} 题` : "无题型数据")}</span>
    </div>
    <div class="category-card-grid">
      ${entries.map(([category, count]) => {
        const value = Number(count || 0);
        const pct = total ? `${Math.round((value / total) * 1000) / 10}%` : "-";
        return `
          <article class="category-card">
            <span>${isLocomo ? locomoCategoryBadge(category) : escapeHtml(category)}</span>
            <strong>${escapeHtml(formatInt(value))} 题</strong>
            <small>${escapeHtml(datasetCategoryLabel(data.format, category))} · 占比 ${escapeHtml(pct)}</small>
            <p>${escapeHtml(datasetCategoryHint(data.format, category))}</p>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function formatInt(value) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString();
}

function formatDateTimeLocal(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "-";
  try {
    return date.toLocaleString("zh-Hans-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return date.toISOString();
  }
}

function formatDateTime(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "-";
  try {
    return date.toLocaleString("zh-Hans-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return date.toISOString();
  }
}

function benchmarkRunningSummaryFreshness(task = {}, summary = {}) {
  const summaryJson = summary.summary_json || {};
  const raw = String(summary.updated_at ?? summaryJson.updated_at ?? "").trim();
  const updatedMs = raw ? Date.parse(raw) : NaN;
  if (!Number.isFinite(updatedMs)) {
    return {
      raw,
      updatedMs: NaN,
      ageSeconds: null,
      isStale: false,
      isSeverelyStale: false,
      hasWorkerFailure: false,
      label: "",
    };
  }
  const ageSeconds = Math.max(0, (Date.now() - updatedMs) / 1000);
  const status = String(task?.status || "").trim().toLowerCase();
  const hasWorkerFailure = Number(task?.log_diagnostics?.generic_failure_count || 0) > 0;
  const isStale = status === "running" && ageSeconds >= 10 * 60;
  const isSeverelyStale = status === "running" && ageSeconds >= 20 * 60;
  return {
    raw,
    updatedMs,
    ageSeconds,
    isStale,
    isSeverelyStale,
    hasWorkerFailure,
    label: `${formatDateTimeLocal(updatedMs)}（${formatDuration(ageSeconds)} 前）`,
  };
}

function renderJudgeEstimate(summary = {}) {
  const box = $("judgeEstimateBox");
  if (!box) return;
  const rows = Number(summary.rows || 0);
  if (!rows && !summary.result_counts && !summary.summary_json) {
    box.innerHTML = "";
    box.hidden = true;
    return;
  }
  const pending = Number(summary.result_counts?.UNSCORED ?? Math.max(0, rows - Number(summary.graded || 0)));
  const avgTokens = Number(summary.avg_injection_tokens_est || summary.summary_json?.avg_injection_tokens_est || 0);
  const estimateTokens = pending && avgTokens ? Math.round(pending * avgTokens) : 0;
  const batches = pending ? Math.ceil(pending / 10) : 0;
  const minSeconds = batches ? batches * 3 : 0;
  const maxSeconds = batches ? batches * 12 : 0;
  const status = pending ? `${pending} 行待判` : "无需判分";
  box.innerHTML = `
    <div class="judge-estimate-head">
      <strong>${escapeHtml(status)}</strong>
      <span>估算</span>
    </div>
    <div class="report-kv">
      <article><span>待判行数</span><strong>${formatInt(pending)}</strong></article>
      <article><span>平均上下文</span><strong>${formatInt(avgTokens ? Math.round(avgTokens) : 0)}</strong></article>
      <article><span>预计输入</span><strong>${formatInt(estimateTokens)}</strong></article>
      <article><span>预计耗时</span><strong>${pending ? `${formatDuration(minSeconds)} - ${formatDuration(maxSeconds)}` : "-"}</strong></article>
    </div>
  `;
  box.hidden = !(pending || estimateTokens || avgTokens);
}

function pendingCount(summary = {}) {
  const rows = Number(summary.rows || 0);
  return Number(summary.result_counts?.UNSCORED ?? Math.max(0, rows - Number(summary.graded || 0)));
}

function judgeModelReadiness() {
  const cfg = judgeModelConfig();
  const baseUrl = cfg.baseUrl || "";
  const model = cfg.model || "";
  const tokenSet = Boolean(cfg.token);
  const ok = Boolean(baseUrl && model && tokenSet);
  return {
    value: model || "未配置模型",
    detail: `${baseUrl ? "模型地址已填" : "模型地址未填"} · ${tokenSet ? "API 密钥已填" : "API 密钥必填"}`,
    tone: ok ? "ok" : "warn",
  };
}

function judgeStatusReadiness(summary = {}) {
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.count ?? 0);
  const graded = Number(summary.graded ?? summaryJson.graded ?? 0);
  if (!rows) {
    return {
      value: "等待更新",
      detail: "刷新读取 rows / pending。",
      tone: "warn",
    };
  }
  const pending = pendingCount(summary);
  const accuracy = summary.accuracy == null ? "" : ` · accuracy ${percent(summary.accuracy)}`;
  if (pending > 0) {
    return {
      value: `${formatInt(pending)} 待判分`,
      detail: `${formatInt(graded)} / ${formatInt(rows)} 已判${accuracy}；可抽样或判分全部 pending 行。`,
      tone: "warn",
    };
  }
  return {
    value: "判分完成",
    detail: `${formatInt(graded || rows)} / ${formatInt(rows)} 已判${accuracy || " · accuracy 待计算"}。`,
    tone: "ok",
  };
}

function judgeReportReadiness(summary = {}, input = currentLocomoResultCsv()) {
  const rows = Number(summary.rows ?? summary.summary_json?.count ?? 0);
  const pending = rows ? pendingCount(summary) : 0;
  if (state.lastReportFile) {
    return {
      value: "报告已生成",
      detail: compactPath(state.lastReportFile, 42, 40),
      tone: "ok",
    };
  }
  if (!input) {
    return {
      value: "等待 QA 结果",
      detail: "QA 完成后会自动填入结果文件，再从结果中心生成评测报告。",
      tone: "warn",
    };
  }
  if (!rows) {
    return {
      value: "待读取结果",
      detail: "点刷新后读取结果摘要，再判断是否可以生成正式报告。",
      tone: "warn",
    };
  }
  if (pending > 0) {
    return {
      value: "判分后生成",
      detail: "可以先生成诊断报告，但正式准确率需要判分完成。",
      tone: "warn",
    };
  }
  return {
    value: "可生成 HTML",
    detail: `结果目录 ${compactPath(dirname(input), 42, 40)}；去结果中心生成报告。`,
    tone: "ok",
  };
}

function renderJudgeReadinessPanel(summary = state.lastJudgeSummary || {}, task = null) {
  const target = $("judgeReadinessPanel");
  if (!target) return;
  const input = currentLocomoResultCsv();
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.count ?? 0);
  const pending = rows ? pendingCount(summary) : 0;
  const avgTokens = Number(summary.avg_injection_tokens_est || summaryJson.avg_injection_tokens_est || 0);
  const tokenEstimate = pending && avgTokens ? Math.round(pending * avgTokens) : 0;
  const validation = state.lastJudgeValidation || {};
  const judgeRunning = (task?.kind || state.taskKind) === "judge" && (task?.status || "") === "running";
  const resultTone = input ? "ok" : "warn";
  const status = judgeRunning
    ? {value: "判分运行中", detail: "任务完成后会自动刷新结果摘要。", tone: "active"}
    : judgeStatusReadiness(summary);
  const cards = [
    {
      label: "结果",
      value: input ? (rows ? `${formatInt(rows)} rows` : "已选择") : "未选择",
      detail: input ? compactPath(input, 44, 34) : "运行 QA 或粘贴 CSV。",
      tone: resultTone,
    },
    {
      label: "判分",
      ...status,
      detail: tokenEstimate ? `${status.detail} · 预计 ${formatInt(tokenEstimate)} tokens` : status.detail,
    },
    {
      label: "判分模型",
      ...judgeModelReadiness(),
    },
  ];
  const preflightText = validation.ok === true
    ? "预检通过"
    : (validation.ok === false ? "预检未通过，查看下方检查项" : "可点“检查”验证文件和判分配置");
  target.innerHTML = `
    ${cards.map((card) => `
      <article class="${escapeHtml(card.tone || "")}">
        <span>${escapeHtml(card.label)}</span>
        <strong>${escapeHtml(card.value || "-")}</strong>
        <p>${escapeHtml(card.detail || "")}</p>
      </article>
    `).join("")}
    <p class="judge-readiness-note">${escapeHtml(preflightText)}；报告在“导出报告”页生成。</p>
  `;
  const emptyState = $("judgeEmptyState");
  if (emptyState) {
    const hasVisibleJudgeOutput = Boolean(
      input
      || rows
      || !($("judgePreflightBox")?.hidden ?? true)
      || !($("judgeEstimateBox")?.hidden ?? true)
      || !($("pendingJudgePanel")?.hidden ?? true)
      || !($("resultArtifactList")?.hidden ?? true)
      || !($("evalSampleRows")?.hidden ?? true)
      || !($("sampleRows")?.hidden ?? true)
    );
    emptyState.hidden = hasVisibleJudgeOutput;
  }
  updateJudgeAndReportActionButtons({input, judgeRunning});
}

function updateJudgeAndReportActionButtons({input = currentLocomoResultCsv(), judgeRunning = false} = {}) {
  const hasJudgeInput = Boolean(String(input || "").trim());
  const activeDiagnostics = state.lastQaDiagnosticsInput === String(input || "").trim()
    ? (state.lastQaDiagnostics || {})
    : {};
  const pendingRows = Number((state.lastJudgeSummary || {}).result_counts?.UNSCORED || 0);
  const missingQuestions = Number(activeDiagnostics.missing_questions_count || 0);
  const failedQuestions = Number(activeDiagnostics.retryable_failed_questions || 0);
  const judgeTitle = judgeRunning
    ? "判分正在运行中，请稍候"
    : hasJudgeInput
      ? "对当前结果执行判分"
      : "请先运行 QA 或选择一个结果文件";
  ["runJudgeInline"].forEach((id) => {
    const button = $(id);
    if (!button) return;
    button.disabled = judgeRunning || !hasJudgeInput;
    button.title = judgeTitle;
  });
  const qaRunning = Boolean(isTaskActive(state.currentLocomoTask));
  const retryConfigs = [
    ["retryMissingQa", missingQuestions, "补跑缺失题", "当前结果没有缺失题"],
    ["retryFailedQa", failedQuestions, "重跑失败题", "当前结果没有失败题"],
  ];
  retryConfigs.forEach(([id, count, label, emptyTitle]) => {
    const button = $(id);
    if (!button) return;
    const available = hasJudgeInput && Number(count || 0) > 0;
    button.hidden = !available;
    button.disabled = qaRunning || !available;
    button.title = qaRunning
      ? "当前问答任务仍在运行，请稍候"
      : (available ? `${label}：当前有 ${formatInt(count)} 题` : emptyTitle);
  });
  const exportButton = $("exportRunReport");
  if (exportButton) {
    const hasRun = Boolean(state.selectedRunDir);
    exportButton.disabled = !hasRun;
    exportButton.title = hasRun ? "为当前选中的结果目录生成报告" : "请先在结果列表里选择一个任务";
  }
  renderRunsSelectionState();
}

function resetRunsDetailPanels() {
  state.selectedRunDir = "";
  state.selectedRunDatasetFormat = "";
  state.selectedRunRecord = null;
  state.selectedRunSummary = null;
  state.lastReportFile = "";
  renderKpis("runDetailKpis", []);
  ["runAuditPanel", "failureAttributionPanel", "evidenceContractPanel", "runArtifactList", "runQuestionList", "questionDetailPane", "runReportResult", "runDiffResult", "runCompareResult", "wrongClusterResult", "longMemBaselineResult", "configSnapshotResult"].forEach((id) => {
    const el = $(id);
    if (el) el.innerHTML = "";
  });
  if ($("runDetailPanel")) $("runDetailPanel").hidden = true;
  if ($("runReportDetails")) {
    $("runReportDetails").hidden = true;
    $("runReportDetails").removeAttribute("open");
  }
}

function renderRunsSelectionState() {
  const empty = $("runsEmptyState");
  const emptyText = $("runsEmptyStateText");
  const actionPanel = $("runsActionPanel");
  if (!empty || !actionPanel) return;
  const hasSelectedRun = Boolean(state.selectedRunDir);
  const hasRuns = Array.isArray(state.recentRuns) && state.recentRuns.length > 0;
  const loading = Boolean(state.runsLoading);
  empty.hidden = hasSelectedRun || loading;
  actionPanel.hidden = !hasSelectedRun;
  if (emptyText) {
    emptyText.textContent = loading
      ? "正在读取结果列表。"
      : hasRuns
      ? "先从左侧结果列表选择一条记录，再生成评测报告。"
      : (currentAccountOnlyEnabled("runsCurrentAccountOnly") ? "当前账户还没有结果，先运行问答测试或取消“只看当前空间”。" : "还没有结果，先运行问答测试。");
  }
}

function renderJudgeConfirmation(input, summary = {}, options = {}) {
  const pending = Number(options.estimatedPending ?? pendingCount(summary));
  const avgTokens = Number(summary.avg_injection_tokens_est || summary.summary_json?.avg_injection_tokens_est || 0);
  const estimateTokens = pending && avgTokens ? Math.round(pending * avgTokens) : 0;
  const panel = $("pendingJudgePanel");
  const message = `
    <div class="judge-confirm">
      <div>
        <strong>确认判分？</strong>
        <p>${formatInt(pending)} 行样本，预计输入约 ${formatInt(estimateTokens)} Token。</p>
      </div>
      <div class="panel-actions">
        <button class="secondary" id="cancelJudgeConfirm">取消</button>
        <button class="primary alt" id="confirmJudgeRun">判分</button>
      </div>
    </div>
  `;
  if (panel) panel.insertAdjacentHTML("afterbegin", message);
  $("cancelJudgeConfirm")?.addEventListener("click", () => {
    state.judgeConfirmInput = "";
    document.querySelector(".judge-confirm")?.remove();
    toast("已取消判分");
  });
  $("confirmJudgeRun")?.addEventListener("click", () => {
    state.judgeConfirmInput = input;
    runJudgeForCurrentResult({...options, confirmed: true}).catch((e) => toast(e.message));
  });
}

function locomoDatasetNeedsHydration() {
  const importSelect = $("importSample");
  const evalSelect = $("sample");
  const importOptions = importSelect?.options?.length || 0;
  const evalOptions = evalSelect?.options?.length || 0;
  return !state.locomoDataset || importOptions <= 1 || evalOptions <= 1;
}

function ensureLocomoDatasetLoadedForView(viewId = "") {
  if (!["openvikingView", "evalView", "judgeView", "memoryView"].includes(viewId)) return;
  const path = $("data")?.value?.trim() || "";
  if (!path || state.locomoDatasetLoading || !locomoDatasetNeedsHydration()) return;
  state.locomoDatasetLoading = true;
  loadDataset(true)
    .catch((error) => toast(`读取 LoCoMo conv 失败：${error.message || error}`))
    .finally(() => {
      state.locomoDatasetLoading = false;
      renderImportPaths();
      renderQaReadinessPanel();
      renderImportReadinessPanel();
    });
}

function pendingFilterPayload() {
  return {
    only_pending: true,
    category: $("pendingCategory")?.value || "",
    query: $("pendingSearch")?.value || "",
    min_tokens: $("pendingMinTokens")?.value || "",
    max_tokens: $("pendingMaxTokens")?.value || "",
  };
}

function showView(viewId, options = {}) {
  if (!viewId) return;
  if (viewId === "workbenchView") viewId = "openvikingView";
  if (RETIRED_VIEW_FALLBACKS[viewId]) viewId = RETIRED_VIEW_FALLBACKS[viewId];
  if (!document.getElementById(viewId)?.classList.contains("view-panel")) viewId = "openvikingView";
  if (options.userTriggered && state.bootHydrating && viewId !== (state.bootRequestedView || "")) {
    state.userNavigatedDuringBoot = true;
  }
  const workflowKey = options.workflowKey || workflowKeyForView(viewId);
  state.activeWorkflowKey = workflowKey;
  document.body.dataset.activeView = viewId;
  const previousBenchmarkView = state.activeBenchmarkView;
  const viewFormat = datasetFormatForView(viewId);
  if (viewFormat) {
    if (viewFormat === "locomo") rememberActiveDatasetView(viewId, viewFormat);
    else restoreBenchmarkDatasetForView(viewId);
  }
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === viewId);
  });
  const workflowGuide = $("workflowGuide");
  if (workflowGuide) workflowGuide.hidden = true;
  const activeNavView = VIEW_NAV_PARENT[viewId] || viewId;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === activeNavView);
  });
  const panel = $(viewId);
  if (panel) {
    if ($("viewTitle")) $("viewTitle").textContent = panel.dataset.title || "";
    if ($("viewSubtitle")) $("viewSubtitle").textContent = panel.dataset.subtitle || "";
  }
  const benchmarkContext = benchmarkFlowContext(viewId);
  if (benchmarkContext) {
    const requestedStage = normalizeBenchmarkFlowStage(options.benchmarkStage || options.flowStage || "");
    const sameBenchmark = previousBenchmarkView === benchmarkContext.view;
    const nextStage = requestedStage
      || (sameBenchmark ? normalizeBenchmarkFlowStage(state.activeBenchmarkFlowStage) : "")
      || defaultBenchmarkFlowStage(viewId);
    state.activeBenchmarkView = benchmarkContext.view;
    state.activeBenchmarkFlowStage = nextStage;
    applyBenchmarkFlowStage(benchmarkContext.view, nextStage);
  }
  updateWorkflowSelection(workflowKey);
  updateLocomoFlowNav(viewId);
  normalizeLegacyLabels();
  syncEvalTaskContainersForView(viewId);
  renderGlobalTaskChip(state.currentRunningTask || null);
  if (viewId === "evalView") {
    renderQaReadinessPanel();
    refreshTasks().catch((e) => toast(e.message));
  }
  if (isStandaloneBenchmarkView(viewId)) {
    const standaloneFormat = datasetFormatForView(viewId);
    if (standaloneFormat === "hotpotqa") updateHotpotQaInlineLiveReport(null);
    if (standaloneFormat === "hotpotqa") refreshHotpotQaModelReadiness().catch(() => null);
    if (standaloneFormat) {
      forceRefreshStandaloneBenchmarkView(standaloneFormat).catch(() => null);
    }
    refreshTasks()
      .catch((e) => toast(e.message))
      .then(() => refreshStandaloneBenchmarkViewOnly())
      .catch(() => {});
  }
  if (viewId === "judgeView" || viewId === "runsView") {
    updateJudgeAndReportActionButtons();
  }
  if (viewId === "runsView") {
    const emptyRuns = !document.querySelector("#runsList .run-card");
    if (emptyRuns || Date.now() - state.runsLoadedAt > 30000) {
      refreshRuns().catch((e) => toast(e.message));
    }
  }
  if (viewId === "chatView") {
    syncContextPanelDefaultForViewport();
    renderChatDebugStrip();
    loadChatDefaultContextPreview().catch((e) => {
      renderChatContextPlaceholder(`上下文预览失败：${e.message}`);
    });
  }
  if (viewId === "systemConfigView") {
    refreshEchoMemorySourceCard().catch((e) => toast(e.message));
  }
  ensureLocomoDatasetLoadedForView(viewId);
  updateWorkflowGuide();
  syncViewUrl(viewId, options);
  if (!options.preserveScroll) {
    stabilizeViewScroll(viewId);
  }
  updateJudgeAndReportActionButtons();
  updateStopActionButtons();
}

function syncViewUrl(viewId, options = {}) {
  if (!window.history?.replaceState) return;
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("ui_refresh", UI_REFRESH_VERSION);
    url.searchParams.set("view", viewId);
    if (options.taskLog) {
      url.searchParams.set("task_log", options.taskLog);
    } else if (options.clearTaskLog !== false) {
      url.searchParams.delete("task_log");
      url.searchParams.delete("run");
    }
    window.history.replaceState({}, "", url);
  } catch {
  }
}

function stabilizeViewScroll(viewId) {
  requestAnimationFrame(() => {
    window.scrollTo({top: 0, left: 0, behavior: "auto"});
  });
}

function initialViewFromUrl() {
  try {
    const view = new URLSearchParams(window.location.search).get("view") || "";
    return document.getElementById(view)?.classList.contains("view-panel") ? view : "";
  } catch {
    return "";
  }
}

function initialTaskLogFromUrl() {
  try {
    return new URLSearchParams(window.location.search).get("task_log") || "";
  } catch {
    return "";
  }
}

function workflowKeyForView(viewId) {
  if (viewId === "workbenchView" || viewId === "datasetView" || viewId === "openvikingView") return "import";
  if (viewId === "evalView") return "qa";
  if (viewId === "judgeView") return "judge";
  if (viewId === "runsView") return "report";
  return "import";
}

function updateWorkflowSelection(workflowKey = "") {
  const activeKey = workflowKey || state.activeWorkflowKey || "import";
  document.querySelectorAll("#workflowGuide .workflow-step").forEach((step) => {
    const selected = step.dataset.workflowKey === activeKey;
    step.classList.toggle("selected", selected);
    step.setAttribute("aria-current", selected ? "step" : "false");
  });
}

function updateLocomoFlowNav(viewId) {
  const nav = $("locomoFlowNav");
  if (!nav) return;
  const flowViews = new Set(["workbenchView", "datasetView", "openvikingView", "evalView", "judgeView", "runsView", "memoryView"]);
  const benchmarkContext = benchmarkFlowContext(viewId);
  const visible = flowViews.has(viewId) || Boolean(benchmarkContext);
  nav.hidden = !visible;
  if ($("locomoOverviewPanel")) $("locomoOverviewPanel").hidden = true;
  if (benchmarkContext) {
    nav.setAttribute("aria-label", `${benchmarkContext.label || "当前数据集"} 评测流程`);
    const activeStage = viewId === "runsView"
      ? "report"
      : normalizeBenchmarkFlowStage(state.activeBenchmarkFlowStage) || defaultBenchmarkFlowStage(viewId);
    renderFlowNavCards(nav, benchmarkFlowCards(benchmarkContext), activeStage);
    return;
  }
  nav.setAttribute("aria-label", "LoCoMo 评测流程");
  const activeView = locomoFlowMainView(viewId);
  renderFlowNavCards(nav, LOCOMO_FLOW_CARDS, activeView);
}

function normalizeLegacyLabels() {
  document.querySelectorAll("h1, h2, strong, button, span").forEach((node) => {
    const text = node.textContent?.trim();
    if (isLegacyEvalTitle(text)) node.textContent = "问答测试";
  });
  const viewTitle = $("viewTitle");
  if (isLegacyEvalTitle(viewTitle?.textContent)) {
    viewTitle.textContent = "问答测试";
  }
}

function isLegacyEvalTitle(text) {
  return (text || "").trim().replace(/^\d+\.\s*/, "").replace(/\s+/g, "") === "LoCoMo测试";
}

function setGuideStep(index, status, text = "") {
  const steps = document.querySelectorAll("#workflowGuide .workflow-step");
  const step = steps[index];
  if (!step) return;
  step.classList.toggle("done", status === "done");
  step.classList.toggle("active", status === "active");
  step.classList.toggle("warn", status === "warn");
  step.classList.toggle("pending", status === "pending");
  step.dataset.status = status || "pending";
  if (text) {
    const p = step.querySelector("p");
    if (p) p.textContent = text;
  }
  updateWorkflowSelection();
}

function currentLocomoDataset() {
  const path = $("data")?.value.trim() || "";
  const candidates = [state.locomoDataset, state.dataset];
  return candidates.find((item) => item?.format === "locomo" && (!path || datasetPathMatches(item.path, path) || datasetPathMatches(item.resolved_path, path))) || null;
}

function updateWorkflowGuide() {
  const workflowGuide = $("workflowGuide");
  if (!workflowGuide) return;
  workflowGuide.hidden = true;
}

function slugTime() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function safeAccountSlug(account) {
  return String(account || "default").replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "") || "default";
}

function recordAccount(record = {}) {
  const source = record && typeof record === "object" ? record : {};
  const config = source.meta?.config || source.config || {};
  const summaryJson = source.summary?.summary_json || source.summary_json || {};
  const command = source.command || [];
  return String(
    source.account
    || config.account
    || summaryJson.account
    || commandOption(command, "account")
    || "",
  ).trim();
}

function recordWorkspace(record = {}) {
  const config = record.meta?.config || record.config || {};
  const summaryJson = record.summary?.summary_json || record.summary_json || {};
  const command = record.command || [];
  return String(
    record.workspace
    || config.workspace
    || config.openviking_workspace
    || summaryJson.workspace
    || summaryJson.openviking_workspace
    || commandOption(command, "workspace")
    || "",
  ).trim();
}

function importScopeFromTask(task = {}, fallback = {}) {
  const config = task.meta?.config || task.config || {};
  const backend = normalizeMemoryBackend(
    config.backend
    || fallback.backend
    || (task.kind === "echomemory_import" ? "echomemory" : "")
    || currentMemoryBackend()
  );
  const account = safeAccountSlug(recordAccount(task) || fallback.account || currentAccount() || "default");
  const workspace = recordWorkspace(task) || fallback.workspace || "";
  return {backend, account, workspace};
}

function syncImportTaskFields(task = {}, options = {}) {
  const scope = importScopeFromTask(task);
  if (scope.account) {
    saveAccountList([...readAccountList(), scope.account]);
    const hasAccountOption = Boolean($("accountSelect")?.querySelector(`option[value="${CSS.escape(scope.account)}"]`));
    if (options.selectAccount || !hasAccountOption) {
      renderAccountSelect(scope.account);
    } else {
      syncAccountFields(scope.account);
    }
  }
  if (scope.workspace) {
    if ($("ovWorkspace")) $("ovWorkspace").value = scope.workspace;
    if ($("memoryWorkspace")) $("memoryWorkspace").value = scope.workspace;
  }
  if (scope.backend && $("memoryBackendSelect")) $("memoryBackendSelect").value = scope.backend;
  return scope;
}

function normalizeEvidenceScope(scope = {}) {
  const workspace = String(scope.workspace || "").trim();
  const account = safeAccountSlug(scope.account || "default");
  return workspace ? {workspace, account} : null;
}

function evidenceScopeFromRecord(record = {}) {
  return normalizeEvidenceScope({
    workspace: recordWorkspace(record),
    account: recordAccount(record) || "default",
  });
}

function rememberEvidenceScope(record = {}, outputFile = "") {
  const scope = evidenceScopeFromRecord(record);
  if (!scope) return null;
  state.activeEvidenceScope = scope;
  const file = String(outputFile || record.output_file || "").trim();
  if (file) state.evidenceScopesByOutput[file] = scope;
  return scope;
}

function currentEvidenceScope(outputFile = "") {
  const file = String(outputFile || state.outputFile || "").trim();
  if (file && state.evidenceScopesByOutput[file]) return state.evidenceScopesByOutput[file];
  if (file && state.currentLocomoTask?.output_file === file) {
    const scope = rememberEvidenceScope(state.currentLocomoTask, file);
    if (scope) return scope;
  }
  if (file) {
    const run = (state.recentRuns || []).find((item) => item.output_file === file);
    const scope = run ? rememberEvidenceScope(run, file) : null;
    if (scope) return scope;
  }
  const selectedScope = evidenceScopeFromRecord(state.selectedRunRecord || {});
  if (selectedScope) return selectedScope;
  return state.activeEvidenceScope || currentWorkspaceAndAccount();
}

function matchesCurrentAccount(record = {}) {
  const wanted = safeAccountSlug(currentAccount());
  const account = safeAccountSlug(recordAccount(record) || "default");
  return account === wanted;
}

function currentAccountOnlyEnabled(id) {
  const el = $(id);
  return !el || Boolean(el.checked);
}

async function loadTaskLogIntoBox(task, kind = "") {
  if (!task?.id) return;
  let taskRecord = task;
  if (!taskRecord.kind || !taskRecord.dataset_format) {
    taskRecord = await api(`/api/tasks/${encodeURIComponent(task.id)}`).catch(() => task) || task;
  }
  enrichTaskDatasetFormat(taskRecord, task.dataset_format || "");
  const ui = taskUi(kind || taskRecord.kind || state.taskKind || "openviking_qa", taskRecord);
  const box = $(ui.logBox);
  if (!box) return;
  const data = await api(`/api/tasks/${encodeURIComponent(taskRecord.id)}/log?offset=0`);
  state.logOffsets[taskRecord.id] = data.offset || 0;
  box.dataset.taskId = String(taskRecord.id || "");
  box.textContent = data.text || "这个任务还没有写出日志。";
  box.scrollTop = box.scrollHeight;
  box.closest(".log-details")?.setAttribute("open", "");
  return data.task || taskRecord;
}

function runLogPathFromRecord(record = {}) {
  const direct = String(record.log_file || "").trim();
  if (direct) return direct;
  const runDir = String(record.run_dir || "").replace(/\/+$/, "");
  if (runDir) return `${runDir}/run.log`;
  const output = String(record.output_file || "").trim();
  if (!output) return "";
  const folder = dirname(output);
  const parent = dirname(folder);
  return parent ? `${parent}/run.log` : "";
}

async function loadLogPathIntoBox(path, logBoxId = "importLogBox") {
  const logPath = String(path || "").trim();
  if (!logPath) return false;
  const box = openTaskLogBox(logBoxId);
  if (!box) return false;
  box.dataset.logPath = logPath;
  try {
    const data = await api(`/api/log-tail?path=${encodeURIComponent(logPath)}&limit=80000`);
    box.textContent = data.text || "这个日志文件还没有内容。";
    box.scrollTop = box.scrollHeight;
    return Boolean(data.exists);
  } catch (error) {
    box.textContent = `日志读取失败：${error.message || error}\n${logPath}`;
    return false;
  }
}

async function loadLatestImportLogFallback() {
  const box = $("importLogBox");
  if (!box || !/日志会显示在这里|这个任务还没有写出日志/.test(String(box.textContent || "").trim())) return;
  const lastImport = readLastImport();
  const lastLog = runLogPathFromRecord(lastImport);
  if (lastLog && await loadLogPathIntoBox(lastLog, "importLogBox")) return;
  const data = await api("/api/runs?include_history=1&limit=80").catch((error) => {
    box.textContent = `历史日志查询失败：${error.message || error}`;
    return {};
  });
  const run = (data.runs || []).find((item) => isMemoryImportKind(item.kind || ""));
  const runLog = runLogPathFromRecord(run || {});
  if (runLog) {
    await loadLogPathIntoBox(runLog, "importLogBox");
  } else if (!box.dataset.logPath) {
    box.textContent = "暂无运行中的导入任务，也没有找到历史导入日志文件。";
  }
}

function openTaskLogBox(logBoxId) {
  const box = $(logBoxId);
  if (!box) return null;
  box.closest(".log-details")?.setAttribute("open", "");
  box.closest(".import-section")?.classList.add("live-log-section");
  return box;
}

function resetTaskLogPlaceholder(logBoxId) {
  const box = openTaskLogBox(logBoxId);
  if (!box) return null;
  const text = String(box.textContent || "").trim();
  if (!text || /日志会显示在这里|这个任务还没有写出日志/.test(text)) {
    box.textContent = "";
  }
  return box;
}

function ensureTaskPolling(task, kind = "") {
  if (!task?.id || (!isTaskActive(task) && !isTaskTerminal(task))) return;
  const taskKind = kind || task.kind || state.taskKind || "openviking_qa";
  const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
  if (!taskVisibleInCurrentTaskPanel({...task, kind: task.kind || taskKind}, format, activeViewId())) return;
  rememberTaskDatasetFormat(task.id, format);
  const ui = taskUi(taskKind, {...task, dataset_format: format});
  resetTaskLogPlaceholder(ui.logBox);
  if (state.logTimers[task.id]) return;
  state.logOffsets[task.id] = state.logOffsets[task.id] || 0;
  pollTask(task.id, taskKind).catch(() => {});
  state.logTimers[task.id] = setInterval(() => pollTask(task.id, taskKind).catch(() => {}), 1000);
}

function cleanWorkspaceForAccount(account) {
  return timestampWorkspaceForAccount(account, currentMemoryBackend());
}

function maybeRegenerateWorkspaceForBackend(account, backend) {
  const input = $("ovWorkspace");
  if (!input) return "";
  const current = input.value.trim();
  const targetPrefix = workspacePrefixForBackend(backend);
  const currentName = current.split("/").pop() || "";
  const shouldReplace = !current
    || isLegacyFixedWorkspace(current)
    || (isGeneratedMemoryWorkspace(current) && !currentName.startsWith(`${targetPrefix}_`));
  if (!shouldReplace) return "";
  const generated = timestampWorkspaceForAccount(account, backend);
  input.value = generated;
  if ($("memoryWorkspace")) $("memoryWorkspace").value = generated;
  return generated;
}

function generateWorkspaceForCurrentAccount() {
  const account = currentAccount();
  const backend = currentMemoryBackend();
  const generated = timestampWorkspaceForAccount(account, backend);
  if ($("ovWorkspace")) $("ovWorkspace").value = generated;
  if ($("memoryWorkspace")) $("memoryWorkspace").value = generated;
  persistCurrentAccountConfig();
  renderImportPaths();
  renderImportReadinessPanel();
  renderQaReadinessPanel();
  toast(`已生成新的${memoryBackendLabel(backend)}记忆目录`);
  return generated;
}

function initializeCleanAccountConfig(account, inherited = {}) {
  const workspace = cleanWorkspaceForAccount(account);
  saveAccountConfig(account, {
    memoryBackend: normalizeMemoryBackend(inherited.memoryBackend || $("memoryBackendSelect")?.value || "openviking"),
    ovHost: inherited.ovHost || $("ovHost")?.value.trim() || state.config.server_host || "127.0.0.1",
    ovPort: inherited.ovPort || $("ovPort")?.value.trim() || state.config.server_port || "19080",
    ovWorkspace: workspace,
    memoryWorkspace: workspace,
    ovApiKey: inherited.ovApiKey || $("ovApiKey")?.value.trim() || "",
    judgeBaseUrl: inherited.judgeBaseUrl || $("judgeBaseUrl")?.value.trim() || state.config.judge_base_url || "",
    judgeModel: inherited.judgeModel || $("judgeModel")?.value.trim() || state.config.judge_model || "",
    judgeToken: inherited.judgeToken || $("systemJudgeToken")?.value.trim() || $("judgeToken")?.value.trim() || "",
    agentBaseUrl: inherited.agentBaseUrl || $("systemAgentBaseUrl")?.value.trim() || $("judgeBaseUrl")?.value.trim() || state.config.judge_base_url || "",
    agentModel: inherited.agentModel || $("systemAgentModel")?.value.trim() || $("judgeModel")?.value.trim() || state.config.judge_model || "",
    agentToken: inherited.agentToken || $("systemAgentToken")?.value.trim() || "",
    memoryInjectBaseUrl: inherited.memoryInjectBaseUrl || $("systemMemoryBaseUrl")?.value.trim() || $("ovVlmBaseUrl")?.value.trim() || state.config.vlm_base_url || "",
    memoryInjectModel: inherited.memoryInjectModel || $("systemMemoryModel")?.value.trim() || $("ovVlmModel")?.value.trim() || state.config.vlm_model || "",
    memoryInjectToken: inherited.memoryInjectToken || $("systemMemoryToken")?.value.trim() || $("ovVlmApiKey")?.value.trim() || "",
    chatTopK: inherited.chatTopK || $("chatTopK")?.value || "",
  });
  applyAccountConfig(account);
  return workspace;
}

async function loadConfig() {
  const cfg = await api("/api/config");
  const lastImport = readLastImport();
  const lastDataset = readLastLocomoDataset();
  const initialView = initialViewFromUrl();
  state.bootRequestedView = initialView || "openvikingView";
  state.bootHydrating = true;
  state.userNavigatedDuringBoot = false;
  state.config = cfg;
  applyUiContract(cfg.ui_contract || {});
  $("data").value = relativeDatasetPath(lastDataset.path || cfg.data || "");
  $("judgeBaseUrl").value = cfg.judge_base_url || "";
  $("judgeModel").value = cfg.judge_model || "gpt-5.5";
  $("ovHost").value = cfg.server_host || "127.0.0.1";
  $("ovPort").value = cfg.server_port || "19080";
  const bootAccountRecords = Array.isArray(cfg.accounts) ? cfg.accounts : [];
  if (bootAccountRecords.length) {
    mergeBackendAccountState({
      accounts: bootAccountRecords,
      active_account: cfg.active_account || cfg.account || "default",
      state_file: cfg.account_state_file || "",
    });
  }
  const bootActiveAccount = String(
    cfg.active_account
    || localStorage.getItem(ACTIVE_ACCOUNT_KEY)
    || lastImport.account
    || cfg.account
    || "default"
  ).trim() || "default";
  if (cfg.active_account_config && typeof cfg.active_account_config === "object") {
    cacheAccountConfig(bootActiveAccount, cfg.active_account_config);
    saveAccountConfig(bootActiveAccount, cfg.active_account_config);
  }
  saveAccountList([...readAccountList(), ...bootAccountRecords.map((item) => String(item?.id || "").trim()).filter(Boolean), bootActiveAccount]);
  renderAccountSelect(bootActiveAccount);
  $("ovAccount").value = bootActiveAccount;
  if ($("memoryAccount")) $("memoryAccount").value = bootActiveAccount;
  applyAccountConfig(bootActiveAccount);
  const accountState = await loadBackendAccounts(2500).catch(() => null);
  if (!accountState) {
    loadBackendAccounts(10000).catch(() => null);
  }
  const activeAccount = accountState?.active_account || bootActiveAccount;
  saveAccountList([...readAccountList(), activeAccount]);
  renderAccountSelect(activeAccount);
  $("ovAccount").value = activeAccount;
  await loadAccountConfigFromBackend(activeAccount).catch(() => null);
  const activeAccountConfig = readAccountConfig(activeAccount);
  if (!activeAccountConfig.ovWorkspace && (cfg.openviking_workspace || cfg.workspace) && !isLegacyFixedWorkspace(cfg.openviking_workspace || cfg.workspace)) {
    saveAccountConfig(activeAccount, {
      ovWorkspace: cfg.openviking_workspace || cfg.workspace || "",
      memoryWorkspace: cfg.openviking_workspace || cfg.workspace || "",
    });
  }
  if ($("memoryWorkspace")) $("memoryWorkspace").value = $("ovWorkspace").value || "";
  if ($("memoryAccount")) $("memoryAccount").value = activeAccount;
  applyAccountConfig(activeAccount);
  if (initialView) showView(initialView, {preserveScroll: true, clearTaskLog: false});
  renderImportPaths();
  await Promise.allSettled([
    loadBackends(),
    loadDatasetRegistry(),
  ]);
  const needsLocomoDataset = !initialView || ["workbenchView", "datasetView", "openvikingView", "evalView", "judgeView", "memoryView"].includes(initialView);
  if (needsLocomoDataset) await loadDataset(true);
  else renderLocomoOverview();
  if (lastImport.sample_value && $("importSample")) $("importSample").value = lastImport.sample_value;
  renderImportPaths();
  setConnection(true, "已就绪");
  state.tasksHydrating = true;
  await Promise.allSettled([
    refreshImportedMemories(),
    refreshTasks()
      .catch(() => null)
      .then(() => refreshStandaloneBenchmarkViewOnly())
      .catch(() => null),
  ]);
  state.tasksHydrating = false;
  refreshLiveTaskDisplays();
  setConnection(true, "已就绪");
  updateWorkflowGuide();
  Promise.allSettled([
    runSystemPreflight(true),
    runHandoffDashboard(["handoffDashboardPanel", "handoffDashboardReadmePanel"], true),
    runDeliveryBoundaryGate(["deliveryBoundaryPanel", "deliveryBoundaryReadmePanel"], true),
    runAgentAlignment(["agentAlignmentPanel", "agentAlignmentReadmePanel", "agentAlignmentWorkbenchPanel"], true),
    runAccountIsolation(["accountIsolationGatePanel", "accountIsolationReadmePanel"], true),
    runGithubLaunchKit(["githubLaunchKitReadmePanel"], true),
    refreshLocomoFlowStatus(true),
  ])
    .then(() => updateWorkflowGuide())
    .catch(() => {});
  const activeViewAfterBootWork = document.body.dataset.activeView || "";
  const locomoBootViews = new Set(["workbenchView", "datasetView", "openvikingView", "evalView", "judgeView", "memoryView"]);
  const initialViewIsLocomoBoot = locomoBootViews.has(initialView);
  const shouldRestoreBenchmarkView = isStandaloneBenchmarkView(initialView)
    && (!activeViewAfterBootWork || locomoBootViews.has(activeViewAfterBootWork));
  const stayedOnBootView = !activeViewAfterBootWork
    || activeViewAfterBootWork === initialView
    || shouldRestoreBenchmarkView;
  if (initialView && !state.userNavigatedDuringBoot) {
    if (stayedOnBootView) showView(initialView, {preserveScroll: shouldRestoreBenchmarkView});
  } else if (!activeViewAfterBootWork) {
    showView("openvikingView", {preserveScroll: true});
  }
  const initialTaskLog = initialTaskLogFromUrl();
  if (initialTaskLog && (!initialView || stayedOnBootView) && !state.userNavigatedDuringBoot) {
    const taskRecord = await loadTaskLogIntoBox({id: initialTaskLog}).catch((e) => {
      toast(e.message);
      return null;
    });
    if (taskRecord) {
      const logView = taskView(taskRecord, initialView || "runsView");
      showView(logView, {taskLog: initialTaskLog});
      await loadTaskLogIntoBox(taskRecord).catch((e) => toast(e.message));
    }
  }
  state.bootHydrating = false;
}

async function loadDatasetRegistry() {
  const data = await api("/api/datasets");
  state.datasetRegistry = data.datasets || [];
  const cards = $("datasetCards");
  if (cards) {
    const visibleDatasets = state.datasetRegistry.filter((item) => normalizeDatasetFormat(item.format) !== "chenmo");
    if ($("data") && !$("data").value.trim()) {
      $("data").value = preferredLocomoDatasetPath();
    }
    cards.innerHTML = visibleDatasets.map((item) => `
      <article class="dataset-card ${item.exists ? "" : "missing"}" data-path="${escapeHtml(item.path || "")}" data-format="${escapeHtml(item.format || "")}">
        <span>${escapeHtml(datasetTypeLabel(item.format))}</span>
        <strong>${escapeHtml(item.name || item.id || "-")}</strong>
        <small>${escapeHtml(item.description || "")}</small>
        <em>${item.exists ? `${escapeHtml(item.samples ?? "-")} samples · ${escapeHtml(item.questions ?? "-")} questions` : "missing"}</em>
        <code>${escapeHtml(item.path || "")}</code>
      </article>
    `).join("") || "<p>没有在数据集注册表里找到数据集。请检查 dataset/manifest.json。</p>";
    document.querySelectorAll("#datasetCards .dataset-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.path === $("data").value.trim());
      card.addEventListener("click", async () => {
        document.querySelectorAll("#datasetCards .dataset-card").forEach((item) => item.classList.remove("active"));
        card.classList.add("active");
        await openDatasetCard(card.dataset.path || "", card.dataset.format || "");
      });
    });
  }
  renderLongMemDatasetCards();
  initializeGenericBenchmarkDefaults();
  updateAllGenericRunButtons();
}

function genericBenchmarkKeyForFormat(format = "") {
  const normalized = normalizeDatasetFormat(format);
  for (const [key, config] of Object.entries(GENERIC_BENCHMARKS)) {
    if (normalizeDatasetFormat(config.adapterFormat) === normalized) return key;
  }
  return "";
}

async function openDatasetCard(path, format = "") {
  if (!path) return;
  let normalized = normalizeDatasetFormat(format);
  if (!normalized) {
    const data = await api(`/api/dataset?path=${encodeURIComponent(path)}`);
    normalized = normalizeDatasetFormat(data.format);
    if (!normalized || normalized === "generic") {
      await validateSelectedDatasetCard(path);
      return;
    }
    await openDatasetCard(data.resolved_path || path, normalized);
    return;
  }
  const resolvedPath = path;
  if (normalized === "locomo") {
    if ($("data")) $("data").value = relativeDatasetPath(resolvedPath);
    saveLastLocomoDataset({path: resolvedPath});
  } else {
    saveLastBenchmarkDataset({path: resolvedPath, format: normalized});
  }
  document.querySelectorAll("#datasetCards .dataset-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.path === resolvedPath);
  });
  const targetView = viewForDatasetFormat(normalized, "");
  if (normalized === "locomo") {
    rememberActiveDatasetView(targetView || "evalView", normalized, resolvedPath);
    await loadDataset();
    return;
  }
  if (normalized === "chenmo") {
    toast("ChenMo 评测入口已移除");
    showView("openvikingView");
    return;
  }
  if (normalized === "longmemeval") {
    if ($("longMemData")) $("longMemData").value = resolvedPath;
    renderLongMemEntryStatus(resolvedPath, datasetRecordForPath(resolvedPath, normalized));
    rememberActiveDatasetView("longMemEvalView", normalized, resolvedPath);
    showView("longMemEvalView");
    validateLongMemDataset().catch((e) => {
      renderLongMemEntryStatus(resolvedPath, datasetRecordForPath(resolvedPath, normalized));
      toast(e.message);
    });
    return;
  }
  const benchmarkKey = genericBenchmarkKeyForFormat(normalized);
  if (benchmarkKey) {
    const config = benchmarkConfig(benchmarkKey);
    if ($(config.dataInput)) $(config.dataInput).value = resolvedPath;
    renderGenericEntryStatus(benchmarkKey, resolvedPath, datasetRecordForPath(resolvedPath, normalized));
    rememberActiveDatasetView(config.view, normalized, resolvedPath);
    showView(config.view);
    validateGenericBenchmark(benchmarkKey).catch((e) => {
      renderGenericEntryStatus(benchmarkKey, resolvedPath, datasetRecordForPath(resolvedPath, normalized));
      toast(e.message);
    });
    return;
  }
  await validateSelectedDatasetCard(resolvedPath);
}

async function validateSelectedDatasetCard(path) {
  if (!path) return;
  const data = await api(`/api/dataset?path=${encodeURIComponent(path)}`);
  renderKpis("datasetKpis", [
    ["数据集类型", datasetTypeLabel(data.format)],
    ["样本数", data.samples ?? "-"],
    ["题目数", data.questions ?? "-"],
  ]);
  renderDatasetCategories(data);
  if ($("datasetRunnerNote")) {
    $("datasetRunnerNote").innerHTML = `
      <p><strong>${escapeHtml(datasetTypeLabel(data.format))} 已加入系统</strong> · ${escapeHtml(data.samples ?? "-")} 个样本 · ${escapeHtml(data.questions ?? "-")} 题</p>
      <p class="dataset-next-step">${escapeHtml(datasetRunnerNote(data.format, data.runner_note, "可在历史结果中查看对应测试报告。"))}</p>
    `;
  }
  toast(`${datasetTypeLabel(data.format)} 校验完成`);
}

function renderLongMemDatasetCards() {
  const target = $("longMemDatasetCards");
  if (!target) return;
  const rows = state.datasetRegistry.filter((item) => String(item.format || "").toLowerCase() === "longmemeval");
  if ($("longMemData") && !$("longMemData").value.trim()) {
    const firstExisting = rows.find((item) => item.exists) || rows[0];
    if (firstExisting?.path) $("longMemData").value = firstExisting.path;
  }
  target.innerHTML = rows.map((item) => `
    <article class="dataset-card ${item.exists ? "" : "missing"}" data-path="${escapeHtml(item.path || "")}">
      <span>${escapeHtml(datasetTypeLabel(item.format))}</span>
      <strong>${escapeHtml(item.name || item.id || "-")}</strong>
      <small>${escapeHtml(item.description || "")}</small>
      <em>${item.exists ? `${escapeHtml(item.samples ?? "-")} samples · ${escapeHtml(item.questions ?? "-")} questions` : "missing"}</em>
      <code>${escapeHtml(item.path || "")}</code>
    </article>
  `).join("") || "<p>没有在数据集注册表里找到 LongMemEval。可以手动填写 JSON 路径。</p>";
  document.querySelectorAll("#longMemDatasetCards .dataset-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.path === $("longMemData")?.value.trim());
    card.addEventListener("click", async () => {
      if ($("longMemData")) $("longMemData").value = card.dataset.path || "";
      document.querySelectorAll("#longMemDatasetCards .dataset-card").forEach((item) => item.classList.remove("active"));
      card.classList.add("active");
      renderLongMemEntryStatus(card.dataset.path || "", datasetRecordForPath(card.dataset.path || "", "longmemeval"));
      validateLongMemDataset().catch((e) => {
        renderLongMemEntryStatus(card.dataset.path || "", datasetRecordForPath(card.dataset.path || "", "longmemeval"));
        toast(e.message);
      });
    });
  });
}

function benchmarkConfig(key) {
  const config = GENERIC_BENCHMARKS[key];
  if (!config) throw new Error(`未知评测入口：${key}`);
  return config;
}

function genericBenchmarkLaunchError(format = "") {
  const key = genericBenchmarkKeyForFormat(format);
  return key ? state.genericBenchmarkLaunchErrors[key] || null : null;
}

function clearGenericBenchmarkLaunchError(format = "") {
  const key = genericBenchmarkKeyForFormat(format);
  if (key) delete state.genericBenchmarkLaunchErrors[key];
  if (normalizeDatasetFormat(format) === "hotpotqa" && document.body) {
    delete document.body.dataset.hotpotqaLaunchBlocked;
  }
}

function rememberGenericBenchmarkLaunchError(format = "", payload = null) {
  const key = genericBenchmarkKeyForFormat(format);
  if (!key || !payload) return;
  state.genericBenchmarkLaunchErrors[key] = payload;
  if (normalizeDatasetFormat(format) === "hotpotqa" && document.body) {
    document.body.dataset.hotpotqaLaunchBlocked = "1";
  }
}

function renderGenericBenchmarkLaunchError(format = "", payload = null) {
  const key = genericBenchmarkKeyForFormat(format);
  if (!key) return;
  const config = benchmarkConfig(key);
  const data = payload || genericBenchmarkLaunchError(format);
  if (!data) return;
  if ($(config.status)) {
    $(config.status).innerHTML = `
      <p><strong>${escapeHtml(config.label)} 启动失败</strong></p>
      <p class="dataset-next-step bad-text">${escapeHtml(data.friendly || "启动失败，请检查模型配置或服务状态")}</p>
      <p class="dataset-next-step">账户 ${escapeHtml(data.account || currentAccount())} · 回答模型 ${escapeHtml(data.agentModel || "-")} @ ${escapeHtml(data.agentBaseUrl || "-")}</p>
      <p class="dataset-next-step">判分模型 ${escapeHtml(data.judgeModel || "-")} @ ${escapeHtml(data.judgeBaseUrl || "-")}</p>
      <p class="dataset-next-step">本次请求 ${escapeHtml(data.requestLabel || "-")} · 数据 ${escapeHtml(data.path || "")}</p>
      ${errorDetailHtml(data.raw || "")}
    `;
  }
  if ($(config.result)) {
    $(config.result).innerHTML = `
      <p><strong>任务未创建</strong></p>
      <p>${escapeHtml(data.friendly || "启动失败，请检查模型配置或服务状态")}</p>
      <p class="dataset-next-step">服务端在创建阶段已拦截这次运行，所以不会继续消耗题目，也不会出现新的活动进度。</p>
      <p class="dataset-next-step">优先处理系统配置里的回答模型账号状态或 token；修复后再重新点击“开始测试”。</p>
      ${errorDetailHtml(data.raw || "")}
    `;
  }
  if ($(config.progressText)) $(config.progressText).textContent = `${config.label} 启动失败`;
  if ($(config.progressBar)) $(config.progressBar).style.width = "0%";
}

function invalidateHotpotQaModelReadiness() {
  state.hotpotQaModelReadiness = null;
  state.hotpotQaModelReadinessFetchedAt = 0;
}

function renderHotpotQaModelReadiness(data = state.hotpotQaModelReadiness) {
  const target = $("hotpotQaModelReadiness");
  if (!target) return;
  const agentCfg = agentModelConfig();
  const judgeCfg = judgeModelConfig();
  const launchError = genericBenchmarkLaunchError("hotpotqa");
  if (state.hotpotQaModelReadinessLoading && !data) {
    target.innerHTML = `
      <p><strong>启动前模型检查</strong></p>
      <p>正在检查回答模型和判分模型可用性...</p>
    `;
    return;
  }
  const checkedAt = data?.checkedAt ? formatDateTimeLocal(data.checkedAt) : "";
  const answer = data?.answer || null;
  const judge = data?.judge || null;
  const answerSummary = answer
    ? (answer.ok
      ? `可用 · ${answer.model || agentCfg.model || "-"}`
      : `${friendlyUiError(answer.error || "", "回答模型不可用")} · status ${answer.status || "-"}`)
    : "尚未检查";
  const judgeSummary = judge
    ? (judge.ok
      ? `可用 · ${judge.model || judgeCfg.model || "-"}`
      : `${friendlyUiError(judge.error || "", "判分模型不可用")} · status ${judge.status || "-"}`)
    : "尚未检查";
  target.innerHTML = `
    <p><strong>启动前模型检查</strong>${checkedAt ? ` · ${escapeHtml(checkedAt)}` : ""}</p>
    <p class="dataset-next-step">回答模型 ${escapeHtml(agentCfg.model || "-")} @ ${escapeHtml(agentCfg.baseUrl || "-")}</p>
    <p class="dataset-next-step ${answer && !answer.ok ? "bad-text" : ""}">${escapeHtml(answerSummary)}</p>
    <p class="dataset-next-step">判分模型 ${escapeHtml(judgeCfg.model || "-")} @ ${escapeHtml(judgeCfg.baseUrl || "-")}</p>
    <p class="dataset-next-step ${judge && !judge.ok ? "bad-text" : ""}">${escapeHtml(judgeSummary)}</p>
    ${launchError ? `<p class="dataset-next-step bad-text">最近一次启动被拦截：${escapeHtml(launchError.friendly || "模型检查未通过")}</p>` : ""}
    <p class="dataset-next-step">这一步只做模型可用性预检，不会启动任务。</p>
  `;
}

async function refreshHotpotQaModelReadiness(force = false) {
  if (!$("hotpotQaModelReadiness")) return null;
  const now = Date.now();
  if (!force && state.hotpotQaModelReadiness && state.hotpotQaModelReadinessFetchedAt && now - state.hotpotQaModelReadinessFetchedAt < 60000) {
    renderHotpotQaModelReadiness(state.hotpotQaModelReadiness);
    return state.hotpotQaModelReadiness;
  }
  if (state.hotpotQaModelReadinessLoading) {
    renderHotpotQaModelReadiness(state.hotpotQaModelReadiness);
    return state.hotpotQaModelReadiness;
  }
  state.hotpotQaModelReadinessLoading = true;
  renderHotpotQaModelReadiness(state.hotpotQaModelReadiness);
  try {
    const agentCfg = agentModelConfig();
    const judgeCfg = judgeModelConfig();
    const [answer, judge] = await Promise.all([
      api("/api/model-preflight", {
        method: "POST",
        body: JSON.stringify({
          role: "answer",
          base_url: agentCfg.baseUrl,
          model: agentCfg.model,
          api_key: agentCfg.token,
          timeout_s: 45,
        }),
      }).catch((error) => ({ok: false, status: "request_error", error: error.message || String(error), model: agentCfg.model, base_url: agentCfg.baseUrl})),
      api("/api/model-preflight", {
        method: "POST",
        body: JSON.stringify({
          role: "judge",
          base_url: judgeCfg.baseUrl,
          model: judgeCfg.model,
          api_key: judgeCfg.token,
          timeout_s: 45,
        }),
      }).catch((error) => ({ok: false, status: "request_error", error: error.message || String(error), model: judgeCfg.model, base_url: judgeCfg.baseUrl})),
    ]);
    state.hotpotQaModelReadiness = {
      checkedAt: new Date().toISOString(),
      answer,
      judge,
      account: currentAccount(),
    };
    state.hotpotQaModelReadinessFetchedAt = Date.now();
    renderHotpotQaModelReadiness(state.hotpotQaModelReadiness);
    return state.hotpotQaModelReadiness;
  } finally {
    state.hotpotQaModelReadinessLoading = false;
  }
}

function genericBenchmarkPath(key) {
  const config = benchmarkConfig(key);
  return $(config.dataInput)?.value.trim() || "";
}

function renderLongMemEntryStatus(path = "", record = {}) {
  if (!$("longMemStatus")) return;
  const size = datasetSizeLabel(record);
  const backendLabel = memoryBackendLabel(currentMemoryBackend());
  $("longMemStatus").innerHTML = `
    <p><strong>已进入 LongMemEval 评测页</strong>${size ? ` · ${escapeHtml(size)}` : ""}</p>
    <p class="dataset-next-step">${escapeHtml(path || "请先选择或填写 LongMemEval JSON。")}</p>
    <p class="dataset-next-step">这里是 LongMemEval 专用入口；当前会通过 ${escapeHtml(backendLabel)} 运行。正式评测要求完整 LongMemEval-S、全部样本、题数 0。</p>
    <div class="panel-actions">
      ${benchmarkPlanLinkHtml()}
      <button class="secondary" type="button" data-view-jump="runsView">查看任务/报告</button>
    </div>
  `;
  $("longMemStatus").querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
}

function renderGenericEntryStatus(key, path = "", record = {}) {
  const config = benchmarkConfig(key);
  if (!$(config.status)) return;
  const size = datasetSizeLabel(record);
  const gate = genericBenchmarkRunGate(key, path, record);
  const backendLabel = memoryBackendLabel(currentMemoryBackend());
  $(config.status).innerHTML = `
    <p><strong>已进入 ${escapeHtml(config.label)} 评测页</strong>${size ? ` · ${escapeHtml(size)}` : ""}</p>
    <p class="dataset-next-step">${escapeHtml(path || config.emptyPathHint)}</p>
    <p class="dataset-next-step">${escapeHtml(gate.ok ? `该入口会固定停留在当前数据集页面，启动后运行正式 MemoryBench ${backendLabel} 记忆问答：导入上下文、检索证据、调用答案模型、自动判分并可导出报告。` : gate.reason)}</p>
    <p class="dataset-next-step">${escapeHtml(benchmarkMetricNote(config))}</p>
  `;
  if (config.adapterFormat === "hotpotqa") updateHotpotQaInlineLiveReport(null);
  updateGenericRunButton(key, record);
}

function updateHotpotQaInlineLiveReport(task = null, options = {}) {
  const frame = $("hotpotQaLiveInlineFrame");
  const open = $("hotpotQaLiveInlineOpen");
  const meta = $("hotpotQaLiveInlineMeta");
  const idle = $("hotpotQaLiveInlineIdle");
  if (!frame || !open || !meta) return;
  const fallbackHref = "/generated-reports/hotpotqa_echomemory_live_current.html";
  const reportPath = task?.run_dir ? `${task.run_dir}/report.html` : "";
  const reportHref = artifactHref(reportPath);
  const hasReportHtml = Boolean(options.reportReady || options.summary?._artifact_status?.report_html?.exists);
  const href = isTaskActive(task)
    ? fallbackHref
    : (hasReportHtml ? (reportHref || options.href || fallbackHref) : (options.href || fallbackHref));
  if (isTaskActive(task)) {
    if (href && frame.getAttribute("src") !== href) frame.setAttribute("src", href);
    frame.hidden = false;
    if (idle) idle.hidden = true;
  } else {
    frame.hidden = true;
    if (idle) idle.hidden = false;
  }
  open.setAttribute("href", href || fallbackHref);
  const summary = options.summary && typeof options.summary === "object" ? options.summary : {};
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const execution = task ? taskExecutionProgress(task, "hotpotqa") : null;
  const progressLabel = execution?.total_questions
    ? `${execution.current_question || execution.answered_questions || 0}/${execution.total_questions}`
    : "";
  const taskId = String(task?.id || "").trim();
  const account = String(options.account || recordAccount(task) || "").trim();
  if (taskId) {
    meta.textContent = [
      `内嵌的是当前运行任务的 live 报告：${taskId}`,
      progressLabel ? `进度 ${progressLabel}` : "",
      rows ? `已写 ${formatInt(rows)} 行` : "",
      account ? `账户 ${account}` : "",
    ].filter(Boolean).join(" · ");
    return;
  }
  meta.textContent = "当前没有运行中的 HotpotQA 任务";
}

function renderGenericRunningStatus(key, task = {}, summary = null) {
  const config = benchmarkConfig(key);
  const target = $(config.status);
  if (!target) return;
  const format = config.adapterFormat || task.dataset_format || "";
  const progress = taskWithLiveProgress(task).progress || {};
  const execution = taskExecutionProgress(task, format);
  const authoritativeProgressLabel = execution?.total_questions
    ? `${execution.current_question || execution.answered_questions || 0}/${execution.total_questions} · 已答 ${execution.answered_questions || 0}`
    : "";
  const summaryJson = summary?.summary_json || {};
  const rows = Number(summary?.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const totalInjection = summary?.total_memory_injection_time_s ?? summaryJson.total_memory_injection_time_s;
  const avgInjection = summary?.avg_memory_injection_time_s ?? summaryJson.avg_memory_injection_time_s;
  const totalQa = summary?.total_qa_time_s ?? summaryJson.total_qa_time_s;
  const avgQa = summary?.avg_qa_time_s ?? summaryJson.avg_qa_time_s ?? summary?.avg_time;
  const totalEndToEnd = summary?.total_end_to_end_time_s ?? summaryJson.total_end_to_end_time_s;
  const avgEndToEnd = summary?.avg_end_to_end_time_s ?? summaryJson.avg_end_to_end_time_s;
  const lastQuestionId = String(summary?.last_question_id ?? summaryJson.last_question_id ?? "").trim();
  const elapsed = task.duration != null ? formatDuration(task.duration) : (progress.elapsed_seconds != null ? formatDuration(progress.elapsed_seconds) : "-");
  const eta = progress.eta_seconds != null ? formatDuration(progress.eta_seconds) : "-";
  const output = task.output_file || "";
  target.innerHTML = `
    <p><strong>${escapeHtml(config.label)} 运行中</strong>${authoritativeProgressLabel ? ` · ${escapeHtml(authoritativeProgressLabel)}` : ""}</p>
    <p class="dataset-next-step">已用 ${escapeHtml(elapsed)} · 剩余 ${escapeHtml(eta)} · 已写结果 ${escapeHtml(formatInt(rows))} 行</p>
    <p class="dataset-next-step">平均注入 ${escapeHtml(formatSecondsMetric(avgInjection))} · 平均 QA ${escapeHtml(formatSecondsMetric(avgQa))} · 平均端到端 ${escapeHtml(formatSecondsMetric(avgEndToEnd))}</p>
    <p class="dataset-next-step">累计注入 ${escapeHtml(formatSecondsMetric(totalInjection))} · 累计 QA ${escapeHtml(formatSecondsMetric(totalQa))} · 累计端到端 ${escapeHtml(formatSecondsMetric(totalEndToEnd))}</p>
    ${lastQuestionId ? `<p class="dataset-next-step">最近完成题 ${escapeHtml(lastQuestionId)}</p>` : ""}
    ${output ? `<p class="dataset-next-step">${escapeHtml(output)}</p>` : ""}
  `;
  if (config.adapterFormat === "hotpotqa") {
    updateHotpotQaInlineLiveReport(task, {
      account: recordAccount(task),
      summary,
    });
  }
}

function initializeGenericBenchmarkDefaults() {
  for (const [key, config] of Object.entries(GENERIC_BENCHMARKS)) {
    if (!config.defaultDatasetId) continue;
    const input = $(config.dataInput);
    if (!input || input.value.trim()) continue;
    const preferredIds = config.preferredDatasetIds || [config.defaultDatasetId];
    const record = preferredIds
      .map((id) => state.datasetRegistry.find((item) => item.id === id && item.exists))
      .find(Boolean)
      || state.datasetRegistry.find((item) => item.id === config.defaultDatasetId);
    if (record?.path) input.value = record.path;
	    if (record?.exists && $(config.status)) {
	      const sampleLike = /(^|[/.])[^/]*sample[^/]*\.(jsonl?|ndjson)$/i.test(record.path || "")
	        || String(record.id || "").toLowerCase().includes("sample");
	      const backendLabel = memoryBackendLabel(currentMemoryBackend());
	      const readinessLabel = sampleLike ? "示例数据已就绪" : "默认数据已就绪";
	      const scopeNote = sampleLike
	        ? "当前是内置 sample。它用于小样本核验；换完整数据路径后可产出正式 MemoryBench 分数。"
	        : `当前默认使用已注册数据文件；开始测试会运行正式 MemoryBench ${backendLabel} 记忆问答，官方原 benchmark 指标会在报告中单独标注。`;
	      $(config.status).innerHTML = `
	        <p><strong>${escapeHtml(config.label)} ${escapeHtml(readinessLabel)}</strong> · ${escapeHtml(record.questions ?? "-")} 题 · ${escapeHtml(record.samples ?? "-")} 样本</p>
	        <p class="dataset-next-step">${escapeHtml(record.path || "")}</p>
	        <p class="dataset-next-step ${sampleLike ? "bad-text" : ""}">${escapeHtml(scopeNote)}</p>
	        <p class="dataset-next-step">${escapeHtml(benchmarkMetricNote(config))}</p>
	      `;
	    }
	    updateGenericRunButton(key, record);
	  }
}

async function loadGenericExample(key) {
  const config = benchmarkConfig(key);
  const record = state.datasetRegistry.find((item) => item.id === config.defaultDatasetId);
  if (!record?.path) return toast(`${config.label} 没有注册示例数据`);
  if ($(config.dataInput)) $(config.dataInput).value = record.path;
  await validateGenericBenchmark(key);
}

function renderGenericBenchmarkKpis(key, data) {
  const config = benchmarkConfig(key);
  const format = String(data.format || "").toLowerCase() === "generic" ? config.adapterFormat : (data.format || config.adapterFormat);
  renderKpis(config.kpis, [
    ["数据集类型", datasetTypeLabel(format)],
    ["样本数", data.samples ?? "-"],
    ["题目数", data.questions ?? "-"],
    ["记忆事件", data.memory_events_total ?? "-"],
    ["加载模式", data.runner_status === "large_dataset_lazy" ? "大文件懒加载" : "已读取"],
  ]);
}

async function validateGenericBenchmark(key) {
  const config = benchmarkConfig(key);
  const path = genericBenchmarkPath(key);
  if (!path) return toast(config.emptyPathHint);
  const data = await api(`/api/dataset?path=${encodeURIComponent(path)}`);
  renderGenericBenchmarkKpis(key, data);
  const gate = genericBenchmarkRunGate(key, path, data);
  if ($(config.status)) {
    const backendLabel = memoryBackendLabel(currentMemoryBackend());
    const warnings = [];
    if (!data.questions || data.questions === 0) warnings.push("没有识别到 question/query/input 字段");
    if (!data.memory_events_total || data.memory_events_total === 0) warnings.push("没有识别到 events/messages/context 字段");
    const sampleLike = /(^|[/.])[^/]*sample[^/]*\.(jsonl?|ndjson)$/i.test(path);
    const warningHtml = warnings.length
      ? `<p class="bad-text">需要检查字段映射：${escapeHtml(warnings.join("；"))}</p>`
      : "";
	    const sampleHtml = sampleLike
	      ? `<p class="dataset-next-step bad-text">当前路径是内置 sample。它用于小样本核验，不作为正式分数。</p>`
	      : "";
	    $(config.status).innerHTML = `
	      ${warningHtml}
	      <p><strong>${escapeHtml(config.label)} 校验完成</strong> · 格式 ${escapeHtml(String(data.format || "").toLowerCase() === "generic" ? config.adapterFormat : (data.format || "-"))} · ${escapeHtml(data.resolved_path || path)}</p>
	      <p class="dataset-next-step">${escapeHtml(datasetRunnerNote(data.format, data.runner_note, `运行正式 MemoryBench ${backendLabel} 记忆问答：写入样本上下文、检索证据、调用答案模型，并自动执行判分。`))}</p>
	      ${sampleHtml}
	      ${gate.ok ? "" : `<p class="dataset-next-step bad-text">${escapeHtml(gate.reason)}</p>`}
	      <p class="dataset-next-step">${escapeHtml(benchmarkMetricNote(config))}</p>
	    `;
  }
  updateGenericRunButton(key, data);
  toast(`${config.label} 校验完成`);
  return data;
}

function renderGenericPreviewRows(key, rows = []) {
  const config = benchmarkConfig(key);
  const target = $(config.preview);
  if (!target) return;
  target.innerHTML = rows.map((q) => `
    <article class="memory-hit">
      <strong>${escapeHtml(q.question || "(未识别 question 字段)")}</strong>
      <small>${escapeHtml(q.sample_id || "-")} · ${escapeHtml(q.question_id || "-")} · ${escapeHtml(q.category || "-")}</small>
      <p>标准答案：${escapeHtml(q.answer || "-")}</p>
      ${q.question_time ? `<small>time: ${escapeHtml(q.question_time)}</small>` : ""}
    </article>
  `).join("") || "<p>没有加载到可预览样本。请检查 JSON 里是否有 question/query/input 字段。</p>";
}

async function previewGenericBenchmark(key) {
  const config = benchmarkConfig(key);
  const path = genericBenchmarkPath(key);
  if (!path) return toast(config.emptyPathHint);
  const query = String(benchmarkQuestionElements(key).search?.value || "").trim();
  const data = await api(`/api/questions-page?path=${encodeURIComponent(path)}&offset=0&limit=100&q=${encodeURIComponent(query)}`);
  const rows = data.questions || [];
  state.benchmarkQuestions[key] = rows;
  state.selectedBenchmarkQuestions[key] = new Set([...benchmarkQuestionState(key).selected].filter((id) => rows.some((q) => q.question_id === id)));
  renderBenchmarkQuestionSelection(key);
  toast(`${config.label} 已加载 ${data.count || 0} 条预览`);
}

async function runGenericBenchmark(key) {
  const config = benchmarkConfig(key);
  const path = genericBenchmarkPath(key);
  if (!path) return toast(config.emptyPathHint);
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  const taskKind = genericQaTaskKindForBackend(backend);
  const expectedView = config.view || viewForDatasetFormat(config.adapterFormat, "runsView");
  rememberActiveDatasetView(expectedView, config.adapterFormat, path);
  showView(expectedView, {preserveScroll: true});
  const data = await validateGenericBenchmark(key);
  const gate = genericBenchmarkRunGate(key, path, data);
  const count = benchmarkCount(config.countInput, 3);
  const selectedQuestions = [...benchmarkQuestionState(key).selected].join(",");
  const effectiveCount = selectedQuestions ? 0 : count;
  clearGenericBenchmarkLaunchError(config.adapterFormat);
  let task;
  try {
    task = await startTask(taskKind, {
      data: path,
      dataset_format: config.adapterFormat,
      format: config.adapterFormat,
      count: effectiveCount,
      questions: selectedQuestions,
      sample: "all",
      identity_mode: "isolated_sample",
	      auto_judge: true,
	      official_eval_after: Boolean(config.officialEvalAfter),
	      read_openviking_content: true,
      top_k: Math.max(1, Number($("chatTopK")?.value || 8)),
      commit_timeout_s: 300,
	      name: `${config.label} ${backendLabel} MemoryBench QA ${selectedQuestions ? `${benchmarkQuestionState(key).selected.size} selected` : benchmarkCountLabel(effectiveCount)}`,
    });
  } catch (error) {
    const raw = String(error?.message || error || "").trim();
    const friendly = friendlyUiError(raw, "启动失败，请检查模型配置或服务状态");
    const agentCfg = agentModelConfig();
    const judgeCfg = judgeModelConfig();
    const requestLabel = selectedQuestions ? `${benchmarkQuestionState(key).selected.size} 题` : benchmarkCountLabel(effectiveCount);
    const launchError = {
      raw,
      friendly,
      account: currentAccount(),
      agentModel: agentCfg.model || "",
      agentBaseUrl: agentCfg.baseUrl || "",
      judgeModel: judgeCfg.model || "",
      judgeBaseUrl: judgeCfg.baseUrl || "",
      requestLabel,
      path,
      at: Date.now(),
    };
    rememberGenericBenchmarkLaunchError(config.adapterFormat, launchError);
    renderGenericBenchmarkLaunchError(config.adapterFormat, launchError);
    throw error;
  }
  clearGenericBenchmarkLaunchError(config.adapterFormat);
  rememberTaskDatasetFormat(task?.id, task?.dataset_format || config.adapterFormat);
  rememberBenchmarkRecord(task || {data: path}, task?.dataset_format || config.adapterFormat);
  showView(expectedView, {preserveScroll: true});
  state.currentRunningTask = task || null;
  updateProgress(taskWithLiveProgress(task || {}), task?.kind || taskKind);
  if (config.adapterFormat) {
    forceRefreshStandaloneBenchmarkView(config.adapterFormat).catch(() => null);
  }
  const output = task?.output_file || "";
  const runDir = task?.run_dir || dirname(output);
  if ($(config.result)) {
    $(config.result).innerHTML = `
      <article class="path-row">
        <span>任务</span>
        <code>${escapeHtml(task?.id || state.taskId || "")}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(task?.id || state.taskId || "")}">复制</button>
      </article>
      <article class="path-row">
        <span>结果文件</span>
        <code>${escapeHtml(output)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(output)}">复制</button>
      </article>
      <article class="path-row">
        <span>任务目录</span>
        <code>${escapeHtml(runDir)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(runDir)}">复制</button>
      </article>
	      <p>这一步运行 MemoryBench ${escapeHtml(backendLabel)} 记忆问答：写入会话并提交、检索长期记忆、调用答案模型、自动判分。${escapeHtml(benchmarkMetricNote(config))} 当前页会保留；右上角运行中入口显示进度，结果中心查看报告。若输入仍是 sample 文件，本次结果只代表小样本核验。</p>
	      ${gate.ok ? "" : `<p class="dataset-next-step bad-text">正式分数门禁：${escapeHtml(gate.reason)} 本次仍作为小样本核验运行。</p>`}
	      <p class="dataset-next-step">选题：${escapeHtml(selectedQuestions ? `${benchmarkQuestionState(key).selected.size} 题` : benchmarkCountLabel(effectiveCount))}</p>
      <div class="panel-actions">
        <button class="secondary" type="button" data-view-jump="runsView">查看任务/报告</button>
      </div>
    `;
    bindCopyButtons(`#${config.result}`);
    $(config.result).querySelectorAll("[data-view-jump]").forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.viewJump));
    });
  }
  toast(`${config.label} ${backendLabel} 测试已开始`);
}

async function renderGenericBenchmarkResultSummary(task = {}, format = "") {
  const key = genericBenchmarkKeyForFormat(format || task.dataset_format || "");
  if (!key) return;
  const config = benchmarkConfig(key);
  const target = $(config.result);
  if (!target) return;
  let record = task || {};
  let summary = task?.summary || {};
  let artifactStatus = {};
  if (task?.run_dir) {
    try {
      const detail = await api(`/api/run-detail?run_dir=${encodeURIComponent(task.run_dir)}`);
      record = {...(detail.record || {}), ...task};
      summary = detail.record?.summary || summary;
      artifactStatus = detail.artifact_status || {};
    } catch {
      record = task || {};
      summary = task?.summary || {};
    }
  }
  const artifactSummary = await loadFinalBenchmarkArtifactSummary(task, format).catch(() => null);
  if (artifactSummary && typeof artifactSummary === "object") {
    summary = {
      ...(summary || {}),
      ...artifactSummary,
    };
  }
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const avgMemoryInjectionTime = summary.avg_memory_injection_time_s ?? summaryJson.avg_memory_injection_time_s;
  const totalMemoryInjectionTime = summary.total_memory_injection_time_s ?? summaryJson.total_memory_injection_time_s;
  const avgMemorySettleWaitTime = summary.avg_memory_settle_wait_time_s ?? summaryJson.avg_memory_settle_wait_time_s;
  const totalMemorySettleWaitTime = summary.total_memory_settle_wait_time_s ?? summaryJson.total_memory_settle_wait_time_s;
  const avgQaTime = summary.avg_qa_time_s ?? summaryJson.avg_qa_time_s ?? summary.avg_time;
  const totalQaTime = summary.total_qa_time_s ?? summaryJson.total_qa_time_s;
  const avgEndToEndTime = summary.avg_end_to_end_time_s ?? summaryJson.avg_end_to_end_time_s;
  const totalEndToEndTime = summary.total_end_to_end_time_s ?? summaryJson.total_end_to_end_time_s;
  const answerEm = summary.official_answer_em ?? summaryJson.official_answer_em ?? summary.answer_em ?? summaryJson.answer_em;
  const answerF1 = summary.official_answer_f1 ?? summaryJson.official_answer_f1 ?? summary.answer_f1 ?? summaryJson.answer_f1;
  const status = String(record.status || task.status || "").trim();
  const logFile = record.log_file || task.log_file || "";
  const output = record.output_file || task.output_file || "";
  const runDir = record.run_dir || task.run_dir || dirname(output);
  const reportHtml = runDir ? `${runDir}/report.html` : "";
  const reportHtmlHref = artifactHref(reportHtml);
  const statusLabel = status ? taskStatusLabel({status}) : "";
  const isTerminalFailure = ["failed", "interrupted", "cancelled", "canceled"].includes(status);
  target.innerHTML = `
    <div class="report-kv">
      <article><span>任务状态</span><strong>${escapeHtml(statusLabel || "-")}</strong></article>
      <article><span>结果行数</span><strong>${escapeHtml(formatInt(rows))}</strong></article>
      <article><span>平均注入时间</span><strong>${escapeHtml(formatSecondsMetric(avgMemoryInjectionTime))}</strong></article>
      <article><span>总注入时间</span><strong>${escapeHtml(formatSecondsMetric(totalMemoryInjectionTime))}</strong></article>
      <article><span>平均记忆落稳等待</span><strong>${escapeHtml(formatSecondsMetric(avgMemorySettleWaitTime))}</strong></article>
      <article><span>总记忆落稳等待</span><strong>${escapeHtml(formatSecondsMetric(totalMemorySettleWaitTime))}</strong></article>
      <article><span>平均 QA 时间</span><strong>${escapeHtml(formatSecondsMetric(avgQaTime))}</strong></article>
      <article><span>总 QA 时间</span><strong>${escapeHtml(formatSecondsMetric(totalQaTime))}</strong></article>
      <article><span>平均端到端</span><strong>${escapeHtml(formatSecondsMetric(avgEndToEndTime))}</strong></article>
      <article><span>总端到端</span><strong>${escapeHtml(formatSecondsMetric(totalEndToEndTime))}</strong></article>
      <article><span>答案 EM</span><strong>${answerEm == null ? "-" : escapeHtml(percent(answerEm))}</strong></article>
      <article><span>答案 F1</span><strong>${answerF1 == null ? "-" : escapeHtml(percent(answerF1))}</strong></article>
    </div>
    <article class="path-row">
      <span>结果文件</span>
      <code>${escapeHtml(output)}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(output)}">复制</button>
    </article>
    <article class="path-row">
      <span>任务目录</span>
      <code>${escapeHtml(runDir)}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(runDir)}">复制</button>
    </article>
    ${reportHtml ? `
      <article class="path-row">
        <span>HTML 报告</span>
        <code>${escapeHtml(reportHtml)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(reportHtml)}">复制</button>
        ${reportHtmlHref ? `<a class="path-link" href="${escapeHtml(reportHtmlHref)}" target="_blank" rel="noreferrer">浏览器打开</a>` : ""}
        <button class="path-open" type="button" data-path="${escapeHtml(reportHtml)}">打开</button>
      </article>
    ` : ""}
    ${logFile ? `
      <article class="path-row">
        <span>运行日志</span>
        <code>${escapeHtml(logFile)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(logFile)}">复制</button>
      </article>
    ` : ""}
    ${isTerminalFailure ? `<p>这次 ${escapeHtml(config.label)} 任务没有跑完。当前结果只包含已写出的 ${escapeHtml(formatInt(rows))} 行；排查请先看 run.log。</p>` : ""}
    ${(artifactStatus.summary?.exists || artifactStatus.hotpotqa_answer_summary?.exists) ? `
      <div class="panel-actions">
        <button class="secondary" type="button" data-view-jump="runsView">查看任务/报告</button>
      </div>
    ` : ""}
  `;
  bindCopyButtons(`#${config.result}`);
  target.querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
}

function benchmarkArtifactDir(task = {}) {
  if (task.output_file) return dirname(task.output_file);
  if (task.run_dir) return task.run_dir;
  return "";
}

function benchmarkArtifactPaths(task = {}) {
  const dir = benchmarkArtifactDir(task);
  if (!dir) return {};
  return {
    summary: `${dir}/summary.json`,
    judge: `${dir}/judge_summary.json`,
    hotpotqa: `${dir}/hotpotqa_answer_summary.json`,
    longmemeval: `${dir}/longmemeval_official_summary.json`,
    report_html: task.run_dir ? `${task.run_dir}/report.html` : "",
  };
}

async function loadArtifactJson(path = "") {
  if (!path) return null;
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  const text = String(data?.text || "").trim();
  if (!text) return null;
  const parsed = JSON.parse(text);
  return parsed && typeof parsed === "object" ? parsed : null;
}

async function loadBenchmarkArtifactStatus(task = {}) {
  const paths = benchmarkArtifactPaths(task);
  const entries = await Promise.all(Object.entries(paths).map(async ([key, path]) => {
    if (!path) return [key, null];
    try {
      if (key === "report_html") {
        await api(`/api/file?path=${encodeURIComponent(path)}`);
        return [key, {exists: true, path}];
      }
      await loadArtifactJson(path);
      return [key === "hotpotqa" ? "hotpotqa_answer_summary" : key === "longmemeval" ? "longmemeval_official_summary" : `${key}_summary`, {exists: true, path}];
    } catch {
      if (key === "report_html") return [key, {exists: false, path}];
      return [key === "hotpotqa" ? "hotpotqa_answer_summary" : key === "longmemeval" ? "longmemeval_official_summary" : `${key}_summary`, {exists: false, path}];
    }
  }));
  return Object.fromEntries(entries.filter(([key]) => key));
}

async function loadFinalBenchmarkArtifactSummary(task = {}, format = "") {
  const normalized = normalizeDatasetFormat(format || task.dataset_format || "");
  const paths = benchmarkArtifactPaths(task);
  if (!paths.summary && !paths.judge && !paths.hotpotqa && !paths.longmemeval) return null;
  const [summaryJson, judgeSummary, hotpotSummary, longmemSummary] = await Promise.all([
    loadArtifactJson(paths.summary).catch(() => null),
    loadArtifactJson(paths.judge).catch(() => null),
    loadArtifactJson(paths.hotpotqa).catch(() => null),
    loadArtifactJson(paths.longmemeval).catch(() => null),
  ]);
  const merged = {
    ...(summaryJson || {}),
  };
  if (judgeSummary) {
    if (judgeSummary.graded != null) merged.graded = judgeSummary.graded;
    if (judgeSummary.correct != null) merged.correct = judgeSummary.correct;
    if (judgeSummary.wrong != null) merged.wrong = judgeSummary.wrong;
    if (judgeSummary.accuracy != null) merged.accuracy = judgeSummary.accuracy;
  }
  if (normalized === "hotpotqa" && hotpotSummary) {
    if (hotpotSummary.answer_em != null) {
      merged.answer_em = hotpotSummary.answer_em;
      merged.official_answer_em = hotpotSummary.answer_em;
    }
    if (hotpotSummary.answer_f1 != null) {
      merged.answer_f1 = hotpotSummary.answer_f1;
      merged.official_answer_f1 = hotpotSummary.answer_f1;
      merged.official_score = hotpotSummary.answer_f1;
    }
    if (hotpotSummary.metric_scope != null) merged.official_metric_scope = hotpotSummary.metric_scope;
  }
  if (normalized === "longmemeval" && longmemSummary) {
    if (longmemSummary.overall_accuracy != null) {
      merged.official_metric = "overall_accuracy";
      merged.official_score = longmemSummary.overall_accuracy;
    }
    if (longmemSummary.task_averaged_accuracy != null) merged.official_task_averaged_accuracy = longmemSummary.task_averaged_accuracy;
    if (longmemSummary.abstention_accuracy != null) merged.official_abstention_accuracy = longmemSummary.abstention_accuracy;
  }
  return Object.keys(merged).length ? merged : null;
}

async function loadRunningBenchmarkSummary(task = {}, format = "") {
  if (!task?.id || !task?.run_dir || !isGenericBenchmarkQaTask(task, format)) return null;
  const taskId = task.id;
  const cached = state.runningBenchmarkSummaries[taskId] || null;
  const fetchedAt = Number(state.runningBenchmarkSummariesFetchedAt[taskId] || 0);
  const now = Date.now();
  const cacheTtlMs = cached?._partial_source === "running_summary_json"
    ? 15000
    : cached?._partial_source === "csv_preview"
    ? 15000
    : 5000;
  if (cached && fetchedAt && now - fetchedAt < cacheTtlMs) return cached;
  if (state.runningBenchmarkSummariesLoading[taskId]) return cached;
  state.runningBenchmarkSummariesLoading[taskId] = true;
  try {
    const summary = task?.summary && typeof task.summary === "object"
      ? {...task.summary}
      : null;
    const runningJsonSummary = await loadRunningBenchmarkJsonSummary(task).catch(() => null);
    const rowHint = Number(runningJsonSummary?.rows ?? summary?.rows ?? 0);
    const csvSummary = !runningJsonSummary && summaryNeedsClientTimingSummary(summary)
      ? await loadRunningBenchmarkCsvSummary(task, rowHint).catch(() => null)
      : null;
    const artifactStatus = await loadBenchmarkArtifactStatus(task).catch(() => ({}));
    const mergedSummary = summary || runningJsonSummary || csvSummary
      ? {
          ...(summary || {}),
          ...(runningJsonSummary || {}),
          ...(csvSummary || {}),
          ...(summary?.rows != null ? {rows: summary.rows} : {}),
          _artifact_status: artifactStatus,
        }
      : null;
    if (mergedSummary) state.runningBenchmarkSummaries[taskId] = mergedSummary;
    state.runningBenchmarkSummariesFetchedAt[taskId] = Date.now();
    return mergedSummary;
  } catch {
    return cached;
  } finally {
    delete state.runningBenchmarkSummariesLoading[taskId];
  }
}

function summaryNeedsClientTimingSummary(summary = null) {
  if (!summary || typeof summary !== "object") return true;
  return ![
    summary.avg_memory_injection_time_s,
    summary.total_memory_injection_time_s,
    summary.avg_memory_settle_wait_time_s,
    summary.total_memory_settle_wait_time_s,
    summary.avg_qa_time_s,
    summary.total_qa_time_s,
    summary.avg_end_to_end_time_s,
    summary.total_end_to_end_time_s,
  ].some((value) => value !== undefined && value !== null && value !== "");
}

function runningBenchmarkSummaryFile(task = {}) {
  if (task.output_file) return `${dirname(task.output_file)}/running_summary.json`;
  if (task.run_dir) return `${task.run_dir}/running_summary.json`;
  return "";
}

async function loadRunningBenchmarkJsonSummary(task = {}) {
  const path = runningBenchmarkSummaryFile(task);
  if (!path) return null;
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  const payload = JSON.parse(data?.text || "{}");
  if (!payload || typeof payload !== "object") return null;
  return {
    ...payload,
    _partial_source: "running_summary_json",
    _partial_exact: true,
    _partial_preview_rows: Number(payload.rows || 0),
  };
}

async function loadRunningBenchmarkCsvSummary(task = {}, totalRowsHint = 0) {
  const rowCount = Number(totalRowsHint || 0);
  if (!task?.output_file || !rowCount || rowCount > 200) return null;
  const limit = Math.max(20, rowCount);
  const data = await api(`/api/csv-preview?path=${encodeURIComponent(task.output_file)}&limit=${limit}`);
  const rows = Array.isArray(data?.rows) ? data.rows : [];
  if (!rows.length) return null;
  const summary = partialBenchmarkSummaryFromRows(rows, rowCount);
  if (!summary) return null;
  summary._partial_source = "csv_preview";
  summary._partial_exact = rows.length >= rowCount;
  summary._partial_preview_rows = rows.length;
  return summary;
}

function partialBenchmarkSummaryFromRows(rows = [], totalRowsHint = 0) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const numeric = (value) => {
    if (value === undefined || value === null || value === "") return null;
    const raw = Number(value);
    return Number.isFinite(raw) ? raw : null;
  };
  const average = (values) => (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null);
  const sum = (values) => (values.length ? values.reduce((total, value) => total + value, 0) : null);
  const injectionValues = rows.map((row) => numeric(row?.memory_injection_time_s)).filter((value) => value != null);
  const settleValues = rows.map((row) => numeric(row?.memory_settle_wait_elapsed_s)).filter((value) => value != null);
  const qaValues = rows.map((row) => numeric(row?.qa_time_s ?? row?.time_cost)).filter((value) => value != null);
  const endToEndValues = rows.map((row) => numeric(row?.end_to_end_time_s)).filter((value) => value != null);
  const exactRows = Number.isFinite(Number(totalRowsHint)) && Number(totalRowsHint) > 0 ? Number(totalRowsHint) : rows.length;
  const exact = exactRows <= rows.length;
  return {
    rows: exactRows,
    _partial_source: "preview_rows",
    _partial_exact: exact,
    _partial_preview_rows: rows.length,
    avg_memory_injection_time_s: average(injectionValues),
    total_memory_injection_time_s: exact ? sum(injectionValues) : null,
    avg_memory_settle_wait_time_s: average(settleValues),
    total_memory_settle_wait_time_s: exact ? sum(settleValues) : null,
    avg_qa_time_s: average(qaValues),
    total_qa_time_s: exact ? sum(qaValues) : null,
    avg_end_to_end_time_s: average(endToEndValues),
    total_end_to_end_time_s: exact ? sum(endToEndValues) : null,
  };
}

function renderGenericBenchmarkRunningSummary(task = {}, format = "", options = {}) {
  const key = genericBenchmarkKeyForFormat(format || task.dataset_format || "");
  if (!key) return;
  const config = benchmarkConfig(key);
  const target = $(config.result);
  if (!target) return;
  const summary = options.summary || {};
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const avgMemoryInjectionTime = summary.avg_memory_injection_time_s ?? summaryJson.avg_memory_injection_time_s;
  const totalMemoryInjectionTime = summary.total_memory_injection_time_s ?? summaryJson.total_memory_injection_time_s;
  const avgMemorySettleWaitTime = summary.avg_memory_settle_wait_time_s ?? summaryJson.avg_memory_settle_wait_time_s;
  const totalMemorySettleWaitTime = summary.total_memory_settle_wait_time_s ?? summaryJson.total_memory_settle_wait_time_s;
  const avgQaTime = summary.avg_qa_time_s ?? summaryJson.avg_qa_time_s ?? summary.avg_time;
  const totalQaTime = summary.total_qa_time_s ?? summaryJson.total_qa_time_s;
  const avgEndToEndTime = summary.avg_end_to_end_time_s ?? summaryJson.avg_end_to_end_time_s;
  const totalEndToEndTime = summary.total_end_to_end_time_s ?? summaryJson.total_end_to_end_time_s;
  const lastQuestionId = String(summary.last_question_id ?? summaryJson.last_question_id ?? "").trim();
  const summaryUpdatedAt = String(summary.updated_at ?? summaryJson.updated_at ?? "").trim();
  const freshness = benchmarkRunningSummaryFreshness(task, summary);
  const output = task.output_file || "";
  const runDir = task.run_dir || dirname(output);
  const reportHtml = runDir ? `${runDir}/report.html` : "";
  const reportHtmlHref = artifactHref(reportHtml);
  const stableLiveReportHref = "/generated-reports/hotpotqa_echomemory_live_current.html";
  const artifactStatus = summary._artifact_status || {};
  const runningSummaryPath = output ? `${dirname(output)}/running_summary.json` : "";
  const runningSummaryHref = artifactHref(runningSummaryPath);
  const hasReportHtml = Boolean(artifactStatus.report_html?.exists);
  const account = String(options.account || recordAccount(task) || "").trim();
  const execution = taskExecutionProgress(task, format || task.dataset_format || "");
  const currentQuestionLabel = benchmarkCurrentQuestionLabel(task);
  const currentImportLabel = benchmarkCurrentImportLabel(task);
  const progress = taskWithLiveProgress(task).progress || {};
  const authoritativeProgressLabel = execution?.total_questions
    ? `${execution.current_question || execution.answered_questions || 0}/${execution.total_questions} · 已答 ${execution.answered_questions || 0}`
    : "";
  const reportedProgressLabel = progress?.total ? `${Number(progress.current || 0)}/${Number(progress.total || 0)}` : "";
  const progressMismatchText = authoritativeProgressLabel && reportedProgressLabel
    && !authoritativeProgressLabel.startsWith(reportedProgressLabel)
    ? `任务接口当前显示 ${reportedProgressLabel}，日志权威进度是 ${authoritativeProgressLabel}。`
    : "";
  const phaseLabel = String(progress.phase || "").trim();
  const progressWarnings = Array.isArray(progress.warnings) ? progress.warnings.map((item) => String(item || "").trim()).filter(Boolean) : [];
  const elapsedSeconds = Number(task.duration ?? progress.elapsed_seconds ?? 0);
  const rowsPerHour = rows > 0 && elapsedSeconds > 0 ? (rows / elapsedSeconds) * 3600 : null;
  const etaFinishLabel = progress.eta_seconds != null ? formatDateTimeLocal(Date.now() + Number(progress.eta_seconds || 0) * 1000) : "";
  const genericFailureCount = Number(task?.log_diagnostics?.generic_failure_count || 0);
  const modelIssueCount = Number(
    task?.log_diagnostics?.model_issue_count
    || task?.log_diagnostics?.model_api_error_count
    || 0
  );
  const partialPreviewCount = Number(summary._partial_preview_rows || 0);
  const partialTimingNote = summary._partial_source === "running_summary_json"
    ? " 运行态时间统计来自后台持续刷新的小型汇总文件。"
    : summary._partial_source === "csv_preview"
    ? " 运行态时间统计直接来自当前已写入 CSV 的全部结果行。"
    : summary._partial_source === "preview_rows"
    ? (summary._partial_exact
      ? " 运行态时间统计直接来自当前已写入 CSV 的结果行。"
      : ` 当前时间均值基于前 ${formatInt(partialPreviewCount)} 行预览；总时间会在后端重启后切换成全量实时汇总。`)
    : "";
  const note = String(options.note || "").trim()
    || (account
      ? `当前任务运行中，所属账户：${account}。平台会持续刷新题目级进度；完成后这里自动切换到时间统计和 ${config.label} EM/F1。${partialTimingNote}`
      : `当前任务运行中。平台会持续刷新题目级进度；完成后这里自动切换到时间统计和 ${config.label} EM/F1。${partialTimingNote}`);
  const artifactReadinessText = [
    ["summary", artifactStatus.summary?.exists],
    ["judge", artifactStatus.judge_summary?.exists],
    [normalizeDatasetFormat(format || task.dataset_format || "") === "hotpotqa" ? "hotpotqa_answer" : "", artifactStatus.hotpotqa_answer_summary?.exists],
  ]
    .filter(([label]) => label)
    .map(([label, ready]) => `${label}:${ready ? "ready" : "pending"}`)
    .join(" · ");
  const partialStats = rows > 0 || avgMemoryInjectionTime != null || avgQaTime != null || avgEndToEndTime != null
    ? `
      <div class="report-kv">
        <article><span>已完成行数</span><strong>${escapeHtml(formatInt(rows))}</strong></article>
        <article><span>当前平均注入时间</span><strong>${escapeHtml(formatSecondsMetric(avgMemoryInjectionTime))}</strong></article>
        <article><span>当前总注入时间</span><strong>${escapeHtml(formatSecondsMetric(totalMemoryInjectionTime))}</strong></article>
        <article><span>当前平均记忆落稳等待</span><strong>${escapeHtml(formatSecondsMetric(avgMemorySettleWaitTime))}</strong></article>
        <article><span>当前总记忆落稳等待</span><strong>${escapeHtml(formatSecondsMetric(totalMemorySettleWaitTime))}</strong></article>
        <article><span>当前平均 QA 时间</span><strong>${escapeHtml(formatSecondsMetric(avgQaTime))}</strong></article>
        <article><span>当前总 QA 时间</span><strong>${escapeHtml(formatSecondsMetric(totalQaTime))}</strong></article>
        <article><span>当前平均端到端</span><strong>${escapeHtml(formatSecondsMetric(avgEndToEndTime))}</strong></article>
        <article><span>当前总端到端</span><strong>${escapeHtml(formatSecondsMetric(totalEndToEndTime))}</strong></article>
        <article><span>当前吞吐</span><strong>${escapeHtml(rowsPerHour == null ? "-" : `${rowsPerHour.toFixed(2)} 行/小时`)}</strong></article>
        <article><span>预计完成</span><strong>${escapeHtml(etaFinishLabel || "-")}</strong></article>
      </div>
    `
    : "";
  target.innerHTML = `
    ${partialStats}
    <article class="path-row">
      <span>任务</span>
      <code>${escapeHtml(task.id || "")}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(task.id || "")}">复制</button>
    </article>
    <article class="path-row">
      <span>结果文件</span>
      <code>${escapeHtml(output)}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(output)}">复制</button>
    </article>
    <article class="path-row">
      <span>任务目录</span>
      <code>${escapeHtml(runDir)}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(runDir)}">复制</button>
    </article>
    ${runningSummaryPath ? `
      <article class="path-row">
        <span>运行态摘要</span>
        <code>${escapeHtml(runningSummaryPath)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(runningSummaryPath)}">复制</button>
        ${runningSummaryHref ? `<a class="path-link" href="${escapeHtml(runningSummaryHref)}" target="_blank" rel="noreferrer">浏览器打开</a>` : ""}
        <button class="path-open" type="button" data-path="${escapeHtml(runningSummaryPath)}">打开</button>
      </article>
    ` : ""}
    ${hasReportHtml && reportHtml ? `
      <article class="path-row">
        <span>HTML 报告</span>
        <code>${escapeHtml(reportHtml)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(reportHtml)}">复制</button>
        ${reportHtmlHref ? `<a class="path-link" href="${escapeHtml(reportHtmlHref)}" target="_blank" rel="noreferrer">浏览器打开</a>` : ""}
        <button class="path-open" type="button" data-path="${escapeHtml(reportHtml)}">打开</button>
      </article>
    ` : ""}
    ${normalizeDatasetFormat(format || task.dataset_format || "") === "hotpotqa" ? `
      <article class="path-row">
        <span>稳定入口</span>
        <code>${escapeHtml(stableLiveReportHref)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(stableLiveReportHref)}">复制</button>
        <a class="path-link" href="${escapeHtml(stableLiveReportHref)}" target="_blank" rel="noreferrer">浏览器打开</a>
      </article>
    ` : ""}
    ${currentQuestionLabel ? `
      <article class="path-row">
        <span>当前题</span>
        <code>${escapeHtml(currentQuestionLabel)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(currentQuestionLabel)}">复制</button>
      </article>
    ` : ""}
    ${authoritativeProgressLabel ? `
      <article class="path-row">
        <span>日志权威进度</span>
        <code>${escapeHtml(authoritativeProgressLabel)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(authoritativeProgressLabel)}">复制</button>
      </article>
    ` : ""}
    ${progressMismatchText ? `<p class="dataset-next-step bad-text">${escapeHtml(progressMismatchText)}</p>` : ""}
    ${phaseLabel ? `
      <article class="path-row">
        <span>当前阶段</span>
        <code>${escapeHtml(phaseLabel)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(phaseLabel)}">复制</button>
      </article>
    ` : ""}
    ${artifactReadinessText ? `
      <article class="path-row">
        <span>最终产物</span>
        <code>${escapeHtml(artifactReadinessText)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(artifactReadinessText)}">复制</button>
      </article>
    ` : ""}
    ${currentImportLabel ? `
      <article class="path-row">
        <span>当前记忆写入</span>
        <code>${escapeHtml(currentImportLabel)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(currentImportLabel)}">复制</button>
      </article>
    ` : ""}
    ${lastQuestionId ? `
      <article class="path-row">
        <span>最近完成题</span>
        <code>${escapeHtml(lastQuestionId)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(lastQuestionId)}">复制</button>
      </article>
    ` : ""}
    ${progressWarnings.length ? `<p class="dataset-next-step bad-text">运行态告警：${escapeHtml(progressWarnings.join("；"))}</p>` : ""}
    ${(freshness.isStale || (freshness.hasWorkerFailure && task.status === "running")) ? `<p class="dataset-next-step bad-text"><strong>疑似卡住：</strong>${escapeHtml(
      freshness.label
        ? `运行态汇总自 ${freshness.label} 后未再推进，但任务仍显示 running。`
        : "运行态汇总长时间未推进，但任务仍显示 running。"
    )}${freshness.hasWorkerFailure ? " 日志里已经出现 worker 异常，当前页面展示的大概率是停住后的旧进度。" : " 当前页面展示的可能是停住后的旧进度。"} 建议先打开 run.log 或直接停止后重跑。</p>` : ""}
    ${genericFailureCount ? `<p class="dataset-next-step bad-text">运行日志异常 ${escapeHtml(formatInt(genericFailureCount))} 条；建议在结果目录里查看 run.log。</p>` : ""}
    ${modelIssueCount ? `<p class="dataset-next-step bad-text">模型/检索异常 ${escapeHtml(formatInt(modelIssueCount))} 条；最终分数前建议复核。</p>` : ""}
    <p>${escapeHtml(note)}${hasReportHtml ? " 最终 HTML 报告已经生成，可直接打开。" : (runningSummaryPath ? " 运行中请先看运行态摘要；最终 HTML 报告会在任务结束后出现。" : "")}${normalizeDatasetFormat(format || task.dataset_format || "") === "hotpotqa" ? " 也可以直接打开稳定入口。" : ""}${artifactReadinessText ? " 最终结果文件会在这里从 pending 切到 ready。" : ""}</p>
    ${summaryUpdatedAt ? `<p class="dataset-next-step">运行态时间统计更新于 ${escapeHtml(freshness.label || summaryUpdatedAt)}</p>` : ""}
  `;
  if (normalizeDatasetFormat(format || task.dataset_format || "") === "hotpotqa") {
    updateHotpotQaInlineLiveReport(task, {
      account,
      summary,
    });
  }
  bindCopyButtons(`#${config.result}`);
}

function renderIdleBenchmarkProgress(format = "", run = null) {
  const ui = benchmarkUiForFormat(format);
  const text = ui ? $(ui.progressText) : null;
  const bar = ui ? $(ui.progressBar) : null;
  if (!ui || !text || !bar) return;
  if (normalizeDatasetFormat(format) === "hotpotqa") {
    text.textContent = "当前没有运行中的 HotpotQA 任务";
    bar.style.width = "0%";
    bar.style.animation = "none";
    updateHotpotQaInlineLiveReport(null);
    return;
  }
  if (!run) {
    text.textContent = state.tasksHydrating ? "正在恢复任务状态" : ui.waiting;
    bar.style.width = "0%";
    bar.style.animation = "none";
    return;
  }
  const summary = run.summary || {};
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const answerEm = summary.official_answer_em ?? summaryJson.official_answer_em ?? summary.answer_em ?? summaryJson.answer_em;
  const answerF1 = summary.official_answer_f1 ?? summaryJson.official_answer_f1 ?? summary.answer_f1 ?? summaryJson.answer_f1;
  const metrics = [];
  if (rows > 0) metrics.push(`${rows} 行`);
  if (answerEm != null || answerF1 != null) {
    metrics.push(`EM/F1 ${answerEm == null ? "-" : percent(answerEm)} / ${answerF1 == null ? "-" : percent(answerF1)}`);
  }
  const status = String(run.status || "").trim().toLowerCase();
  const prefix = status === "failed"
    ? "最近失败"
    : (status === "interrupted" || status === "cancelled" || status === "canceled")
    ? "最近中断"
    : "最近完成";
  text.textContent = metrics.length ? `${prefix} · ${metrics.join(" · ")}` : prefix;
  bar.style.width = "100%";
  bar.style.animation = "none";
}

async function restoreLatestBenchmarkRunForView(viewId = "", visibleTasks = []) {
  const format = datasetFormatForView(viewId);
  if (!format || format === "locomo") return;
  if ((visibleTasks || []).some((task) => isTaskActive(task) && normalizeDatasetFormat(taskDatasetFormat(task, state.taskDatasetFormats[task.id] || "")) === format)) {
    return null;
  }
  let runs = Array.isArray(state.recentRuns) ? state.recentRuns : [];
  const runsFresh = state.runsLoadedAt && (Date.now() - state.runsLoadedAt) < 30000 && runs.length;
  if (!runsFresh) {
    const data = await api("/api/runs?limit=80");
    runs = (data.runs || [])
      .filter((run) => !currentAccountOnlyEnabled("runsCurrentAccountOnly") || matchesCurrentAccount(run))
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    state.recentRuns = runs;
    state.runsLoadedAt = Date.now();
  }
  const latest = runs.find((run) => normalizeDatasetFormat(benchmarkFormatFromRecord(run)) === format);
  if (!latest) {
    renderIdleBenchmarkProgress(format, null);
    return null;
  }
  rememberBenchmarkRecord(latest, format);
  if (latest.output_file) markDatasetOutputFile(latest.output_file, format);
  await renderGenericBenchmarkResultSummary(latest, format);
  renderIdleBenchmarkProgress(format, latest);
  return latest;
}

async function loadDataset(silent = false) {
  const path = $("data").value.trim();
  if (!path) return;
  const data = await api(`/api/dataset?path=${encodeURIComponent(path)}`);
  const isLocomo = (data.format || "") === "locomo";
  if (!isLocomo) {
    state.locomoDataset = null;
    state.dataset = null;
    state.questions = [];
    state.filteredQuestions = [];
    state.selectedQuestions.clear();
    clearLocomoResultState();
    $("sample").innerHTML = `<option value='all'>${LOCOMO_ALL_SESSIONS_LABEL}</option>`;
    $("importSample").innerHTML = `<option value='all'>${LOCOMO_ALL_SESSIONS_LABEL}</option>`;
    if ($("activeDatasetPill")) {
      $("activeDatasetPill").textContent = `${datasetTypeLabel(data.format)} · 已跳转`;
      $("activeDatasetPill").classList.toggle("ok", false);
      $("activeDatasetPill").classList.toggle("bad", false);
      $("activeDatasetPill").classList.toggle("muted", true);
    }
    refreshImportActionLabels();
    $("runTimeQuestions").disabled = true;
    renderKpis("datasetKpis", [
      ["LoCoMo 状态", "未选择"],
      ["识别到的格式", datasetTypeLabel(data.format)],
      ["题目数", data.questions ?? "-"],
    ]);
    renderKpis("questionSelectionKpis", [
      ["题目范围", "0/0"],
      ["已选", "0/0"],
      ["运行模式", "等待 LoCoMo"],
    ]);
    $("datasetCategoryPanel").innerHTML = "";
    const targetView = viewForDatasetFormat(data.format, "runsView");
    $("questionPicker").innerHTML = "<p>当前不是 LoCoMo 数据集。已为你切到对应评测入口；LoCoMo 页只处理 LoCoMo JSON。</p>";
    $("datasetRunnerNote").innerHTML = `
      <p class="bad-text"><strong>这不是 LoCoMo 数据集</strong> · 识别为 ${escapeHtml(datasetTypeLabel(data.format))}</p>
      <p class="dataset-next-step">LoCoMo 评测页只负责 LoCoMo。已切到 ${escapeHtml(datasetTypeLabel(data.format))} 的对应评测入口。</p>
    `;
    updateWorkflowGuide();
    const normalizedFormat = normalizeDatasetFormat(data.format);
    saveLastDataset({path: data.resolved_path || path, format: normalizedFormat});
    if (normalizedFormat === "longmemeval" && $("longMemData")) {
      $("longMemData").value = data.resolved_path || path;
    } else {
      const benchmarkKey = genericBenchmarkKeyForFormat(normalizedFormat);
      if (benchmarkKey) {
        const config = benchmarkConfig(benchmarkKey);
        if ($(config.dataInput)) $(config.dataInput).value = data.resolved_path || path;
        updateGenericRunButton(benchmarkKey, data);
      }
    }
    if (targetView !== "datasetView") {
      rememberActiveDatasetView(targetView, normalizedFormat, data.resolved_path || path);
      showView(targetView);
    }
    if (!silent) toast(`当前路径识别为 ${datasetTypeLabel(data.format)}，已切到对应评测入口`);
    return;
  }
  state.dataset = data;
  state.locomoDataset = data;
  state.lastValidation = null;
  saveLastLocomoDataset({path, runner_status: data.runner_status || ""});
  document.querySelectorAll("#datasetCards .dataset-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.path === path);
  });
  if ($("activeDatasetPill")) {
    const lazy = data.runner_status === "large_dataset_lazy";
    $("activeDatasetPill").textContent = `${datasetTypeLabel(data.format)} · ${data.questions ?? "?"} 题 · ${lazy ? "大文件" : "已读取"}`;
    $("activeDatasetPill").classList.toggle("ok", !lazy);
    $("activeDatasetPill").classList.toggle("bad", false);
    $("activeDatasetPill").classList.toggle("muted", lazy);
  }
  $("sample").innerHTML = `<option value='all'>${LOCOMO_ALL_SESSIONS_LABEL}</option>`;
  $("importSample").innerHTML = `<option value='all'>${LOCOMO_ALL_SESSIONS_LABEL}</option>`;
  for (const row of data.sample_rows || []) {
    const opt = document.createElement("option");
    opt.value = row.index;
    opt.textContent = locomoQaSampleOptionLabel(row);
    $("sample").appendChild(opt);

    const importOpt = document.createElement("option");
    importOpt.value = row.index;
    importOpt.textContent = locomoImportSampleOptionLabel(row);
    $("importSample").appendChild(importOpt);
    if (String(row.sample_id || "").trim() === "conv-30") {
      const smokeOpt = document.createElement("option");
      smokeOpt.value = `${row.index}${IMPORT_SINGLE_SESSION_SUFFIX}`;
      smokeOpt.textContent = `${row.index} · conv-30 · 单 session 测试 · 1 段 session`;
      $("importSample").appendChild(smokeOpt);
    }
  }
  refreshImportActionLabels();
  $("runTimeQuestions").disabled = !isLocomo;
  renderDatasetCategories(data);
  if ($("datasetRunnerNote")) {
    // 校验数据集完整性
    const validationIssues = [];
    const validationWarnings = [];

    // 检查基本字段
    if (!data.samples || data.samples === 0) {
      validationIssues.push("❌ 没有对话数据");
    }
    if (!data.questions || data.questions === 0) {
      validationIssues.push("❌ 没有问题数据");
    }
    if (!data.format) {
      validationWarnings.push("⚠️ 未识别数据集格式");
    }

    // 检查 LoCoMo 特定字段
    if (data.format === "locomo") {
      if (!data.categories || Object.keys(data.categories).length === 0) {
        validationWarnings.push("⚠️ 没有分类信息");
      }
      if (!data.sample_rows || data.sample_rows.length === 0) {
        validationWarnings.push("⚠️ 没有样本行数据");
      }
      // 检查对话数和 questions 的比例
      if (data.samples && data.questions) {
        const avgQuestionsPerConv = Math.round(data.questions / data.samples);
        if (avgQuestionsPerConv < 10) {
          validationWarnings.push(`⚠️ 平均每个对话只有 ${avgQuestionsPerConv} 题，可能数据不完整`);
        }
      }
    }

    // 生成校验报告
    let validationReport = "";
    if (validationIssues.length > 0) {
      validationReport += `<div style="color: #e74c3c; margin-bottom: 8px;">${validationIssues.join("<br>")}</div>`;
    }
    if (validationWarnings.length > 0) {
      validationReport += `<div style="color: #f39c12; margin-bottom: 8px;">${validationWarnings.join("<br>")}</div>`;
    }

    const avgQuestionsPerConv = data.samples && data.questions ? Math.round(data.questions / data.samples) : 0;
    $("datasetRunnerNote").innerHTML = `${validationReport}`;
  }
  if (data.runner_status === "large_dataset_lazy") {
    state.questions = [];
    state.selectedQuestions.clear();
    state.filteredQuestions = [];
    if ($("largeDatasetActions")) $("largeDatasetActions").hidden = false;
    $("questionPicker").innerHTML = "<p>这是较大的 LoCoMo 数据集，页面不会自动全量读取题目。请使用搜索或具体 conv / 对话缩小范围。</p>";
    renderKpis("questionSelectionKpis", [
      ["题目范围", "-"],
      ["已选", "0/0"],
      ["运行模式", "100题抽样"],
    ]);
    if (!silent) toast(`${datasetTypeLabel(data.format)} 校验完成，大文件使用 lazy 模式`);
    updateBackendUi();
    updateWorkflowGuide();
    refreshLocomoFlowStatus(true).catch(() => {});
    return;
  }
  if ($("largeDatasetActions")) $("largeDatasetActions").hidden = true;
  await loadQuestions();
  updateBackendUi();
  updateWorkflowGuide();

  // 生成校验摘要
  const summary = `${datasetTypeLabel(data.format)} 校验完成：${data.samples || 0} 个对话样本，共 ${data.questions || 0} 题`;
  if (!silent) toast(summary);
  refreshLocomoFlowStatus(true).catch(() => {});
}

async function loadLargeQuestionPage(offset = 0) {
  const path = $("data").value.trim();
  if (!path) return toast("请先选择数据集");
  const query = ($("questionSearch")?.value || "").trim();
  const sample = $("sample")?.value || "all";
  const data = sample === "all"
    ? await api(`/api/questions-page?path=${encodeURIComponent(path)}&offset=${offset}&limit=100&q=${encodeURIComponent(query)}`)
    : await api(`/api/questions?path=${encodeURIComponent(path)}&sample=${encodeURIComponent(sample)}`);
  state.questions = data.questions || [];
  state.selectedQuestions = new Set([...state.selectedQuestions].filter((id) => state.questions.some((q) => q.question_id === id)));
  renderQuestions();
  const scope = currentLocomoSampleScope();
  const hasNext = data.next_offset != null;
  $("quickTestStatus").innerHTML = `
    <p><strong>${escapeHtml(scope.isAll ? `已加载 ${data.count} 题预览` : `${scope.label} · ${scope.questionCount} 题`)}</strong>${scope.isAll ? ` · offset ${escapeHtml(data.offset)}${query ? ` · query ${escapeHtml(query)}` : ""} · ${hasNext ? `下一页 ${escapeHtml(data.next_offset)}` : "没有下一页"}` : " · 当前 conv 全量"}</p>
  `;
  if ($("largeDatasetActions")) {
    $("largeDatasetActions").innerHTML = `
      <button class="secondary" id="loadLargeQuestionPage">${scope.isAll ? "按当前搜索加载前 100 题" : "重新加载当前 conv 题目"}</button>
      ${scope.isAll && hasNext ? `<button class="secondary" id="loadNextLargeQuestionPage" data-next-offset="${escapeHtml(data.next_offset)}">加载下一页</button>` : ""}
    `;
    $("loadLargeQuestionPage").addEventListener("click", () => loadLargeQuestionPage(0).catch((e) => toast(e.message)));
    const next = $("loadNextLargeQuestionPage");
    if (next) next.addEventListener("click", () => loadLargeQuestionPage(Number(next.dataset.nextOffset || 0)).catch((e) => toast(e.message)));
  }
}

async function validateLongMemDataset() {
  const path = $("longMemData")?.value.trim() || "";
  if (!path) return toast("请先选择或填写 LongMemEval JSON");
  const data = await api(`/api/dataset?path=${encodeURIComponent(path)}`);
  state.longMemDataset = data;
  document.querySelectorAll("#longMemDatasetCards .dataset-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.path === path);
  });
  renderKpis("longMemKpis", [
    ["数据集类型", datasetTypeLabel(data.format)],
    ["样本数", data.samples ?? "-"],
    ["题目数", data.questions ?? "-"],
    ["加载模式", data.runner_status === "large_dataset_lazy" ? "大文件懒加载" : "已读取"],
  ]);
  const sampleLike = /(^|[/.])[^/]*sample[^/]*\.(jsonl?|ndjson)$/i.test(path);
  const backendLabel = memoryBackendLabel(currentMemoryBackend());
  $("longMemStatus").innerHTML = `
    <p><strong>${escapeHtml(datasetTypeLabel(data.format))} 校验完成</strong> · ${escapeHtml(data.resolved_path || path)}</p>
    <p class="dataset-next-step">${escapeHtml(datasetRunnerNote(data.format, data.runner_note, `运行正式 MemoryBench ${backendLabel} 记忆问答：写入样本上下文、检索证据、调用答案模型，并自动执行判分。`))}</p>
    <p class="dataset-next-step">运行后会额外输出 LongMemEval 官方式摘要：overall accuracy、task-averaged accuracy 和 abstention accuracy。</p>
    ${sampleLike ? `<p class="dataset-next-step bad-text">当前路径是内置 sample。它用于小样本核验，不作为正式 LongMemEval 分数。</p>` : ""}
  `;
  const sample = $("longMemSample");
  if (sample) {
    sample.innerHTML = "<option value='all'>全部样本</option>";
    for (const row of data.sample_rows || []) {
      const opt = document.createElement("option");
      opt.value = row.index;
      opt.textContent = `${row.index} · ${row.sample_id} · ${row.questions} 题`;
      sample.appendChild(opt);
    }
  }
  toast("LongMemEval 校验完成");
  return data;
}

async function loadLongMemQuestionPreview() {
  const path = $("longMemData")?.value.trim() || "";
  if (!path) return toast("请先选择 LongMemEval 数据集");
  const sample = $("longMemSample")?.value || "all";
  const query = String($("longMemQuestionSearch")?.value || "").trim();
  const endpoint = sample === "all"
    ? `/api/questions-page?path=${encodeURIComponent(path)}&offset=0&limit=100&q=${encodeURIComponent(query)}`
    : `/api/questions?path=${encodeURIComponent(path)}&sample=${encodeURIComponent(sample)}`;
  const data = await api(endpoint);
  const rows = data.questions || [];
  state.longMemQuestions = rows;
  state.selectedLongMemQuestions = new Set([...state.selectedLongMemQuestions].filter((id) => rows.some((q) => q.question_id === id)));
  renderLongMemQuestionSelection();
  toast(`已加载 ${rows.length} 题预览`);
}

function filteredLongMemQuestions() {
  const query = String($("longMemQuestionSearch")?.value || "").trim().toLowerCase();
  if (!query) return state.longMemQuestions;
  return state.longMemQuestions.filter((q) => {
    const haystack = [
      q.question_id,
      q.sample_id,
      q.question,
      q.answer,
      q.category,
      q.question_time,
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function renderLongMemQuestionSelection() {
  const rows = filteredLongMemQuestions();
  const visibleRows = rows.slice(0, 100);
  const hiddenHint = rows.length > visibleRows.length
    ? `<p class="dataset-next-step">仅显示前 100 题；用搜索缩小范围。</p>`
    : "";
  if ($("longMemSelectedText")) {
    $("longMemSelectedText").textContent = state.selectedLongMemQuestions.size
      ? `已选 ${state.selectedLongMemQuestions.size} 题；开始测试只跑选中题。`
      : `未勾选时按“题数”运行；当前可见 ${rows.length}/${state.longMemQuestions.length} 题。`;
  }
  $("longMemQuestionPreview").innerHTML = hiddenHint + visibleRows.map((q) => `
    <label class="question-row">
      <input type="checkbox" data-question-id="${escapeHtml(q.question_id || "")}" ${state.selectedLongMemQuestions.has(q.question_id) ? "checked" : ""}>
      <span>
        <strong>${escapeHtml(q.question || "-")}</strong>
        <small>${escapeHtml(q.sample_id || "-")} · ${escapeHtml(q.question_id || "-")} · ${escapeHtml(q.category || "-")}</small>
        <em>标准答案：${escapeHtml(q.answer || "-")}</em>
      </span>
    </label>
  `).join("") || "<p>没有加载到可选题目。</p>";
  document.querySelectorAll("#longMemQuestionPreview input[type='checkbox']").forEach((box) => {
    box.addEventListener("change", () => {
      const questionId = box.dataset.questionId || "";
      if (!questionId) return;
      if (box.checked) state.selectedLongMemQuestions.add(questionId);
      else state.selectedLongMemQuestions.delete(questionId);
      renderLongMemQuestionSelection();
    });
  });
}

function selectVisibleLongMemQuestions() {
  filteredLongMemQuestions().slice(0, 100).forEach((q) => {
    if (q.question_id) state.selectedLongMemQuestions.add(q.question_id);
  });
  renderLongMemQuestionSelection();
}

function clearLongMemQuestionSelection() {
  state.selectedLongMemQuestions.clear();
  renderLongMemQuestionSelection();
}

async function runLongMemDiagnostic() {
  const path = $("longMemData")?.value.trim() || "";
  if (!path) return toast("请先选择 LongMemEval 数据集");
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  const taskKind = genericQaTaskKindForBackend(backend);
  rememberActiveDatasetView("longMemEvalView", "longmemeval", path);
  if (!state.longMemDataset || state.longMemDataset.format !== "longmemeval" || state.longMemDataset.path !== path) {
    await validateLongMemDataset();
  }
  const count = benchmarkCount("longMemCount", 3);
  const sample = $("longMemSample")?.value || "all";
  const selectedQuestions = [...state.selectedLongMemQuestions].join(",");
  const effectiveCount = selectedQuestions ? 0 : count;
  const formalWarnings = [];
  if (isSampleDatasetPath(path)) formalWarnings.push("当前路径是内置 sample，不可作为正式 LongMemEval 分数");
  if (sample !== "all") formalWarnings.push("正式 LongMemEval 要求样本范围为全部样本");
  if (selectedQuestions || effectiveCount > 0) formalWarnings.push("正式 LongMemEval 要求题数为 0（全量）");
  const topK = Math.max(1, Number($("longMemTopK")?.value || 4));
  const task = await startTask(taskKind, {
    data: path,
    dataset_format: "longmemeval",
    format: "longmemeval",
    sample,
    count: effectiveCount,
    questions: selectedQuestions,
    top_k: topK,
    identity_mode: "isolated_sample",
    auto_judge: true,
    official_eval_after: true,
    read_openviking_content: true,
    commit_timeout_s: 300,
    name: `LongMemEval ${backendLabel} MemoryBench QA ${selectedQuestions ? `${state.selectedLongMemQuestions.size} selected` : benchmarkCountLabel(effectiveCount)}`,
  });
  rememberBenchmarkRecord(task || {data: path}, "longmemeval");
  showView("longMemEvalView", {preserveScroll: true});
  $("longMemRunResult").innerHTML = `
    <article class="path-row">
      <span>任务</span>
      <code>${escapeHtml(task?.id || state.taskId || "")}</code>
    </article>
    <article class="path-row">
      <span>结果文件</span>
      <code>${escapeHtml(task?.output_file || "")}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(task?.output_file || "")}">复制</button>
    </article>
    <p>任务已启动：${escapeHtml(backendLabel)} MemoryBench 记忆问答会完成上下文写入、记忆检索、大模型回答、判分和 LongMemEval 官方式摘要。当前页会保留；右上角运行中入口显示进度，结果中心查看报告。</p>
    <p class="dataset-next-step">选题：${escapeHtml(selectedQuestions ? `${state.selectedLongMemQuestions.size} 题` : benchmarkCountLabel(effectiveCount))}；样本范围：${escapeHtml(sample)}。</p>
    ${formalWarnings.length ? `<p class="dataset-next-step bad-text">正式分数门禁：${escapeHtml(formalWarnings.join("；"))}。本次仍作为小样本核验运行。</p>` : ""}
    <div class="panel-actions">
      <button class="secondary" type="button" data-view-jump="runsView">查看任务/报告</button>
    </div>
  `;
  bindCopyButtons("#longMemRunResult");
  $("longMemRunResult").querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewJump));
  });
}

async function injectLongMemMemory() {
  const path = $("longMemData")?.value.trim() || "";
  if (!path) return toast("请先选择 LongMemEval 数据集");
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  const taskKind = genericQaTaskKindForBackend(backend);
  rememberActiveDatasetView("longMemEvalView", "longmemeval", path);
  if (!state.longMemDataset || state.longMemDataset.format !== "longmemeval" || state.longMemDataset.path !== path) {
    await validateLongMemDataset();
  }
  const sample = $("longMemSample")?.value || "all";
  const selectedQuestions = [...state.selectedLongMemQuestions].join(",");
  const count = benchmarkCount("longMemImportCount", 3);
  const effectiveCount = selectedQuestions ? 0 : count;
  const task = await startTask(taskKind, {
    data: path,
    dataset_format: "longmemeval",
    format: "longmemeval",
    sample,
    count: effectiveCount,
    questions: selectedQuestions,
    import_only: true,
    auto_judge: false,
    official_eval_after: false,
    read_openviking_content: false,
    commit_timeout_s: 300,
    name: `LongMemEval memory import ${selectedQuestions ? `${state.selectedLongMemQuestions.size} selected` : benchmarkCountLabel(effectiveCount)}`,
  });
  rememberBenchmarkRecord(task || {data: path}, "longmemeval");
  showView("longMemEvalView", {preserveScroll: true, benchmarkStage: "import"});
    if ($("longMemImportResult")) {
    $("longMemImportResult").innerHTML = `
      <article class="path-row">
        <span>任务</span>
        <code>${escapeHtml(task?.id || state.taskId || "")}</code>
      </article>
      <p>已启动记忆注入：通过 ${escapeHtml(backendLabel)} 只写入 LongMemEval 原始文档并生成导入摘要，不调用答案模型或判分。</p>
      <p class="dataset-next-step">注入范围：${escapeHtml(selectedQuestions ? `${state.selectedLongMemQuestions.size} 道选中题` : benchmarkCountLabel(effectiveCount))}；样本范围：${escapeHtml(sample)}。</p>
      <div class="panel-actions">
        <button class="secondary" type="button" data-view-jump="runsView">查看任务/报告</button>
        <button class="secondary" type="button" data-copy="${escapeHtml(task?.run_dir || "")}">复制任务目录</button>
      </div>
    `;
    bindCopyButtons("#longMemImportResult");
    $("longMemImportResult").querySelectorAll("[data-view-jump]").forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.viewJump, {benchmarkStage: "report"}));
    });
  }
}

async function loadQuestions() {
  const path = $("data").value.trim();
  if (!path) return;
  const sample = $("sample").value || "all";
  const scope = currentLocomoSampleScope();
  const requestSeq = ++state.locomoQuestionLoadSeq;
  state.locomoQuestionsLoading = true;
  state.filteredQuestions = [];
  $("questionPicker").innerHTML = `<p>正在加载 ${escapeHtml(scope.isAll ? LOCOMO_ALL_SESSIONS_LABEL : scope.label)} 的题目...</p>`;
  renderKpis("questionSelectionKpis", [
    ["题目范围", "加载中"],
    ["已选", "-"],
    ["运行模式", "切换中"],
  ]);
  refreshLocomoQaActionLabels();
  renderQaReadinessPanel();
  try {
    const data = await api(`/api/questions?path=${encodeURIComponent(path)}&sample=${encodeURIComponent(sample)}`);
    if (requestSeq !== state.locomoQuestionLoadSeq) return;
    state.questions = data.questions || [];
    state.selectedQuestions = new Set([...state.selectedQuestions].filter((id) => state.questions.some((q) => q.question_id === id)));
    renderQuestions();
    refreshLocomoQaActionLabels();
  } finally {
    if (requestSeq === state.locomoQuestionLoadSeq) {
      state.locomoQuestionsLoading = false;
      refreshLocomoQaActionLabels();
      renderQaReadinessPanel();
    }
  }
}

async function runSelectedMemoryQa() {
  if (state.locomoQuestionsLoading) return toast("题目范围还在切换，请稍候");
  const busyMessage = locomoQaLaunchPendingMessage();
  if (busyMessage) return toast(busyMessage);
  const selectedQuestionIds = currentScopeSelectedQuestionIds();
  if (!selectedQuestionIds.length) {
    return toast("请先勾选至少 1 题；要跑当前范围全量请点“跑当前 conv 全部题”或“跑全部 LoCoMo”");
  }
  const launchButton = $("runOpenVikingQa");
  if (launchButton) launchButton.disabled = true;
  state.locomoQaLaunchSource = "selected";
  state.locomoQaLaunchPending = true;
  refreshLocomoQaActionLabels();
  try {
    return await startTask(locomoQaTaskKind(), {
      sample: $("sample").value || "all",
      questions: selectedQuestionIds.join(","),
      require_selected_questions: true,
    });
  } finally {
    state.locomoQaLaunchPending = false;
    if (state.locomoQaLaunchSource === "selected") state.locomoQaLaunchSource = "";
    refreshLocomoQaActionLabels();
  }
}

function filteredQuestions() {
  const keyword = ($("questionSearch")?.value || "").trim().toLowerCase();
  const category = $("questionCategory")?.value || "all";
  return state.questions.filter((q) => {
    const categoryOk = category === "all" || String(q.category || "") === category;
    if (!categoryOk) return false;
    if (!keyword) return true;
    const haystack = [
      q.question_id,
      q.sample_id,
      q.question,
      q.answer,
      q.question_time,
      q.category ? `c${q.category}` : "",
    ].join(" ").toLowerCase();
    return haystack.includes(keyword);
  });
}

function renderQuestions() {
  const rows = filteredQuestions();
  state.filteredQuestions = rows;
  const keyword = ($("questionSearch")?.value || "").trim();
  const isAllSamples = ($("sample")?.value || "all") === "all";
  const visibleLimit = isAllSamples && !keyword ? 200 : 600;
  const visibleRows = rows.slice(0, visibleLimit);
  renderKpis("questionSelectionKpis", locomoQuestionSelectionKpis(rows));
  const limitHint = rows.length > visibleRows.length
    ? `<p>当前显示 ${visibleRows.length} / ${rows.length} 题。请选择具体 conv 或输入关键词继续缩小范围。</p>`
    : "";
  $("questionPicker").innerHTML = limitHint + visibleRows.map((q) => `
    <label class="question-row">
      <input type="checkbox" data-question-id="${escapeHtml(q.question_id)}" ${state.selectedQuestions.has(q.question_id) ? "checked" : ""}>
      <span>
        <strong>${escapeHtml(q.question)}</strong>
        <small>${escapeHtml(q.sample_id)} · ${escapeHtml(q.question_id)}${q.question_time ? ` · ${escapeHtml(q.question_time)}` : ""}</small>
        ${locomoCategoryBadge(q.category)}
        <em>标准答案：${escapeHtml(q.answer || "-")}</em>
      </span>
    </label>
  `).join("") || "<p>当前范围没有可选问题。</p>";
  document.querySelectorAll("#questionPicker input[type='checkbox']").forEach((box) => {
    box.addEventListener("change", () => {
      if (box.checked) state.selectedQuestions.add(box.dataset.questionId);
      else state.selectedQuestions.delete(box.dataset.questionId);
      updateQuestionKpis();
    });
  });
  refreshLocomoQaActionLabels();
  renderMemoryMismatchWarning();
}

function updateQuestionKpis() {
  const rows = state.filteredQuestions || filteredQuestions();
  renderKpis("questionSelectionKpis", locomoQuestionSelectionKpis(rows));
  refreshLocomoQaActionLabels();
  renderQaReadinessPanel();
  renderMemoryMismatchWarning();
}

function qaSelectionReadiness() {
  const dataset = currentLocomoDataset();
  const sampleValue = $("sample")?.value || "all";
  const sampleText = $("sample")?.selectedOptions?.[0]?.textContent?.trim() || LOCOMO_ALL_SESSIONS_LABEL;
  const selected = state.selectedQuestions.size;
  const rows = state.filteredQuestions?.length ? state.filteredQuestions : filteredQuestions();
  const scope = currentLocomoSampleScope();
  if (!dataset) {
    return {
      value: "待加载数据集",
      detail: "先在 LoCoMo 评测的数据集步骤读取 JSON。",
      tone: "warn",
    };
  }
  if (state.locomoQuestionsLoading) {
    return {
      value: `${scope.label} · 题目加载中`,
      detail: "正在切换 conv 范围，请稍候再勾选或启动问答。",
      tone: "active",
    };
  }
  if (selected) {
    const selectedInScope = rows.filter((q) => state.selectedQuestions.has(q.question_id)).length;
    return {
      value: `已选 ${formatInt(selectedInScope)}/${formatInt(rows.length)} 题`,
      detail: `${formatInt(rows.length)} 题在当前筛选范围内；只会运行勾选的 question_id。`,
      tone: "ok",
    };
  }
  if (sampleValue !== "all") {
    return {
      value: `${scope.label} · ${formatInt(scope.questionCount)} 题`,
      detail: `未勾选具体题目时，只会运行当前 conv，不会运行全部 ${formatInt(dataset.questions || 0)} 题。`,
      tone: scope.questionCount ? "ok" : "warn",
    };
  }
  return {
    value: "等待选择",
      detail: `${LOCOMO_ALL_SESSIONS_LABEL}模式下需要先勾选题目；要跑完整 LoCoMo 请点“跑全部 LoCoMo”。`,
    tone: "warn",
  };
}

function qaImportReadiness(backend, account, workspace, lastImport = readCurrentAccountLastImport()) {
  const imported = state.importedMemoryStatus || {};
  const mismatchInfo = memoryMismatchInfo();
  const selectedSamples = mismatchInfo.selectedSamples || [];
  const importedSample = mismatchInfo.importedSample || (lastImport.sample_value === "all" ? LOCOMO_ALL_SESSIONS_LABEL : "");
  const importBackend = normalizeMemoryBackend(lastImport.backend || backend);
  const backendMatches = importBackend === backend;
  const importWorkspace = lastImport.workspace || imported.workspace || workspace || "";
  const completeCount = Number(imported.complete_count || 0);
  const hasImport = Boolean(importWorkspace || lastImport.output_file || completeCount);
  if (mismatchInfo.mismatch) {
    return {
      value: "导入范围不匹配",
      detail: `最后导入 ${mismatchInfo.importedSample}，本次题目来自 ${mismatchInfo.selectedSamples.join(", ")}。`,
      tone: "bad",
    };
  }
  if (!hasImport) {
    return {
      value: "待导入",
      detail: "先完成导入和完整性检查，再运行正式测试。",
      tone: "warn",
    };
  }
  if (!backendMatches) {
    return {
      value: "后端不一致",
      detail: `最后导入使用 ${memoryBackendLabel(importBackend)}，当前选择 ${memoryBackendLabel(backend)}。请先切到一致后端，或重新导入。`,
      tone: "warn",
    };
  }
  return {
    value: selectedSamples.length ? (importedSample || "已记录导入") : (lastImport.sample_value === "all" ? LOCOMO_ALL_SESSIONS_LABEL : "已记录导入"),
    detail: compactPath(importWorkspace || storageRootForBackend(workspace, account, backend), 42, 38),
    tone: "ok",
  };
}

function qaModelReadiness() {
  const cfg = agentModelConfig();
  const baseUrl = cfg.baseUrl;
  const model = cfg.model;
  const tokenSet = Boolean(cfg.token || $("judgeToken")?.value.trim());
  const ok = Boolean(baseUrl && model && tokenSet);
  return {
    value: model || "未配置模型",
    detail: `${baseUrl ? "模型地址已填" : "模型地址未填"} · ${tokenSet ? "密钥已填" : "密钥未填"}`,
    tone: ok ? "ok" : "warn",
  };
}

function memoryInjectModelReadiness(backend = currentMemoryBackend()) {
  if (normalizeMemoryBackend(backend) === "openviking") {
    return {
      value: "后端服务处理",
      detail: "页面无需填写模型密钥；OpenViking 服务负责归档/抽取。",
      tone: "ok",
    };
  }
  const cfg = memoryInjectModelConfig();
  const baseUrl = cfg.baseUrl;
  const model = cfg.model;
  const tokenSet = Boolean(cfg.token || $("ovVlmApiKey")?.value.trim());
  const ok = Boolean(baseUrl && model && tokenSet);
  return {
    value: model || "未配置模型",
    detail: `${baseUrl ? "模型地址已填" : "模型地址未填"} · ${tokenSet ? "密钥已填" : "密钥未填"}`,
    tone: ok ? "ok" : "warn",
  };
}

function renderQaReadinessPanel(task = null) {
  const target = $("qaReadinessPanel");
  if (!target) return;
  const storedImport = readCurrentAccountLastImport();
  const candidateImportTask = isMemoryImportKind(state.currentImportTask?.kind || "") ? state.currentImportTask : null;
  const candidateScope = candidateImportTask ? importScopeFromTask(candidateImportTask, storedImport) : {};
  const importTask = candidateImportTask
    && matchesCurrentAccount(candidateImportTask)
    && normalizeMemoryBackend(candidateScope.backend) === currentMemoryBackend()
    ? candidateImportTask
    : null;
  const importScope = importTask ? candidateScope : storedImport;
  const taskConfig = task?.meta?.config || {};
  const backend = currentMemoryBackend();
  const qaKind = locomoQaTaskKind();
  const account = safeAccountSlug(taskConfig.account || recordAccount(task || {}) || importScope.account || $("ovAccount")?.value.trim() || currentAccount());
  const workspace = taskConfig.workspace || importScope.workspace || effectiveOpenVikingWorkspace(qaKind) || "";
  const dataset = currentLocomoDataset();
  const selection = qaSelectionReadiness();
  const importReady = qaImportReadiness(backend, account, workspace, storedImport);
  const modelReady = qaModelReadiness();
  const launchGate = locomoQaLaunchGate();
  const taskRunning = isMemoryQaKind(task?.kind || state.currentLocomoTask?.kind || "") && (task?.status || state.currentLocomoTask?.status) === "running";
  const modeValue = "MemoryBench Agent（VikingBoat 对齐）";
  const repeatedGate = launchGate.value === importReady.value && launchGate.detail === importReady.detail;
  const cards = [
    {
      label: "回答模型",
      ...modelReady,
    },
    {
      label: "记忆范围",
      ...importReady,
    },
    {
      label: "启动门禁",
      value: repeatedGate ? (launchGate.blocking ? "当前不可启动" : launchGate.value) : launchGate.value,
      detail: repeatedGate ? "先处理上方记忆范围问题，再启动测试。" : launchGate.detail,
      tone: launchGate.tone,
    },
  ];
  const datasetNote = dataset ? `${dataset.samples ?? "-"} conv · ${dataset.questions ?? "-"} QA` : "数据集未校验";
  const selectionNote = selection.value ? `题目 ${selection.value}` : "";
  const agentNote = `${RETRIEVAL_COUNT_LABEL} ${VIKINGBOAT_LITE_TOP_K} · ${TOOL_SEARCH_LABEL} ${VIKINGBOAT_LITE_TOOL_SEARCH_LIMIT} · ${MAX_ITERATION_LABEL} ${VIKINGBOAT_LITE_MAX_ITERATIONS}`;
  target.innerHTML = `
    ${cards.map((card) => `
      <article class="${escapeHtml(card.tone || "")}">
        <span>${escapeHtml(card.label)}</span>
        <strong>${escapeHtml(card.value || "-")}</strong>
        <p>${escapeHtml(card.detail || "")}</p>
      </article>
    `).join("")}
    <p class="qa-readiness-note">${escapeHtml([datasetNote, selectionNote, agentNote].filter(Boolean).join("；"))}</p>
  `;
}

function selectedQuestionSamples() {
  const selected = new Set(state.selectedQuestions);
  if (selected.size) {
    return [...new Set(state.questions.filter((q) => selected.has(q.question_id)).map((q) => q.sample_id).filter(Boolean))];
  }
  const sample = $("sample")?.value || "all";
  if (sample !== "all") {
    const found = state.questions.find((q) => String(q.sample_index) === String(sample) || q.sample_id === sample);
    return found?.sample_id ? [found.sample_id] : [];
  }
  return [];
}

function importedSampleFromLastImport(lastImport = readCurrentAccountLastImport()) {
  const label = lastImport.sample_label || "";
  const match = label.match(/·\s*([^·]+?)\s*·/) || String(lastImport.session_id || "").match(/locomo-(conv-\d+)/);
  return match ? match[1].trim() : "";
}

function memoryMismatchInfo() {
  const isLocomo = Boolean(currentLocomoDataset());
  const importedSample = importedSampleFromLastImport();
  const selectedSamples = selectedQuestionSamples();
  const mismatch = isLocomo && importedSample && selectedSamples.length > 0 && !selectedSamples.includes(importedSample);
  return {isLocomo, importedSample, selectedSamples, mismatch};
}

function locomoQaLaunchIssueMessage(preflight = state.systemPreflight) {
  const gate = locomoQaLaunchGate(preflight, {requirePreflight: true});
  if (!gate.blocking) return "";
  return `问答启动前检查未通过：${gate.value}${gate.detail ? `。${gate.detail}` : ""}`;
}

async function ensureLocomoQaLaunchReady() {
  state.locomoQaSubmitPhase = "preflight";
  refreshLocomoQaActionLabels();
  const preflight = await runSystemPreflight(true).catch(() => state.systemPreflight);
  const issue = locomoQaLaunchIssueMessage(preflight || state.systemPreflight);
  if (issue) throw new Error(issue);
  state.locomoQaSubmitPhase = "submit";
  refreshLocomoQaActionLabels();
}

function renderMemoryMismatchWarning() {
  const warning = $("memoryMismatchWarning");
  if (!warning) return;
  const {importedSample, selectedSamples, mismatch} = memoryMismatchInfo();
  warning.hidden = !mismatch;
  warning.classList.toggle("unsafe", mismatch);
  if (mismatch) {
    warning.textContent = `当前最后导入的记忆是 ${importedSample}，但本次选择的问题来自 ${selectedSamples.join(", ")}。建议先导入匹配的对话，避免用错记忆空间评测。`;
  }
  renderQaReadinessPanel();
}

async function mirrorImportSampleToQa(options = {}) {
  const selected = parseImportSampleSelection();
  const value = selected.baseValue || "all";
  const sample = $("sample");
  if (!sample) return;
  const allowAll = options.allowAll !== false;
  if (!allowAll && value === "all") return;
  if (![...sample.options].some((option) => option.value === value)) return;
  const nextScope = currentLocomoSampleScope();
  if (sample.value === value && locomoQuestionsMatchScope(nextScope)) {
    refreshLocomoQaActionLabels();
    renderQaReadinessPanel();
    return;
  }
  sample.value = value;
  state.selectedQuestions.clear();
  if (currentLocomoDataset()) {
    await loadQuestions();
  } else {
    renderQuestions();
    refreshLocomoQaActionLabels();
  }
}

async function probeOpenViking() {
  const backend = currentMemoryBackend();
  const defaultPort = "19080";
  const probeParams = {
    backend,
    host: $("ovHost").value.trim() || "127.0.0.1",
    port: $("ovPort").value.trim() || defaultPort,
    root_api_key: $("ovApiKey").value.trim(),
    workspace: $("ovWorkspace")?.value.trim() || "",
    account: currentAccount(),
  };
  if (backend === "echomemory") {
    const localCfg = readAccountConfig(currentAccount());
    probeParams.echomem_root = $("echomemRoot")?.value.trim() || localCfg.echomemRoot || "";
    probeParams.user_id = $("memoryUserId")?.value.trim() || localCfg.memoryUserId || "default";
    probeParams.agent_id = $("memoryAgentId")?.value.trim() || localCfg.memoryAgentId || "default";
  }
  const qs = new URLSearchParams(probeParams);
  const data = await api(`/api/probe?${qs.toString()}`);
  const label = memoryBackendLabel(backend);
  const status = String(data.status || data.status_text || (data.ok ? "ok" : "fail")).toLowerCase();
  const statusClass = data.ok ? "ok" : (status === "warn" ? "warn" : "bad");
  const statusText = data.ok ? "正常" : (status === "warn" ? "警告" : "错误");
  const target = data.url || data.root || data.message || "-";
  $("ovStatus").innerHTML = `
    <span class="check ${statusClass}">${statusText} · ${escapeHtml(target)}</span>
    <span class="check ${statusClass}">后端 ${escapeHtml(memoryBackendShortLabel(data.backend || backend))} · 状态 ${escapeHtml(status || "-")}</span>
  `;
  setConnection(Boolean(data.ok), data.ok ? `${label} Ready` : `${label} 需要检查`);
}

function currentEchoMemoryImportMode() {
  return "fast";
}

function isSingleSessionImportSummary(summary = {}) {
  const sample = String(summary.sample || "").trim();
  return Number(summary.session_limit || 0) === 1 && sample !== "" && sample !== "all";
}

function taskPayload(kind, extra = {}) {
  const selectedQuestions = kind === "local_agent" || isMemoryQaKind(kind) ? [...state.selectedQuestions].join(",") : "";
  const locomoDataset = currentLocomoDataset();
  const inferredFormat = normalizeDatasetFormat(extra.dataset_format || inferDatasetFormatFromText(extra.data, extra.name, kind));
  const datasetFormat = inferredFormat || ((kind === "openviking_generic_qa" || kind === "echomemory_generic_qa") ? "generic" : (locomoDataset?.format || "locomo"));
  const dataPath = extra.data || (datasetFormat === "locomo" ? $("data").value.trim() : "");
  const workspace = effectiveOpenVikingWorkspace(kind, extra);
  const agentCfg = agentModelConfig();
  const judgeCfg = judgeModelConfig();
  const memoryCfg = memoryInjectModelConfig();
  const openvikingQaKind = kind === "openviking_qa"
    || kind === "openviking_qa_retry_failed"
    || kind === "openviking_qa_retry_missing";
  const openvikingCompatibleImportKind = kind === "openviking_import";
  const echomemoryCompatibleImportKind = kind === "echomemory_import";
  const echoImportMode = currentEchoMemoryImportMode();
  const echoFastImport = echomemoryCompatibleImportKind && echoImportMode === "fast";
  const defaultServicePort = "19080";
  const importSelection = isMemoryImportKind(kind) ? parseImportSampleSelection() : null;
  const importSample = importSelection ? (importSelection.baseValue || "all") : ($("sample").value || "all");
  const importSmoke = Boolean(importSelection?.smoke);
  const workspaceMode = String(extra.workspace_mode || "manual").trim() || "manual";
  const localCfg = readAccountConfig(currentAccount());
  const memoryUserId = $("memoryUserId")?.value.trim() || localCfg.memoryUserId || "default";
  const memoryAgentId = $("memoryAgentId")?.value.trim() || localCfg.memoryAgentId || "default";
  const echomemRoot = $("echomemRoot")?.value.trim() || localCfg.echomemRoot || "";
  return {
    kind,
    runner: "local_agent",
    agent_type: agentTypeForKind(kind),
    data: dataPath,
    dataset_format: datasetFormat,
    backend: currentMemoryBackend(),
    sample: importSample,
    questions: selectedQuestions,
    judge_base_url: judgeCfg.baseUrl,
    judge_model: judgeCfg.model,
    judge_token: judgeCfg.token,
    answer_base_url: agentCfg.baseUrl,
    answer_model: agentCfg.model,
    answer_token: agentCfg.token,
    host: $("ovHost").value.trim() || "127.0.0.1",
    port: $("ovPort").value.trim() || defaultServicePort,
    root_api_key: $("ovApiKey").value.trim(),
    account: currentAccount(),
    user_id: memoryUserId,
    agent_id: memoryAgentId,
    workspace,
    workspace_mode: isMemoryImportKind(kind) ? workspaceMode : "manual",
    ov_user_id: openvikingCompatibleImportKind ? "" : memoryUserId,
    ov_agent_id: openvikingCompatibleImportKind ? "" : memoryAgentId,
    em_user_id: memoryUserId,
    em_agent_id: memoryAgentId,
    echomem_root: echomemRoot,
    vlm_base_url: memoryCfg.baseUrl,
    vlm_api_key: memoryCfg.token,
    vlm_model: memoryCfg.model,
    ...(openvikingCompatibleImportKind ? {session_mode: "locomo", commit_timeout_s: 600} : {}),
    ...(echomemoryCompatibleImportKind ? {
      session_mode: "locomo",
      import_wait_mode: echoImportMode,
      defer_artifact_wait: echoFastImport,
      commit_wait_s: echoFastImport ? 8 : 300,
      commit_call_timeout_s: echoFastImport ? 300 : 300,
      flush_call_timeout_s: echoFastImport ? 15 : 600,
      flush_attempts: echoFastImport ? 0 : 2,
    } : {}),
    ...(isMemoryImportKind(kind) && importSmoke ? {
      max_sessions: 1,
      name: `locomo ${memoryBackendLabel(currentMemoryBackend())} ${importSelection?.sampleId || importSample} 单 session 注入测试`,
    } : {}),
    ...(kind === "echomemory_qa" ? {
      prompt_mode: "vikingboat_lite",
      retrieval_mode: "search",
      top_k: VIKINGBOAT_LITE_TOP_K,
      score_threshold: 0.1,
      tool_set: "vikingbot_native_safe",
      tool_search_limit: VIKINGBOAT_LITE_TOOL_SEARCH_LIMIT,
      tool_min_score: 0.35,
      max_iterations: VIKINGBOAT_LITE_MAX_ITERATIONS,
      user_memory_budget_chars: 4000,
      agent_memory_budget_chars: 2000,
      vikingboat_compat: false,
      vikingboat_tool_loop: true,
      initial_tool_prefetch: true,
      fallback_to_one_shot: true,
    } : {}),
    ...(kind === "echomemory_generic_qa" ? {
      import_wait_mode: echoImportMode,
      defer_artifact_wait: echoImportMode === "fast",
      commit_wait_s: echoImportMode === "fast" ? 8 : 300,
      commit_call_timeout_s: echoImportMode === "fast" ? 900 : 900,
      flush_call_timeout_s: echoImportMode === "fast" ? 15 : 600,
      flush_attempts: echoImportMode === "fast" ? 0 : 2,
      runtime_recycle_every: 50,
      import_timeout_s: 900,
    } : {}),
    ...(openvikingQaKind ? vikingbotAlignedQaPayload() : {}),
    name: taskNameForKind(kind, datasetFormat),
    ...extra,
  };
}

function benchmarkUiForFormat(format) {
  const key = normalizeDatasetFormat(format);
  if (key === "longmemeval") {
    return {
      progressBar: "longMemProgressBar",
      progressText: "longMemProgressText",
      logBox: "longMemLogBox",
      waiting: "等待测试",
    };
  }
  const config = GENERIC_BENCHMARKS[key];
  if (config?.progressBar && config?.progressText && config?.logBox) {
    return {
      progressBar: config.progressBar,
      progressText: config.progressText,
      logBox: config.logBox,
      waiting: "等待测试",
    };
  }
  return null;
}

function taskUi(kind, task = {}) {
  const taskId = task?.id || state.taskId || "";
  const format = taskDatasetFormat(task || {}, taskId ? (state.taskDatasetFormats[taskId] || "") : "");
  if (kind === "openviking_generic_qa" || kind === "echomemory_generic_qa") {
    const benchmarkUi = benchmarkUiForFormat(format);
    if (benchmarkUi) return benchmarkUi;
  }
  if (isMemoryImportKind(kind)) {
    return {
      progressBar: "importProgressBar",
      progressText: "importProgressText",
      logBox: "importLogBox",
      waiting: "等待导入",
    };
  }
  if (kind === "judge") {
    return {
      progressBar: "judgeProgressBar",
      progressText: "judgeProgressText",
      logBox: "judgeLogBox",
      waiting: "等待判分",
    };
  }
  return {
    progressBar: "evalProgressBar",
    progressText: "evalProgressText",
    logBox: "evalLogBox",
    waiting: "等待测试",
  };
}

function taskShouldUseLocomoTaskStrip(kind, task = {}, format = "") {
  const taskKind = task?.kind || kind || "";
  if (isMemoryImportKind(taskKind)) return false;
  if (taskKind === "judge") return true;
  const normalizedFormat = normalizeDatasetFormat(format || taskDatasetFormat(task || {}, ""));
  return isLocomoTaskOutput(taskKind, task || {}, normalizedFormat);
}

function compactInlineText(value = "", limit = 260) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 3))}...` : text;
}

function taskDatasetPath(task = {}) {
  const cfg = task?.meta?.config || task?.config || {};
  return cfg.data || cfg.dataset || commandOption(task.command || [], "dataset") || "";
}

function taskSampleFilter(task = {}) {
  const cfg = task?.meta?.config || task?.config || {};
  return String(cfg.sample || commandOption(task.command || [], "sample") || "").trim();
}

function ensureTaskImportDataset(task = {}) {
  const path = taskDatasetPath(task);
  if (!path || state.importPreviewDatasets[path] || state.importPreviewDatasetLoading[path] || state.importPreviewDatasetErrors[path]) return;
  state.importPreviewDatasetLoading[path] = true;
  api(`/api/dataset/load?path=${encodeURIComponent(path)}`)
    .then((data) => {
      state.importPreviewDatasets[path] = Array.isArray(data) ? data : [];
      const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
      if (state.currentLocomoTask?.id === task.id && taskShouldUseLocomoTaskStrip(task.kind || "", task, format)) {
        renderActiveTaskStrip(task);
      }
    })
    .catch((error) => {
      state.importPreviewDatasetErrors[path] = error.message || String(error || "dataset load failed");
    })
    .finally(() => {
      delete state.importPreviewDatasetLoading[path];
    });
}

function taskImportMessageCounts(progress = {}) {
  const direct = progress.current_import || {};
  let index = Number(direct.message_index || direct.index || 0);
  let total = Number(direct.message_total || direct.total || 0);
  if ((!index || !total) && progress.detail) {
    const match = String(progress.detail || "").match(/(\d+)\s*\/\s*(\d+)\s+messages/i);
    if (match) {
      index = Number(match[1] || 0);
      total = Number(match[2] || 0);
    }
  }
  return {index, total};
}

function locomoTurnPreview(raw = {}) {
  const parts = [];
  if (raw.text) parts.push(raw.text);
  if (raw.blip_caption) parts.push(`image: ${raw.blip_caption}`);
  if (raw.query) parts.push(`query: ${raw.query}`);
  return compactInlineText(parts.join(" "), 280);
}

function locomoSampleMatches(sample = {}, index = 0, sampleKey = "") {
  const sampleId = String(sample.sample_id || `sample_${index}`);
  const value = String(sampleKey || "").trim();
  return !value || value === "all" || value === "*" || value === sampleId || value === String(index);
}

function currentImportPreviewFromTask(task = {}) {
  const progress = task.progress || {};
  const direct = progress.current_import || {};
  if (direct.content || direct.text) {
    return {
      source: direct.source || "log",
      sample: direct.sample || direct.sample_id || progress.sample || "",
      session: direct.session || direct.session_key || direct.session_label || progress.session_label || "",
      messageIndex: Number(direct.message_index || direct.index || 0),
      messageTotal: Number(direct.message_total || direct.total || 0),
      role: direct.role_id || direct.role || "",
      diaId: direct.dia_id || "",
      text: compactInlineText(direct.content || direct.text || "", 280),
      note: direct.note || "",
    };
  }
  if (!isMemoryImportKind(task.kind || "")) return null;
  const path = taskDatasetPath(task);
  if (!path) return null;
  const dataset = state.importPreviewDatasets[path];
  if (!dataset) {
    ensureTaskImportDataset(task);
    return {
      source: "loading",
      sample: progress.sample || taskSampleFilter(task) || "",
      session: progress.session_label || "",
      messageIndex: 0,
      messageTotal: 0,
      role: "",
      diaId: "",
      text: state.importPreviewDatasetErrors[path] ? `读取数据集失败：${state.importPreviewDatasetErrors[path]}` : "正在读取当前导入内容...",
      note: path,
    };
  }
  const sessionLabel = String(progress.session_label || "");
  const [sampleFromLabel, sessionFromLabel] = sessionLabel.includes("/") ? sessionLabel.split("/") : ["", sessionLabel];
  const sampleKey = progress.sample || sampleFromLabel || taskSampleFilter(task);
  const sampleEntry = dataset.find((sample, index) => locomoSampleMatches(sample, index, sampleKey)) || dataset[0];
  const conv = sampleEntry?.conversation || {};
  const sessionKey = sessionFromLabel || Object.keys(conv).find((key) => /^session_\d+$/.test(key) && Array.isArray(conv[key])) || "";
  const messages = Array.isArray(conv[sessionKey]) ? conv[sessionKey] : [];
  const counts = taskImportMessageCounts(progress);
  const messageTotal = counts.total || messages.length;
  const messageIndex = Math.max(1, Math.min(messages.length || 1, counts.index || messageTotal || 1));
  const raw = messages[messageIndex - 1] || messages[messages.length - 1] || {};
  const speaker = raw.speaker || raw.role || "";
  const phase = String(progress.phase || "");
  const note = phase.startsWith("commit")
    ? "当前 session 已提交，正在归档/索引；展示该 session 最近提交的消息。"
    : "当前正在写入长期记忆后端。";
  return {
    source: "dataset",
    sample: sampleEntry?.sample_id || sampleKey || "",
    session: sessionKey ? `${sampleEntry?.sample_id || sampleKey || ""}/${sessionKey}` : sessionLabel,
    messageIndex,
    messageTotal,
    role: speaker,
    diaId: raw.dia_id || "",
    text: locomoTurnPreview(raw),
    note,
  };
}

function renderCurrentImportPreview(task = {}) {
  const preview = currentImportPreviewFromTask(task);
  if (!preview) return "";
  const meta = [
    preview.sample,
    preview.session,
    preview.messageTotal ? `消息 ${preview.messageIndex || "-"}/${preview.messageTotal}` : "",
    preview.role,
    preview.diaId,
  ].filter(Boolean).join(" · ");
  return `
    <section class="current-import-card ${preview.source === "loading" ? "loading" : ""}">
      <span>当前导入内容</span>
      <strong>${escapeHtml(meta || "等待导入内容")}</strong>
      <p>${escapeHtml(preview.text || "暂未读取到消息预览。")}</p>
      ${preview.note ? `<small>${escapeHtml(preview.note)}</small>` : ""}
    </section>
  `;
}

function dirname(path) {
  const value = String(path || "");
  const idx = value.lastIndexOf("/");
  return idx > 0 ? value.slice(0, idx) : value;
}

function renderArtifactList(items = []) {
  const rows = items.filter(([, value]) => value);
  const target = $("resultArtifactList");
  if (!target) return;
  target.innerHTML = rows.length ? rows.map(([label, value]) => `
    <article class="path-row">
      <span>${escapeHtml(label)}</span>
      <code>${escapeHtml(value)}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(value)}">复制</button>
      <button class="path-open" type="button" data-path="${escapeHtml(value)}">打开</button>
    </article>
  `).join("") : "";
  target.hidden = !rows.length;
  bindCopyButtons("#resultArtifactList");
  bindOpenButtons("#resultArtifactList");
}

function renderActiveTaskStrip(task = null) {
  const strip = $("activeTaskStrip");
  if (!strip) return;
  if (!task) {
    strip.innerHTML = "";
    strip.removeAttribute("data-task-id");
    strip.removeAttribute("data-task-kind");
    strip.classList.remove("running", "succeeded", "failed");
    return;
  }
  task = taskWithLiveProgress(task);
  const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
  if (!taskVisibleInActiveTaskStrip(task, format)) {
    const currentKind = strip.dataset.taskKind || "";
    if (strip.dataset.taskId === task.id || isMemoryImportKind(currentKind) || currentKind === "judge") {
      renderActiveTaskStrip(null);
    }
    return;
  }
  const stageLabel = taskStageLabel(task.kind || "", task);
  const statusLabel = taskDisplayStatusLabel(task, format);
  const displayTitle = taskDisplayTitle(task, format) || (task.name || task.id || "task");
  const summary = task.summary || {};
  const status = task.status || "-";
  const progress = task.progress;
  const execution = taskExecutionProgress(task, format);
  const authoritativeProgressNote = taskAuthoritativeProgressNote(task, format);
  const pct = execution?.total_questions
    ? Number(execution.pct || 0)
    : (progress?.total ? Number(progress.pct || 0) : null);
  const pctText = pct == null ? "-" : `${pct.toFixed(1)}%`;
  const phase = String(progress?.phase || "").trim();
  const countText = execution?.total_questions
    ? `第 ${execution.current_question || execution.answered_questions || 0}/${execution.total_questions} 题`
    : (progress?.total
      ? (progress?.unit === "questions"
        ? `已答 ${progress.current}/${progress.total}`
        : (progress?.unit === "sessions"
          ? (phase.startsWith("commit")
            ? `已归档 ${progress.current}/${progress.total}`
            : `已导入 ${progress.current}/${progress.total}`)
          : `${progress.current}/${progress.total}`))
      : (summary.rows != null ? `${summary.rows} 行` : "-"));
  const unitLabel = execution?.total_questions
    ? "题"
    : (progress?.unit === "questions" ? "题" : (progress?.unit === "sessions" ? "会话" : (progress?.unit || "items")));
  const etaValue = progress?.eta_seconds != null ? formatDuration(progress.eta_seconds) : "-";
  const elapsedValue = task.duration != null ? formatDuration(task.duration) : (progress?.elapsed_seconds != null ? formatDuration(progress.elapsed_seconds) : "-");
  const detailText = benchmarkProgressDetail(task, format) || (task.output_file || "");
  const isQaTask = stageLabel === "QA";
  const progressComplete = isQaTask && (
    (execution?.total_questions && Number(execution.answered_questions || 0) >= Number(execution.total_questions || 0))
    || (progress?.total && Number(progress.current || 0) >= Number(progress.total || 0))
  );
  const displayStatusLabel = progressComplete ? statusLabel : statusLabel;
  const questionMeta = parseActiveTaskQuestionDetail(detailText);
  const previewKey = activeTaskQaPreviewCacheKey(task, questionMeta.questionId);
  const qaPreview = previewKey ? state.activeTaskQaPreview[previewKey] || null : null;
  const qaQuestion = qaPreview?.question || questionMeta.question || detailText || "-";
  const qaAnswer = qaPreview?.answer || "-";
  const qaResultPath = qaPreview?.resultPath || task.output_file || "";
  const progressWidth = `${Math.max(0, Math.min(100, pct || 0)).toFixed(1)}%`;
  const rows = execution?.total_questions
    ? ` · 已答 ${execution.answered_questions || 0}/${execution.total_questions}`
    : isTaskActive(task) && progress?.current != null
    ? ` · 结果行≈${progress.current}`
    : (summary.rows != null ? ` · 结果行 ${summary.rows}` : "");
  const acc = summary.accuracy != null ? ` · 准确率 ${percent(summary.accuracy)}` : "";
  const progressText = execution?.total_questions
    ? ` · 第 ${execution.current_question || execution.answered_questions || 0}/${execution.total_questions} 题 · ${Number(execution.pct || 0).toFixed(1)}%`
    : progress?.total
    ? ` · ${progress.current}/${progress.total} · ${Number(progress.pct || 0).toFixed(1)}%`
    : "";
  const etaText = progress?.eta_seconds != null ? ` · ETA ${formatDuration(progress.eta_seconds)}` : "";
  const rateHits = task.log_diagnostics?.rate_limit_hits || [];
  const modelIssueHits = task.log_diagnostics?.model_issue_hits
    || task.log_diagnostics?.model_api_error_hits
    || rateHits;
  const modelIssueCount = task.log_diagnostics?.model_issue_count
    || task.log_diagnostics?.model_api_error_count
    || rateHits.length;
  strip.dataset.taskId = task.id || "";
  strip.dataset.taskKind = task.kind || "";
  strip.classList.toggle("running", isTaskRunningStatus(task) && !progressComplete);
  strip.classList.toggle("succeeded", status === "succeeded" || progressComplete);
  strip.classList.toggle("failed", status === "failed");
  strip.innerHTML = `
    <div class="task-progress-head">
      <div>
        <span class="task-status-pill">${escapeHtml(stageLabel)} · ${escapeHtml(displayStatusLabel)}</span>
        <strong>${escapeHtml(displayTitle)}</strong>
      </div>
      <div class="task-progress-percent">${escapeHtml(pctText)}</div>
    </div>
    <div class="task-progress-meter" aria-label="任务进度">
      <span style="width:${escapeHtml(progressWidth)}"></span>
    </div>
    <div class="task-progress-grid">
      <article><span>当前进度</span><strong>${escapeHtml(countText)}</strong><small>${escapeHtml(unitLabel)}</small></article>
      <article><span>已用</span><strong>${escapeHtml(elapsedValue)}</strong></article>
      <article><span>剩余</span><strong>${escapeHtml(etaValue)}</strong></article>
      <article><span>结果</span><strong>${escapeHtml(summary.accuracy != null ? percent(summary.accuracy) : "等待判分")}</strong></article>
    </div>
    ${isQaTask ? `
      <div class="task-qa-summary">
        <article>
          <span>问题</span>
          <p>${escapeHtml(qaQuestion)}</p>
        </article>
        <article>
          <span>答案</span>
          <p>${escapeHtml(qaAnswer)}</p>
        </article>
        <article>
          <span>结果地址</span>
          <div class="task-qa-summary-path">
            <code>${escapeHtml(qaResultPath || "-")}</code>
            ${qaResultPath ? copyButtonHtml(qaResultPath, "复制") : ""}
          </div>
        </article>
      </div>
    ` : `
      <div class="task-strip-main">
        <span>${escapeHtml(stageLabel)} · ${escapeHtml(displayStatusLabel)}${rows}${acc}${escapeHtml(progressText)}${authoritativeProgressNote ? ` · ${escapeHtml(authoritativeProgressNote)}` : ""}${escapeHtml(etaText)}</span>
        ${modelIssueCount ? `<span class="log-alert">模型/检索异常 ${escapeHtml(modelIssueCount)} 条</span>` : ""}
      </div>
      ${detailText ? `<p class="task-progress-detail">${escapeHtml(detailText)}</p>` : ""}
      ${renderCurrentImportPreview(task)}
      ${modelIssueHits.length ? `
        <div class="log-diagnostic">
          <strong>模型或检索提醒</strong>
          <p>${escapeHtml(modelIssueHits[modelIssueHits.length - 1])}</p>
        </div>
      ` : ""}
      <div class="task-strip-meta">
        <code>${escapeHtml(task.id || "")}</code>
        <code>${escapeHtml(task.output_file || "")}</code>
      </div>
    `}
  `;
  bindCopyButtons("#activeTaskStrip");
  if (isQaTask && task.output_file && questionMeta.questionId && !qaPreview && !state.activeTaskQaPreviewLoading[previewKey]) {
    ensureActiveTaskQaPreview(task, questionMeta.questionId).then((preview) => {
      if (!preview) return;
      const activeStrip = $("activeTaskStrip");
      if (!activeStrip || activeStrip.dataset.taskId !== (task.id || "")) return;
      renderActiveTaskStrip(task);
    }).catch(() => {});
  }
}

function renderGlobalTaskChip(task = null) {
  const chip = $("globalTaskChip");
  const row = $("accountTaskRow");
  if (!chip) return;
  if (!task) {
    renderGlobalBenchmarkBanner(null);
    if (row) row.hidden = false;
    chip.hidden = false;
    chip.removeAttribute("data-task-id");
    chip.removeAttribute("href");
    chip.classList.remove("running", "succeeded", "failed");
    chip.innerHTML = `
      <div class="workspace-task-chip-head">
        <span class="workspace-task-chip-eyebrow">运行任务</span>
        <strong>当前没有任务运行</strong>
      </div>
      <div class="workspace-task-chip-grid">
        <article>
          <span>任务状态</span>
          <strong>等待启动</strong>
        </article>
        <article>
          <span>数据集</span>
          <strong>当前账户</strong>
        </article>
        <article>
          <span>当前进度</span>
          <strong>暂无运行中的评测</strong>
        </article>
      </div>
      <p class="workspace-task-chip-note">启动记忆导入、问答、判分或报告后，这里会显示当前阶段、进度、已用时间和剩余时间。</p>
    `;
    chip.title = "当前没有运行任务 · 启动记忆导入、问答、判分或报告后，这里会显示状态与进展";
    chip.setAttribute("aria-label", "当前没有运行任务，等待启动");
    chip.onclick = null;
    if (row) row.hidden = false;
    return;
  }
  if (!shouldShowGlobalTaskChip(task)) {
    renderGlobalBenchmarkBanner(null);
    chip.hidden = true;
    chip.textContent = "";
    chip.removeAttribute("data-task-id");
    chip.removeAttribute("href");
    chip.onclick = null;
    if (row) row.hidden = true;
    return;
  }
  if (row) row.hidden = false;
  task = taskWithLiveProgress(task);
  const progress = task.progress || {};
  const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
  const stageLabel = taskStageLabel(task.kind || "", task);
  const backendLabel = taskBackendLabel(task);
  const statusLabel = taskStatusLabel(task);
  const progressText = taskProgressLabel(task);
  const authoritativeProgressNote = taskAuthoritativeProgressNote(task, format);
  const elapsedText = task.duration != null
    ? `已用 ${formatDuration(task.duration)}`
    : (progress.elapsed_seconds != null ? `已用 ${formatDuration(progress.elapsed_seconds)}` : "");
  const etaText = progress.eta_seconds != null ? `剩余 ${formatDuration(progress.eta_seconds)}` : "";
  const benchmarkSummaryText = taskLiveBenchmarkSummaryLabel(task, format);
  const activeView = document.body?.dataset?.activeView || "";
  const compactShell = ["openvikingView", "evalView", "judgeView", "runsView"].includes(activeView);
  const datasetLabel = datasetTypeLabel(format || task.dataset_format || "");
  const progressLabel = compactShell
    ? (progressText || benchmarkSummaryText || statusLabel || "运行中")
    : [
      progressText && progressText !== statusLabel ? progressText : "",
      authoritativeProgressNote,
      benchmarkSummaryText,
      elapsedText,
      etaText,
    ].filter(Boolean).join(" · ");
  const noteText = compactShell
    ? [authoritativeProgressNote, elapsedText, etaText].filter(Boolean).join(" · ")
    : [backendLabel, authoritativeProgressNote, benchmarkSummaryText, elapsedText, etaText].filter(Boolean).join(" · ");
  rememberBenchmarkRecord(task, format);
  const targetView = benchmarkViewForTask({...task, dataset_format: format}, "runsView");
  const taskKind = task.kind || "";
  const scrollTarget = taskShouldUseLocomoTaskStrip(taskKind, task, format)
    ? "activeTaskStrip"
    : taskUi(taskKind, {...task, dataset_format: format}).progressText;
  renderGlobalBenchmarkBanner({...task, dataset_format: format});
  chip.hidden = false;
  chip.dataset.taskId = task.id || "";
  chip.href = `/?ui_refresh=${UI_REFRESH_VERSION}&view=${targetView}`;
  chip.innerHTML = `
    <div class="workspace-task-chip-head">
      <span class="workspace-task-chip-eyebrow">运行任务</span>
      <strong>${escapeHtml(stageLabel || statusLabel || "运行任务")}</strong>
    </div>
    <div class="workspace-task-chip-grid">
      <article>
        <span>任务状态</span>
        <strong>${escapeHtml(statusLabel || "运行中")}</strong>
      </article>
      <article>
        <span>数据集</span>
        <strong>${escapeHtml(datasetLabel || "当前数据集")}</strong>
      </article>
      <article>
        <span>当前进度</span>
        <strong>${escapeHtml(progressLabel || "运行中")}</strong>
      </article>
    </div>
    ${noteText ? `<p class="workspace-task-chip-note">说明：${escapeHtml(noteText)}</p>` : ""}
  `;
  chip.title = [taskDisplayTitle(task, format) || stageLabel, statusLabel, datasetLabel, progressLabel, noteText].filter(Boolean).join(" · ");
  chip.setAttribute("aria-label", [stageLabel || statusLabel || "运行任务", statusLabel, datasetLabel, progressLabel].filter(Boolean).join("，"));
  chip.onclick = (event) => {
    event.preventDefault();
    showView(targetView);
    refreshTasks().catch((e) => toast(e.message));
    setTimeout(() => {
      $(scrollTarget)?.scrollIntoView({behavior: "smooth", block: "center"});
    }, 120);
  };
  if (row) row.hidden = chip.hidden;
}

function shouldShowGlobalTaskChip(task = {}) {
  const kind = task.kind || "";
  const activeView = document.body?.dataset?.activeView || document.querySelector(".view-panel.active")?.id || "";
  if (isMemoryImportKind(kind) && !["openvikingView", "runsView"].includes(activeView)) return false;
  return true;
}

function renderGlobalBenchmarkBanner(task = null) {
  const banner = $("globalBenchmarkBanner");
  if (!banner) return;
  const clear = () => {
    banner.hidden = true;
    banner.innerHTML = "";
  };
  if (!task?.id) {
    clear();
    return;
  }
  const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
  if (!isGenericBenchmarkQaTask(task, format)) {
    clear();
    return;
  }
  task = taskWithLiveProgress(task);
  const normalized = normalizeDatasetFormat(format);
  const key = genericBenchmarkKeyForFormat(normalized);
  const config = key ? benchmarkConfig(key) : null;
  const summary = state.runningBenchmarkSummaries[task.id] || {};
  const summaryJson = summary.summary_json || {};
  const scope = benchmarkQuestionScope(task, normalized);
  const progress = task.progress || {};
  const rows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const avgInjection = summary.avg_memory_injection_time_s ?? summaryJson.avg_memory_injection_time_s;
  const avgQa = summary.avg_qa_time_s ?? summaryJson.avg_qa_time_s ?? summary.avg_time;
  const freshness = benchmarkRunningSummaryFreshness(task, summary);
  const genericFailureHits = Array.isArray(task?.log_diagnostics?.generic_failure_hits) ? task.log_diagnostics.generic_failure_hits : [];
  const latestGenericFailure = String(genericFailureHits[genericFailureHits.length - 1] || "").trim();
  const normalizedFailure = /arrearage|access denied|overdue-payment/i.test(latestGenericFailure)
    ? "模型服务返回 Arrearage / Access denied，当前尾部导入失败和 pending_async 升高大概率由此触发。"
    : latestGenericFailure;
  const phase = progress?.phase?.startsWith("commit")
    ? "写入/索引"
    : (progress?.phase === "qa" ? "问答" : (progress?.phase === "import" ? "导入" : (progress?.phase || "运行中")));
  const scopeLabel = scope?.total
    ? `第 ${scope.current || scope.answered}/${scope.total} 题 · 已答 ${scope.answered}/${scope.total}`
    : (taskProgressLabel(task) || taskStatusLabel(task));
  const detailParts = [
    scopeLabel,
    phase ? `阶段 ${phase}` : "",
    rows > 0 ? `结果 ${formatInt(rows)} 行` : "",
    avgInjection != null ? `注入 ${formatSecondsMetric(avgInjection)}` : "",
    avgQa != null ? `QA ${formatSecondsMetric(avgQa)}` : "",
    freshness.label ? `刷新 ${freshness.label}` : "",
  ].filter(Boolean);
  const warningText = [
    taskAuthoritativeProgressNote(task, normalized),
    freshness.isStale ? "运行态汇总刷新偏旧，页面里看到的可能是停住后的旧进度。" : "",
    normalizedFailure,
  ].filter(Boolean).join(" · ");
  const targetView = benchmarkViewForTask({...task, dataset_format: normalized}, "runsView");
  const liveHref = normalized === "hotpotqa" ? "/generated-reports/hotpotqa_echomemory_live_current.html" : "";
  banner.hidden = false;
  banner.innerHTML = `
    <div class="benchmark-banner-main">
      <div class="benchmark-banner-title">${escapeHtml(config?.label || datasetTypeLabel(normalized) || "运行中评测")}</div>
      <div class="benchmark-banner-detail">${escapeHtml(detailParts.join(" · ") || taskStatusLabel(task) || "运行中")}</div>
      ${warningText ? `<div class="benchmark-banner-detail bad-text">${escapeHtml(warningText)}</div>` : ""}
    </div>
    <div class="benchmark-banner-actions">
      <a class="benchmark-banner-link" href="/?ui_refresh=${UI_REFRESH_VERSION}&view=${escapeHtml(targetView)}">查看进度</a>
      ${liveHref ? `<a class="benchmark-banner-link" href="${escapeHtml(liveHref)}" target="_blank" rel="noreferrer">Live 报告</a>` : ""}
    </div>
  `;
}

function renderGlobalBenchmarkBannerFromRun(run = null) {
  const banner = $("globalBenchmarkBanner");
  if (!banner) return;
  if (!run?.id) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  const format = normalizeDatasetFormat(benchmarkFormatFromRecord(run));
  const key = genericBenchmarkKeyForFormat(format);
  const config = key ? benchmarkConfig(key) : null;
  if (!config) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  const summary = run.summary || {};
  const summaryJson = summary.summary_json || {};
  const rows = Number(summary.rows ?? summaryJson.rows ?? summaryJson.count ?? 0);
  const answerEm = summary.official_answer_em ?? summaryJson.official_answer_em ?? summary.answer_em ?? summaryJson.answer_em;
  const answerF1 = summary.official_answer_f1 ?? summaryJson.official_answer_f1 ?? summary.answer_f1 ?? summaryJson.answer_f1;
  const status = String(run.status || "").trim().toLowerCase();
  const statusLabel = taskStatusLabel({status});
  const endedAt = formatDateTime(run.ended_at || run.updated_at || run.created_at || "");
  const detailParts = [
    statusLabel || "最近终态",
    rows > 0 ? `${formatInt(rows)} 行` : "",
    (answerEm != null || answerF1 != null) ? `EM/F1 ${answerEm == null ? "-" : percent(answerEm)} / ${answerF1 == null ? "-" : percent(answerF1)}` : "",
    endedAt ? `结束 ${endedAt}` : "",
  ].filter(Boolean);
  const warningText = status === "failed"
    ? "最近一次任务已失败结束；这里保留最近终态，点进去可直接看固定报告和诊断。"
    : (status === "interrupted" || status === "cancelled" || status === "canceled")
    ? "最近一次任务已中断；这里保留最近终态，点进去可继续排查。"
    : "当前没有运行中的同类任务；这里显示最近一次终态。";
  const targetView = config.view || "runsView";
  const completedHref = format === "hotpotqa"
    ? "/generated-reports/hotpotqa_echomemory_completed_current.html"
    : "";
  banner.hidden = false;
  banner.innerHTML = `
    <div class="benchmark-banner-main">
      <div class="benchmark-banner-title">${escapeHtml(config.label || datasetTypeLabel(format) || "最近评测")}</div>
      <div class="benchmark-banner-detail">${escapeHtml(detailParts.join(" · ") || "最近终态")}</div>
      <div class="benchmark-banner-detail">${escapeHtml(warningText)}</div>
    </div>
    <div class="benchmark-banner-actions">
      <a class="benchmark-banner-link" href="/?ui_refresh=${UI_REFRESH_VERSION}&view=${escapeHtml(targetView)}">查看页面</a>
      ${completedHref ? `<a class="benchmark-banner-link" href="${escapeHtml(completedHref)}" target="_blank" rel="noreferrer">固定报告</a>` : ""}
    </div>
  `;
}

async function refreshTasks() {
  const data = await api("/api/tasks");
  const allTasks = (data.tasks || []).map(stampTaskSnapshot);
  const activeView = activeViewId();
  const scopedTasks = currentAccountOnlyEnabled("taskCurrentAccountOnly")
    ? allTasks.filter(matchesCurrentAccount)
    : allTasks;
  const runningTasks = allTasks.filter(isTaskActive);
  const visibleInCurrentPanel = (task) => {
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    return taskVisibleInCurrentTaskPanel(task, format, activeView);
  };
  const runningTasksForView = runningTasks.filter(visibleInCurrentPanel);
  const visibleTasksForPanel = scopedTasks.filter(visibleInCurrentPanel);
  const runningVisibleIds = new Set(runningTasksForView.map((task) => task.id));
  const tasks = [];
  for (const task of visibleTasksForPanel) {
    if (!runningVisibleIds.has(task.id)) continue;
    tasks.push(task);
    if (tasks.length >= 8) break;
  }
  if (tasks.length < 8) {
    for (const task of visibleTasksForPanel) {
      if (runningVisibleIds.has(task.id)) continue;
      tasks.push(task);
      if (tasks.length >= 8) break;
    }
  }
  const runningAnyTask = scopedTasks.find((task) => isTaskActive(task) && visibleInCurrentPanel(task))
    || runningTasksForView[0]
    || (activeView === "evalView" ? null : (scopedTasks.find((task) => isTaskActive(task)) || runningTasks[0]));
  state.currentRunningTask = runningAnyTask || null;
  updateStopActionButtons(runningTasks);
  if (runningAnyTask) ensureTaskPolling(runningAnyTask, runningAnyTask.kind || "");
  runningTasksForView.forEach((task) => ensureTaskPolling(task, task.kind || ""));
  const benchmarkTasksToHydrate = new Map();
  runningTasksForView.forEach((task) => {
    benchmarkTasksToHydrate.set(task.id, task);
  });
  if (runningAnyTask?.id) benchmarkTasksToHydrate.set(runningAnyTask.id, runningAnyTask);
  await Promise.all([...benchmarkTasksToHydrate.values()].map(async (task) => {
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    if (!isGenericBenchmarkQaTask(task, format)) return Promise.resolve(null);
    await ensureGenericBenchmarkExecutionProgress(task, format).catch(() => null);
    return loadRunningBenchmarkSummary(task, format).catch(() => null);
  }));
  renderGlobalTaskChip(runningAnyTask || null);
  if (activeView === "evalView" && isMemoryImportKind(state.currentImportTask?.kind || "")) {
    state.currentImportTask = null;
  }
  const importTask = activeView === "evalView"
    ? null
    : (latestMemoryImportTask(tasks) || latestAnyMemoryImportTask(allTasks));
  if (importTask) {
    state.currentImportTask = importTask;
    if (isTaskActive(importTask)) syncImportTaskFields(importTask);
    if (isTaskActive(importTask)) ensureTaskPolling(importTask, importTask.kind || "");
    renderImportPaths(importTask);
    if (taskShouldUseLocomoTaskStrip(importTask.kind || "", importTask, enrichTaskDatasetFormat(importTask, state.taskDatasetFormats[importTask.id] || ""))) {
      renderActiveTaskStrip(importTask);
    }
    updateProgress(importTask, importTask.kind || state.taskKind);
    renderImportDiagnostics(importTask);
    const importLogBox = $("importLogBox");
    if (!isTaskActive(importTask) && importLogBox && /日志会显示在这里|这个任务还没有写出日志/.test(String(importLogBox.textContent || "").trim())) {
      loadTaskLogIntoBox(importTask, importTask.kind || "openviking_import").catch(() => {});
    }
  } else if (activeView !== "evalView") {
    const trackedActiveImport = [state.currentImportTask, state.currentRunningTask, state.currentLocomoTask]
      .find((task) => task?.id && isMemoryImportKind(task.kind || "") && isTaskActive(task));
    if (trackedActiveImport) {
      state.currentImportTask = trackedActiveImport;
      renderImportPaths(trackedActiveImport);
      renderImportDiagnostics(trackedActiveImport);
      updateProgress(trackedActiveImport, trackedActiveImport.kind || locomoImportTaskKind());
    } else {
      const fallbackImport = (
        isMemoryImportKind(state.currentImportTask?.kind || "") && !isTaskActive(state.currentImportTask)
          ? state.currentImportTask
          : null
      ) || await latestMemoryImportRecord().catch(() => null);
      if (fallbackImport) {
        state.currentImportTask = fallbackImport;
        renderImportPaths(fallbackImport);
        renderImportDiagnostics(fallbackImport);
        updateProgress(fallbackImport, fallbackImport.kind || locomoImportTaskKind());
      }
    }
    await loadLatestImportLogFallback().catch((error) => {
      const box = $("importLogBox");
      if (box) box.textContent = `历史导入日志加载失败：${error.message || error}`;
    });
  }
  const runningLocomoTask = tasks.find((task) => {
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    return isTaskActive(task) && isLocomoTaskOutput(task.kind || "", task, format) && taskVisibleInActiveTaskStrip(task, format, activeView);
  });
  if (runningLocomoTask) {
    const format = enrichTaskDatasetFormat(runningLocomoTask, state.taskDatasetFormats[runningLocomoTask.id] || "");
    state.currentLocomoTask = runningLocomoTask;
    state.taskId = runningLocomoTask.id;
    state.taskKind = runningLocomoTask.kind || "openviking_qa";
    rememberTaskDatasetFormat(runningLocomoTask.id, format);
    renderActiveTaskStrip(runningLocomoTask);
    updateProgress(runningLocomoTask, state.taskKind);
    if (runningLocomoTask.output_file) {
      rememberEvidenceScope(runningLocomoTask, runningLocomoTask.output_file);
      markLocomoOutputFile(runningLocomoTask.output_file);
    }
    ensureTaskPolling(runningLocomoTask, state.taskKind);
  } else if (isTaskActive(state.currentLocomoTask)) {
    state.currentLocomoTask = null;
  } else if (!taskVisibleInActiveTaskStrip({kind: $("activeTaskStrip")?.dataset.taskKind || ""}, "", activeView)) {
    renderActiveTaskStrip(null);
  }
  const recentTasks = tasks.filter((task) => {
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    return taskVisibleInCurrentTaskPanel(task, format, activeView);
  });
  const recentTaskList = $("recentTaskList");
  if (activeView === "evalView" && recentTaskList) {
    let recentEvalRuns = [];
    let recentEvalRunsError = null;
    if (!recentTasks.length && !state.tasksHydrating) {
      try {
        recentEvalRuns = await loadRecentEvalQaRunsForTaskPanel();
      } catch (error) {
        recentEvalRunsError = error;
      }
    }
    recentTaskList.innerHTML = recentTasks.length ? recentTasks.map((task) => {
      const summary = task.summary || {};
      const acc = summary.accuracy == null ? "待判分" : percent(summary.accuracy);
      const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
      const execution = taskExecutionProgress(task, format);
      const account = recordAccount(task) || "default";
      const stage = taskStageLabel(task.kind || "", task);
      const statusLabel = taskDisplayStatusLabel(task, format);
      const displayTitle = taskDisplayTitle(task, format) || (task.name || task.id || "-");
      const liveTask = taskWithLiveProgress(task);
      const progress = liveTask.progress;
      const rows = execution?.total_questions
        ? `${execution.current_question || execution.answered_questions || 0}/${execution.total_questions} · 已答 ${execution.answered_questions || 0}`
        : isTaskActive(task) && progress?.current != null
        ? `≈${progress.current}`
        : (summary.rows ?? "-");
      const authoritativeProgressNote = taskAuthoritativeProgressNote(task, format);
      const progressText = execution?.total_questions
        ? ` · 第 ${execution.current_question || execution.answered_questions || 0}/${execution.total_questions} 题`
        : progress?.total
        ? ` · ${progress.current}/${progress.total} · ${Number(progress.pct || 0).toFixed(1)}%`
        : "";
      const etaText = progress?.eta_seconds != null ? ` · ETA ${formatDuration(progress.eta_seconds)}` : "";
      const taskClass = isTaskRunningStatus(task) ? "task compact-task running-task" : "task compact-task";
      return `
        <article class="${taskClass}" data-task-id="${escapeHtml(task.id || "")}" data-output-file="${escapeHtml(task.output_file || "")}" data-dataset-format="${escapeHtml(format)}">
          <div>
            <strong>${escapeHtml(displayTitle)}</strong>
            <small>${escapeHtml(stage)} · ${escapeHtml(statusLabel)} · ${escapeHtml(account)} · ${escapeHtml(displayDatasetFormatForTask(task, format))} · rows ${escapeHtml(rows)} · ${escapeHtml(acc)}${escapeHtml(progressText)}${authoritativeProgressNote ? ` · ${escapeHtml(authoritativeProgressNote)}` : ""}${escapeHtml(etaText)}</small>
            ${task.log_diagnostics?.model_issue_count ? `<small class="bad-text">模型/检索异常 ${escapeHtml(task.log_diagnostics.model_issue_count)} 条</small>` : ""}
          </div>
          <code>${escapeHtml(task.output_file || task.run_dir || "")}</code>
        </article>
      `;
    }).join("") : recentEvalRuns.length
      ? recentEvalRuns.map(renderEvalQaRunFallbackCard).join("")
      : `<p class="muted-list-note${recentEvalRunsError ? " bad-text" : ""}">${
        state.tasksHydrating
          ? "正在恢复问答任务列表..."
          : recentEvalRunsError
          ? `问答任务列表读取失败：${escapeHtml(recentEvalRunsError.message || recentEvalRunsError)}`
          : "当前账户暂无问答任务。"
      }</p>`;
    const recentRunMap = new Map(recentEvalRuns.map((run) => [runCompareKey(run), run]));
    document.querySelectorAll("#recentTaskList .task").forEach((card) => {
      card.addEventListener("click", () => {
        const run = recentRunMap.get(card.dataset.runKey || "");
        if (run) {
          if (run.output_file) {
            rememberEvidenceScope(run, run.output_file);
            markLocomoOutputFile(run.output_file);
            state.selectedRunRecord = run;
            refreshResult().catch((e) => toast(e.message));
          }
          return;
        }
        const output = card.dataset.outputFile || "";
        const task = recentTasks.find((item) => item.id === card.dataset.taskId);
        const format = normalizeDatasetFormat(card.dataset.datasetFormat || taskDatasetFormat(task || {}, ""));
        if (output) {
          if (isLocomoTaskOutput(task?.kind || "", task || {}, format)) {
            markLocomoOutputFile(output);
            refreshResult().catch((e) => toast(e.message));
          } else if (format) {
            markDatasetOutputFile(output, format);
          } else {
            toast("这个任务不是 LoCoMo 结果，已保持当前 LoCoMo 判分输入不变");
          }
        }
        if (task) {
          enrichTaskDatasetFormat(task, format);
          if (taskShouldUseLocomoTaskStrip(task.kind || "", task, format)) {
            renderActiveTaskStrip(task);
          } else {
            updateProgress({...task, dataset_format: format}, task.kind);
          }
          loadTaskLogIntoBox(task, task.kind).catch((e) => toast(e.message));
          const targetView = benchmarkViewForTask({...task, dataset_format: format}, "");
          if (targetView) showView(targetView, {preserveScroll: true});
        }
      });
    });
  } else if (recentTaskList) {
    recentTaskList.innerHTML = "";
  }
  if (activeView === "openvikingView") {
    renderRecentLocomoRuns().catch(() => {});
  }
  if (isStandaloneBenchmarkView(activeView)) {
    const activeFormat = datasetFormatForView(activeView);
    const runningBenchmarkTask = (visibleTasksForPanel || []).find((task) => {
      const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
      return isTaskActive(task) && normalizeDatasetFormat(format) === activeFormat;
    });
    const runningBenchmarkTaskAnyAccount = runningBenchmarkTask || (runningTasks || []).find((task) => {
      const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
      return normalizeDatasetFormat(format) === activeFormat;
    });
    if (runningBenchmarkTask) {
      const taskAccount = recordAccount(runningBenchmarkTask) || "default";
      const liveTask = taskWithLiveProgress(runningBenchmarkTask);
      const runningSummary = await loadRunningBenchmarkSummary(runningBenchmarkTask, activeFormat).catch(() => null);
      renderGenericBenchmarkRunningSummary(runningBenchmarkTask, activeFormat, {
        account: taskAccount,
        summary: runningSummary,
      });
      const benchmarkKey = genericBenchmarkKeyForFormat(activeFormat);
      if (benchmarkKey) renderGenericRunningStatus(benchmarkKey, runningBenchmarkTask, runningSummary);
      updateProgress(liveTask, liveTask.kind || state.taskKind);
    } else if (runningBenchmarkTaskAnyAccount) {
      const taskAccount = recordAccount(runningBenchmarkTaskAnyAccount) || "default";
      const liveTask = taskWithLiveProgress(runningBenchmarkTaskAnyAccount);
      const activeBenchmarkLabel = benchmarkConfig(genericBenchmarkKeyForFormat(activeFormat))?.label || datasetTypeLabel(activeFormat);
      const runningSummary = await loadRunningBenchmarkSummary(runningBenchmarkTaskAnyAccount, activeFormat).catch(() => null);
      renderGenericBenchmarkRunningSummary(runningBenchmarkTaskAnyAccount, activeFormat, {
        account: taskAccount,
        summary: runningSummary,
        note: `当前页面账户是 ${currentAccount()}，但运行中的 ${activeBenchmarkLabel} 任务属于账户 ${taskAccount}。这里先显示只读进度；切换到账户 ${taskAccount} 后可查看同账户日志和结果。`,
      });
      const benchmarkKey = genericBenchmarkKeyForFormat(activeFormat);
      if (benchmarkKey) renderGenericRunningStatus(benchmarkKey, runningBenchmarkTaskAnyAccount, runningSummary);
      updateProgress(liveTask, liveTask.kind || state.taskKind);
    }
  }
  if (isStandaloneBenchmarkView(activeView) && !runningTasksForView.length) {
    const idleFormat = datasetFormatForView(activeView);
    const launchError = genericBenchmarkLaunchError(idleFormat);
    if (launchError) {
      renderGenericBenchmarkLaunchError(idleFormat, launchError);
      renderGlobalBenchmarkBanner(null);
      updateWorkflowGuide();
      return;
    }
    const latestRun = await restoreLatestBenchmarkRunForView(activeView, visibleTasksForPanel).catch(() => {
      renderIdleBenchmarkProgress(datasetFormatForView(activeView), null);
      return null;
    });
    renderGlobalBenchmarkBannerFromRun(latestRun || null);
  }
  updateWorkflowGuide();
}

function currentImportNamespace() {
  const cfg = state.config || {};
  const workspace = ($("ovWorkspace")?.value.trim()) || cfg.openviking_workspace || cfg.workspace || "";
  const account = $("ovAccount")?.value.trim() || cfg.account || "default";
  const user = $("memoryUserId")?.value.trim() || readAccountConfig(currentAccount()).memoryUserId || "default";
  const agent = $("memoryAgentId")?.value.trim() || readAccountConfig(currentAccount()).memoryAgentId || "default";
  const sampleValue = $("importSample")?.value || "all";
  const sampleText = $("importSample")?.selectedOptions?.[0]?.textContent || "";
  const sampleMatch = sampleText.match(/·\s*([^·]+?)\s*·/);
  const sampleId = sampleValue === "all" ? "" : (sampleMatch ? sampleMatch[1].trim() : "");
  return {workspace, account, user, agent, sampleId, sampleValue};
}

function renderImportReadinessPanel(task = null) {
  const target = $("importReadinessPanel");
  if (!target) return;
  const ns = currentImportNamespace();
  const taskConfig = task?.meta?.config || {};
  const taskScope = task ? importScopeFromTask(task, ns) : ns;
  const backend = normalizeMemoryBackend(taskConfig.backend || (task?.kind === "echomemory_import" ? "echomemory" : currentMemoryBackend()));
  const backendLabel = memoryBackendLabel(backend);
  const dataset = currentLocomoDataset();
  const workspace = taskConfig.workspace
    || (taskConfig.workspace_mode === "new_each_import" ? taskConfig.openviking_workspace : "")
    || ns.workspace;
  const account = taskScope.account || ns.account;
  const lastImport = readLastImport();
  const imported = state.importedMemoryStatus || {};
  const importRunning = isMemoryImportKind(task?.kind || state.currentImportTask?.kind || "") && (task?.status || state.currentImportTask?.status) === "running";
  const importScope = currentImportSampleScope();
  const sampleLabel = importScope.isAll
    ? "全部对话"
    : (importScope.smoke
      ? importScope.label
      : `${importScope.label}${importScope.questionCount ? ` · ${formatInt(importScope.questionCount)} 题` : ""}`);
  const sampleDetail = dataset
    ? (
      importScope.isAll
        ? `全部对话样本；${formatInt(dataset.samples || 0)} 个样本 / ${formatInt(dataset.questions || 0)} 题。`
        : (importScope.smoke
          ? `仅验证 1 个 session 的注入链路。`
          : `${importScope.label}${importScope.questionCount ? ` · ${formatInt(importScope.questionCount)} 题` : ""}。`)
    )
    : "";
  const workspaceHint = workspaceBackendNameHint(workspace);
  const cards = [
    {
      label: "导入范围",
      value: sampleLabel,
      detail: sampleDetail,
      tone: dataset ? "ok" : "warn",
    },
    {
      label: "目录",
      value: compactPath(workspace || "请到系统配置确认目录", 42, 24),
      detail: "",
      tone: workspace ? "ok" : "warn",
    },
  ];
  target.innerHTML = cards.map((card) => `
    <article class="${escapeHtml(card.tone || "")}">
      <span>${escapeHtml(card.label)}</span>
      <strong>${escapeHtml(card.value || "-")}</strong>
      ${card.detail ? `<p>${escapeHtml(card.detail)}</p>` : ""}
    </article>
  `).join("");
}

function importDiagnosticItems(task = null) {
  const diagnostics = task?.log_diagnostics || {};
  const progress = task?.progress || {};
  const pushItems = (label, severity, values = []) => {
    return (Array.isArray(values) ? values : [])
      .filter((text) => String(text || "").trim())
      .map((text) => ({label, severity, text: String(text)}));
  };
  const items = [
    ...pushItems("模型限流", "warn", diagnostics.rate_limit_hits),
    ...pushItems("模型调用失败", "bad", diagnostics.model_api_error_hits),
    ...pushItems("检索重试", "warn", diagnostics.retrieval_retry_hits),
    ...pushItems("Embedding 超时", "warn", diagnostics.embedding_timeout_hits),
    ...pushItems("Embedding 熔断", "bad", diagnostics.embedding_circuit_breaker_hits),
  ];
  const genericHits = pushItems("任务异常", "bad", diagnostics.generic_failure_hits);
  for (const item of genericHits) {
    if (!items.some((existing) => existing.text === item.text)) items.push(item);
  }
  const progressWarnings = pushItems("运行告警", "warn", progress.warnings);
  for (const item of progressWarnings) {
    if (!items.some((existing) => existing.text === item.text)) items.push(item);
  }
  const progressDetail = String(progress.detail || "").trim();
  if (progressDetail && progressWarnings.length && !items.some((existing) => existing.text === progressDetail)) {
    items.push({label: "运行阶段", severity: "warn", text: progressDetail});
  }
  if (task?.status === "failed") {
    const failureText = task.error || task.message || task.progress?.detail || "";
    if (failureText) items.unshift({label: "导入任务失败", severity: "bad", text: failureText});
  }
  return items;
}

function renderImportDiagnostics(task = state.currentImportTask || null) {
  const panel = $("importDiagnosticPanel");
  if (!panel) return;
  const items = importDiagnosticItems(task);
  if (!task || !items.length) {
    panel.innerHTML = `
      <article class="log-diagnostic ok">
        <strong>暂无异常</strong>
      </article>
    `;
    return;
  }
  const diagnostics = task.log_diagnostics || {};
  const counts = [
    ["限流", diagnostics.rate_limit_count],
    ["模型/API", diagnostics.model_api_error_count],
    ["检索重试", diagnostics.retrieval_retry_count],
    ["Embedding 超时", diagnostics.embedding_timeout_count],
    ["熔断", diagnostics.embedding_circuit_breaker_count],
  ].filter(([, value]) => Number(value || 0) > 0);
  const summaryText = counts.map(([label, value]) => `${label} ${value}`).join(" · ") || taskStatusLabel(task);
  panel.innerHTML = `
    <div class="import-diagnostic-summary">
      <strong>检测到 ${escapeHtml(items.length)} 条异常线索</strong>
      <span>${escapeHtml(summaryText)}</span>
    </div>
    ${items.slice(-8).reverse().map((item) => `
      <article class="log-diagnostic ${escapeHtml(item.severity || "warn")}">
        <strong>${escapeHtml(item.label)}</strong>
        <p>${escapeHtml(item.text)}</p>
      </article>
    `).join("")}
  `;
}

function renderImportPaths(task = null) {
  const ns = currentImportNamespace();
  const lastImport = readLastImport();
  const taskConfig = task?.meta?.config || {};
  const taskScope = task ? importScopeFromTask(task, ns) : ns;
  const realWorkspace = taskConfig.workspace || (taskConfig.workspace_mode === "new_each_import" ? taskConfig.openviking_workspace : "");
  const backend = normalizeMemoryBackend(
    taskConfig.backend
    || taskScope.backend
    || lastImport.backend
    || (task?.kind === "echomemory_import" ? "echomemory" : currentMemoryBackend())
  );
  const backendMatchesCurrent = backend === currentMemoryBackend();
  if (realWorkspace && backendMatchesCurrent && $("ovWorkspace")) $("ovWorkspace").value = realWorkspace;
  if (realWorkspace && backendMatchesCurrent && $("memoryWorkspace")) $("memoryWorkspace").value = realWorkspace;
  const workspace = realWorkspace
    || (task ? (taskScope.workspace || "") : "")
    || (normalizeMemoryBackend(lastImport.backend || "") === backend ? String(lastImport.workspace || "").trim() : "")
    || ns.workspace;
  const summaryPath = task?.output_file || lastImport.output_file || "";
  const importFolder = summaryPath ? dirname(summaryPath) : "";
  const account = taskScope.account || lastImport.account || ns.account;
  const hasWorkspace = workspace && !workspace.includes("自动生成");
  const samplePattern = ns.sampleId ? `*${ns.sampleId}*` : "*";
  const importKind = task?.kind && isMemoryImportKind(task.kind) ? task.kind : importTaskKindForBackend(backend);
  const importScript = importScriptForBackend(backend);
  const importStageLabel = taskStageLabel(importKind);
  const backendRoot = hasWorkspace ? storageRootForBackend(workspace, account, backend) : "";
  const effectiveLogFile = task?.log_file || runLogPathFromRecord(lastImport) || "";
  const rows = [
    {label: "当前进度", value: importStageLabel, copy: false, open: false},
    {label: "记忆目录", value: backendRoot || workspace, copy: true, open: true},
    {label: "导入脚本", value: importScript, copy: true, open: true},
    {label: "日志文件", value: effectiveLogFile, copy: true, open: true},
    {label: "摘要文件", value: summaryPath, copy: true, open: true},
  ].filter((item) => item.value);
  $("importPathList").innerHTML = rows.map((item) => `
    <article class="path-row">
      <span>${escapeHtml(item.label)}</span>
      <code>${escapeHtml(item.value)}</code>
      ${item.copy || item.open ? `
        <div class="path-row-actions">
          ${item.copy ? `<button class="path-copy" type="button" data-copy="${escapeHtml(item.value)}">复制</button>` : ""}
          ${item.open ? `<button class="path-open" type="button" data-path="${escapeHtml(item.value)}">打开</button>` : ""}
        </div>
      ` : ""}
    </article>
  `).join("") || "<p>导入后会显示记忆目录、导入脚本、日志文件和摘要文件。</p>";
  bindCopyButtons("#importPathList");
  bindOpenButtons("#importPathList");
  const displayTask = task || {
    kind: importKind,
    meta: {config: {workspace, account, backend}},
    output_file: summaryPath,
    log_file: effectiveLogFile,
  };
  renderImportReadinessPanel(displayTask);
}

async function refreshImportedMemories() {
  const lastImport = readLastImport();
  const taskWorkspace = state.currentImportTask?.meta?.config?.workspace || "";
  const currentBackend = currentMemoryBackend();
  const activeTaskBackend = normalizeMemoryBackend(state.currentImportTask?.meta?.config?.backend || "");
  const lastImportBackend = normalizeMemoryBackend(lastImport.backend || "");
  let workspace = configuredWorkspaceForBackend(currentBackend);
  if (!workspace && taskWorkspace && activeTaskBackend === currentBackend) {
    workspace = taskWorkspace;
  }
  if (!workspace && lastImport.workspace && lastImportBackend === currentBackend) {
    workspace = lastImport.workspace;
  }
  const account = state.currentImportTask?.meta?.config?.account || $("ovAccount").value.trim() || lastImport.account || "default";
  const sampleId = currentImportNamespace().sampleId;
  if (!workspace) {
    $("importedMemoryList").innerHTML = "<p>当前会在导入时自动生成新的目录。请先导入一次。</p>";
    state.importedMemoryStatus = null;
    updateWorkflowGuide();
    refreshLocomoFlowStatus(true).catch(() => {});
    return;
  }
  if (isMemoryImportKind(state.currentImportTask?.kind) && isTaskActive(state.currentImportTask)) {
    const activeAccountRoot = storageRootForBackend(workspace, account, currentBackend);
    $("importedMemoryList").innerHTML = `
      <article class="path-row">
        <span>Workspace 根目录</span>
        <code>${escapeHtml(workspace)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(workspace)}">复制</button>
      </article>
      ${activeAccountRoot ? `
      <article class="path-row">
        <span>${escapeHtml(currentBackend === "echomemory" ? "账户存储根" : "账户目录")}</span>
        <code>${escapeHtml(activeAccountRoot)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(activeAccountRoot)}">复制</button>
      </article>
      ` : ""}
      <p>导入还在进行中。这里只显示当前账户的结果。</p>
    `;
    bindCopyButtons("#importedMemoryList");
    state.importedMemoryStatus = {
      workspace,
      account,
      sample_id: sampleId,
      session_count: 0,
      summary_count: 0,
      complete_count: 0,
    };
    updateWorkflowGuide();
    refreshLocomoFlowStatus(true).catch(() => {});
    return;
  }
  const backend = normalizeMemoryBackend(state.currentImportTask?.meta?.config?.backend || lastImport.backend || currentMemoryBackend());
  const data = await api(`/api/memory-imported?backend=${encodeURIComponent(backend)}&workspace=${encodeURIComponent(workspace)}&account=${encodeURIComponent(account)}&sample=${encodeURIComponent(sampleId)}`);
  const sessions = data.sessions || [];
  const summaries = data.summaries || [];
  const completeCount = summaries.filter((item) => String(item.integrity || "").toLowerCase() === "complete").length;
  state.importedMemoryStatus = {
    workspace,
    account,
    sample_id: sampleId,
    session_count: sessions.length,
    summary_count: summaries.length,
    complete_count: completeCount,
  };
  updateWorkflowGuide();
  refreshLocomoFlowStatus(true).catch(() => {});
  const sessionRows = sessions.map((item) => `
    <article class="memory-hit imported-memory-card">
      <div class="imported-memory-head">
        <strong>${escapeHtml(item.session_id)}</strong>
        <span class="imported-memory-badge">历史文件 ${escapeHtml(item.history_files ?? 0)}</span>
      </div>
      <p class="imported-memory-meta">${escapeHtml(item.updated_at || "未记录更新时间")}</p>
      <p class="imported-memory-path"><code>${escapeHtml(item.path || "")}</code></p>
    </article>
  `).join("");
  const summaryRows = summaries.map((item) => {
    const extracted = item.memories_extracted || {};
    return `
      <article class="memory-hit imported-memory-card imported-memory-card-summary">
        <div class="imported-memory-head">
          <strong>${escapeHtml(item.sample_id || "-")} · ${escapeHtml(item.session_id || "-")}</strong>
          <span class="imported-memory-badge">${escapeHtml(item.integrity || "-")}</span>
        </div>
        <p class="imported-memory-meta">${escapeHtml(item.updated_at || "未记录更新时间")}</p>
        <div class="imported-memory-stats">
          <span>对话 ${escapeHtml(item.submitted_messages ?? "-")} / ${escapeHtml(item.expected_messages ?? "-")}</span>
          <span>记忆 ${escapeHtml(extracted.total ?? "-")}</span>
        </div>
        <p class="imported-memory-path"><code>${escapeHtml(item.summary_path || "")}</code></p>
      </article>
    `;
  }).join("");
  const workspaceRoot = data.workspace || workspace;
  const accountRoot = data.account_path || data.memory_root || storageRootForBackend(workspaceRoot, account, data.backend || backend);
  $("importedMemoryList").innerHTML = `
    <article class="path-row">
      <span>Workspace 根目录</span>
      <code>${escapeHtml(workspaceRoot || "")}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(workspaceRoot || "")}">复制</button>
    </article>
    ${accountRoot ? `
    <article class="path-row">
      <span>${escapeHtml(data.backend === "echomemory" ? "账户存储根" : "账户目录")}</span>
      <code>${escapeHtml(accountRoot)}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(accountRoot)}">复制</button>
    </article>
    ` : ""}
    ${sessionRows || "<p>当前账户下没有已导入内容。</p>"}
    ${summaryRows ? `<div class="list-divider">导入摘要</div>${summaryRows}` : ""}
  `;
  document.querySelectorAll("#importedMemoryList .path-copy").forEach((button) => {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy || "");
      toast("路径已复制");
    });
  });
  refreshLocomoFlowStatus(true).catch(() => {});
}

async function startTask(kind, extra = {}) {
  const inferredFormat = normalizeDatasetFormat(extra.dataset_format || inferDatasetFormatFromText(extra.data, extra.name, kind));
  const datasetFormat = inferredFormat || ((kind === "openviking_generic_qa" || kind === "echomemory_generic_qa") ? "generic" : (currentLocomoDataset()?.format || "locomo"));
  if (datasetFormat && datasetFormat !== "locomo") {
    rememberActiveDatasetView(viewForDatasetFormat(datasetFormat, preferredBenchmarkFallback("runsView")), datasetFormat, extra.data || "");
  }
  const targetSample = extra.sample ?? ($("sample")?.value || "all");
  const targetQuestions = String(extra.questions ?? [...state.selectedQuestions].join(",")).trim();
  if (extra.require_selected_questions && !targetQuestions) {
    return toast("请先勾选至少 1 题；要跑当前范围全量请点右边按钮");
  }
  const explicitFullLocomoRun = Boolean(extra.full_locomo_run || extra.allow_full_locomo);
  if ((kind === "local_agent" || isMemoryQaKind(kind)) && datasetFormat === "locomo" && targetSample === "all" && !targetQuestions && !extra.count && !explicitFullLocomoRun) {
    return toast("当前是全部对话。请先勾选题目；要跑完整 LoCoMo 请点“跑全部 LoCoMo”。");
  }
  const locomoQaLaunch = isMemoryQaKind(kind) && datasetFormat === "locomo";
  if (locomoQaLaunch) {
    if (state.locomoQaSubmitInFlight) return toast("问答任务正在提交，请稍候");
    if (activeLocomoQaTask()) return toast("已有问答任务运行中，请等当前任务结束后再点");
    state.locomoQaSubmitInFlight = true;
    state.locomoQaSubmitPhase = "preflight";
    refreshLocomoQaActionLabels();
  }
  try {
    if (locomoQaLaunch) {
      await ensureLocomoQaLaunchReady();
    }
    if (isMemoryImportKind(kind)) {
      $("commitImport").disabled = true;
      renderImportDiagnostics(null);
    }
    let task;
    try {
      task = await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify(taskPayload(kind, extra)),
      });
    } catch (error) {
      const activeTask = error?.status === 409 && error?.data?.task;
      if (!activeTask) throw error;
      task = activeTask;
      toast(error?.data?.error || `已有任务正在运行：${task.name || task.id}`);
    }
    stampTaskSnapshot(task);
    state.taskId = task.id;
    state.taskKind = kind;
    state.logOffsets[task.id] = 0;
    state.currentRunningTask = task;
    rememberTaskDatasetFormat(task.id, task.dataset_format || datasetFormat);
    rememberBenchmarkRecord(task, task.dataset_format || datasetFormat);
    if (locomoQaLaunch) {
      state.currentLocomoTask = task;
    }
    if (task.output_file && isLocomoTaskOutput(kind, task, datasetFormat)) {
      markLocomoOutputFile(task.output_file);
    } else if (task.output_file && datasetFormat && datasetFormat !== "locomo") {
      markDatasetOutputFile(task.output_file, datasetFormat);
    }
    if (taskShouldUseLocomoTaskStrip(kind, task, task.dataset_format || datasetFormat)) {
      renderActiveTaskStrip(task);
    } else {
      updateProgress(task, kind);
    }
    refreshTasks().catch(() => {});
    refreshLocomoFlowStatus(true).catch(() => {});
    if (isMemoryImportKind(kind)) {
      renderImportPaths(task);
      renderImportDiagnostics(task);
      const taskWorkspace = task.meta?.config?.workspace;
      if (taskWorkspace) {
        state.currentImportTask = task;
        const scope = syncImportTaskFields(task);
        saveLastImport({
          workspace: scope.workspace || taskWorkspace,
          account: scope.account || "default",
          sample_value: $("importSample").value || "",
          sample_label: $("importSample")?.selectedOptions?.[0]?.textContent || "",
          run_dir: task.run_dir || "",
          log_file: task.log_file || runLogPathFromRecord(task),
          output_file: task.output_file || "",
          backend: scope.backend || (task.kind === "echomemory_import" ? "echomemory" : "openviking"),
        });
        updateWorkflowGuide();
        refreshLocomoFlowStatus(true).catch(() => {});
      }
      $("importMemoryPreview").innerHTML = "<p>导入进行中，完成后显示 session、message 数量和 token 估算。</p>";
    }
    const ui = taskUi(kind, {...task, dataset_format: task.dataset_format || datasetFormat});
    const logBox = openTaskLogBox(ui.logBox);
    if (logBox) {
      logBox.dataset.taskId = task.id;
      logBox.textContent = "";
    }
    clearInterval(state.logTimers[task.id]);
    delete state.logTimers[task.id];
    ensureTaskPolling(task, kind);
    toast(`任务已启动：${task.name}`);
    return task;
  } catch (error) {
    if (isMemoryImportKind(kind) && $("commitImport")) {
      $("commitImport").disabled = false;
    }
    throw error;
  } finally {
    if (locomoQaLaunch) {
      state.locomoQaSubmitInFlight = false;
      state.locomoQaSubmitPhase = "";
      refreshLocomoQaActionLabels();
    }
  }
}

async function pollTask(taskId = state.taskId, kind = state.taskKind) {
  if (!taskId) return;
  const requestOffset = Number(state.logOffsets[taskId] || 0);
  const data = await api(`/api/tasks/${taskId}/log?offset=${requestOffset}`);
  const latestKnownOffset = Number(state.logOffsets[taskId] || 0);
  const responseOffset = Number(data.offset || 0);
  const staleFullReplay = latestKnownOffset > requestOffset && responseOffset <= latestKnownOffset;
  state.logOffsets[taskId] = Math.max(latestKnownOffset, responseOffset);
  const task = stampTaskSnapshot(data.task || {id: taskId, kind});
  const taskFormat = enrichTaskDatasetFormat(task, state.taskDatasetFormats[taskId] || "");
  const effectiveKind = task.kind || kind;
  const otherActiveImport = isMemoryImportKind(task.kind) ? trackedActiveImportTask(taskId) : null;
  const shouldOwnImportUi = isMemoryImportKind(task.kind)
    ? (isTaskActive(task) || !otherActiveImport || state.currentImportTask?.id === taskId)
    : false;
  if (!taskVisibleInCurrentTaskPanel({...task, kind: effectiveKind}, taskFormat, activeViewId())) {
    if (state.currentRunningTask?.id === taskId) state.currentRunningTask = null;
    if (state.currentLocomoTask?.id === taskId) state.currentLocomoTask = null;
    updateStopActionButtons();
    if (isTaskTerminal(task)) {
      clearInterval(state.logTimers[taskId]);
      delete state.logTimers[taskId];
    }
    return;
  }
  const ui = taskUi(effectiveKind, {...task, dataset_format: taskFormat});
  const logBox = resetTaskLogPlaceholder(ui.logBox);
  if (logBox) {
    const switchedTaskLog = Boolean(logBox.dataset.taskId && logBox.dataset.taskId !== taskId);
    logBox.dataset.taskId = taskId;
    if (data.text && !staleFullReplay) {
      logBox.textContent = switchedTaskLog ? data.text : `${logBox.textContent}${data.text}`;
      logBox.scrollTop = logBox.scrollHeight;
    }
  }
  if (isTaskActive(task)) state.currentRunningTask = task;
  ensureGenericBenchmarkExecutionProgress(task, taskFormat).then(() => {
    if (state.currentRunningTask?.id === task.id || state.currentLocomoTask?.id === task.id) {
      refreshLiveTaskDisplays();
    }
  }).catch(() => {});
  const locomoResultTask = isLocomoTaskOutput(effectiveKind, task, taskFormat);
  if (locomoResultTask) state.currentLocomoTask = task;
  updateStopActionButtons();
  if (taskShouldUseLocomoTaskStrip(effectiveKind, task, taskFormat) && (isTaskActive(task) || !otherActiveImport)) {
    renderActiveTaskStrip(task);
  }
  if (shouldOwnImportUi) {
    renderImportPaths(task);
    renderImportDiagnostics(task);
    const taskWorkspace = task.meta?.config?.workspace;
    if (taskWorkspace) {
      state.currentImportTask = task;
      const scope = syncImportTaskFields(task);
      saveLastImport({
        workspace: scope.workspace || taskWorkspace,
        account: scope.account || "default",
        sample_value: $("importSample").value || "",
        sample_label: $("importSample")?.selectedOptions?.[0]?.textContent || "",
        run_dir: task.run_dir || "",
        log_file: task.log_file || runLogPathFromRecord(task),
        output_file: task.output_file || "",
        backend: scope.backend || (task.kind === "echomemory_import" ? "echomemory" : "openviking"),
      });
      updateWorkflowGuide();
    }
  }
  if (task.output_file) {
    if (locomoResultTask) {
      markLocomoOutputFile(task.output_file);
      updateWorkflowGuide();
    } else if (taskFormat && taskFormat !== "locomo") {
      markDatasetOutputFile(task.output_file, taskFormat);
    }
  }
  if (isTaskActive(task) && isGenericBenchmarkQaTask(task, taskFormat)) {
    const runningSummary = await loadRunningBenchmarkSummary(task, taskFormat).catch(() => null);
    renderGenericBenchmarkRunningSummary(task, taskFormat, {summary: runningSummary});
  }
  updateProgress(task, effectiveKind);
  if (task.kind === "judge" || effectiveKind === "judge") {
    renderJudgeReadinessPanel(state.lastJudgeSummary || {}, task);
  }
  if (isTaskTerminal(task)) {
    clearInterval(state.logTimers[taskId]);
    delete state.logTimers[taskId];
    if (state.currentRunningTask?.id === taskId) state.currentRunningTask = null;
    if (shouldOwnImportUi) {
      if ($("commitImport")) $("commitImport").disabled = false;
      state.currentImportTask = task;
      updateBackendUi();
    }
    if (task.kind === "openviking_import" && task.output_file) {
      await refreshCommitSummary(task.output_file);
    } else if (task.kind === "echomemory_import" && task.output_file) {
      await refreshEchoMemoryImportSummary(task.output_file);
    } else if (locomoResultTask && currentLocomoResultCsv()) {
      await refreshResult();
    } else if (isGenericBenchmarkQaTask(task, taskFormat)) {
      await renderGenericBenchmarkResultSummary(task, taskFormat);
    } else if ((task.kind === "judge" || effectiveKind === "judge") && currentLocomoResultCsv()) {
      await refreshResult();
    }
    if (!isTaskActive(task) && taskShouldUseLocomoTaskStrip(effectiveKind, task, taskFormat) && !trackedActiveImportTask(taskId)) {
      renderActiveTaskStrip(task);
    }
    updateStopActionButtons();
    updateWorkflowGuide();
    refreshTasks().catch(() => {});
    refreshLocomoFlowStatus(true).catch(() => {});
  }
}

function formatDuration(seconds) {
  const raw = Math.max(0, Number(seconds || 0));
  if (raw > 0 && raw < 1) return `${raw.toFixed(1)}s`;
  const value = Math.round(raw);
  const mins = Math.floor(value / 60);
  const secs = value % 60;
  if (mins <= 0) return `${secs}s`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return hours ? `${hours}h ${remMins}m` : `${mins}m ${secs}s`;
}

function formatSecondsMetric(value) {
  const raw = Number(value);
  if (!Number.isFinite(raw)) return "-";
  if (raw === 0) return "0.00s";
  if (Math.abs(raw) >= 100) return `${raw.toFixed(1)}s`;
  return `${raw.toFixed(2)}s`;
}

function updateProgress(task, kind = state.taskKind) {
  task = taskWithLiveProgress(task || {});
  const ui = taskUi(kind || task.kind || state.taskKind, task || {});
  const bar = $(ui.progressBar);
  const text = $(ui.progressText);
  if (!bar || !text) return;
  const barWrap = bar.closest(".progress-bar");
  const progress = task && task.progress;
  const importConfig = task?.meta?.config || task?.config || {};
  const importSmoke = isMemoryImportKind(kind) || isMemoryImportKind(task?.kind)
    ? Number(importConfig.max_sessions || 0) === 1
    : false;
  const progressCurrent = progress ? Number(progress.current || 0) : 0;
  const progressTotal = progress ? Number(progress.total || 0) : 0;
  const displayCurrent = importSmoke && progress ? Math.min(Math.max(progressCurrent, 0), 1) : progressCurrent;
  const displayTotal = importSmoke && progressTotal > 0 && String(progress.unit || "") === "sessions" ? 1 : progressTotal;
  const pct = displayTotal ? Math.max(0, Math.min(100, (displayCurrent / Math.max(displayTotal, 1)) * 100)) : 0;
  const format = taskDatasetFormat(task, state.taskDatasetFormats[task?.id || ""] || "");
  const execution = taskExecutionProgress(task, format);
  const elapsedValue = task?.duration != null
    ? formatDuration(task.duration)
    : (progress?.elapsed_seconds != null ? formatDuration(progress.elapsed_seconds) : "");
  const elapsed = elapsedValue ? ` · 已运行 ${elapsedValue}` : "";
  const detailLabel = benchmarkProgressDetail(task, format);
  const progressDetail = detailLabel ? ` · ${detailLabel}` : "";
  if (isMemoryImportKind(kind) || isMemoryImportKind(task?.kind)) {
    renderImportDiagnostics(task);
  }
  if (!progress) {
    bar.style.width = "0%";
    bar.style.animation = "none";
    if (barWrap && ui.progressText === "judgeProgressText") barWrap.hidden = true;
    if (ui.progressText === "judgeProgressText") text.hidden = true;
    if (ui.progressText !== "judgeProgressText") {
      text.hidden = false;
      text.style.display = "block";
    }
    text.textContent = state.tasksHydrating
      ? "正在恢复任务状态"
      : (taskStatusLabel(task) || ui.waiting);
    return;
  }
  if (isMemoryImportKind(kind) || isMemoryImportKind(task?.kind)) {
    const banner = $("importCompletionBanner");
    if (banner && isTaskActive(task)) {
      banner.hidden = true;
      banner.className = "completion-banner";
    }
    const phase = String(progress.phase || "");
    const taskStatus = String(task?.status || "").toLowerCase();
    const liveRunning = taskStatus === "running";
    const isCommit = phase.startsWith("commit");
    const unit = progress.unit || "messages";
    const sessionProgressCurrent = String(unit) === "sessions" ? Math.max(0, Number(progress.current || 0)) : 0;
    const sessionProgressTotal = String(unit) === "sessions" ? Math.max(sessionProgressCurrent, Number(progress.total || 0)) : 0;
    const importPct = sessionProgressTotal > 0
      ? Math.max(0, Math.min(100, (sessionProgressCurrent / Math.max(sessionProgressTotal, 1)) * 100))
      : pct;
    const totalSamples = Number(progress.total_samples || 0);
    const completedSamples = Number(progress.completed_samples || 0);
    const sessionLabel = String(progress.session_label || progress.current_import?.session || "").trim();
    const importMessage = taskImportMessageCounts(progress);
    const importMessageText = importMessage.total
      ? ` · 当前会话消息 ${importMessage.index}/${importMessage.total}`
      : "";
    const sessionText = sessionLabel ? ` · 当前会话 ${sessionLabel}` : "";
    const sessionProgressText = sessionProgressTotal > 0
      ? ` · 已完成会话 ${sessionProgressCurrent}/${sessionProgressTotal}`
      : "";
    const scopeText = importSmoke
      ? ` · 模式 单 session 测试`
      : "";
    bar.style.width = `${importPct}%`;

    if (!liveRunning) {
      text.textContent = `${taskStatusLabel(task)}${sessionProgressText}${scopeText}${sessionText}${importMessageText}${elapsed}`;
      bar.style.animation = "none";
      return;
    }
    if (isCommit) {
      if (phase === "commit:embedding_retry") {
        const issueCount = task?.log_diagnostics?.model_issue_count || 0;
        const issueText = issueCount ? ` · 检测到 ${issueCount} 条模型/检索异常日志` : "";
        text.textContent = `归档阶段${sessionProgressText}${scopeText}${sessionText}${importMessageText}${elapsed}${issueText}`;
        bar.style.animation = "none";
      } else if (unit === "sessions" && progress.total > 0) {
        text.textContent = `归档阶段${sessionProgressText}${scopeText}${sessionText}${importMessageText}${elapsed}`;
        bar.style.animation = "none";
      } else if (progress.indeterminate && isTaskActive(task)) {
        text.textContent = `归档阶段${sessionProgressText}${scopeText}${sessionText}${importMessageText}${elapsed}`;
        bar.style.animation = "none";
      } else {
        text.textContent = `归档阶段完成${sessionProgressText}${scopeText}${elapsed}`;
        bar.style.animation = "none";
      }
    } else {
      text.textContent = `导入阶段${sessionProgressText}${scopeText}${sessionText}${importMessageText}${elapsed}`;
      bar.style.animation = "none";
    }
    return;
  }
  bar.style.width = `${pct}%`;
  if (execution?.total_questions) {
    const currentQuestion = execution.current_question || execution.answered_questions || 0;
    const answeredQuestions = execution.answered_questions || 0;
    const totalQuestions = execution.total_questions || currentQuestion || answeredQuestions || 0;
    const authoritativeScope = `${currentQuestion || answeredQuestions}/${totalQuestions}`;
    const progressScope = progress?.total ? `${Number(progress.current || 0)}/${Number(progress.total || 0)}` : "";
    const authoritativeNote = progressScope && progressScope !== authoritativeScope
      ? ` · 日志权威 ${authoritativeScope}`
      : "";
    const benchmarkPct = totalQuestions > 0
      ? Math.max(0, Math.min(100, (Math.max(currentQuestion, answeredQuestions) / totalQuestions) * 100))
      : pct;
    const phaseName = progress?.phase?.startsWith("commit")
      ? "写入/索引"
      : (progress?.phase === "qa" ? "问答" : (progress?.phase === "import" ? "导入" : (progress?.phase || "运行中")));
    bar.style.width = `${benchmarkPct}%`;
    bar.style.animation = progress?.indeterminate && isTaskActive(task) ? "pulse 2s ease-in-out infinite" : "none";
    text.textContent = `${phaseName} · 第 ${authoritativeScope} 题 · 已答 ${answeredQuestions}/${totalQuestions}${authoritativeNote}${elapsed}${eta}${progressDetail}`;
    return;
  }
  const phaseName = task?.kind === "adapter"
    ? "预览任务"
    : task?.kind === "local_agent"
    ? (progress.phase === "import" ? "准备本地基线数据" : "MemoryBench 本地基线 QA")
    : (progress.phase || "running");
  if (barWrap && ui.progressText === "judgeProgressText") {
    barWrap.hidden = false;
    barWrap.style.display = "block";
  }
  if (ui.progressText === "judgeProgressText") {
    text.hidden = false;
    text.style.display = "block";
  }
  text.textContent = `${phaseName} · ${progress.current}/${progress.total} · ${pct.toFixed(1)}%${elapsed}${eta}`;
}

function runningTaskCandidates() {
  const seen = new Set();
  return [state.currentRunningTask, state.currentLocomoTask, state.currentImportTask]
    .filter((task) => task?.id && isTaskActive(task))
    .filter((task) => {
      if (seen.has(task.id)) return false;
      seen.add(task.id);
      return true;
    });
}

function trackedActiveImportTask(excludeTaskId = "") {
  const excluded = String(excludeTaskId || "").trim();
  return [state.currentImportTask, state.currentRunningTask, state.currentLocomoTask]
    .find((task) => task?.id
      && task.id !== excluded
      && isMemoryImportKind(task.kind || "")
      && isTaskActive(task));
}

function refreshLiveTaskDisplays() {
  const activeView = activeViewId();
  const tasks = runningTaskCandidates().filter((task) => {
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    return taskVisibleInCurrentTaskPanel(task, format, activeView);
  });
  if (!tasks.length) {
    updateStopActionButtons([]);
    renderGlobalTaskChip(null);
    const terminalImportTask = activeView !== "evalView"
      && isMemoryImportKind(state.currentImportTask?.kind || "")
      && !isTaskActive(state.currentImportTask)
      ? state.currentImportTask
      : null;
    if (terminalImportTask) {
      renderImportPaths(terminalImportTask);
      renderImportDiagnostics(terminalImportTask);
      updateProgress(terminalImportTask, terminalImportTask.kind || locomoImportTaskKind());
    }
    const activeStrip = $("activeTaskStrip");
    const currentTaskId = activeStrip?.dataset.taskId || "";
    const trackedTask = [state.currentRunningTask, state.currentLocomoTask, state.currentImportTask]
      .find((task) => task?.id && task.id === currentTaskId);
    if (!trackedTask || !isTaskActive(trackedTask)) renderActiveTaskStrip(null);
    return;
  }
  const primary = tasks[0];
  renderGlobalTaskChip(primary);
  for (const task of tasks) {
    const kind = task.kind || state.taskKind;
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    ensureGenericBenchmarkExecutionProgress(task, format).catch(() => {});
    const liveTask = taskWithLiveProgress(task);
    const isImportTask = isMemoryImportKind(kind) || isMemoryImportKind(liveTask?.kind);
    const isCurrentImportTask = isImportTask && state.currentImportTask?.id && state.currentImportTask.id === liveTask.id;
    const isLocomoTask = taskShouldUseLocomoTaskStrip(kind, liveTask, format);
    if (isCurrentImportTask || !isLocomoTask) {
      updateProgress(liveTask, kind);
    }
    if (taskShouldUseLocomoTaskStrip(kind, liveTask, format)) {
      renderActiveTaskStrip(liveTask);
    }
  }
}

async function refreshStandaloneBenchmarkViewOnly() {
  const activeView = activeViewId();
  if (!isStandaloneBenchmarkView(activeView)) return;
  const activeFormat = datasetFormatForView(activeView);
  if (!activeFormat) return;
  const data = await api("/api/tasks");
  const allTasks = (data.tasks || []).map(stampTaskSnapshot);
  const runningTasks = allTasks.filter(isTaskActive);
  const runningTask = runningTasks.find((task) => {
    const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    return normalizeDatasetFormat(format) === activeFormat;
  });
  if (!runningTask) return;
  const taskAccount = recordAccount(runningTask) || "default";
  await ensureGenericBenchmarkExecutionProgress(runningTask, activeFormat).catch(() => null);
  const runningSummary = await loadRunningBenchmarkSummary(runningTask, activeFormat).catch(() => null);
  renderGenericBenchmarkRunningSummary(runningTask, activeFormat, {
    account: taskAccount,
    summary: runningSummary,
    note: taskAccount !== currentAccount()
      ? `当前页面账户是 ${currentAccount()}，但运行中的 ${benchmarkConfig(genericBenchmarkKeyForFormat(activeFormat))?.label || datasetTypeLabel(activeFormat)} 任务属于账户 ${taskAccount}。这里直接显示只读进度。`
      : "",
  });
  state.currentRunningTask = runningTask;
  renderGlobalTaskChip(runningTask);
  updateProgress(taskWithLiveProgress(runningTask), runningTask.kind || state.taskKind);
}

async function forceRefreshStandaloneBenchmarkView(format = "", options = {}) {
  const normalized = normalizeDatasetFormat(format || datasetFormatForView(activeViewId()));
  if (!normalized || normalized === "locomo") return null;
  const data = await api("/api/tasks");
  const allTasks = (data.tasks || []).map(stampTaskSnapshot);
  const runningTasks = allTasks.filter(isTaskActive);
  const runningTask = runningTasks.find((task) => {
    const taskFormat = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || "");
    return normalizeDatasetFormat(taskFormat) === normalized;
  });
  if (!runningTask) return null;
  const taskAccount = recordAccount(runningTask) || "default";
  await ensureGenericBenchmarkExecutionProgress(runningTask, normalized).catch(() => null);
  const runningSummary = await loadRunningBenchmarkSummary(runningTask, normalized).catch(() => null);
  renderGenericBenchmarkRunningSummary(runningTask, normalized, {
    account: taskAccount,
    summary: runningSummary,
    note: options.note || (taskAccount !== currentAccount()
      ? `当前页面账户是 ${currentAccount()}，但运行中的 ${benchmarkConfig(genericBenchmarkKeyForFormat(normalized))?.label || datasetTypeLabel(normalized)} 任务属于账户 ${taskAccount}。这里直接显示只读进度。`
      : ""),
  });
  const benchmarkKey = genericBenchmarkKeyForFormat(normalized);
  if (benchmarkKey) {
    renderGenericRunningStatus(benchmarkKey, runningTask, runningSummary);
  }
  state.currentRunningTask = runningTask;
  renderGlobalTaskChip(runningTask);
  updateProgress(taskWithLiveProgress(runningTask), runningTask.kind || state.taskKind);
  return runningTask;
}

function startTaskUiTimers() {
  if (!state.liveTaskTimer) {
    state.liveTaskTimer = setInterval(refreshLiveTaskDisplays, 1000);
  }
  if (!state.taskRefreshTimer) {
    state.taskRefreshTimer = setInterval(() => {
      const activeTasks = runningTaskCandidates();
      if (!activeTasks.length) return;
      refreshTasks()
        .catch(() => null)
        .then(() => refreshStandaloneBenchmarkViewOnly())
        .catch(() => {});
    }, 5000);
  }
}

async function refreshEchoMemoryImportSummary(path) {
  if (!path) return;
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  const summary = JSON.parse(data.text || "{}");
  const smoke = isSingleSessionImportSummary(summary);
  const records = Array.isArray(summary.records) ? summary.records : [];
  const incomplete = Number(summary.incomplete_samples || 0);
  const pendingAsync = Number(summary.pending_async_samples || 0);
  const retrievalReady = Number(summary.retrieval_ready_samples || 0);
  const status = String(summary.status || "").trim();
  const complete = incomplete === 0 && pendingAsync === 0;
  const asyncSettling = status === "ECHOMEMORY_IMPORT_ASYNC_SETTLING" || pendingAsync > 0;
  const expected = summary.expected_messages ?? records.reduce((acc, item) => acc + Number(item.expected_messages || 0), 0);
  const submitted = summary.submitted_messages ?? records.reduce((acc, item) => acc + Number(item.submitted_messages || 0), 0);
  const workspace = summary.workspace || $("ovWorkspace").value.trim();
  const account = summary.account || $("ovAccount").value.trim() || "default";
  const banner = $("importCompletionBanner");
  if (banner) {
    banner.hidden = false;
    banner.className = `completion-banner ${complete ? "ok" : (asyncSettling ? "warn" : "warn")}`;
    banner.textContent = complete
      ? (smoke
        ? `EchoMemory 单 session 注入测试完成：${submitted || "-"} / ${expected || "-"} 条对话消息已提交，检索产物已就绪。`
        : `EchoMemory 导入完成：${submitted || "-"} / ${expected || "-"} 条对话消息已提交，检索产物已就绪。`)
      : asyncSettling
        ? (smoke
          ? `EchoMemory 单 session 注入测试已提交：${submitted || "-"} / ${expected || "-"} 条对话消息，记忆仍在后台生成。`
          : `EchoMemory 导入已提交：${submitted || "-"} / ${expected || "-"} 条对话消息，记忆仍在后台生成。`)
        : `EchoMemory 导入存在未完成样本：${incomplete} 个，请检查日志和 summary。`;
  }
  saveLastImport({
    workspace,
    account,
    sample_value: $("importSample").value || "",
    sample_label: $("importSample")?.selectedOptions?.[0]?.textContent || "",
    output_file: path,
    integrity: complete ? "complete" : (asyncSettling ? "pending_async_memory" : "incomplete"),
    backend: "echomemory",
  });
  if ($("ovWorkspace") && workspace) $("ovWorkspace").value = workspace;
  if ($("memoryWorkspace") && workspace) $("memoryWorkspace").value = workspace;
  if ($("memoryAccount")) $("memoryAccount").value = account;
  if (workspace && account) {
    const nextConfig = {
      ovWorkspace: workspace,
      memoryWorkspace: workspace,
      memoryBackend: "echomemory",
    };
    saveAccountConfig(account, nextConfig);
    syncAccountConfigToBackend(account, nextConfig).catch(() => {});
  }
  renderImportPaths({
    kind: "echomemory_import",
    output_file: path,
    log_file: runLogPathFromRecord({output_file: path}),
    meta: {config: {workspace, account, backend: "echomemory"}},
  });
  $("importProgressBar").style.width = complete ? "100%" : $("importProgressBar").style.width;
  $("importProgressText").textContent = complete
    ? (smoke
      ? `EchoMemory 单 session 注入测试完成：对话消息 ${submitted || "-"} / ${expected || "-"}，检索产物已就绪。`
      : `EchoMemory 导入完成：对话消息 ${submitted || "-"} / ${expected || "-"}，检索产物已就绪。`)
    : asyncSettling
      ? (smoke
        ? `EchoMemory 单 session 注入测试已完成写入：对话消息 ${submitted || "-"} / ${expected || "-"}，后台仍在生成 atom / graph。`
        : `EchoMemory 导入已提交：对话消息 ${submitted || "-"} / ${expected || "-"}，后台仍在生成 atom / graph。`)
      : `EchoMemory 导入结束但需要检查：未完成样本 ${incomplete}。`;
  renderKpis("commitKpis", [
    ["模式", smoke ? "单 session 测试" : "正式导入"],
    ["完整性", complete ? "完整" : (asyncSettling ? "后台补齐中" : "未完成")],
    ["样本数", summary.samples ?? records.length ?? "-"],
    ["对话消息", `${submitted || "-"} / ${expected || "-"}`],
    ["检索就绪", `${retrievalReady || 0} / ${summary.samples ?? records.length ?? "-"}`],
    ["后端", "EchoMemory"],
    ["工作空间", workspace || "-"],
  ]);
  const importFolder = dirname(path);
  $("importMemoryPreview").innerHTML = `
    <article class="path-row">
      <span>导入文件夹</span>
      <code>${escapeHtml(importFolder)}</code>
    </article>
  ` + records.map((record) => `
    <article class="memory-hit ${String(record.integrity || "").toLowerCase() === "complete" ? "ok" : ""}">
      <strong>${escapeHtml(record.sample_id || "-")} · ${escapeHtml(record.session_id || "EchoMemory session")}</strong>
      <p>对话消息 ${escapeHtml(record.submitted_messages ?? "-")} / ${escapeHtml(record.expected_messages ?? "-")} · 完整性 ${escapeHtml(record.integrity || (complete ? "complete" : "-"))}</p>
    </article>
  `).join("");
  toast(
    complete
      ? (smoke ? "EchoMemory 单 session 注入测试完成" : "EchoMemory 导入完成")
      : (asyncSettling
        ? (smoke ? "EchoMemory 单 session 注入测试已提交，记忆仍在后台生成" : "EchoMemory 导入已提交，记忆仍在后台生成")
        : "EchoMemory 导入结束，请检查完整性")
  );
}

async function refreshResult() {
  const input = currentLocomoResultCsv();
  if (!input) return toast("请先运行或选择 LoCoMo 结果文件");
  const data = await api(`/api/results?path=${encodeURIComponent(input)}`);
  const summary = data.summary || {};
  const sj = summary.summary_json || {};
  const format = summaryDatasetFormat(summary);
  if (format && format !== "locomo") {
    toast(`当前结果是 ${datasetTypeLabel(format)}，LoCoMo 页面不加载它`);
    return;
  }
  markLocomoOutputFile(input);
  state.lastJudgeSummary = summary;
  const judged = (summary.graded || 0) > 0;
  const simpleReference = summary.exact_match_reference ?? sj.exact_match_rate ?? summary.simple_accuracy;
  const simpleCorrect = summary.simple_correct ?? sj.exact_match_count ?? "-";
  const formalAccuracy = judged ? percent(summary.accuracy) : "待判分";
  renderKpis("resultKpis", [
    ["正式准确率", formalAccuracy],
    ["题数", summary.rows ?? "-"],
    ["判对", summary.correct ?? "-"],
    ["待判", summary.result_counts?.UNSCORED ?? "-"],
      ["总 Token", summary.total_injection_tokens_est ?? sj.total_injection_tokens_est ?? "-"],
      ["平均 Token", summary.avg_injection_tokens_est ?? sj.avg_injection_tokens_est ?? "-"],
  ]);
  if ($("evalResultKpis")) {
    renderKpis("evalResultKpis", [
      ["正式准确率", formalAccuracy],
      ["题数", summary.rows ?? "-"],
      ["判对", summary.correct ?? "-"],
      ["待判", summary.result_counts?.UNSCORED ?? "-"],
      ["精确匹配参考", simpleReference == null ? "-" : `${simpleCorrect}/${summary.rows ?? sj.count ?? "-"} · ${percent(simpleReference)}`],
      ["Token 用量", summary.total_injection_tokens_est ?? sj.total_injection_tokens_est ?? "-"],
    ]);
  }
  renderJudgeEstimate(summary);
  renderJudgeReadinessPanel(summary);
  await renderQaDiagnostics(input).catch((error) => {
    const panel = $("qaDiagnosticsPanel");
    if (panel) panel.innerHTML = `<p class="bad-text">结果读取失败：${escapeHtml(error.message)}</p>`;
  });
  const summaryJson = summary.summary_json || {};
  const baseDir = dirname(input);
  renderArtifactList([
    ["结果文件", input],
    ["目录", baseDir],
    ["摘要", summaryJson.output_csv ? `${dirname(summaryJson.output_csv)}/summary.json` : `${baseDir}/summary.json`],
    ["判分摘要", `${baseDir}/judge_summary.json`],
    ["错题分析", `${input.replace(/\\.csv$/i, ".wrong_analysis.json")}`],
  ]);
  await renderPendingJudgePanel(input);
  await renderPreview(input);
  refreshLocomoFlowStatus(true).catch(() => {});
}

async function refreshLocomoResultAction(buttonId = "refreshResult") {
  const button = $(buttonId);
  const previousText = button?.textContent || "";
  if (button) {
    button.disabled = true;
    button.textContent = "刷新中...";
  }
  try {
    const currentInput = currentLocomoResultCsv();
    if (!currentInput) {
      await refreshTasks().catch(() => null);
    } else {
      refreshTasks().catch(() => null);
    }
    const input = currentInput || await ensureCurrentLocomoResultInput({forceRuns: true});
    if (!input) {
      toast("当前还没有可刷新的 LoCoMo 问答结果");
      return;
    }
    await refreshResult();
    const summary = state.lastJudgeSummary || {};
    const judged = Number(summary.graded || 0) > 0;
    const accuracyLabel = judged ? percent(summary.accuracy) : "待判分";
    toast(`已刷新问答结果 · ${summary.rows ?? "-"} 题 · ${accuracyLabel}`);
  } catch (error) {
    toast(error.message || String(error || "刷新失败"));
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previousText || "刷新问答结果";
    }
  }
}

async function loadQaDiagnostics(input = currentLocomoResultCsv()) {
  if (!input) throw new Error("请先运行或选择 LoCoMo 结果文件");
  const datasetPath = $("data")?.value.trim() || "";
  const sample = $("sample")?.value || "all";
  const qs = new URLSearchParams({path: input});
  if (datasetPath) qs.set("dataset", datasetPath);
  if (sample) qs.set("sample", sample);
  return api(`/api/qa-diagnostics?${qs.toString()}`);
}

function renderQaDiagnosticsSummary(data = {}) {
  const panel = $("qaDiagnosticsPanel");
  if (!panel) return;
  const missing = Number(data.missing_questions_count || 0);
  const failed = Number(data.retryable_failed_questions || 0);
  const duplicates = Number(data.duplicate_question_ids_count || 0);
  const pending = Number(data.summary?.result_counts?.UNSCORED || 0);
  const expected = data.expected_questions == null ? "-" : formatInt(data.expected_questions);
  const unique = data.unique_question_ids == null ? "-" : formatInt(data.unique_question_ids);
  const missingExamples = (data.missing_examples || []).slice(0, 3).map((item) => item.question_id).filter(Boolean).join(" · ");
  const failedExamples = (data.retryable_failed_examples || []).slice(0, 3).map((item) => item.question_id).filter(Boolean).join(" · ");
  const tone = missing || failed || duplicates ? "bad-text" : "ok-text";
  if (!(missing || failed || duplicates || pending)) {
    panel.innerHTML = "";
    panel.hidden = true;
    updateJudgeAndReportActionButtons();
    return;
  }
  panel.hidden = false;
  panel.innerHTML = `
    <p class="${tone}"><strong>链路诊断</strong> · 期望 ${escapeHtml(expected)} 题 · 当前唯一题 ${escapeHtml(unique)} · 缺失 ${escapeHtml(missing)} · 可重跑失败 ${escapeHtml(failed)} · 重复 ${escapeHtml(duplicates)} · 待判 ${escapeHtml(pending)}</p>
    ${missingExamples ? `<p>缺失示例：${escapeHtml(missingExamples)}</p>` : ""}
    ${failedExamples ? `<p>失败示例：${escapeHtml(failedExamples)}</p>` : ""}
  `;
  updateJudgeAndReportActionButtons();
}

async function renderQaDiagnostics(input = currentLocomoResultCsv()) {
  const data = await loadQaDiagnostics(input);
  state.lastQaDiagnostics = data;
  state.lastQaDiagnosticsInput = String(input || "").trim();
  renderQaDiagnosticsSummary(data);
  return data;
}

async function renderPendingJudgePanel(path) {
  const panel = $("pendingJudgePanel");
  if (!panel || !path) return;
  const filters = pendingFilterPayload();
  const qs = new URLSearchParams({path, limit: "3"});
  if (filters.category) qs.set("category", filters.category);
  if (filters.query) qs.set("q", filters.query);
  if (filters.min_tokens) qs.set("min_tokens", filters.min_tokens);
  if (filters.max_tokens) qs.set("max_tokens", filters.max_tokens);
  const data = await api(`/api/pending-preview?${qs.toString()}`);
  const rows = data.rows || [];
  if (!data.total_pending) {
    panel.innerHTML = "";
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.innerHTML = `
    <div class="pending-head">
      <div>
        <strong>待判分</strong>
        <span>${escapeHtml(data.total_pending)} 行需要判分；下方只显示少量示例。</span>
      </div>
      <div class="panel-actions">
        <button class="secondary" id="refreshPendingPreview">刷新示例</button>
        <button class="secondary" id="exportPendingCsv">导出待判 CSV</button>
        <button class="primary" id="pendingRunJudge">判分当前结果</button>
      </div>
    </div>
    <div class="form-grid four compact-form">
      <label>
        <span>类别</span>
        <select id="pendingCategory">
          <option value="">全部</option>
          <option value="1" ${filters.category === "1" ? "selected" : ""}>C1</option>
          <option value="2" ${filters.category === "2" ? "selected" : ""}>C2</option>
          <option value="3" ${filters.category === "3" ? "selected" : ""}>C3</option>
          <option value="4" ${filters.category === "4" ? "selected" : ""}>C4</option>
        </select>
      </label>
      <label>
        <span>搜索</span>
        <input id="pendingSearch" spellcheck="false" placeholder="question_id / 关键词" value="${escapeHtml(filters.query || "")}">
      </label>
      <label>
        <span>最小 Token</span>
        <input id="pendingMinTokens" type="number" min="0" step="1" value="${escapeHtml(filters.min_tokens || "")}">
      </label>
      <label>
        <span>最大 Token</span>
        <input id="pendingMaxTokens" type="number" min="0" step="1" value="${escapeHtml(filters.max_tokens || "")}">
      </label>
    </div>
    <div class="pending-list">
      ${rows.map((row) => `
        <article class="pending-row" data-question-id="${escapeHtml(row.question_id || "")}" data-row-index="${escapeHtml(row._row_index ?? "")}" data-csv-path="${escapeHtml(path)}">
          <header>
            <strong>${escapeHtml(row.question_id || `row-${Number(row._row_index || 0) + 1}`)}</strong>
            <span>${escapeHtml(row.sample_id || "")} · C${escapeHtml(row.category || "-")} · tokens ${escapeHtml(row.injection_tokens_est || "-")}</span>
          </header>
          <p>${escapeHtml(row.question || "-")}</p>
          <div class="answer-grid">
            <section><span>标准答案</span><p>${escapeHtml(row.answer || "-")}</p></section>
            <section><span>模型回答</span><p>${escapeHtml(row.response || "-")}</p></section>
          </div>
        </article>
      `).join("")}
    </div>
  `;
  $("refreshPendingPreview")?.addEventListener("click", () => renderPendingJudgePanel(path).catch((e) => toast(e.message)));
  $("exportPendingCsv")?.addEventListener("click", () => exportPendingCsv(path).catch((e) => toast(e.message)));
  $("pendingRunJudge")?.addEventListener("click", () => runJudgeForCurrentResult({
    requireConfirm: true,
    filterPayload: {only_pending: true},
    name: "judge all pending",
  }).catch((e) => toast(e.message)));
}

async function exportPendingCsv(path) {
  const filters = pendingFilterPayload();
  const qs = new URLSearchParams({path});
  if (filters.category) qs.set("category", filters.category);
  if (filters.query) qs.set("q", filters.query);
  if (filters.min_tokens) qs.set("min_tokens", filters.min_tokens);
  if (filters.max_tokens) qs.set("max_tokens", filters.max_tokens);
  const data = await api(`/api/export-pending-csv?${qs.toString()}`);
  renderArtifactList([
    ["待判结果", data.output || ""],
    ["来源结果", data.input || path],
    ["行数", `${data.rows ?? 0}`],
  ]);
  toast(`已导出待判样本：${data.rows} 行`);
}

async function refreshCommitSummary(path) {
  if (!path) return;
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  const summary = JSON.parse(data.text || "{}");
  const smoke = isSingleSessionImportSummary(summary);
  const status = summary.status || "-";
  const first = (summary.records || [])[0] || {};
  const complete = (first.integrity || "").toLowerCase() === "complete" && Number(first.pending_message_count_after_commit ?? 0) === 0;
  const expected = summary.expected_messages ?? first.expected_messages ?? "-";
  const submitted = summary.submitted_messages ?? first.submitted_messages ?? "-";
  const banner = $("importCompletionBanner");
  if (banner) {
    banner.hidden = false;
    banner.className = `completion-banner ${complete ? "ok" : "warn"}`;
    banner.textContent = complete
      ? (smoke
        ? `单 session 注入测试完成：${submitted}/${expected} 条对话消息已写入，记忆已落盘。`
        : `导入完成：${submitted}/${expected} 条对话消息已写入，记忆已落盘。`)
      : `导入未完全完成：${submitted}/${expected} 条对话消息，状态 ${first.integrity || status}，请检查日志。`;
  }
  saveLastImport({
    workspace: $("ovWorkspace").value.trim(),
    account: $("ovAccount").value.trim() || "default",
    sample_value: $("importSample").value || "",
    sample_label: $("importSample")?.selectedOptions?.[0]?.textContent || "",
    output_file: path,
    session_id: first.session_id || "",
    integrity: first.integrity || "",
  });
  if ($("memoryWorkspace")) $("memoryWorkspace").value = $("ovWorkspace").value.trim();
  if ($("memoryAccount")) $("memoryAccount").value = $("ovAccount").value.trim() || "default";
  $("importProgressBar").style.width = complete ? "100%" : $("importProgressBar").style.width;
  $("importProgressText").textContent = complete
    ? (smoke
      ? `单 session 注入测试完成：对话消息 ${submitted}/${expected} 条，归档后待处理 ${first.pending_message_count_after_commit ?? "-"}。`
      : `导入完成：对话消息 ${submitted}/${expected} 条，归档后待处理 ${first.pending_message_count_after_commit ?? "-"}。`)
    : `导入结束但需要检查：对话消息 ${submitted}/${expected} 条，完整性 ${first.integrity || "-"}.`;
  toast(complete ? (smoke ? "单 session 注入测试完成，记忆已落盘" : "导入完成，记忆已落盘") : "导入结束，请检查完整性");
  renderKpis("commitKpis", [
    ["模式", smoke ? "单 session 测试" : "正式导入"],
    ["完整性", first.integrity === "complete" ? "完整" : (first.integrity === "incomplete" ? "未完成" : (summary.incomplete_samples ? "未完成" : "完整"))],
    ["会话数", summary.samples ?? "-"],
    ["对话消息", `${summary.submitted_messages ?? "-"} / ${summary.expected_messages ?? "-"}`],
    ["待处理", first.pending_message_count_after_commit ?? "-"],
    ["会话 ID", first.session_id || "-"],
    ["Token 估算", summary.estimated_import_tokens ?? "-"],
  ]);
  const importFolder = dirname(path);
  $("importMemoryPreview").innerHTML = `
    <article class="path-row">
      <span>导入文件夹</span>
      <code>${escapeHtml(importFolder)}</code>
    </article>
  ` + (summary.records || []).map((record) => `
    <article class="memory-hit">
      <strong>${escapeHtml(record.sample_id || "-")} · 会话 ${escapeHtml(record.session_id || "-")}</strong>
      <p>对话消息 ${escapeHtml(record.submitted_messages ?? "-")} / ${escapeHtml(record.expected_messages ?? "-")} · 归档后待处理 ${escapeHtml(record.pending_message_count_after_commit ?? "-")} · 完整性 ${escapeHtml(record.integrity || "-")} · Token ${escapeHtml(record.estimated_import_tokens ?? "-")}</p>
    </article>
  `).join("");
  await checkImportIntegrity(path).catch((e) => {
    $("importIntegrityPanel").innerHTML = `<p class="bad-text">${escapeHtml(e.message)}</p>`;
  });
}

function renderIntegrity(data) {
  const statusClass = data.status === "complete" ? "ok" : (data.status === "warning" ? "warn" : "bad");
  const memoryLabel = data.memory_label || memoryBackendLabel(data.backend || currentMemoryBackend());
  const probe = data.evidence_probe || {};
  const probeRows = (probe.results || []).map((item) => {
    const missing = (item.memory_groups || [])
      .filter((group) => !group.ok)
      .map((group) => (group.terms || []).join(" / "))
      .join("；") || "-";
    const cls = item.status === "pass" ? "ok" : (item.status === "missing" ? "bad" : "warn");
    const label = item.status === "pass" ? "PASS" : (item.status === "partial" ? "PARTIAL" : (item.status === "fact_only" ? "FACT ONLY" : (item.status === "archive_only" ? "ARCHIVE ONLY" : "MISSING")));
    return `
      <article class="memory-hit ${cls}">
        <strong>${escapeHtml(label)} · ${escapeHtml(item.question_id || "")}</strong>
        <p>${escapeHtml(item.question || "")}</p>
        <small>标准答案：${escapeHtml(item.gold || "-")}</small>
        <small>memory exact ${escapeHtml(item.evidence_memory_hits ?? 0)}/${escapeHtml(item.evidence_total ?? 0)} · archive exact ${escapeHtml(item.evidence_archive_hits ?? 0)}/${escapeHtml(item.evidence_total ?? 0)} · missing facts: ${escapeHtml(missing)}</small>
        <p><b>${escapeHtml(item.diagnosis || "")}</b>：${escapeHtml(item.diagnosis_detail || "")}</p>
        <small>建议：${escapeHtml(item.recommended_action || "-")}</small>
      </article>
    `;
  }).join("");
  $("importIntegrityPanel").innerHTML = `
    <div class="integrity-head ${statusClass}">
      <div>
        <strong>${data.status === "complete" ? "记忆导入完整" : (data.status === "warning" ? "导入基本完成，有提醒" : "导入不完整")}</strong>
        <p>${escapeHtml(memoryLabel)} · ${escapeHtml(data.submitted_messages ?? "-")} / ${escapeHtml(data.expected_messages ?? "-")} 条对话消息 · ${escapeHtml(data.session_count ?? 0)} 个 session · artifact files ${escapeHtml(data.memory_files ?? 0)}</p>
      </div>
      <span>${escapeHtml(data.status || "-")}</span>
    </div>
    <div class="integrity-checks">
      ${(data.checks || []).map((item) => `
        <article class="${item.ok ? "ok" : (item.level === "warn" ? "warn" : "bad")}">
          <strong>${escapeHtml(item.ok ? "通过" : (item.level === "warn" ? "警告" : "失败"))} · ${escapeHtml(item.name || "-")}</strong>
          <p>${escapeHtml(item.message || "")}</p>
        </article>
      `).join("")}
    </div>
    ${probe.enabled ? `
      <details class="integrity-sessions" open>
        <summary>LoCoMo 证据检查 · 通过 ${escapeHtml(probe.counts?.pass ?? 0)} · 部分 ${escapeHtml(probe.counts?.partial ?? 0)} · 仅事实 ${escapeHtml(probe.counts?.fact_only ?? 0)} · 仅原文 ${escapeHtml(probe.counts?.archive_only ?? 0)} · 缺失 ${escapeHtml(probe.counts?.missing ?? 0)}</summary>
        <p>这里检查标准证据是否进入长期记忆；“部分”表示只抽出部分原始证据；“仅事实”表示事实词进入记忆但原始证据不完整；“仅原文”表示原始会话里有，但长期记忆没抽出来。</p>
        <div class="memory-list">${probeRows || "<p>没有 probe 结果。</p>"}</div>
      </details>
    ` : ""}
    <details class="integrity-sessions">
      <summary>会话明细</summary>
      ${(data.sessions || []).map((item) => `
        <article class="path-row">
          <span>${escapeHtml(item.ok ? "通过" : "待查")} · ${escapeHtml(item.session_key || "")}</span>
          <code>${escapeHtml(item.session_path || item.session_id || "")}</code>
          <button class="path-copy" type="button" data-copy="${escapeHtml(item.session_path || "")}">复制</button>
        </article>
      `).join("") || "<p>没有会话明细。</p>"}
    </details>
    <div class="path-row">
      <span>摘要</span>
      <code>${escapeHtml(data.summary_path || "")}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(data.summary_path || "")}">复制</button>
    </div>
    <div class="path-row">
      <span>${escapeHtml(data.backend === "echomemory" ? "EchoMemory 根目录" : "记忆根目录")}</span>
      <code>${escapeHtml(data.memory_root || "")}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(data.memory_root || "")}">复制</button>
    </div>
  `;
  bindCopyButtons("#importIntegrityPanel");
  renderImportStageRail({complete: data.status === "complete", warn: data.status !== "complete"});
  renderLocomoOverview();
  refreshLocomoFlowStatus(true).catch(() => {});
}

async function checkImportIntegrity(summaryPath = "") {
  const lastImport = readLastImport();
  const workspace = $("ovWorkspace").value.trim() || lastImport.workspace || "";
  const account = $("ovAccount").value.trim() || lastImport.account || "default";
  const sampleId = currentImportNamespace().sampleId || "";
  const summary = summaryPath || lastImport.output_file || "";
  const backend = normalizeMemoryBackend(lastImport.backend || state.currentImportTask?.meta?.config?.backend || currentMemoryBackend());
  if (!workspace) return toast("请先选择或导入一个记忆目录");
  const qs = new URLSearchParams({backend, workspace, account, sample: sampleId});
  if (summary) qs.set("summary", summary);
  $("importIntegrityPanel").innerHTML = `<p>正在检查 ${escapeHtml(memoryBackendLabel(backend))} summary、session 和记忆文件...</p>`;
  const data = await api(`/api/memory-import-integrity?${qs.toString()}`);
  renderIntegrity(data);
  toast(data.status === "complete" ? "完整性检查通过" : "完整性检查有问题，请看详情");
  return data;
}

async function renderPreview(path) {
  const data = await api(`/api/csv-preview?path=${encodeURIComponent(path)}&limit=5`);
  const rows = data.rows || [];
  const html = rows.map((row) => `
    <article class="sample-row result-card ${resultClass(row)}">
      <header class="result-card-head">
        <div>
          <span class="badge ${resultClass(row)}">${escapeHtml(resultLabel(row))}</span>
          <small>${escapeHtml(resultMeta(row) || "-")}</small>
        </div>
        ${((row.result || "").trim()) ? "" : `<button class="secondary judge-row-button" type="button">判分此结果</button>`}
      </header>
      <section class="question-strip">
        <span>问题</span>
        <h3>${escapeHtml(row.question || "-")}</h3>
      </section>
      <div class="answer-grid">
        <section>
          <span>标准答案</span>
          <p>${escapeHtml(row.answer || "-")}</p>
        </section>
        <section>
          <span>模型回答</span>
          <p>${escapeHtml(row.response || "-")}</p>
        </section>
      </div>
      <section class="judge-detail ${resultClass(row)}">
        <span>${((row.result || "").trim()) ? "判分原因" : "判分状态"}</span>
        <p>${escapeHtml(judgeReason(row) || "尚未执行判分。点击上方按钮后会写入正式判定和原因。")}</p>
      </section>
      <details class="evidence-details" open>
        ${(() => {
          const items = parseEvidence(row.relevant_memory);
          const counts = evidenceCounts(row, items);
          const archiveText = counts.archive > 0 ? ` · 会话补充 ${escapeHtml(counts.archive)}` : "";
          return `<summary>证据 · ${escapeHtml(row.retrieval_count || items.length || 0)} 条 · 长期记忆 ${escapeHtml(counts.memory)}${archiveText}</summary>`;
        })()}
        <div class="evidence-list">${renderEvidenceList(row)}</div>
      </details>
    </article>
  `).join("") || "<p>暂无结果</p>";
  $("sampleRows").innerHTML = html;
  if ($("evalSampleRows")) {
    $("evalSampleRows").innerHTML = html;
    $("evalSampleRows").hidden = !rows.length;
  }
  bindCopyButtons("#sampleRows");
  bindOpenButtons("#sampleRows");
  if ($("evalSampleRows")) {
    bindCopyButtons("#evalSampleRows");
    bindOpenButtons("#evalSampleRows");
  }
  document.querySelectorAll(".judge-row-button").forEach((button) => {
    button.addEventListener("click", () => runJudgeForCurrentResult().catch((e) => toast(e.message)));
  });
}

function renderChat() {
  $("chatTranscript").innerHTML = state.chatMessages.map((msg) => `
    <article class="chat-bubble ${escapeHtml(msg.role)} ${msg.pending_archive ? "pending-archive" : ""} ${msg.archive_error ? "archive-error" : ""}">
      <strong>${msg.role === "user" ? "你" : "MemoryBench Agent"}</strong>
      <p>${escapeHtml(msg.content)}</p>
      ${msg.archive ? renderArchiveChatDetails(msg.archive) : ""}
    </article>
  `).join("") || `
    <article class="chat-bubble assistant">
      <strong>MemoryBench Agent</strong>
      <p>输入问题后会显示回答和召回记忆。</p>
    </article>
  `;
  $("chatTranscript").scrollTop = $("chatTranscript").scrollHeight;
  renderArchiveStatus();
  renderChatDebugStrip();
  saveChatDraft(currentAccount(), state.chatMessages);
}

function chatDebugMetric(label, value, detail = "", tone = "") {
  return `
    <article class="chat-debug-card ${escapeHtml(tone)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "-")}</strong>
      ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}
    </article>
  `;
}

function chatMemorySpaceInfo(record = null) {
  const account = currentAccount();
  const cfg = readAccountConfig(account);
  const accountState = accountRecord(account);
  const isolation = accountState?.isolation || {};
  const backend = normalizeMemoryBackend(record?.backend || cfg.memoryBackend || currentMemoryBackend());
  const backendLabel = memoryBackendLabel(backend);
  const lastImport = readLastImport();
  const workspace = String(
    cfg.memoryWorkspace
    || cfg.ovWorkspace
    || record?.workspace
    || record?.openviking_paths?.workspace
    || record?.echomemory_paths?.workspace
    || lastImport.workspace
    || $("memoryWorkspace")?.value
    || $("ovWorkspace")?.value
    || ""
  ).trim();
  const expectedReadRoot = String(storageRootForBackend(workspace, account, backend) || "").trim();
  const readRoot = String(
    (isolation.backend === backend && isolation.workspace === workspace ? isolation.storage_root : "")
    || expectedReadRoot
    || ""
  ).trim();
  const backendRoot = String(
    record?.openviking_paths?.viking_root
    || record?.openviking_paths?.account_dir
    || record?.echomemory_paths?.account_dir
    || record?.echomemory_paths?.memory_root
    || readRoot
    || ""
  ).trim();
  const fileCounts = {
    total: Number(isolation.file_count || 0),
    sessions: Number(isolation.session_file_count || 0),
    memory: Number(isolation.memory_file_count || 0),
    atoms: Number(isolation.atom_file_count || 0),
    retrievable: Number(isolation.retrievable_memory_file_count || 0),
  };
  const hasCount = Boolean(isolation.storage_root && isolation.workspace === workspace && isolation.backend === backend);
  return {account, backend, backendLabel, workspace, readRoot, backendRoot, fileCounts, hasCount, empty: hasCount && fileCounts.retrievable === 0};
}

function memoryCountLabel(info = {}) {
  if (!info.hasCount) return "待检查";
  const counts = info.fileCounts || {};
  if (Number(counts.atoms || 0)) {
    const parts = [`结构化记忆 ${Number(counts.atoms || 0).toLocaleString()} atoms`];
    if (Number(counts.sessions || 0)) parts.push(`sessions ${Number(counts.sessions || 0).toLocaleString()}`);
    if (Number(counts.memory || 0)) parts.push(`memory ${Number(counts.memory || 0).toLocaleString()}`);
    return parts.join(" · ");
  }
  if (!Number(counts.retrievable || 0)) {
    const total = Number(counts.total || 0);
    return total ? `暂无长期记忆 · ${total.toLocaleString()} 个系统文件` : "未导入记忆";
  }
  const parts = [`${Number(counts.total || 0).toLocaleString()} 个文件`];
  if (Number(counts.memory || 0)) parts.push(`memory ${Number(counts.memory || 0).toLocaleString()}`);
  if (Number(counts.atoms || 0)) parts.push(`atoms ${Number(counts.atoms || 0).toLocaleString()}`);
  if (Number(counts.sessions || 0)) parts.push(`sessions ${Number(counts.sessions || 0).toLocaleString()}`);
  return parts.join(" · ");
}

function currentConfiguredModelInfo(account = currentAccount()) {
  const cfg = readAccountConfig(account);
  const active = account === currentAccount();
  const activeAgent = active ? agentModelConfig() : null;
  const model = String(
    activeAgent?.model
    || cfg.agentModel
    || cfg.judgeModel
    || state.config?.judge_model
    || "gpt-5.5"
  ).trim();
  const baseUrl = String(
    activeAgent?.baseUrl
    || cfg.agentBaseUrl
    || cfg.judgeBaseUrl
    || state.config?.judge_base_url
    || ""
  ).trim();
  return {
    model: model || "未配置",
    baseUrl,
    label: "当前大模型",
  };
}

function renderChatMemorySpace(record = null) {
  const target = $("chatMemorySpace");
  const info = chatMemorySpaceInfo(record);
  const modelInfo = currentConfiguredModelInfo(info.account);
  const structuredMemoryPath = info.backend === "echomemory" && info.readRoot
    ? `${info.readRoot}/memory/.structured/atoms`
    : "";
  const inline = $("chatMemorySpaceInline");
  if (inline) {
    inline.innerHTML = `
      <span>当前账户读取路径</span>
      <code>${escapeHtml(info.readRoot || "未配置")}</code>
      ${info.readRoot ? copyButtonHtml(info.readRoot) : ""}
      <em class="${info.empty ? "memory-space-empty" : "memory-space-count"}">${escapeHtml(memoryCountLabel(info))}</em>
      <em class="memory-space-model">${escapeHtml(modelInfo.label)}：${escapeHtml(modelInfo.model)}</em>
    `;
    bindCopyButtons("#chatMemorySpaceInline");
  }
  if (target) {
    target.innerHTML = `
      ${info.empty ? `
        <div class="memory-space-alert">
          当前账户没有已导入记忆。对话 agent 会读取这个路径，但现在没有可召回内容；请切到已导入 LoCoMo 的账户，或先在 LoCoMo 评测里完成记忆导入。
        </div>
      ` : ""}
      <div class="memory-space-row">
        <span>账户</span>
        <strong>${escapeHtml(info.account || "-")}</strong>
      </div>
      <div class="memory-space-row">
        <span>后端</span>
        <strong>${escapeHtml(info.backendLabel || "-")}</strong>
      </div>
      <div class="memory-space-row">
        <span>文件数</span>
        <strong>${escapeHtml(memoryCountLabel(info))}</strong>
      </div>
      <div class="memory-space-path">
        <span>当前账户读取路径</span>
        <code>${escapeHtml(info.readRoot || "未配置")}</code>
        ${info.readRoot ? copyButtonHtml(info.readRoot) : ""}
      </div>
      ${structuredMemoryPath ? `
        <div class="memory-space-path">
          <span>EchoMemory 结构化记忆目录</span>
          <code>${escapeHtml(structuredMemoryPath)}</code>
          ${copyButtonHtml(structuredMemoryPath)}
        </div>
        <div class="memory-space-row subtle">
          <span>说明</span>
          <strong>长期记忆写在隐藏目录 memory/.structured 下；Finder 默认可能看起来像空目录。</strong>
        </div>
      ` : ""}
      <div class="memory-space-path">
        <span>记忆目录</span>
        <code>${escapeHtml(info.workspace || "未配置")}</code>
        ${info.workspace ? copyButtonHtml(info.workspace) : ""}
      </div>
      <div class="memory-space-path">
        <span>后端返回目录</span>
        <code>${escapeHtml(info.backendRoot || "未配置")}</code>
        ${info.backendRoot ? copyButtonHtml(info.backendRoot) : ""}
      </div>
    `;
    bindCopyButtons("#chatMemorySpace");
  }
}

function chatDefaultPreviewQuestion() {
  const info = chatMemorySpaceInfo();
  const backend = memoryBackendLabel(info.backend || currentMemoryBackend());
  return [
    "请只做当前账户长期记忆的只读预览。",
    `当前后端是 ${backend}，当前账户是 ${info.account || currentAccount()}。`,
    "请召回这个账户下最能代表当前目录的相关记忆，用于界面展示证据。",
    "不要写入记忆，不要进行闲聊。",
  ].join("\n");
}

function chatContextPreviewKey(messages = state.chatMessages) {
  const info = chatMemorySpaceInfo();
  const lastUser = [...messages].reverse().find((item) => item.role === "user")?.content || "";
  return [
    info.backend,
    info.account,
    info.workspace,
    info.readRoot,
    $("chatTopK")?.value || "",
    lastUser || "__default__",
  ].join("|");
}

function renderChatContextPlaceholder(reason = "") {
  renderChatPersona(state.lastChatContextData);
  const info = chatMemorySpaceInfo();
  const countText = memoryCountLabel(info);
  const detail = reason || (info.empty
    ? "当前账户还没有可读取的长期记忆；导入 LoCoMo 后这里会显示记忆证据。"
    : "正在根据当前账户目录准备只读预览。");
  if ($("contextTrace")) {
    $("contextTrace").innerHTML = `
      <div class="trace-kpis context-summary">
        <article><span>阶段</span><strong>ready</strong></article>
        <article><span>账户</span><strong>${escapeHtml(info.account || "-")}</strong></article>
        <article><span>后端</span><strong>${escapeHtml(info.backendLabel || "-")}</strong></article>
        <article><span>记忆文件</span><strong>${escapeHtml(countText)}</strong></article>
      </div>
      <div class="trace-section">
        <b>上下文组装</b>
        <p>${escapeHtml(detail)}</p>
        <p>系统人设会固定注入；提问后会按问题召回相关记忆并展示证据。</p>
      </div>
    `;
  }
  if ($("memoryEvidence")) {
    $("memoryEvidence").innerHTML = `
      <article class="memory-hit">
        <strong>当前账户目录</strong>
        <small>${escapeHtml(info.backendLabel || "-")} · ${escapeHtml(countText)}</small>
        <p>${escapeHtml(detail)}</p>
        ${info.readRoot ? `<p><code>${escapeHtml(info.readRoot)}</code></p>${copyButtonHtml(info.readRoot)}` : ""}
      </article>
    `;
    bindCopyButtons("#memoryEvidence");
  }
}

function stripPromptTags(text = "") {
  return String(text || "")
    .replace(/<\/?(agent_charter|behavior_policy|retrieved_memory|recent_conversation|current_request)[^>]*>/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function defaultSystemPersona(backend = currentMemoryBackend()) {
  return [
    "你是一个接入当前记忆后端的任务型 Agent。",
    "回答要准确、直接、可验证，先给结论，再给关键依据。",
    "你具备长期记忆检索能力。请优先基于当前记忆后端返回的证据作答。",
    "回答时先给结论，再给证据与推理；尽量明确时间、人物、公司与因果关系。",
  ].join("\n");
}

function personaFromContextData(data = null) {
  const blocks = data?.context_trace?.blocks || [];
  const charter = blocks.find((block) => String(block.kind || "").toLowerCase() === "charter")
    || blocks.find((block) => /agent|章程|人设/i.test(String(block.title || "")));
  const policy = blocks.find((block) => String(block.kind || "").toLowerCase() === "policy")
    || blocks.find((block) => /policy|规则/i.test(String(block.title || "")));
  const parts = [];
  if (charter?.content) parts.push(stripPromptTags(charter.content));
  if (policy?.content) parts.push("行为规则：\n" + stripPromptTags(policy.content));
  return parts.filter(Boolean).join("\n\n");
}

function renderChatPersona(data = null) {
  const backend = normalizeMemoryBackend(data?.backend || currentMemoryBackend());
  const source = personaFromContextData(data);
  const body = source || defaultSystemPersona(backend);
  const title = source ? "本次实际组装进模型的 system prompt" : "当前记忆后端默认 system prompt";
  if ($("contextPersonaTitle")) $("contextPersonaTitle").textContent = title;
  if ($("contextPersonaBody")) $("contextPersonaBody").textContent = body;
}

async function loadChatDefaultContextPreview({force = false} = {}) {
  if (!backendSupportsAgentWorkbench()) {
    renderChatContextPlaceholder("当前后端暂未接入人工对话上下文预览。");
    return;
  }
  const info = chatMemorySpaceInfo();
  const content = $("chatInput")?.value.trim() || "";
  const isDefaultPreview = !content && !state.chatMessages.length;
  if (isDefaultPreview && info.empty) {
    state.chatContextPreviewKey = chatContextPreviewKey([{role: "user", content: chatDefaultPreviewQuestion()}]);
    renderChatContextPlaceholder("当前账户目录为空；导入 LoCoMo 后这里会展示相关记忆。");
    await loadChatTimelineEvidenceFallback("当前账户暂无可展示记忆。");
    return;
  }
  const messages = content
    ? [...state.chatMessages, {role: "user", content}]
    : (state.chatMessages.length ? state.chatMessages : [{role: "user", content: chatDefaultPreviewQuestion(), non_archivable: true}]);
  const key = chatContextPreviewKey(messages);
  if (!force && state.lastChatContextData && state.chatContextPreviewKey === key) {
    const cachedItems = state.lastChatContextData.retrieval?.items || [];
    if (cachedItems.length || !isDefaultPreview) {
      renderMemoryEvidence(cachedItems, (state.lastChatContextData.retrieval?.errors || [])[0] || "");
    } else {
      loadChatTimelineEvidenceFallback().catch(() => {});
    }
    renderContextTrace(state.lastChatContextData);
    return;
  }
  if (state.chatContextPreviewLoading) return;
  state.chatContextPreviewLoading = true;
  renderChatContextPlaceholder("正在读取当前账户的人设和相关记忆预览。");
  try {
    const data = await apiWithTimeout("/api/agent/context", {
      method: "POST",
      body: JSON.stringify(chatPayload({messages})),
    }, 5000);
    state.lastChatContextData = data;
    state.chatContextPreviewKey = key;
    const hits = (data.retrieval?.items || []).length;
    const retrievalError = (data.retrieval?.errors || [])[0] || "";
    const retrievalBadge = retrievalError
      ? `<span class="check warn">${escapeHtml(friendlyUiError(retrievalError, "检索异常"))}</span>`
      : `<span class="check ok">记忆正常</span>`;
    $("chatMeta").innerHTML = `
      <span class="check ok">上下文就绪</span>
      <span class="check">相关记忆 ${escapeHtml(hits)}</span>
      ${retrievalBadge}
    `;
    if (hits || !isDefaultPreview) {
      renderMemoryEvidence(data.retrieval?.items || [], retrievalError);
    } else {
      await loadChatTimelineEvidenceFallback("默认预览没有按查询召回到 evidence；下面展示当前账户最近的长期记忆文件。");
    }
    renderContextTrace(data);
  } catch (e) {
    const friendly = friendlyUiError(e.message, "上下文预览失败");
    renderChatContextPlaceholder(friendly);
    if (isDefaultPreview) await loadChatTimelineEvidenceFallback(`${friendly}；下面展示当前账户记忆概览。`);
  } finally {
    state.chatContextPreviewLoading = false;
    updateAgentWorkbenchControls();
  }
}

function chatAlignmentStatus(backend, topK, workbenchSupported) {
  const k = Number(topK || 0);
  if (!workbenchSupported) {
    return {
      value: "待接入",
      detail: `${memoryBackendLabel(backend)} 可跑 LoCoMo 批量评测；人工对话调试台尚未接入`,
      tone: "warn",
    };
  }
  if (k >= 30) {
    return {
      value: "LoCoMo 可比",
      detail: "召回条数 30 · 相关记忆 + 上下文追踪 · 人工对话不计正式分数",
      tone: "ok",
    };
  }
  return {
    value: "待确认",
    detail: "建议召回条数 30；正式准确率以 LoCoMo 评测报告为准",
    tone: "warn",
  };
}

function updateAgentWorkbenchControls(supported = backendSupportsAgentWorkbench()) {
  const backendLabel = memoryBackendLabel(currentMemoryBackend());
  const title = supported ? "" : `${backendLabel} 尚未实现人工对话工作台；LoCoMo 导入、QA、报告仍可使用该后端`;
  ["sendChat", "previewContext", "archiveChat"].forEach((id) => {
    const button = $(id);
    if (!button) return;
    const sendBusy = id === "sendChat" && state.chatSendInFlight;
    const archiveBusy = id === "archiveChat" && state.chatArchiveInFlight;
    const waitForAnswer = id === "archiveChat" && state.chatSendInFlight;
    button.disabled = !supported || sendBusy || archiveBusy || waitForAnswer;
    button.title = sendBusy ? "回答生成中，请稍等" : (archiveBusy ? "正在归档中，请稍等" : (waitForAnswer ? "回答生成中，请等待完成后再归档" : title));
  });
  const contextToggle = $("toggleContextPanel");
  if (contextToggle) contextToggle.hidden = false;
  if ($("chatInput")) $("chatInput").classList.toggle("muted-input", !supported);
}

function ensureAgentWorkbenchSupported(actionLabel = "对话") {
  if (backendSupportsAgentWorkbench()) return true;
  toast(`${memoryBackendLabel(currentMemoryBackend())} 暂未接入${actionLabel}工作台`);
  renderChatDebugStrip();
  return false;
}

function renderChatDebugStrip(record = null) {
  const strip = $("chatDebugStrip");
  renderChatMemorySpace(record);
  renderChatPersona(state.lastChatContextData);
  if (!strip) return;
  const account = currentAccount();
  const backend = currentMemoryBackend();
  const backendLabel = memoryBackendLabel(backend);
  const workbenchSupported = backendSupportsAgentWorkbench(backend);
  const lastImport = readLastImport();
  const workspace = ($("memoryWorkspace")?.value || $("ovWorkspace")?.value || lastImport.workspace || "").trim();
  const storageRoot = storageRootForBackend(workspace, account, backend);
  const stats = archiveStats();
  const thresholds = archiveThresholds();
  const newMessages = Math.max(0, archivableMessages().length - (state.lastArchivedMessageCount || 0));
  const topK = $("chatTopK")?.value || readAccountConfig(account).chatTopK || "30";
  const alignment = chatAlignmentStatus(backend, topK, workbenchSupported);
  if (!record && state.lastArchiveRecord && newMessages === 0) {
    record = state.lastArchiveRecord;
  }
  const lastSession = record?.session_id || lastImport.session_id || "";
  const saveState = record
    ? (record.committed ? "已保存" : "已提交")
    : (newMessages > 0 ? "未保存" : "只读回答");
  const saveDetail = record
    ? `会话 ${lastSession || "-"}`
    : (newMessages > 0 ? `${newMessages} 条新消息，点击“手动 commit”才写入` : "发送问题不会写入长期记忆");
  const contextSourceValue = workbenchSupported ? "相关记忆" : "待接入";
  const contextSourceDetail = workbenchSupported
    ? (backend === "echomemory" ? "EchoMemory find/search 证据；可展开上下文追踪" : "OpenViking user/agent 记忆；可展开上下文追踪")
    : `${backendLabel} 可跑 LoCoMo 批量评测；人工上下文追踪待接入`;
  strip.innerHTML = `
    ${chatDebugMetric("当前账户", account, backendLabel, "primary")}
    ${chatDebugMetric("Agent", "MemoryBench Agent", "OpenViking / EchoMemory 后端；正式分数以 LoCoMo 评测页为准", "primary")}
    ${chatDebugMetric("Agent 能力", workbenchSupported ? "可用" : "待接入", agentWorkbenchSupportText(backend), workbenchSupported ? "ok" : "warn")}
    ${chatDebugMetric("LoCoMo 对齐", alignment.value, alignment.detail, alignment.tone)}
    ${chatDebugMetric("上下文来源", contextSourceValue, contextSourceDetail, workbenchSupported ? "" : "warn")}
    ${chatDebugMetric("读写边界", saveState, saveDetail, record?.committed ? "ok" : (newMessages > 0 ? "warn" : ""))}
    ${chatDebugMetric("目录", compactPath(workspace || "未配置"), storageRoot ? `存储根：${compactPath(storageRoot, 24, 32)}` : "导入或系统配置后生成路径")}
    ${chatDebugMetric("召回参数", `${RETRIEVAL_COUNT_LABEL} ${topK}`, "对话页只读检索使用该数量")}
    ${chatDebugMetric("待保存上下文", `${stats.messages} 条 / ${stats.tokens} tokens`, `建议阈值 ${thresholds.messages} 条 / ${thresholds.tokens} tokens`)}
  `;
  updateAgentWorkbenchControls(workbenchSupported);
}

function archivableMessages(messages = state.chatMessages) {
  return messages.filter((item) => !item.non_archivable && !item.archive);
}

function archiveTriggerLabel(record = {}) {
  const value = record.trigger_reason || record.trigger || "";
  const labels = {
    manual_button_before_threshold: "手动保存，未达到建议阈值也已归档",
    manual_button_threshold_already_met: "手动保存，已达到建议阈值",
    threshold_auto_messages_or_tokens: "达到阈值后自动归档",
    manual_button: "手动保存",
  };
  return labels[value] || value || "-";
}

function renderArchiveChatDetails(record = {}) {
  const backend = normalizeMemoryBackend(record.backend || currentMemoryBackend());
  const backendLabel = memoryBackendLabel(backend);
  const paths = record.openviking_paths || record.echomemory_paths || {};
  const workspaceInput = record.workspace_input || "";
  const actualWorkspace = paths.workspace || record.workspace || "";
  const workspaceMismatch = workspaceInput && actualWorkspace && workspaceInput !== actualWorkspace;
  const extracted = record.session_after_commit?.memories_extracted || record.task?.result?.memories_extracted || {};
  const extractedTotal = extracted.total ?? Object.values(extracted).reduce((sum, value) => sum + (Number(value) || 0), 0);
  const tokenUsage = record.task?.result?.token_usage?.total?.total_tokens
    ?? record.session_after_commit?.llm_token_usage?.total_tokens
    ?? "-";
  const logRows = [
    ["日志目录", record.harness_log_dir || record.run_dir],
    ["操作记录", record.harness_summary_path || record.summary_path],
    ["对话记录", record.harness_transcript_path || record.transcript_path],
  ].filter(([, value]) => value !== undefined && value !== null && String(value) !== "");
  const backendRows = backend === "echomemory" ? [
    ["接入方式", "EchoMemory 本地 SDK"],
    ["EchoMemory 根目录", record.echomem_root],
    ["Runtime 配置", record.echomem_config],
    ["记忆根目录", paths.memory_root],
    ["账户目录", paths.account_dir],
    ["会话目录", paths.sessions_dir],
    ["Atoms 目录", paths.atoms_dir],
    ["会话目录", paths.session_dir],
    ["用户目录", paths.user_dir],
    ["Agent 目录", paths.agent_dir],
  ] : [
    ["服务地址", record.openviking_url],
    ["记忆根目录", paths.viking_dir],
    ["路径来源", paths.path_source],
    ["会话目录", paths.session_dir],
    ["用户目录", paths.user_dir],
    ["长期记忆目录", paths.user_memories_dir],
    ["Agent 目录", paths.agent_dir],
  ];
  const rows = [
    ["后端", backendLabel],
    ["实际目录", actualWorkspace],
    ["页面目录", workspaceInput],
    ["账户", record.account],
    ["用户 / Agent", `${record.user_id || "-"} / ${record.agent_id || "-"}`],
    ["会话 ID", record.session_id],
    ...backendRows,
    ["提交消息", record.submitted_messages],
    ["待处理", record.pending_after_commit],
    ["抽取记忆数", extractedTotal],
    ["用量", tokenUsage],
    ["任务 ID", record.task_id || record.task?.task_id || record.task?.id],
  ].filter(([, value]) => value !== undefined && value !== null && String(value) !== "");
  return `
    <div class="archive-chat-card">
      <div class="archive-chat-title">
        <strong>${record.committed ? "记忆保存完成" : "记忆已提交，等待确认"}</strong>
        <span>${escapeHtml(record.status || "")}</span>
      </div>
      <p>${escapeHtml(archiveTriggerLabel(record))}</p>
      <p>抽取记忆数：<code>${escapeHtml(extractedTotal)}</code>；会话：<code>${escapeHtml(record.session_id || "-")}</code></p>
      ${workspaceMismatch ? `<p class="archive-warning">实际写入目录和页面填写目录不一致，请以详情里的实际目录为准。</p>` : ""}
      ${Number(extractedTotal) === 0 ? `<p class="archive-warning">归档完成，但没有抽取出长期记忆。</p>` : ""}
      <details class="archive-log-details">
        <summary>保存详情</summary>
        <div class="archive-detail-grid">
          ${rows.map(([label, value]) => `
            <div>
              <span>${escapeHtml(label)}</span>
              <code>${escapeHtml(value)}</code>
            </div>
          `).join("")}
        </div>
      </details>
      <details class="archive-log-details">
        <summary>操作日志</summary>
        <div class="archive-detail-grid">
          ${logRows.map(([label, value]) => `
            <div>
              <span>${escapeHtml(label)}</span>
              <code>${escapeHtml(value)}</code>
            </div>
          `).join("") || "<p>没有本地日志路径。</p>"}
        </div>
      </details>
      <p>阈值：${escapeHtml(record.threshold?.messages ?? "-")} 条消息或 ${escapeHtml(record.threshold?.tokens_est ?? "-")} token；本次 ${escapeHtml(record.current?.messages ?? "-")} 条 / ${escapeHtml(record.current?.tokens_est ?? "-")} token。</p>
    </div>
  `;
}

function memoryTitle(item, index) {
  return item.uri || item.path || item.id || item.memory_id || `memory-${index + 1}`;
}

function memoryBody(item) {
  if (!item || typeof item !== "object") return String(item || "");
  return item.full_content || item.content || item.text || item.abstract || item.overview || item.summary || JSON.stringify(item).slice(0, 500);
}

function memoryMeta(item, index) {
  const parts = [`#${index + 1}`];
  for (const key of ["score", "rank", "time", "created_at", "source", "content_source", "_query"]) {
    if (item && item[key] != null && item[key] !== "") parts.push(`${key}: ${item[key]}`);
  }
  return parts.join(" · ");
}

function vikingUriToLocalPath(uri = "", scope = null) {
  const value = String(uri || "").trim();
  if (!value.startsWith("viking://")) return "";
  const {workspace, account} = normalizeEvidenceScope(scope || currentEvidenceScope()) || {};
  if (!workspace) return "";
  const rel = value.replace(/^viking:\/\//, "").replace(/^\/+/, "");
  if (!rel || rel.includes("..")) return "";
  return `${workspace.replace(/\/+$/, "")}/viking/${safeAccountSlug(account)}/${rel}`;
}

function findMemoryUri(item = {}) {
  for (const key of ["uri", "path", "source", "content_source", "id", "memory_id"]) {
    const value = String(item?.[key] || "").trim();
    if (value.startsWith("viking://")) return value;
    if (value.startsWith("/") && value.endsWith(".md")) return value;
  }
  const title = memoryTitle(item, 0);
  const match = String(title || "").match(/viking:\/\/[^\s)]+\.md/);
  return match ? match[0] : "";
}

function memoryLocalPath(item = {}) {
  const uri = findMemoryUri(item);
  if (uri.startsWith("/")) return uri;
  return vikingUriToLocalPath(uri, currentEvidenceScope());
}

function memoryOpenActions(item = {}) {
  const path = memoryLocalPath(item);
  if (!path || !path.endsWith(".md")) return "";
  const scope = currentEvidenceScope();
  return `
    <div class="memory-card-actions">
      <button class="secondary path-open" type="button" data-path="${escapeHtml(path)}" data-workspace="${escapeHtml(scope.workspace || "")}" data-account="${escapeHtml(scope.account || "default")}">打开 MD</button>
      <button class="secondary path-copy" type="button" data-copy="${escapeHtml(path)}">复制路径</button>
    </div>
  `;
}

function evidenceSource(item = {}) {
  const raw = String(item.source || item.content_source || item.uri || item.path || "").toLowerCase();
  if (raw.includes("archive_fallback") || raw.includes("session_archive") || raw.includes("/history/archive_")) {
    return {label: "会话补充", className: "archive"};
  }
  if (raw.includes("lexical_memory_file") || raw.includes("/memories/") || raw.includes("memory_file")) {
    return {label: "长期记忆", className: "memory"};
  }
  if (raw.includes("openviking")) {
    return {label: "记忆检索", className: "search"};
  }
  return {label: "证据", className: "generic"};
}

function evidenceCounts(row = {}, items = parseEvidence(row.relevant_memory)) {
  const archive = row.archive_fallback_count !== undefined && row.archive_fallback_count !== ""
    ? Number(row.archive_fallback_count)
    : items.filter((item) => evidenceSource(item).className === "archive").length;
  const memory = row.memory_hit_count !== undefined && row.memory_hit_count !== ""
    ? Number(row.memory_hit_count)
    : items.filter((item) => evidenceSource(item).className !== "archive").length;
  return {
    archive: Number.isFinite(archive) ? archive : 0,
    memory: Number.isFinite(memory) ? memory : 0,
  };
}

function evidenceModeLabel(row = {}, data = {}) {
  const d = data.diagnostics || {};
  return d.retrieval_mode || row.retrieval_mode || "strict_original_query";
}

function compactCountMap(map = {}, limit = 3) {
  const entries = Object.entries(map || {}).filter(([, value]) => Number(value || 0) > 0);
  if (!entries.length) return "-";
  return entries
    .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))
    .slice(0, limit)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");
}

function healthClass(value = "") {
  const text = String(value || "").toLowerCase();
  if (!text || text === "ok" || text === "succeeded") return "ok";
  if (text.includes("rate") || text.includes("error") || text.includes("failed") || text.includes("timeout")) return "bad";
  if (text.includes("no_answer_token") || text.includes("unknown") || text.includes("pending")) return "warn";
  return "warn";
}

function renderDiagnosticPanel(data = {}, row = {}) {
  const d = data.diagnostics || {};
  const status = d.health_status || row.health_status || "-";
  const retrievalStatus = d.retrieval_status || row.retrieval_status || "-";
  const answerStatus = d.answer_status || row.answer_status || "-";
  const modelStatus = d.model_status || row.model_status || "-";
  const archiveRaw = d.archive_fallback_count ?? row.archive_fallback_count ?? "0";
  const archive = Number(archiveRaw || 0);
  const memory = d.memory_hit_count ?? row.memory_hit_count ?? "-";
  const archiveText = archive > 0 ? ` · 会话补充 ${archive}` : "";
  const retrievalError = d.retrieval_error || row.retrieval_error || "";
  const modelError = d.model_error || row.model_error || "";
  return `
    <details class="diagnostic-panel ${healthClass(status)}">
      <summary class="diagnostic-head">
        <span>链路健康</span>
        <strong>${escapeHtml(status)}</strong>
      </summary>
      <div class="diagnostic-body">
        <div class="detail-meta-grid diagnostic-grid">
          <article><span>检索</span><strong>${escapeHtml(retrievalStatus)}</strong></article>
          <article><span>回答</span><strong>${escapeHtml(answerStatus)}</strong></article>
          <article><span>模型</span><strong>${escapeHtml(modelStatus)}</strong></article>
          <article><span>模式</span><strong>${escapeHtml(evidenceModeLabel(row, data))}</strong></article>
          <article><span>证据</span><strong>长期记忆 ${escapeHtml(memory)}${archiveText}</strong></article>
          <article><span>检索 Token</span><strong>${escapeHtml(d.retrieval_tokens_est || row.retrieval_tokens_est || "-")}</strong></article>
          <article><span>回答 Token</span><strong>${escapeHtml(d.answer_total_tokens || row.answer_total_tokens || "-")}</strong></article>
        </div>
        ${retrievalError ? `<p class="diagnostic-error"><strong>检索错误</strong>${escapeHtml(retrievalError)}</p>` : ""}
        ${modelError ? `<p class="diagnostic-error"><strong>模型错误</strong>${escapeHtml(modelError)}</p>` : ""}
      </div>
    </details>
  `;
}

function renderEvidenceCard(item, index) {
  const source = evidenceSource(item);
  return `
    <article class="evidence-card source-${source.className}">
      <div class="evidence-card-head">
        <strong>${escapeHtml(memoryTitle(item, index))}</strong>
        <span class="source-badge ${source.className}">${escapeHtml(source.label)}</span>
      </div>
      <small>${escapeHtml(memoryMeta(item, index))}</small>
      <p>${escapeHtml(memoryBody(item)).slice(0, 900)}</p>
      ${memoryOpenActions(item)}
    </article>
  `;
}

function renderMemoryEvidence(items = [], error = "") {
  if (error) {
    $("memoryEvidence").innerHTML = `
      <article class="memory-hit warn">
        <strong>记忆检索暂不可用</strong>
        <p>${escapeHtml(friendlyUiError(error, "检索失败，请查看日志"))}</p>
        ${errorDetailHtml(error)}
      </article>
    `;
    return;
  }
  const visible = items.slice(0, 12);
  $("memoryEvidence").innerHTML = items.length ? `
    <p class="memory-note">本次回答召回 ${escapeHtml(items.length)} 条相关记忆，下面展示前 ${escapeHtml(visible.length)} 条。</p>
    ${visible.map((item, index) => `
      <article class="memory-hit">
        <div class="memory-hit-title">
          <strong>${escapeHtml(memoryTitle(item, index))}</strong>
          <span class="source-badge">${escapeHtml(item.context_type || item.source || item.content_source || "memory")}</span>
        </div>
        <small>${escapeHtml(memoryMeta(item, index))}</small>
        <p>${escapeHtml(memoryBody(item)).slice(0, 1200)}</p>
        ${memoryOpenActions(item)}
      </article>
    `).join("")}
  ` : "<p>本次问题没有召回相关记忆。</p>";
  bindCopyButtons("#memoryEvidence");
  bindOpenButtons("#memoryEvidence");
}

function renderMemoryTimelineEvidence(items = [], note = "") {
  if (!$("memoryEvidence")) return;
  $("memoryEvidence").innerHTML = items.length ? `
    ${note ? `<p class="memory-note">${escapeHtml(note)}</p>` : ""}
    ${items.slice(0, 6).map((item, index) => `
      <article class="memory-hit" data-memory-path="${escapeHtml(item.path || "")}">
        <strong>${escapeHtml(item.title || item.uri || `记忆 ${index + 1}`)}</strong>
        <small>${escapeHtml([item.kind, item.date, item.uri].filter(Boolean).join(" · "))}</small>
        <p>${escapeHtml(item.snippet || "")}</p>
        ${item.path ? `${copyButtonHtml(item.path)} <button class="path-open" type="button" data-path="${escapeHtml(item.path)}">打开</button>` : ""}
      </article>
    `).join("")}
  ` : `
    <article class="memory-hit">
      <strong>当前账户暂无可展示记忆</strong>
      <small>没有找到 timeline memory 文件</small>
      <p>完成 LoCoMo 记忆导入后，这里会在未提问时展示当前账户的记忆概览；提问后展示本次问题召回到的相关记忆。</p>
    </article>
  `;
  bindCopyButtons("#memoryEvidence");
  bindOpenButtons("#memoryEvidence");
}

async function loadChatTimelineEvidenceFallback(note = "") {
  const {workspace, account, backend} = currentWorkspaceAndAccount();
  if (!workspace) {
    renderMemoryTimelineEvidence([], "当前账户还没有配置记忆目录。");
    return;
  }
  const qs = new URLSearchParams({backend, workspace, account, q: "", limit: "12"});
  try {
    const timeline = await api(`/api/memory-timeline?${qs.toString()}`);
    renderMemoryTimelineEvidence(timeline.items || [], note || "未输入问题时展示当前账户最近的记忆文件；输入问题后会替换成本次召回证据。");
  } catch (e) {
    renderChatContextPlaceholder(`读取当前账户记忆概览失败：${e.message}`);
  }
}

function estimateTextTokens(value) {
  const text = String(value || "");
  if (!text) return 0;
  let ascii = 0;
  for (const ch of text) if (ch.charCodeAt(0) < 128) ascii += 1;
  const other = text.length - ascii;
  return Math.max(1, Math.ceil(ascii / 4 + other / 1.6));
}

function archiveStats(messages = state.chatMessages) {
  const source = archivableMessages(messages);
  const text = source.map((item) => item.content || "").join("\n");
  return {
    messages: source.length,
    tokens: estimateTextTokens(text),
    newMessages: Math.max(0, source.length - (state.lastArchivedMessageCount || 0)),
  };
}

function archiveThresholds() {
  return {
    messages: ARCHIVE_MESSAGE_THRESHOLD,
    tokens: ARCHIVE_TOKEN_THRESHOLD,
  };
}

function archiveThresholdMet(messages = state.chatMessages, sinceLast = false) {
  const thresholds = archiveThresholds();
  const source = sinceLast ? archivableMessages(messages).slice(state.lastArchivedMessageCount || 0) : archivableMessages(messages);
  const stats = archiveStats(source);
  return stats.messages >= thresholds.messages || stats.tokens >= thresholds.tokens;
}

function renderArchiveStatus(record = null) {
  const box = $("archiveStatus");
  if (!box) return;
  if (record) state.lastArchiveRecord = record;
  const thresholds = archiveThresholds();
  const stats = archiveStats();
  const messages = archivableMessages();
  const newMessages = Math.max(0, messages.length - (state.lastArchivedMessageCount || 0));
  const newStats = archiveStats(messages.slice(state.lastArchivedMessageCount || 0));
  const met = newStats.messages >= thresholds.messages || newStats.tokens >= thresholds.tokens;
  if (!record && state.lastArchiveRecord && newMessages === 0) {
    record = state.lastArchiveRecord;
  }
  if (record) {
    const backend = normalizeMemoryBackend(record.backend || currentMemoryBackend());
    const paths = record.openviking_paths || record.echomemory_paths || {};
    const memoryRoot = backend === "echomemory"
      ? (paths.memory_root || paths.account_dir)
      : (paths.viking_dir || paths.user_memories_dir);
    const longMemoryDir = backend === "echomemory"
      ? (paths.atoms_dir || paths.memory_root)
      : paths.user_memories_dir;
    box.innerHTML = `
      <div class="archive-line ${record.committed ? "ok" : "warn"}">
        <strong>${record.committed ? "记忆保存完成" : "记忆已提交"}</strong>
        <span>${escapeHtml(archiveTriggerLabel(record))}</span>
      </div>
      <p>阈值：${escapeHtml(record.threshold?.messages ?? thresholds.messages)} 条 / ${escapeHtml(record.threshold?.tokens_est ?? thresholds.tokens)} tokens；本次 ${escapeHtml(record.current?.messages ?? stats.messages)} 条 / ${escapeHtml(record.current?.tokens_est ?? stats.tokens)} tokens。</p>
      <p>Session：<code>${escapeHtml(record.session_id || "-")}</code></p>
      <p>抽取记忆数：<code>${escapeHtml(record.session_after_commit?.memories_extracted?.total ?? record.task?.result?.memories_extracted?.total ?? 0)}</code></p>
      <p>记忆后端：<code>${escapeHtml(memoryBackendLabel(backend))}</code></p>
      <p>记忆根目录：<code>${escapeHtml(memoryRoot || "未填写目录")}</code></p>
      <p>会话目录：<code>${escapeHtml(paths.session_dir || "未填写目录")}</code></p>
      <p>长期记忆目录：<code>${escapeHtml(longMemoryDir || "未填写目录")}</code></p>
    `;
    renderChatDebugStrip(record);
    return;
  }
  box.innerHTML = `
    <div class="archive-line ${met ? "ok" : ""}">
      <strong>${met ? "建议归档" : "尚未归档"}</strong>
      <span>${escapeHtml(newMessages)} 条新消息</span>
    </div>
    <p>点击“手动 commit”保存当前对话。</p>
  `;
  renderChatDebugStrip();
}

function archivePayload(trigger = "manual_button") {
  if (!ensureAgentWorkbenchSupported("手动 commit")) return null;
  const payload = chatPayload({
    messages: archivableMessages(),
    workspace: ($("memoryWorkspace")?.value || $("ovWorkspace")?.value || readLastImport().workspace || "").trim(),
    trigger,
    archive_message_threshold: ARCHIVE_MESSAGE_THRESHOLD,
    archive_token_threshold: ARCHIVE_TOKEN_THRESHOLD,
    commit_timeout_s: 180,
  });
  payload.allow_write = true;
  return payload;
}

async function archiveChat(trigger = "manual_button") {
  if (!ensureAgentWorkbenchSupported("手动 commit")) return;
  if (state.chatArchiveInFlight) {
    toast("正在归档中，请稍等");
    return;
  }
  if (state.chatSendInFlight) {
    toast("回答生成中，请等待完成后再归档");
    return;
  }
  if (!archivableMessages().length) return toast("当前没有可保存的对话");
  const payload = archivePayload(trigger);
  if (!payload) return;
  const button = $("archiveChat");
  const idleButtonText = button?.dataset?.idleText || button?.textContent || "手动 commit";
  const pendingId = `archive-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  state.chatArchiveInFlight = true;
  if (button) {
    button.dataset.idleText = idleButtonText;
    button.disabled = true;
    button.textContent = "归档中...";
  }
  state.chatMessages.push({
    role: "assistant",
    content: "记忆归档中... 正在执行 commit_session 并等待长期记忆抽取，请不要重复点击。",
    non_archivable: true,
    pending_archive: true,
    archive_pending_id: pendingId,
  });
  renderArchiveStatus();
  $("chatMeta").innerHTML = `
    <span class="check warn">归档中 · 正在写入当前账户</span>
    <span class="check">commit_session 执行中，请等待结果</span>
  `;
  renderChat();
  try {
    const data = await api("/api/agent/archive", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.lastArchivedMessageCount = archivableMessages().length;
    const pendingIndex = state.chatMessages.findIndex((msg) => msg.archive_pending_id === pendingId);
    const doneMessage = {
      role: "assistant",
      content: `记忆归档完成 · ${data.session_id || "-"}`,
      archive: data,
      non_archivable: true,
    };
    if (pendingIndex >= 0) state.chatMessages.splice(pendingIndex, 1, doneMessage);
    else state.chatMessages.push(doneMessage);
    renderArchiveStatus(data);
    $("chatMeta").innerHTML = `
      <span class="check ok">记忆归档完成 · ${escapeHtml(data.session_id || "-")}</span>
      <span class="check">${escapeHtml(archiveTriggerLabel(data))}</span>
    `;
    renderKpis("chatTokenKpis", [
      ["写入消息", data.submitted_messages ?? "-"],
      ["当前 Token", data.current?.tokens_est ?? "-"],
      ["待处理", data.pending_after_commit ?? "-"],
    ]);
    renderChat();
    toast("已保存为长期记忆");
  } catch (e) {
    const pendingIndex = state.chatMessages.findIndex((msg) => msg.archive_pending_id === pendingId);
    const errorMessage = {
      role: "assistant",
      content: `记忆归档失败：${e.message}`,
      non_archivable: true,
      archive_error: true,
    };
    if (pendingIndex >= 0) state.chatMessages.splice(pendingIndex, 1, errorMessage);
    else state.chatMessages.push(errorMessage);
    $("chatMeta").innerHTML = `<span class="check bad">记忆写入失败：${escapeHtml(e.message)}</span>`;
    renderChat();
    toast(e.message);
  } finally {
    state.chatArchiveInFlight = false;
    if (button) {
      button.textContent = button.dataset.idleText || idleButtonText;
      button.disabled = false;
    }
    updateAgentWorkbenchControls();
  }
}

async function maybeAutoArchive() {
  renderArchiveStatus();
}

function parseEvidence(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function resultClass(row) {
  const value = String(row.result || row.simple_grade || "").toUpperCase();
  if (value === "CORRECT" || value === "MATCH") return "correct";
  if (value === "WRONG") return "wrong";
  return "pending";
}

function resultLabel(row) {
  return row.result || row.simple_grade || "待判分";
}

function judgeReason(row) {
  return row.reasoning || row.judge_reasoning || row.judge_reason || row.explanation || "";
}

function resultMeta(row) {
  const parts = [];
  if (row.sample_id) parts.push(row.sample_id);
  if (row.question_id) parts.push(row.question_id);
  if (row.category) parts.push(`C${row.category}`);
  if (row.query_time) parts.push(row.query_time);
  if (row.injection_tokens_est) parts.push(`${row.injection_tokens_est} tokens`);
  return parts.join(" · ");
}

function renderEvidenceList(row) {
  const items = parseEvidence(row.relevant_memory);
  if (!items.length) {
    return `<p class="evidence-empty">${escapeHtml(String(row.context_preview || "没有 evidence。").slice(0, 500))}</p>`;
  }
  return items.slice(0, 4).map((item, index) => renderEvidenceCard(item, index)).join("");
}

function renderContextTrace(data = {}) {
  renderChatPersona(data);
  const retrieval = data.retrieval || {};
  const trace = data.context_trace || {};
  const plan = trace.query_plan || retrieval.query_plan || [];
  const retrievalErrors = trace.retrieval_errors || retrieval.errors || [];
  const alignmentNotes = trace.alignment_notes || [];
  const layers = trace.layers || [];
  const blocks = trace.blocks || [];
  const messages = data.messages || [];
  const promptChars = trace.prompt_chars ?? messages.reduce((sum, item) => sum + String(item.content || "").length, 0);
  const promptTokens = trace.prompt_tokens_est ?? Math.ceil(promptChars / 4);
  const memoryHits = trace.memory_hits ?? (retrieval.items || []).length;
  const promptEng = trace.prompt_engineering || {};
  const retrievalConfig = promptEng.retrieval_config || {};
  const systemPromptStructure = promptEng.system_prompt_structure || [];

  const layerRows = layers.map((layer) => `
    <article class="trace-row ${layer.highlight ? "highlight" : ""}">
      <strong>${escapeHtml(layer.name || "-")}</strong>
      <small>${escapeHtml(layer.source || "")} · ${escapeHtml(layer.item_count ?? 0)} items · ${escapeHtml(layer.char_count ?? 0)} chars</small>
    </article>
  `).join("");
  const planRows = plan.map((item, index) => `<code>${index + 1}. ${escapeHtml(item)}</code>`).join("");
  const blockRows = blocks.map((block) => `
    <article class="context-block ${escapeHtml(block.kind || block.role || "")}">
      <div class="context-block-head">
        <div>
          <strong>#${escapeHtml(block.index || "")} ${escapeHtml(block.role || "-")} · ${escapeHtml(block.title || "-")}</strong>
          <small>${escapeHtml(block.source || "")}</small>
        </div>
        <span>${escapeHtml(block.tag || "")}</span>
      </div>
      <pre>${escapeHtml(block.content || "")}</pre>
      <div class="context-block-meta">
        <span>${escapeHtml(block.char_count ?? 0)} chars</span>
        <span>${escapeHtml(block.tokens_est ?? "-")} tokens est</span>
      </div>
    </article>
  `).join("");
  const promptPreview = messages.map((item, index) => `
    <article class="prompt-message">
      <strong>${index + 1}. ${escapeHtml(item.role || "-")}</strong>
      <p>${escapeHtml(String(item.content || "").slice(0, 700))}</p>
    </article>
  `).join("");

  const promptEngSection = Object.keys(promptEng).length ? `
    <div class="trace-section">
      <b>提示词配置</b>
      <div class="trace-kpis context-summary">
        <article><span>${RETRIEVAL_COUNT_LABEL}</span><strong>${escapeHtml(retrievalConfig.top_k ?? "-")}</strong></article>
        <article><span>分数阈值</span><strong>${escapeHtml(retrievalConfig.score_threshold ?? "-")}</strong></article>
        <article><span>检索方式</span><strong>${escapeHtml(retrievalConfig.query_expansion === "enabled" ? "扩展检索" : "原问题检索")}</strong></article>
        <article><span>结构</span><strong>${escapeHtml(promptEng.architecture ?? "-")}</strong></article>
        <article><span>温度</span><strong>${escapeHtml(promptEng.temperature ?? "-")}</strong></article>
      </div>
      <div class="query-plan">
        ${systemPromptStructure.map((item) => `<code>${escapeHtml(item.name)}: ${escapeHtml(item.type)} (${escapeHtml(item.chars)} 字符)</code>`).join("")}
      </div>
    </div>
  ` : "";

  $("contextTrace").innerHTML = `
    <div class="trace-kpis context-summary">
      <article><span>阶段</span><strong>${escapeHtml(trace.phase || "context")}</strong></article>
      <article><span>上下文块</span><strong>${escapeHtml(blocks.length || layers.length || 0)}</strong></article>
      <article><span>命中记忆</span><strong>${escapeHtml(memoryHits)}</strong></article>
      <article><span>完整文件</span><strong>${escapeHtml(trace.memory_file_hits ?? 0)}</strong></article>
      <article><span>估算 Token</span><strong>${escapeHtml(promptTokens)}</strong></article>
    </div>
    ${promptEngSection}
    <div class="trace-section">
      <b>组装后的模型上下文</b>
      <div class="context-blocks">${blockRows || "<p>暂无上下文块。</p>"}</div>
    </div>
    <div class="trace-section">
      <b>检索计划</b>
      <div class="query-plan">${planRows || "<p>没有生成检索词。</p>"}</div>
      ${retrievalErrors.length ? `<p class="bad-text">检索错误：${escapeHtml(friendlyUiError(retrievalErrors[0], "检索异常"))}</p>` : ""}
    </div>
    <div class="trace-section">
      <b>对齐说明</b>
      <div class="query-plan">${alignmentNotes.map((item) => `<code>${escapeHtml(item)}</code>`).join("") || "<p>暂无说明。</p>"}</div>
    </div>
    <details class="prompt-preview">
      <summary>模型消息预览 · ${escapeHtml(trace.model_messages_count ?? messages.length)} 条 · ${escapeHtml(promptChars)} 字符</summary>
      ${promptPreview || "<p>暂无 prompt。</p>"}
    </details>
    <details class="prompt-preview">
      <summary>上下文分层</summary>
      ${layerRows || "<p>暂无上下文层。</p>"}
    </details>
  `;
}

function chatPayload(extra = {}) {
  const info = chatMemorySpaceInfo();
  const backend = info.backend || currentMemoryBackend();
  const imported = readLastImport();
  const agentCfg = agentModelConfig();
  const memoryCfg = memoryInjectModelConfig();
  const memoryToken = memoryCfg.token || "";
  return {
    backend,
    memoryBackend: backend,
    messages: state.chatMessages,
    model: agentCfg.model || "gpt-5.5",
    answer_model: agentCfg.model || "gpt-5.5",
    judge_base_url: agentCfg.baseUrl,
    answer_base_url: agentCfg.baseUrl,
    agent_base_url: agentCfg.baseUrl,
    api_key: agentCfg.token,
    answer_token: agentCfg.token,
    vlm_api_key: memoryToken,
    dashscope_api_key: memoryToken,
    echomem_api_key: memoryToken,
    echomem_chat_api_key: memoryToken,
    vlm_base_url: memoryCfg.baseUrl,
    dashscope_base_url: memoryCfg.baseUrl,
    echomem_chat_base_url: memoryCfg.baseUrl,
    vlm_model: memoryCfg.model,
    echomem_chat_model: memoryCfg.model,
    memory_inject_model: memoryCfg.model,
    host: $("ovHost").value.trim() || "127.0.0.1",
    port: $("ovPort").value.trim() || "19080",
    root_api_key: $("ovApiKey").value.trim(),
    workspace: ($("memoryWorkspace")?.value || $("ovWorkspace")?.value || imported.workspace || "").trim(),
    account: currentAccount(),
    user_id: $("memoryUserId")?.value.trim() || readAccountConfig(currentAccount()).memoryUserId || "default",
    agent_id: $("memoryAgentId")?.value.trim() || readAccountConfig(currentAccount()).memoryAgentId || "default",
    echomem_root: $("echomemRoot")?.value.trim() || readAccountConfig(currentAccount()).echomemRoot || "",
    use_memory: $("useMemory").checked,
    allow_write: false,
    top_k: $("chatTopK").value || "30",
    temperature: 0.2,
    ...extra,
  };
}

async function previewContext() {
  const content = $("chatInput").value.trim();
  const isDefaultPreview = !content && !state.chatMessages.length;
  const messages = content
    ? [...state.chatMessages, {role: "user", content}]
    : (state.chatMessages.length ? state.chatMessages : [{role: "user", content: chatDefaultPreviewQuestion(), non_archivable: true}]);
  if (!ensureAgentWorkbenchSupported("上下文预览")) return;
  $("previewContext").disabled = true;
  try {
    const data = await apiWithTimeout("/api/agent/context", {
      method: "POST",
      body: JSON.stringify(chatPayload({messages})),
    }, 8000);
    const hits = (data.retrieval && data.retrieval.items || []).length;
    $("chatMeta").innerHTML = `
      <span class="check ok">上下文预览 · 召回 ${hits}</span>
      ${(data.retrieval?.errors || []).length ? `<span class="check bad">retrieval degraded</span>` : ""}
    `;
    if (hits || !isDefaultPreview) {
      renderMemoryEvidence(data.retrieval?.items || [], (data.retrieval?.errors || [])[0] || "");
    } else {
      await loadChatTimelineEvidenceFallback("默认预览没有按查询召回到 evidence；下面展示当前账户最近的长期记忆文件。");
    }
    renderContextTrace(data);
    state.lastChatContextData = data;
    state.chatContextPreviewKey = chatContextPreviewKey(messages);
  } catch (e) {
    $("chatMeta").innerHTML = `<span class="check bad">上下文预览失败：${escapeHtml(e.message)}</span>`;
    if (isDefaultPreview) {
      await loadChatTimelineEvidenceFallback(`上下文预览失败：${e.message}；下面展示当前账户记忆概览。`);
    } else {
      renderMemoryEvidence([], e.message);
    }
    toast(e.message);
  } finally {
    $("previewContext").disabled = false;
    updateAgentWorkbenchControls();
  }
}

async function newCleanAccount() {
  const slug = slugTime();
  const account = `clean-${slug}`;
  const previous = currentAccount();
  saveAccountList([...readAccountList(), account]);
  renderAccountSelect(account);
  const workspace = initializeCleanAccountConfig(account, readAccountConfig(previous));
  state.chatMessages = [];
  state.lastArchivedMessageCount = 0;
  state.lastArchiveRecord = null;
  state.lastChatContextData = null;
  state.chatContextPreviewKey = "";
  $("chatMeta").innerHTML = `
    <span class="check ok">已切到新空间：${escapeHtml(account)}</span>
    <span class="check">独立记忆目录</span>
    <span class="check">${escapeHtml(workspace)}</span>
  `;
  renderChatContextPlaceholder("新空间还没有问题上下文；完成导入或提问后会显示相关记忆。");
  $("chatTokenKpis").innerHTML = "";
  renderChat();
  renderArchiveStatus();
  renderChatDebugStrip();
  renderImportPaths();
  refreshImportedMemories().catch(() => {});
  try {
    const data = await api("/api/accounts", {
      method: "POST",
      body: JSON.stringify({account, inherit_from: previous, config: readAccountConfig(account)}),
    });
    mergeBackendAccountState(data);
    applyAccountConfig(account);
    toast("已生成干净空间");
  } catch {
    toast("已生成本地干净空间");
  }
}

function updateWorkspaceMode() {
  const input = $("ovWorkspace");
  if (!input) return;
  input.placeholder = "/path/to/memory_workspace";
  input.classList.remove("muted-input");
  renderImportPaths();
}

async function sendChat() {
  const content = $("chatInput").value.trim();
  if (!content) return toast("请输入消息");
  if (!ensureAgentWorkbenchSupported("对话")) return;
  if ($("allowWrite").checked) {
    toast("发送不会写长期记忆；写入请点击“手动 commit”");
    $("allowWrite").checked = false;
  }
  state.chatMessages.push({role: "user", content});
  $("chatInput").value = "";
  renderChat();
  renderChatDebugStrip();
  state.chatSendInFlight = true;
  $("sendChat").disabled = true;
  updateAgentWorkbenchControls();
  try {
    const data = await api("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify(chatPayload()),
    });
    const answerText = String(data.answer || "").trim();
    state.chatMessages.push({
      role: "assistant",
      content: answerText || "模型服务返回了空内容。记忆检索链路已完成，但上游模型没有生成 answer；请到系统配置检查 Agent 模型，或稍后重试。",
    });
    const hits = (data.retrieval && data.retrieval.items || []).length;
    const iso = data.isolation || {};
    $("chatMeta").innerHTML = `
    <span class="check ok">只读回答 · ${escapeHtml(iso.account || "-")}</span>
      <span class="check">召回 ${hits}</span>
      ${data.retrieval?.error ? `<span class="check bad">${escapeHtml(data.retrieval.error)}</span>` : ""}
    `;
    renderMemoryEvidence(data.retrieval?.items || [], data.retrieval?.error || "");
    renderContextTrace(data);
    state.lastChatContextData = data;
    state.chatContextPreviewKey = chatContextPreviewKey();
    renderKpis("chatTokenKpis", [
      ["输入 Token", data.tokens?.prompt ?? "-"],
      ["输出 Token", data.tokens?.completion ?? "-"],
      ["总 Token", data.tokens?.total ?? "-"],
    ]);
    renderChatDebugStrip();
  } catch (e) {
    state.chatMessages.push({role: "assistant", content: `请求失败：${e.message}`});
    renderMemoryEvidence([], e.message);
  } finally {
    state.chatSendInFlight = false;
    $("sendChat").disabled = false;
    updateAgentWorkbenchControls();
    renderChat();
    maybeAutoArchive().catch((err) => toast(err.message));
  }
}

async function stopAllTasks() {
  const overrideAt = Date.now();
  const optimisticTasks = runningTaskCandidates().map((task) => {
    if (!task?.id) return null;
    state.taskStopOverrides[task.id] = overrideAt;
    return stampTaskSnapshot({...task, status: "stopping"});
  }).filter(Boolean);
  if (optimisticTasks.length) {
    syncTrackedTaskSnapshots(optimisticTasks);
    refreshLiveTaskDisplays();
    updateStopActionButtons(optimisticTasks);
  }
  try {
    const data = await api("/api/tasks/stop-all", {method: "POST", body: "{}"});
    const stoppedTasks = (data.tasks || []).map((task) => {
      if (task?.id && (task.status === "stopping" || task.status === "interrupted")) {
        state.taskStopOverrides[task.id] = Date.now();
      }
      return stampTaskSnapshot(task);
    });
    syncTrackedTaskSnapshots(stoppedTasks);
    for (const task of stoppedTasks) {
      if (!task?.id) continue;
      const format = enrichTaskDatasetFormat(task, state.taskDatasetFormats[task.id] || task.dataset_format || "");
      if (isMemoryImportKind(task.kind || "")) state.currentImportTask = task;
      if (isLocomoTaskOutput(task.kind || "", task, format)) state.currentLocomoTask = task;
      if (isTaskActive(task)) state.currentRunningTask = task;
    }
    refreshLiveTaskDisplays();
    refreshTasks().catch(() => {});
    toast(data.stopped ? `已停止 ${data.stopped} 个任务` : "没有正在运行的任务");
  } catch (error) {
    refreshTasks().catch(() => {});
    throw error;
  }
}

function currentWorkspaceAndAccount() {
  const lastImport = readLastImport();
  const info = chatMemorySpaceInfo();
  const workspace = (info.workspace || $("memoryWorkspace")?.value || $("ovWorkspace")?.value || lastImport.workspace || "").trim();
  const account = info.account || currentAccount() || ($("memoryAccount")?.value || $("ovAccount")?.value || lastImport.account || "default").trim();
  const backend = normalizeMemoryBackend(info.backend || currentMemoryBackend());
  return {workspace, account, backend};
}

function copyButtonHtml(value, label = "复制") {
  return `<button class="path-copy" type="button" data-copy="${escapeHtml(value || "")}">${escapeHtml(label)}</button>`;
}

function bindCopyButtons(rootSelector) {
  document.querySelectorAll(`${rootSelector} .path-copy`).forEach((button) => {
    if (button.dataset.copyBound === "1") return;
    button.dataset.copyBound = "1";
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      await navigator.clipboard.writeText(button.dataset.copy || "");
      toast("已复制");
    });
  });
}

function bindOpenButtons(rootSelector) {
  document.querySelectorAll(`${rootSelector} .path-open`).forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const path = button.dataset.path || "";
      if (!path) return;
      try {
        const scope = normalizeEvidenceScope({
          workspace: button.dataset.workspace || "",
          account: button.dataset.account || "",
        }) || currentEvidenceScope();
        await api("/api/open-path", {
          method: "POST",
          body: JSON.stringify({ path, workspace: scope.workspace, account: scope.account }),
        });
        toast("已打开");
      } catch (e) {
        toast(`打开失败: ${e.message}`);
      }
    });
  });
}

async function refreshMemoryBrowser() {
  const {workspace, account, backend} = currentWorkspaceAndAccount();
  if (!workspace) return toast("请先填写记忆目录，或先完成一次导入");
  if ($("ovWorkspace")) $("ovWorkspace").value = workspace;
  if ($("ovAccount")) $("ovAccount").value = account;
  const rawQuery = ($("memoryQuery")?.value || "").trim();
  const sampleMatch = rawQuery.match(/conv-\d+/);
  const sample = sampleMatch ? sampleMatch[0] : "";
  const sessionsQs = new URLSearchParams({backend, workspace, account, sample, limit: "160"});
  const timelineQs = new URLSearchParams({
    backend,
    workspace,
    account,
    q: sample ? rawQuery.replace(sample, "").trim() : rawQuery,
    limit: "240",
  });
  const [sessions, timeline] = await Promise.all([
    api(`/api/memory-sessions?${sessionsQs.toString()}`),
    api(`/api/memory-timeline?${timelineQs.toString()}`),
  ]);
  renderKpis("memoryBrowserKpis", [
    ["会话", sessions.sessions?.length ?? 0],
    ["记忆文件", timeline.count ?? 0],
    ["空间", account],
    ["记忆根目录", timeline.memory_root || "-"],
  ]);
  $("sessionBrowserList").innerHTML = (sessions.sessions || []).map((item) => `
    <article class="memory-hit">
      <strong>${escapeHtml(item.session_id)}</strong>
      <small>${escapeHtml(item.updated_at || "")} · 文件 ${escapeHtml(item.files ?? 0)} · 历史 ${escapeHtml(item.history_files ?? 0)} · 归档 ${escapeHtml(item.archive_files ?? 0)}</small>
      <p>${escapeHtml(item.path || "")}</p>
      ${copyButtonHtml(item.path || "")}
    </article>
  `).join("") || "<p>没有找到 LoCoMo 会话。请确认当前空间和记忆目录是否正确。</p>";
  $("memoryTimelineList").innerHTML = (timeline.items || []).map((item) => `
    <article class="memory-hit" data-memory-path="${escapeHtml(item.path)}">
      <strong>${escapeHtml(item.date)} · ${escapeHtml(item.kind)} · ${escapeHtml(item.title)}</strong>
      <small>${escapeHtml(item.uri)} · ${escapeHtml(item.chars)} 字符</small>
      <p>${escapeHtml(item.snippet || "")}</p>
    </article>
  `).join("") || "<p>没有找到记忆文件。可以换一个关键词，或确认导入是否完成。</p>";
  document.querySelectorAll("#memoryTimelineList .memory-hit[data-memory-path]").forEach((card) => {
    card.addEventListener("click", () => loadMemoryFile(card.dataset.memoryPath || "", backend).catch((e) => toast(e.message)));
  });
  bindCopyButtons("#sessionBrowserList");
}

async function loadMemoryFile(path, backend = currentMemoryBackend()) {
  if (!path) return;
  const qs = new URLSearchParams({backend: normalizeMemoryBackend(backend), path});
  const data = await api(`/api/memory-file?${qs.toString()}`);
  $("memoryFilePreview").innerHTML = `
    <div class="question-detail-section">
      <h4>${escapeHtml(data.name || "memory file")}</h4>
      <small>${escapeHtml(data.path)} · ${escapeHtml(data.chars)} chars</small>
    </div>
    <pre>${escapeHtml(data.text || "")}</pre>
  `;
}

function runLabel(run) {
  const summary = run.summary || {};
  const summaryJson = summary.summary_json || {};
  const acc = summary.accuracy == null ? "待判分" : percent(summary.accuracy);
  const exact = summary.exact_match_reference == null
    ? ""
    : ` · exact ${summary.simple_correct ?? "-"}/${summary.rows ?? "-"} ${percent(summary.exact_match_reference)}`;
  const archiveTotal = Number(summaryJson.archive_fallback_total ?? summary.archive_fallback_total ?? 0);
  const archive = archiveTotal > 0 ? ` · 会话补充 ${archiveTotal}` : "";
  const agent = agentTypeLabel(run.agent_type || agentTypeForKind(run.kind || ""));
  return `${agent} · ${run.status || "-"} · ${summary.rows ?? "-"} 行 · 正式判分 ${acc}${exact}${archive}`;
}

function pathLeaf(path = "") {
  const value = String(path || "").trim().replace(/\/+$/, "");
  if (!value) return "";
  return value.split("/").filter(Boolean).pop() || value;
}

function runTextForInference(run = {}) {
  return [
    run.id,
    run.name,
    run.kind,
    run.agent_type,
    run.output_file,
    run.run_dir,
  ].map((value) => String(value || "")).join(" ");
}

function extractRunToken(text = "", pattern) {
  const source = String(text || "");
  const match = source.match(pattern);
  return match?.[1] || "";
}

function inferSampleFromRun(run = {}) {
  const text = runTextForInference(run);
  const conv = extractRunToken(text, /(?:^|[_-])(conv[-_]?\d+)(?=$|[_-])/i);
  if (conv) return conv.replace("_", "-").replace(/^conv(\d+)$/i, "conv-$1");
  const sample = extractRunToken(text, /(?:^|[_-])(sample[-_]?\d+)(?=$|[_-])/i);
  if (sample) return sample.replace("_", "-");
  const session = extractRunToken(text, /(?:^|[_-])(session[-_]?\d+)(?=$|[_-])/i);
  return session ? session.replace("_", "-") : "";
}

function runDatasetMeta(run = {}) {
  const summary = run.summary || {};
  const summaryJson = summary.summary_json || {};
  const format = benchmarkFormatFromRecord(run, summaryDatasetFormat(summary)) || "locomo";
  const explicitDatasetPath = firstValue(
    run.dataset,
    run.dataset_path,
    summary.dataset,
    summary.dataset_path,
    summaryJson.dataset,
    summaryJson.dataset_path,
    summaryJson.data,
    summaryJson.input,
  );
  const datasetPath = explicitDatasetPath || (format === "locomo" ? activeDatasetPathForFormat(format) : "");
  const sample = firstValue(
    run.sample,
    summary.sample,
    summaryJson.sample,
    summaryJson.sample_id,
    summaryJson.subset,
    inferSampleFromRun(run),
  );
  const sessionStart = firstValue(run.session_start, summary.session_start, summaryJson.session_start);
  const sessionEnd = firstValue(run.session_end, summary.session_end, summaryJson.session_end);
  const sessionRange = sessionStart || sessionEnd
    ? (String(sessionStart) === String(sessionEnd) ? `session ${sessionStart}` : `session ${sessionStart || "?"}-${sessionEnd || "?"}`)
    : "";
  const requested = firstValue(
    summary.count,
    summaryJson.count,
    summary.requested_count,
    summaryJson.requested_count,
    summaryJson.limit,
  );
  const rows = firstValue(summary.rows, summaryJson.rows, requested);
  const graded = firstValue(summary.graded, summary.simple_graded, summaryJson.graded);
  const pending = summary.result_counts?.UNSCORED ?? summary.simple_counts?.UNSCORED ?? "";
  const accuracy = summary.accuracy ?? summaryJson.accuracy;
  const exact = summary.exact_match_reference ?? summaryJson.exact_match_reference;
  const categories = summary.categories || summaryJson.categories || {};
  const categoryCount = categories && typeof categories === "object" ? Object.keys(categories).length : 0;
  return {
    format,
    label: datasetTypeLabel(format),
    datasetPath,
    datasetName: pathLeaf(datasetPath) || pathLeaf(run.output_file) || pathLeaf(run.run_dir) || "-",
    sample,
    sessionRange,
    rows,
    requested,
    graded,
    pending,
    accuracy,
    exact,
    categoryCount,
  };
}

const GENERIC_RUN_OUTPUT_FILES = new Set([
  "echomemory_memory_qa_results.csv",
  "openviking_generic_qa_results.csv",
  "echomemory_generic_qa_results.csv",
  "locomo10.json",
  "locomo.json",
]);

function inferRunModeLabel(run = {}) {
  const text = runTextForInference(run);
  const tags = [
    extractRunToken(text, /(?:^|[_-])(full\d+)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(subset\d+)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(session\d+)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(smoke)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(baseline)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(fastwait)(?=$|[_-])/i),
  ].filter(Boolean);
  return tags.slice(0, 2).join(" · ");
}

function inferRunVariantLabel(run = {}) {
  const text = runTextForInference(run);
  const tags = [
    extractRunToken(text, /(?:^|[_-])(token\d+)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(window\d+)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(fixrerank|ruleintent|aligned|fixed|search|artifact|localsegment)(?=$|[_-])/i),
    extractRunToken(text, /(?:^|[_-])(probe\d*|calltrace|postcommit)(?=$|[_-])/i),
  ].filter(Boolean);
  return tags.slice(0, 3).join(" · ");
}

function runDisplayTitle(run = {}, meta = runDatasetMeta(run)) {
  const sampleLabel = meta.sample || inferSampleFromRun(run);
  const modeLabel = inferRunModeLabel(run);
  const subsetLabel = [sampleLabel, meta.sessionRange, modeLabel].filter(Boolean).join(" · ");
  if (subsetLabel) return subsetLabel;
  const runLeaf = pathLeaf(run.run_dir || "");
  const outputLeaf = pathLeaf(run.output_file || "");
  const preferred = [
    run.id,
    run.name,
    runLeaf,
    GENERIC_RUN_OUTPUT_FILES.has(String(outputLeaf || "").toLowerCase()) ? "" : outputLeaf,
    meta.datasetName,
  ].map((value) => String(value || "").trim()).find(Boolean);
  return preferred || "-";
}

function renderRunDatasetMeta(run = {}) {
  const meta = runDatasetMeta(run);
  const title = runDisplayTitle(run, meta);
  const titleDisplay = title.length > 42 ? compactPath(title, 20, 16) : title;
  const variant = inferRunVariantLabel(run);
  const scope = [
    variant,
    meta.requested && String(meta.requested) !== String(meta.rows || "") ? `计划 ${meta.requested} 题` : "",
    meta.categoryCount ? `${meta.categoryCount} 类` : "",
  ].filter(Boolean).join(" · ");
  const score = meta.accuracy === undefined || meta.accuracy === null || meta.accuracy === ""
    ? "待判分"
    : percent(meta.accuracy);
  const chipParts = [
    meta.rows ? `<span><b>题量</b><em>${escapeHtml(meta.rows)}</em></span>` : "",
    meta.pending ? `<span><b>未判</b><em>${escapeHtml(meta.pending)}</em></span>` : "",
  ].filter(Boolean);
  chipParts.push(`<span><b>${chipParts.length ? "分数" : "状态"}</b><em>${escapeHtml(score)}</em></span>`);
  const chips = chipParts.join(`<i class="run-meta-separator" aria-hidden="true"></i>`);
  return `
    <div class="run-test-summary">
      <div class="run-dataset-meta">
        <span>${escapeHtml(meta.label)}</span>
        <strong title="${escapeHtml(title)}">${escapeHtml(titleDisplay)}</strong>
      </div>
      <div class="run-meta-chips" aria-label="运行数据集摘要">
        ${chips}
      </div>
    </div>
  `;
}

function renderRunOperationalMeta(run = {}) {
  const agent = agentTypeLabel(run.agent_type || agentTypeForKind(run.kind || ""));
  const duration = run.duration_s === undefined || run.duration_s === null ? "" : formatDuration(run.duration_s);
  const timeText = [run.created_at || "-", duration ? `· ${duration}` : ""].filter(Boolean).join(" ");
  return `
    <div class="run-operational-meta">
      <small>
        <span class="run-time" title="${escapeHtml(run.created_at || "-")}"><b>时间</b>${escapeHtml(timeText)}</span>
        <span class="run-backend"><b>后端</b>${escapeHtml(agent)}</span>
      </small>
    </div>
  `;
}

function runPathLabel(run = {}) {
  return run.output_file || run.run_dir || "";
}

function runPathDisplayLabel(run = {}) {
  const full = runPathLabel(run);
  if (!full) return "";
  return compactPath(full, 20, 24);
}

function renderRunCompareToggle(run = {}) {
  return `
    <label class="run-compare-toggle">
      <input class="run-compare-check" type="checkbox" ${state.selectedRunCompareIds.has(runCompareKey(run)) ? "checked" : ""}>
      <span>对比</span>
    </label>
  `;
}

function isNativeOpenVikingBaselineRun(run = {}) {
  const baseline = state.nativeOpenVikingBaseline?.baseline || {};
  const runDir = String(run.run_dir || "");
  const outputFile = String(run.output_file || "");
  return Boolean(
    (runDir && runDir === String(baseline.run_dir || "")) ||
    (outputFile && outputFile === String(baseline.output_file || ""))
  );
}

function runFormalScore(run = {}) {
  const summary = run.summary || {};
  const summaryJson = summary.summary_json || {};
  const value = summary.accuracy ?? summaryJson.accuracy;
  return value === undefined || value === null || value === "" ? null : Number(value);
}

function runHasFormalScore(run = {}) {
  const value = runFormalScore(run);
  return Number.isFinite(value);
}

function renderRunCard(run = {}) {
  const datasetFormat = benchmarkFormatFromRecord(run);
  const baselineBadge = isNativeOpenVikingBaselineRun(run)
    ? `<span class="run-baseline-badge">原生基线</span>`
    : "";
  const pathLabel = runPathLabel(run);
  const pathDisplay = runPathDisplayLabel(run);
  return `
    <article class="memory-hit run-card" data-run-key="${escapeHtml(runCompareKey(run))}" data-run-dir="${escapeHtml(run.run_dir || "")}" data-output-file="${escapeHtml(run.output_file || "")}" data-dataset-format="${escapeHtml(datasetFormat)}">
      <div class="run-card-primary">
        <div>
          ${renderRunDatasetMeta(run)}
          ${baselineBadge}
        </div>
      </div>
      ${renderRunOperationalMeta(run)}
      ${pathLabel ? `<p class="run-path" title="${escapeHtml(pathLabel)}">${escapeHtml(pathDisplay)}</p>` : ""}
    </article>
  `;
}

function renderRunGroup(title, runs = []) {
  if (!runs.length) return "";
  return `
    <div class="run-group-heading">
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(runs.length)} 条 · 按测试时间倒序</span>
    </div>
    ${runs.map(renderRunCard).join("")}
  `;
}

function runCompareKey(run = {}) {
  return String(run.run_dir || run.id || run.name || run.output_file || "");
}

function commandHas(command, option) {
  return Array.isArray(command) && command.includes(`--${option}`);
}

function commandOption(command, option) {
  if (!Array.isArray(command)) return "";
  const index = command.indexOf(`--${option}`);
  if (index < 0 || index + 1 >= command.length) return "";
  const value = command[index + 1];
  return String(value || "").startsWith("--") ? "" : String(value || "");
}

function firstValue(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return "";
}

function reportBoolLabel(value) {
  const text = String(value ?? "").trim().toLowerCase();
  if (text === "native" || text === "native_vikingbot_cli") return "native";
  if (value === true || value === "true") return "on";
  if (value === false || value === "false") return "off";
  return "-";
}

function nativeTopKLabel(value, promptMode) {
  if (String(promptMode || "").trim() === "native_vikingbot_cli") return "原生内部";
  return value || "-";
}

function summaryMetric(summary = {}, summaryJson = {}, ...keys) {
  for (const key of keys) {
    const value = summary[key] ?? summaryJson[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return "";
}

function runPreviewRow(detail = {}) {
  const rows = detail?.preview?.rows || [];
  return Array.isArray(rows) && rows.length ? rows[0] || {} : {};
}

function previewEvidenceHints(row = {}) {
  const items = parseEvidence(row.relevant_memory);
  const firstPath = items.map((item) => String(item.path || "")).find((path) => path.startsWith("/")) || "";
  const firstEchoUri = items.map((item) => String(item.uri || item.evidence_uri || "")).find((uri) => uri.startsWith("echo://")) || "";
  const accountFromUri = firstEchoUri ? firstEchoUri.replace(/^echo:\/\//, "").split("/")[0] : "";
  return {
    account: accountFromUri,
    workspace: firstPath,
    evidence_count: items.length,
  };
}

function summarizeRunForCompare(detail, fallbackRun = {}) {
  const record = detail?.record || fallbackRun || {};
  const manifest = detail?.manifest || {};
  const snapshot = detail?.config_snapshot || {};
  const config = {...(snapshot.config || {}), ...(manifest.config || {})};
  const command = manifest.command || snapshot.command || [];
  const previewRow = runPreviewRow(detail);
  const previewHints = previewEvidenceHints(previewRow);
  const configSources = [
    manifest.config || manifest.command ? "manifest" : "",
    snapshot.config || snapshot.command ? "config snapshot" : "",
    Object.keys(record.summary?.summary_json || {}).length ? "summary json" : "",
    Object.keys(previewRow || {}).length ? "csv preview" : "",
  ].filter(Boolean);
	  const summary = record.summary || {};
	  const summaryJson = summary.summary_json || {};
	  const officialScore = summary.official_score ?? summaryJson.official_score;
	  const accuracy = officialScore ?? summary.accuracy;
	  const exact = summary.exact_match_reference ?? summaryJson.exact_match_rate;
  const queryExpansion = summaryMetric(summary, summaryJson, "query_expansion_enabled");
  const lexicalFallback = summaryMetric(summary, summaryJson, "lexical_fallback_enabled");
  const archiveFallback = summaryMetric(summary, summaryJson, "archive_fallback_enabled");
  const memoryFileRead = summaryMetric(summary, summaryJson, "memory_file_read_enabled");
  const promptMode = summaryMetric(summary, summaryJson, "prompt_mode");
  const vikingbotPromptAligned = summaryMetric(summary, summaryJson, "vikingbot_prompt_aligned");
  const vikingbotProfile = summaryMetric(summary, summaryJson, "vikingboat_alignment_profile");
  const alignmentBackendRoute = summaryMetric(summary, summaryJson, "alignment_backend_route", "backend_route");
  const groupChat = summaryMetric(summary, summaryJson, "group_chat");
  const memoryUserStrategy = summaryMetric(summary, summaryJson, "memory_user_strategy");
  const identityMode = summaryMetric(summary, summaryJson, "vikingbot_identity_mode");
  const vikingbotChannel = summaryMetric(summary, summaryJson, "vikingbot_channel");
  const initialAgentMemory = summaryMetric(summary, summaryJson, "initial_agent_memory_enabled");
  const rawTurnFallback = summaryMetric(summary, summaryJson, "raw_turn_fallback");
  const initialSearchLimit = summaryMetric(summary, summaryJson, "initial_search_limit");
  const initialScoreThreshold = summaryMetric(summary, summaryJson, "initial_score_threshold");
  const toolSearchLimit = summaryMetric(summary, summaryJson, "tool_search_limit");
  const toolMinScore = summaryMetric(summary, summaryJson, "tool_min_score");
  const toolLoop = summaryMetric(summary, summaryJson, "memory_tool_loop_enabled", "openviking_tool_loop_enabled");
  const toolSet = summaryMetric(summary, summaryJson, "memory_tool_set", "openviking_tool_set");
  const contentRead = summaryMetric(summary, summaryJson, "memory_content_read_enabled", "openviking_content_read_enabled");
  const effectivePromptMode = firstValue(config.prompt_mode, commandOption(command, "prompt-mode"), promptMode, previewRow.prompt_mode);
  const effectiveTopK = nativeTopKLabel(firstValue(config.top_k, commandOption(command, "top-k"), config.chatTopK, summaryJson.top_k, previewRow.retrieval_count, previewRow.memory_hit_count), effectivePromptMode);
  return {
    id: record.id || fallbackRun.id || "-",
    name: record.name || fallbackRun.name || record.id || "-",
    kind: record.kind || fallbackRun.kind || "-",
    status: record.status || fallbackRun.status || "-",
    created_at: record.created_at || fallbackRun.created_at || "-",
    duration_s: record.duration_s ?? fallbackRun.duration_s,
    agent: agentTypeLabel(record.agent_type || fallbackRun.agent_type || agentTypeForKind(record.kind || fallbackRun.kind || "")),
    rows: summary.rows ?? summaryJson.count ?? "-",
	    graded: summary.graded ?? summaryJson.graded ?? "-",
	    accuracy,
	    formal_accuracy: summary.accuracy,
	    official_metric: summary.official_metric ?? summaryJson.official_metric,
	    official_score: officialScore,
	    official_metric_scope: summary.official_metric_scope ?? summaryJson.official_metric_scope,
	    exact,
    correct: summary.correct ?? summaryJson.correct ?? "-",
    wrong: summary.wrong ?? summaryJson.wrong ?? "-",
    pending: (summary.result_counts || {}).UNSCORED ?? "-",
    answer_model: firstValue(config.answer_model, commandOption(command, "answer-model"), config.model, summaryJson.answer_model, config.judge_model),
    judge_model: firstValue(config.judge_model, commandOption(command, "judge-model"), summaryJson.judge_model, summaryJson.judge?.summary?.model),
    embedding_model: firstValue(config.embedding_model, config.embed_model, config.vlm_model, summaryJson.embedding_model, summaryJson.embed_model),
    top_k: effectiveTopK,
    prompt_mode: effectivePromptMode,
    vikingboat_alignment_profile: vikingbotProfile,
    alignment_backend_route: alignmentBackendRoute,
    vikingbot_prompt_aligned: vikingbotPromptAligned,
    group_chat: firstValue(config.group_chat, commandOption(command, "group-chat"), groupChat),
    memory_user_strategy: memoryUserStrategy,
    vikingbot_identity_mode: firstValue(config.vikingbot_identity_mode, commandOption(command, "memory-user-mode"), identityMode),
    vikingbot_channel: vikingbotChannel,
    initial_agent_memory: firstValue(config.initial_agent_memory, commandOption(command, "initial-agent-memory"), initialAgentMemory),
    raw_turn_fallback: rawTurnFallback,
    initial_search_limit: firstValue(config.initial_search_limit, initialSearchLimit),
    initial_score_threshold: firstValue(config.initial_score_threshold, initialScoreThreshold),
    tool_search_limit: firstValue(config.tool_search_limit, toolSearchLimit),
    tool_min_score: firstValue(config.tool_min_score, toolMinScore),
    openviking_tool_set: firstValue(config.openviking_tool_set, config.tool_set, commandOption(command, "openviking-tool-set"), commandOption(command, "tool-set"), toolSet),
    openviking_tool_loop: toolLoop !== ""
      ? toolLoop
      : (commandHas(command, "no-vikingboat-tool-loop")
        ? false
        : (commandHas(command, "vikingboat-tool-loop")
          ? true
          : (commandHas(command, "no-openviking-tool-loop")
            ? false
            : (commandHas(command, "openviking-tool-loop") ? true : config.openviking_tool_loop)))),
    openviking_content_read: contentRead !== "" ? contentRead : (commandHas(command, "no-read-openviking-content") ? false : (commandHas(command, "read-openviking-content") ? true : config.read_openviking_content)),
    max_iterations: firstValue(config.max_iterations, commandOption(command, "max-iterations"), summaryMetric(summary, summaryJson, "max_iterations")),
    avg_iteration: summaryMetric(summary, summaryJson, "avg_iteration"),
    tool_call_rows: summaryMetric(summary, summaryJson, "tool_call_rows"),
    tool_call_total: summaryMetric(summary, summaryJson, "tool_call_total"),
    tool_name_counts: compactCountMap(summary.tool_name_counts || summaryJson.tool_name_counts),
    account: firstValue(config.account, commandOption(command, "account"), summaryJson.account, recordAccount(record), previewHints.account),
    workspace: firstValue(config.workspace, config.openviking_workspace, commandOption(command, "workspace"), summaryJson.workspace, summaryJson.openviking_workspace, recordWorkspace(record), previewHints.workspace),
    sample: firstValue(config.sample, commandOption(command, "sample"), summaryJson.sample, previewRow.sample_id, previewRow.original_sample_id),
    questions: firstValue(config.questions, commandOption(command, "questions"), summaryJson.questions, previewRow.question_id),
    retrieval_mode: firstValue(config.retrieval_mode, commandOption(command, "retrieval-mode"), summaryMetric(summary, summaryJson, "retrieval_mode"), previewRow.retrieval_mode, "-"),
    query_expansion: queryExpansion !== "" ? queryExpansion : (commandHas(command, "no-query-expansion") ? false : ""),
    lexical_fallback: lexicalFallback !== "" ? lexicalFallback : (commandHas(command, "no-lexical-fallback") ? false : ""),
    archive_fallback: archiveFallback !== "" ? archiveFallback : (commandHas(command, "no-archive-fallback") ? false : ""),
    memory_file_read: memoryFileRead !== "" ? memoryFileRead : (commandHas(command, "no-read-memory-files") ? false : ""),
    memory_hits: summaryMetric(summary, summaryJson, "memory_hit_total"),
    avg_memory_hits: summaryMetric(summary, summaryJson, "avg_memory_hit_count", "avg_retrieval_count"),
    retrieval_tokens: summaryMetric(summary, summaryJson, "retrieval_tokens_est_total", "retrieval_tokens_est"),
    answer_tokens: summaryMetric(summary, summaryJson, "answer_total_tokens"),
    injection_tokens: summaryMetric(summary, summaryJson, "total_injection_tokens_est"),
    health: compactCountMap(summary.health_counts || summaryJson.health_counts),
    config_source: configSources.join(" + ") || "旧 run 缺配置",
    output_file: record.output_file || fallbackRun.output_file || "",
    run_dir: record.run_dir || fallbackRun.run_dir || "",
  };
}

function runCompareCell(value, fallback = "-") {
  const text = value === undefined || value === null || value === "" ? fallback : String(value);
  return escapeHtml(text);
}

function updateRunCompareControls() {
  const count = state.selectedRunCompareIds.size;
  const button = $("compareSelectedRuns");
  if (button) button.textContent = count ? `对比选中结果 (${count})` : "对比选中结果";
  document.querySelectorAll(".run-card").forEach((card) => {
    const selected = state.selectedRunCompareIds.has(card.dataset.runKey || "");
    card.classList.toggle("selected-for-compare", selected);
    const checkbox = card.querySelector(".run-compare-check");
    if (checkbox) checkbox.checked = selected;
  });
}

function scoreDeltaText(score, baselineScore) {
  if (score === undefined || score === null || baselineScore === undefined || baselineScore === null) return "-";
  const delta = score - baselineScore;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${(delta * 100).toFixed(1)} pts`;
}

function renderNativeBaselinePanel() {
  const data = state.nativeOpenVikingBaseline || {};
  const baseline = data.baseline || null;
  const status = $("nativeBaselineStatus");
  const body = $("nativeBaselineBody");
  if (status) {
    status.textContent = baseline ? "已固定" : "未固定";
    status.classList.toggle("ready", Boolean(baseline));
  }
  if (!body) return;
  if (baseline) {
    body.innerHTML = `
      <div class="native-baseline-kpis">
        <article>
          <span>数据集 / 题量</span>
          <strong>${escapeHtml(baseline.dataset_format || "locomo")} · ${escapeHtml(baseline.rows ?? "-")} 题</strong>
        </article>
        <article>
          <span>正式分数</span>
          <strong>${baseline.accuracy === undefined || baseline.accuracy === null ? "待判分" : percent(baseline.accuracy)}</strong>
        </article>
        <article>
          <span>测试时间</span>
          <strong class="run-time">${escapeHtml(baseline.created_at || "-")}</strong>
        </article>
      </div>
      <p class="native-baseline-title">${escapeHtml(baseline.id || baseline.name || "原生 OpenViking")}</p>
      <p class="native-baseline-path">${escapeHtml(baseline.run_dir || baseline.output_file || "")}</p>
    `;
    return;
  }
  const candidates = data.candidates || [];
  body.innerHTML = `
    <p>还没有固定原生 OpenViking 基线。可以勾选左侧已跑完的原生结果后固定，或让系统自动寻找候选。</p>
    ${candidates.length ? `
      <div class="native-baseline-candidates">
        <strong>候选</strong>
        ${candidates.slice(0, 3).map((row) => `
          <span>${escapeHtml(row.id || row.name || "-")} · ${row.accuracy === undefined || row.accuracy === null ? "待判分" : percent(row.accuracy)}</span>
        `).join("")}
      </div>
    ` : ""}
  `;
}

async function refreshNativeOpenVikingBaseline({silent = false} = {}) {
  const data = await api("/api/native-openviking-baseline");
  state.nativeOpenVikingBaseline = data;
  renderNativeBaselinePanel();
  if (!silent) toast(data.configured ? "已刷新原生 OpenViking 基线" : "还没有固定原生 OpenViking 基线");
  return data;
}

function selectedRunForNativeBaseline() {
  const selected = state.recentRuns.filter((run) => state.selectedRunCompareIds.has(runCompareKey(run)));
  if (selected.length) return selected[0];
  if (state.selectedRunDir) {
    return state.recentRuns.find((run) => run.run_dir === state.selectedRunDir) || state.selectedRunRecord || null;
  }
  return state.recentRuns[0] || null;
}

async function pinSelectedNativeOpenVikingBaseline() {
  const run = selectedRunForNativeBaseline();
  if (!run?.run_dir) return toast("请先在左侧勾选或打开一个原生 OpenViking 结果");
  const data = await api("/api/native-openviking-baseline", {
    method: "POST",
    body: JSON.stringify({run_dir: run.run_dir, note: "pinned from report UI"}),
  });
  state.nativeOpenVikingBaseline = data;
  renderNativeBaselinePanel();
  await refreshRuns();
  toast("已固定为原生 OpenViking 基线");
}

async function autoPinNativeOpenVikingBaseline() {
  const data = await api("/api/native-openviking-baseline", {
    method: "POST",
    body: JSON.stringify({auto: true, note: "auto selected from report UI"}),
  });
  state.nativeOpenVikingBaseline = data;
  renderNativeBaselinePanel();
  await refreshRuns();
  toast("已自动固定原生 OpenViking 基线");
}

async function compareRunsWithNativeBaseline(runs = []) {
  const baseline = state.nativeOpenVikingBaseline?.baseline || {};
  if (!baseline.run_dir) {
    await refreshNativeOpenVikingBaseline({silent: true});
  }
  const activeBaseline = state.nativeOpenVikingBaseline?.baseline || {};
  if (!activeBaseline.run_dir) return toast("请先固定原生 OpenViking 基线");
  const candidateRuns = runs.length
    ? runs
    : state.recentRuns.filter((run) => state.selectedRunCompareIds.has(runCompareKey(run)));
  const runDirs = candidateRuns.map((run) => run.run_dir).filter(Boolean);
  if (!runDirs.length && state.selectedRunDir) runDirs.push(state.selectedRunDir);
  const uniqueRunDirs = [...new Set(runDirs.filter((runDir) => runDir !== activeBaseline.run_dir))];
  if (!uniqueRunDirs.length) return toast("请再选择至少一个待比较结果");
  $("runCompareResult").innerHTML = "<p>正在与原生 OpenViking 基线对比...</p>";
  const data = await api("/api/run-compare", {
    method: "POST",
    body: JSON.stringify({
      run_dirs: uniqueRunDirs,
      include_native_openviking_baseline: true,
    }),
  });
  renderRunCompareSummary(data.runs || [], {auto: false, baselineLabel: "原生 OpenViking"});
  toast(`已与原生 OpenViking 基线对比 ${uniqueRunDirs.length} 个结果`);
}

function renderRunCompareSummary(rows = [], options = {}) {
  const target = $("runCompareResult");
  if (!target) return;
  if (!rows.length) {
    target.innerHTML = "";
    return;
  }
  revealReportAnalysisPanel("runCompareResult");
  const normalized = rows.map((row) => ({
    id: row.id || row.name || "-",
    name: row.name || row.id || "-",
    kind: row.kind || "-",
    agent: row.agent || agentTypeLabel(row.agent_type || agentTypeForKind(row.kind || "")),
    status: row.status || "-",
    rows: row.rows ?? "-",
    graded: row.graded ?? "-",
	    score: row.accuracy ?? row.score,
	    official_metric: row.official_metric || "",
	    official_metric_scope: row.official_metric_scope || "",
	    exact: row.exact ?? row.exact_match_reference,
    duration_s: row.duration_s,
    answer_model: row.answer_model || "-",
    judge_model: row.judge_model || "-",
    embedding_model: row.embedding_model || "-",
    top_k: row.top_k || "-",
    prompt_mode: row.prompt_mode || "-",
    openviking_tool_set: row.openviking_tool_set || "-",
    openviking_tool_loop: row.openviking_tool_loop,
    openviking_content_read: row.openviking_content_read,
    max_iterations: row.max_iterations || "-",
    avg_iteration: row.avg_iteration || "-",
    tool_call_rows: row.tool_call_rows || "-",
    tool_call_total: row.tool_call_total || "-",
    tool_name_counts: row.tool_name_counts || "-",
    retrieval_mode: row.retrieval_mode || "-",
    query_expansion: row.query_expansion,
    lexical_fallback: row.lexical_fallback,
    archive_fallback: row.archive_fallback,
    memory_file_read: row.memory_file_read,
    memory_hits: row.memory_hits ?? "-",
    avg_memory_hits: row.avg_memory_hits ?? row.selected_memories_avg ?? "-",
    retrieval_tokens: row.retrieval_tokens ?? row.recall_tokens_avg ?? "-",
    answer_tokens: row.answer_tokens ?? "-",
    injection_tokens: row.injection_tokens ?? "-",
    health: row.health || "-",
    config_source: row.config_source || "-",
    output_file: row.output_file || "",
    run_dir: row.run_dir || "",
  }));
  const baseline = normalized.find((row) => row.score !== undefined && row.score !== null);
  const scoredRows = normalized.filter((row) => row.score !== undefined && row.score !== null);
  const best = scoredRows.length ? scoredRows.reduce((a, b) => (Number(a.score) >= Number(b.score) ? a : b)) : null;
  target.dataset.touched = options.auto ? "" : "1";
  target.innerHTML = `
    <div class="report-digest run-compare-digest">
      <div class="report-digest-head">
        <strong>${options.auto ? "最近结果摘要" : "选中结果对比"}</strong>
        <span>${escapeHtml(normalized.length)} 个结果 · 对照 ${escapeHtml(baseline?.id || "-")}</span>
      </div>
      <div class="result-kpis compact">
        <div class="kpi"><span>结果数</span><strong>${escapeHtml(normalized.length)}</strong></div>
        <div class="kpi"><span>最高分</span><strong>${best ? percent(best.score) : "待判分"}</strong></div>
        <div class="kpi"><span>最佳结果</span><strong>${escapeHtml(best?.id || "-")}</strong></div>
        <div class="kpi"><span>未判分</span><strong>${escapeHtml(normalized.filter((row) => row.score === undefined || row.score === null).length)}</strong></div>
      </div>
      <div class="run-compare-table-wrap">
        <table class="run-compare-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>类型</th>
              <th>题数</th>
              <th>分数 / 变化</th>
              <th>精确匹配</th>
              <th>耗时</th>
              <th>模型</th>
              <th>召回</th>
              <th>Token 用量</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            ${normalized.map((row) => `
              <tr>
                <td>
                  <strong>${runCompareCell(row.id)}</strong>
                  <small>${runCompareCell(row.status)} · ${runCompareCell(row.kind)}</small>
                  <small>${runCompareCell(row.config_source)}</small>
                </td>
                <td>${runCompareCell(row.agent)}</td>
                <td>${runCompareCell(row.rows)}<small>已判 ${runCompareCell(row.graded)}</small></td>
                <td>
	                  <strong>${row.score === undefined || row.score === null ? "待判分" : percent(row.score)}</strong>
	                  <small>${runCompareCell(row.official_metric || "formal_judge")}${row.official_metric_scope ? ` · ${runCompareCell(row.official_metric_scope)}` : ""}</small>
	                  <small>${runCompareCell(scoreDeltaText(row.score, baseline?.score))}</small>
                </td>
                <td>${row.exact === undefined || row.exact === null ? "-" : percent(row.exact)}</td>
                <td>${row.duration_s === undefined || row.duration_s === null ? "-" : escapeHtml(formatDuration(row.duration_s))}</td>
                <td>
                  <small>回答 ${runCompareCell(row.answer_model)}</small>
                  <small>判分 ${runCompareCell(row.judge_model)}</small>
                  <small>向量 ${runCompareCell(row.embedding_model)}</small>
                </td>
                <td>
                  <small>${runCompareCell(row.retrieval_mode)}</small>
                  <small>检索 ${runCompareCell(row.top_k)} · 命中 ${runCompareCell(row.avg_memory_hits)}</small>
                  <small>${runCompareCell(row.prompt_mode)} · 工具集合 ${runCompareCell(row.openviking_tool_set)}</small>
                  <small>工具循环 ${escapeHtml(reportBoolLabel(row.openviking_tool_loop))} · 内容读取 ${escapeHtml(reportBoolLabel(row.openviking_content_read))} · 迭代 ${runCompareCell(row.avg_iteration)}/${runCompareCell(row.max_iterations)}</small>
                  <small>工具调用 ${runCompareCell(row.tool_call_total)} · 结果行 ${runCompareCell(row.tool_call_rows)}</small>
                  <small>qe ${escapeHtml(reportBoolLabel(row.query_expansion))} · lex ${escapeHtml(reportBoolLabel(row.lexical_fallback))} · archive ${escapeHtml(reportBoolLabel(row.archive_fallback))} · file ${escapeHtml(reportBoolLabel(row.memory_file_read))}</small>
                </td>
                <td>
                  <small>回答 ${runCompareCell(row.answer_tokens)}</small>
                  <small>召回 ${runCompareCell(row.retrieval_tokens)}</small>
                  <small>注入 ${runCompareCell(row.injection_tokens)}</small>
                </td>
                <td>${runCompareCell(row.health)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
      ${options.auto ? "<p class=\"analysis-box-note\"><strong>提示</strong>勾选左侧结果后，可以生成自定义对比。</p>" : ""}
    </div>
  `;
}

async function compareSelectedRuns() {
  const selected = state.recentRuns.filter((run) => state.selectedRunCompareIds.has(runCompareKey(run)));
  if (state.nativeOpenVikingBaseline?.baseline?.run_dir && selected.length >= 1) {
    await compareRunsWithNativeBaseline(selected);
    return;
  }
  if (selected.length < 2) return toast("请至少勾选 2 个结果，或先固定原生 OpenViking 基线");
  $("runCompareResult").innerHTML = "<p>正在读取结果摘要...</p>";
  const runDirs = selected.map((run) => run.run_dir).filter(Boolean);
  if (runDirs.length >= 2) {
    try {
      const data = await api("/api/run-compare", {
        method: "POST",
        body: JSON.stringify({run_dirs: runDirs}),
      });
      renderRunCompareSummary(data.runs || [], {auto: false});
      toast(`已对比 ${data.count || runDirs.length} 个报告`);
      return;
    } catch {
      // Fall through to per-run detail loading for older servers.
    }
  }
  const rows = await Promise.all(selected.map(async (run) => {
    if (!run.run_dir) return summarizeRunForCompare({record: run}, run);
    try {
      const detail = await api(`/api/run-detail?run_dir=${encodeURIComponent(run.run_dir)}`);
      const snapshot = await api(`/api/config-snapshot?run_dir=${encodeURIComponent(run.run_dir)}`).catch(() => null);
      if (snapshot?.config) detail.config_snapshot = snapshot.config;
      return summarizeRunForCompare(detail, run);
    } catch {
      return summarizeRunForCompare({record: run}, run);
    }
  }));
  renderRunCompareSummary(rows, {auto: false});
  toast(`已对比 ${rows.length} 个报告`);
}

function clearSelectedRuns() {
  state.selectedRunCompareIds.clear();
  updateRunCompareControls();
  const target = $("runCompareResult");
  if (target) {
    target.dataset.touched = "";
    target.innerHTML = "";
  }
  toast("已清空报告对比选择");
}

async function refreshRuns() {
  const runsList = $("runsList");
  state.runsLoading = true;
  renderRunsSelectionState();
  if (runsList && !runsList.querySelector(".run-card")) {
    runsList.innerHTML = `<p class="muted-list-note">正在读取结果列表...</p>`;
  }
  try {
    await refreshNativeOpenVikingBaseline({silent: true}).catch(() => renderNativeBaselinePanel());
    const data = await api("/api/runs?limit=80");
    const allRuns = data.runs || [];
    const runs = allRuns
      .filter((run) => !currentAccountOnlyEnabled("runsCurrentAccountOnly") || matchesCurrentAccount(run))
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    state.recentRuns = runs;
    const visibleKeys = new Set(runs.map(runCompareKey));
    [...state.selectedRunCompareIds].forEach((key) => {
      if (!visibleKeys.has(key)) state.selectedRunCompareIds.delete(key);
    });
    state.runsLoadedAt = Date.now();
    const scoredRuns = runs.filter(runHasFormalScore);
    const pendingRuns = runs.filter((run) => !runHasFormalScore(run));
    if (runsList) {
      runsList.innerHTML = runs.length
      ? `${renderRunGroup("已有分数", scoredRuns)}${renderRunGroup("待判分", pendingRuns)}`
      : `<p class="muted-list-note">${currentAccountOnlyEnabled("runsCurrentAccountOnly") ? "当前空间暂无结果。" : "暂无结果。"}</p>`;
    }
    document.querySelectorAll("#runsList .memory-hit").forEach((card) => {
      card.addEventListener("click", () => loadRunDetail(card.dataset.runDir || "", card.dataset.outputFile || "", card.dataset.datasetFormat || "").catch((e) => toast(e.message)));
    });
    document.querySelectorAll("#runsList .run-compare-toggle").forEach((label) => {
      label.addEventListener("click", (event) => event.stopPropagation());
    });
    document.querySelectorAll("#runsList .run-compare-check").forEach((checkbox) => {
      checkbox.addEventListener("change", (event) => {
        event.stopPropagation();
        const card = event.currentTarget.closest(".run-card");
        const key = card?.dataset.runKey || "";
        if (!key) return;
        if (event.currentTarget.checked) state.selectedRunCompareIds.add(key);
        else state.selectedRunCompareIds.delete(key);
        updateRunCompareControls();
      });
    });
    updateRunCompareControls();
    const selected = state.selectedRunDir
      ? runs.find((run) => run.run_dir === state.selectedRunDir)
      : null;
    if (selected) {
      await loadRunDetail(selected.run_dir || "", selected.output_file || "", benchmarkFormatFromRecord(selected));
    } else {
      resetRunsDetailPanels();
    }
  } finally {
    state.runsLoading = false;
    renderRunsSelectionState();
  }
}

async function handleRunCardAction(runDir, outputFile, action, datasetFormat = "") {
  if (!runDir && !outputFile) return toast("这个结果没有可用路径");
  if (action === "report") {
    if (!runDir) return toast("这个结果没有目录");
    state.selectedRunDir = runDir;
    await loadRunDetail(runDir, outputFile, datasetFormat);
    await exportRunReport();
    return;
  }
  await loadRunDetail(runDir, outputFile, datasetFormat);
}

function runAuditMetric(label, value, detail = "") {
  return `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value === undefined || value === null || value === "" ? "-" : value)}</strong>
      ${detail ? `<small>${escapeHtml(detail)}</small>` : ""}
    </article>
  `;
}

function runAuditChip(label, status = "ok", detail = "") {
  return `
    <span class="run-audit-chip ${escapeHtml(status)}" title="${escapeHtml(detail || label)}">
      ${escapeHtml(label)}
    </span>
  `;
}

function artifactIssueCount(artifactStatus = {}) {
  return Object.values(artifactStatus || {}).filter((item) => item && item.exists === false).length;
}

function configSourceAuditChip(source = "") {
  const text = String(source || "").trim();
  if (!text || text === "旧 run 缺配置") {
    return runAuditChip("缺配置快照", "warn", "无法完整复现模型、top-k 和上下文参数");
  }
  if (!/(manifest|config snapshot)/.test(text) && /(summary json|csv preview)/.test(text)) {
    return runAuditChip("部分配置", "warn", "从 summary/CSV 恢复了部分模型、召回和上下文参数，但缺少完整命令快照");
  }
  return runAuditChip("配置可复现", "ok", text);
}

function auditSwitchOn(value) {
  const text = String(value ?? "").trim().toLowerCase();
  return value === true || ["native", "true", "1", "yes", "on", "enabled"].includes(text);
}

function auditSwitchOff(value) {
  const text = String(value ?? "").trim().toLowerCase();
  return value === false || ["", "-", "false", "0", "no", "none", "disabled", "off"].includes(text);
}

function firstNumber(value) {
  const match = String(value ?? "").match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

function alignmentCheckChip(ok, label, detail = "") {
  return runAuditChip(label, ok ? "ok" : "warn", detail);
}

function vikingbotRunAlignment(row = {}) {
  const promptMode = String(row.prompt_mode || "").trim();
  const promptKey = promptMode.toLowerCase();
  const native = promptKey === "native_vikingbot_cli";
  const customPrompt = ["vikingbot_aligned", "vikingboat_compat", "vikingboat_lite"].includes(promptKey);
  const promptOk = native || customPrompt || auditSwitchOn(row.vikingbot_prompt_aligned);
  const topKNumber = firstNumber(row.top_k) ?? firstNumber(row.initial_search_limit);
  const toolSearchLimitNumber = firstNumber(row.tool_search_limit);
  const topKOk = native || String(row.top_k || "").includes("原生") || (topKNumber !== null && topKNumber >= 30);
  const toolSearchLimitOk = native || toolSearchLimitNumber === null || toolSearchLimitNumber === 20;
  const toolLoopOk = native || auditSwitchOn(row.openviking_tool_loop);
  const toolSet = String(row.openviking_tool_set || "").trim().toLowerCase();
  const toolSetOk = native
    ? ["native_vikingbot_cli", ""].includes(toolSet)
    : ["vikingbot_native_safe", "vikingboat_default", "vikingbot_openviking", "search_read", ""].includes(toolSet);
  const groupChatOk = auditSwitchOn(row.group_chat) || auditSwitchOff(row.group_chat);
  const identityOk = ["sender_session", ""].includes(String(row.vikingbot_identity_mode || "").trim().toLowerCase());
  const channelOk = ["cli", ""].includes(String(row.vikingbot_channel || "").trim().toLowerCase());
  const memoryUsersOk = ["sender_sample_namespace", "vikingbot_group_chat", "memory_users_override", ""].includes(String(row.memory_user_strategy || "").trim().toLowerCase());
  const agentMemoryOk = native || auditSwitchOn(row.initial_agent_memory) || String(row.initial_agent_memory || "").trim() === "";
  const noExtraContext = [
    row.query_expansion,
    row.lexical_fallback,
    row.archive_fallback,
    row.memory_file_read,
    row.raw_turn_fallback,
  ].every(auditSwitchOff);
  const comparable = promptOk && topKOk && toolSearchLimitOk && toolLoopOk && toolSetOk && groupChatOk && identityOk && channelOk && memoryUsersOk && agentMemoryOk && noExtraContext;
  const mode = native ? "OpenViking 参考模式（历史）" : (customPrompt || auditSwitchOn(row.vikingbot_prompt_aligned) ? "MemoryBench Agent 对齐模式" : "非对齐提示词");
  const title = comparable ? `${mode} · 可对比` : `${mode} · 需确认`;
  const detail = comparable
    ? "关键上下文工程与 VikingBoat 参考链路可比；准确率差异可优先归因到后端检索、记忆质量或模型。"
    : "存在未对齐参数或额外上下文；直接比较准确率前需要先排除这些影响。";
  return {
    tone: comparable ? "ok" : "warn",
    comparable,
    title,
    detail,
    chips: [
      alignmentCheckChip(promptOk, "提示词", `prompt=${promptMode || "-"}`),
      alignmentCheckChip(topKOk, RETRIEVAL_COUNT_LABEL, `top_k=${row.top_k || "-"}; initial=${row.initial_search_limit || "-"}; tool=${row.tool_search_limit || "-"}`),
      alignmentCheckChip(toolSearchLimitOk, "工具检索", `limit=${row.tool_search_limit || "-"}; VikingBoat=20`),
      alignmentCheckChip(toolLoopOk, "工具循环", `loop=${reportBoolLabel(row.openviking_tool_loop)}`),
      alignmentCheckChip(toolSetOk, "工具集合", row.openviking_tool_set || "-"),
      alignmentCheckChip(agentMemoryOk, "Agent 记忆", `initial=${reportBoolLabel(row.initial_agent_memory)}; VikingBoat=on`),
      alignmentCheckChip(noExtraContext, "无额外兜底", "query/lexical/archive/file/raw fallback 应关闭"),
    ],
    metrics: [
      ["对齐模式", mode],
      ["对齐档位", row.vikingboat_alignment_profile || "-"],
      ["后端路由", row.alignment_backend_route || "-"],
      ["提示词", promptMode || "-"],
      ["提示词对齐", reportBoolLabel(row.vikingbot_prompt_aligned)],
      [RETRIEVAL_COUNT_LABEL, row.top_k || "-"],
      ["初始检索", row.initial_search_limit || "-", `score ${row.initial_score_threshold || "-"}`],
      ["工具检索", row.tool_search_limit || "-", `score ${row.tool_min_score || "-"}`],
      ["工具循环", reportBoolLabel(row.openviking_tool_loop), `calls ${row.tool_call_total || "-"}`],
      ["工具集合", row.openviking_tool_set || "-"],
      ["初始 Agent 记忆", reportBoolLabel(row.initial_agent_memory), "VikingBoat 默认开启"],
      ["身份模式", row.vikingbot_identity_mode || "-"],
      ["额外上下文", noExtraContext ? "关闭" : "开启", "query / lexical / archive / file / raw fallback"],
    ],
  };
}

function evidenceContractBackend(record = {}) {
  const text = [
    record.backend,
    record.memory_backend,
    record.agent_type,
    record.kind,
    record.eval_engine,
    record.output_file,
    record.run_dir,
    record.name,
    record.id,
  ].map((value) => String(value || "")).join(" ").toLowerCase();
  if (/(echomem|echomemory|echo:\/\/)/.test(text)) return "echomemory";
  if (/(openviking|viking:\/\/|vikingbot)/.test(text)) return "openviking";
  return "";
}

function evidenceContractTone(status = "") {
  const text = String(status || "").toLowerCase();
  if (text === "ok") return "ok";
  if (text === "fail" || text === "bad") return "bad";
  return "warn";
}

function renderEvidenceSnippet(value) {
  if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) {
    return "";
  }
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const clipped = text.length > 900 ? `${text.slice(0, 900)}...` : text;
  return `<pre>${escapeHtml(clipped)}</pre>`;
}

function renderEvidenceContract(data = null) {
  const target = $("evidenceContractPanel");
  if (!target) return;
  if (!data) {
    target.innerHTML = "";
    return;
  }
  const tone = evidenceContractTone(data.status);
  const checks = Array.isArray(data.checks) ? data.checks : [];
  const checkHtml = checks.map((check) => {
    const checkTone = evidenceContractTone(check.status);
    const evidence = renderEvidenceSnippet(check.evidence);
    return `
      <article class="evidence-contract-check ${checkTone}">
        <div>
          <span>${escapeHtml(check.severity || "check")}</span>
          <strong>${escapeHtml(check.title || check.id || "Evidence check")}</strong>
          <p>${escapeHtml(check.detail || "")}</p>
        </div>
        ${runAuditChip(String(check.status || "-").toUpperCase(), checkTone, check.id || "")}
        ${evidence ? `<details><summary>示例 / 缺失字段</summary>${evidence}</details>` : ""}
      </article>
    `;
  }).join("");
  const strictLabel = data.backend === "echomemory"
    ? `${data.echomem_valid_items ?? 0}/${data.total_items ?? 0}`
    : "OpenViking 不强制";
  const summaryCheckCount = checks.length;
  target.innerHTML = `
    <section class="run-audit-card evidence-contract-card ${tone}">
      <div class="run-audit-head">
        <div>
          <span class="label">证据契约</span>
          <h4>相关记忆输出格式</h4>
        </div>
        <div class="run-audit-chips">
          ${runAuditChip(String(data.status || "-").toUpperCase(), tone, data.path || "")}
          ${runAuditChip(`后端 ${data.backend || "unknown"}`, data.backend === "unknown" ? "warn" : "ok")}
        </div>
      </div>
      <div class="run-audit-grid">
        ${runAuditMetric("结果行数", data.rows)}
        ${runAuditMetric("非空召回行", `${data.rows_with_relevant_memory ?? 0}/${data.rows ?? 0}`)}
        ${runAuditMetric("证据条目", data.total_items)}
        ${runAuditMetric("报告可消费", `${data.basic_valid_items ?? 0}/${data.total_items ?? 0}`, "需要 content/abstract、uri/path、score")}
        ${runAuditMetric("EchoMemory 严格字段", strictLabel, "content / uri / score / memory_type / evidence_uri / trace")}
        ${runAuditMetric("类型分布", compactCountMap(data.item_type_counts || {}, 4))}
      </div>
      <details class="evidence-contract-checks-fold">
        <summary>
          <span class="label">检查项</span>
          <strong>${escapeHtml(summaryCheckCount)} 条</strong>
        </summary>
        <div class="evidence-contract-checks">
          ${checkHtml || "<p>没有证据检查项。</p>"}
        </div>
      </details>
      <small class="evidence-contract-path">${escapeHtml(data.path || "")}</small>
    </section>
  `;
}

async function loadEvidenceContract(outputFile = "", record = {}) {
  const target = $("evidenceContractPanel");
  if (!target) return;
  const path = String(outputFile || record.output_file || "").trim();
  if (!path || !/\.csv$/i.test(path)) {
    target.innerHTML = "";
    return;
  }
  target.innerHTML = `
    <section class="run-audit-card evidence-contract-card">
      <div class="run-audit-head">
        <div>
          <span class="label">证据契约</span>
          <h4>正在检查 relevant_memory 字段...</h4>
        </div>
      </div>
    </section>
  `;
  try {
    const backend = evidenceContractBackend(record);
    const data = await api(`/api/evidence-contract?path=${encodeURIComponent(path)}&backend=${encodeURIComponent(backend)}&limit=5000`);
    renderEvidenceContract(data);
  } catch (error) {
    target.innerHTML = `
      <section class="run-audit-card evidence-contract-card bad">
        <div class="run-audit-head">
          <div>
            <span class="label">证据契约</span>
            <h4>检查失败</h4>
          </div>
          <div class="run-audit-chips">${runAuditChip("失败", "bad", error.message)}</div>
        </div>
        <p class="evidence-contract-error">${escapeHtml(error.message || "无法检查证据契约")}</p>
      </section>
    `;
  }
}

function failureSeverityTone(value = "") {
  const text = String(value || "").toLowerCase();
  if (text === "bad" || text === "fail" || text === "error") return "bad";
  if (text === "ok" || text === "pass") return "ok";
  return "warn";
}

function failureOwnerLabel(value = "") {
  const labels = {
    agent: "Agent",
    agent_prompt: "Agent 提示词",
    context_engineering: "上下文工程",
    retrieval: "检索",
    model: "模型/API",
    judge: "判分",
    none: "无",
  };
  return labels[String(value || "")] || value || "-";
}

function failureQuestionKindLabel(value = "") {
  const labels = {
    time: "时间题",
    list: "列表/聚合",
    fact: "事实题",
  };
  return labels[String(value || "")] || value || "-";
}

function renderFailureExample(example = {}, index = 0) {
  const evidence = example.evidence || {};
  const evidenceText = evidence.content || evidence.abstract || evidence.text || "";
  return `
    <article class="failure-example">
      <div>
        <span>${escapeHtml(example.sample_id || "-")} · ${escapeHtml(example.question_id || `row-${Number(example.row_index ?? index) + 1}`)} · C${escapeHtml(example.category || "")}</span>
        <strong>${escapeHtml(example.question || "-")}</strong>
      </div>
      <div class="failure-answer-grid">
        <section><span>Gold</span><p>${escapeHtml(example.gold || "-")}</p></section>
        <section><span>Response</span><p>${escapeHtml(example.response || "-")}</p></section>
      </div>
      ${evidenceText ? `
        <details class="failure-evidence">
          <summary>证据 ${escapeHtml(example.evidence_count ?? 0)} 条 · ${escapeHtml(evidence.uri || "")}</summary>
          <pre>${escapeHtml(evidenceText)}</pre>
        </details>
      ` : ""}
      ${example.reasoning ? `
        <details class="failure-evidence">
          <summary>判分 / 理由</summary>
          <pre>${escapeHtml(example.reasoning)}</pre>
        </details>
      ` : ""}
    </article>
  `;
}

function renderFailureAttribution(data = {}, targetId = "failureAttributionPanel") {
  const target = $(targetId);
  if (!target) return;
  const analysis = data.analysis || data || {};
  const attribution = analysis.failure_attribution || {};
  const buckets = Array.isArray(attribution.buckets) ? attribution.buckets : [];
  if (!attribution.total && !buckets.length) {
    target.innerHTML = "";
    return;
  }
  const badCount = Number((attribution.severity_counts || {}).bad || 0);
  const warnCount = Number((attribution.severity_counts || {}).warn || 0);
  const tone = badCount > 0 ? "bad" : (warnCount > 0 ? "warn" : "ok");
  const actionItems = Array.isArray(attribution.action_items) ? attribution.action_items : [];
  const modeCounts = attribution.mode_counts || {};
  const topMode = Object.entries(modeCounts).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))[0];
  const topBucket = topMode ? buckets.find((bucket) => bucket.mode === topMode[0]) : null;
  const bucketHtml = buckets.slice(0, 8).map((bucket) => {
    const bucketTone = failureSeverityTone(bucket.severity);
    const examples = Array.isArray(bucket.examples) ? bucket.examples.slice(0, 2) : [];
    return `
      <details class="failure-bucket ${bucketTone}">
        <summary class="failure-bucket-head">
          <div>
            <span>${escapeHtml(failureOwnerLabel(bucket.owner))} · ${bucket.retryable ? "可重跑" : "需分析"}</span>
            <strong>${escapeHtml(bucket.label || bucket.mode || "Failure")}</strong>
            <p>${escapeHtml(bucket.reason || "")}</p>
          </div>
          ${runAuditChip(`${bucket.count ?? 0} 题`, bucketTone, bucket.mode || "")}
        </summary>
        ${examples.length ? `<div class="failure-example-list">${examples.map((example, index) => renderFailureExample(example, index)).join("")}</div>` : "<div class=\"failure-empty-note\">暂无示例。</div>"}
      </details>
    `;
  }).join("");
  target.innerHTML = `
    <section class="run-audit-card failure-attribution-card ${tone}">
      <div class="run-audit-head">
        <div>
          <span class="label">错误归因</span>
          <h4>${topBucket ? `${escapeHtml(topBucket.label || topBucket.mode)} 是当前最大问题` : "当前结果没有明显失败桶"}</h4>
        </div>
        <div class="run-audit-chips">
          ${runAuditChip(`问题 ${attribution.problem_rows ?? 0}`, tone)}
          ${runAuditChip(`可重跑 ${attribution.retryable_rows ?? 0}`, Number(attribution.retryable_rows || 0) ? "warn" : "ok")}
          ${runAuditChip(`正确 ${attribution.correct_rows ?? 0}`, "ok")}
        </div>
      </div>
      <div class="run-audit-grid">
        ${runAuditMetric("总行数", attribution.total)}
        ${runAuditMetric("问题行", attribution.problem_rows)}
        ${runAuditMetric("可重跑", attribution.retryable_rows)}
        ${runAuditMetric("责任侧", compactCountMap(attribution.owner_counts || {}, 5))}
        ${runAuditMetric("题型", Object.entries(attribution.question_kind_counts || {}).map(([key, value]) => `${failureQuestionKindLabel(key)}: ${value}`).join(" · ") || "-")}
        ${runAuditMetric("失败桶", compactCountMap(attribution.mode_counts || {}, 5))}
      </div>
      ${actionItems.length ? `
        <div class="failure-actions">
          ${actionItems.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
        </div>
      ` : ""}
      <details class="run-audit-fold failure-bucket-fold">
        <summary>
          <span class="label">失败桶</span>
          <strong>${escapeHtml(buckets.length)} 类问题</strong>
        </summary>
        <div class="failure-bucket-list">
        ${bucketHtml || "<p>没有错误或待处理问题。</p>"}
        </div>
      </details>
      <small class="evidence-contract-path">${escapeHtml(data.analysis_path || data.input || "")}</small>
    </section>
  `;
  bindCopyButtons(`#${targetId}`);
}

async function loadFailureAttribution(outputFile = "", targetId = "failureAttributionPanel") {
  const target = $(targetId);
  if (!target) return null;
  const path = String(outputFile || "").trim();
  if (!path || !/\.csv$/i.test(path)) {
    target.innerHTML = "";
    return null;
  }
  target.innerHTML = `
    <section class="run-audit-card failure-attribution-card">
      <div class="run-audit-head">
        <div>
          <span class="label">错误归因</span>
          <h4>正在分析错误来源...</h4>
        </div>
      </div>
    </section>
  `;
  try {
    const data = await api(`/api/wrong-clusters?path=${encodeURIComponent(path)}`);
    renderFailureAttribution(data, targetId);
    return data;
  } catch (error) {
    target.innerHTML = `
      <section class="run-audit-card failure-attribution-card bad">
        <div class="run-audit-head">
          <div>
            <span class="label">错误归因</span>
            <h4>分析失败</h4>
          </div>
          <div class="run-audit-chips">${runAuditChip("失败", "bad", error.message)}</div>
        </div>
        <p class="evidence-contract-error">${escapeHtml(error.message || "无法分析错误归因")}</p>
      </section>
    `;
    return null;
  }
}

function renderRunAudit(detail = {}, candidateRecord = {}, summary = {}, runDir = "", outputFile = "") {
  const target = $("runAuditPanel");
  if (!target) return;
  if (!detail && !candidateRecord) {
    target.innerHTML = "";
    return;
  }
  const summaryJson = summary.summary_json || {};
  const row = summarizeRunForCompare(detail, candidateRecord);
  const artifactStatus = detail.artifact_status || {};
  const missingArtifacts = artifactIssueCount(artifactStatus);
  const modelFailed = Number(summary.model_failed_count ?? summaryJson.model_failed_count ?? (summary.model_status_counts || {}).failed ?? 0);
  const unknownAnswers = Number(summary.answer_empty_or_unknown_count ?? summaryJson.answer_empty_or_unknown_count ?? (summary.answer_status_counts || {}).empty_or_unknown ?? 0);
  const retrievalErrors = Number(summary.retrieval_error_rows ?? summaryJson.retrieval_error_rows ?? 0);
  const pendingRows = Number((summary.result_counts || {}).UNSCORED ?? summary.pending ?? summaryJson.pending ?? 0);
  const fallbackTotal = Number(summary.archive_fallback_total ?? summaryJson.archive_fallback_total ?? 0);
  const alignment = vikingbotRunAlignment(row);
  const warnings = [
    configSourceAuditChip(row.config_source),
    alignment.comparable ? runAuditChip("Agent 可对比", "ok", alignment.detail) : runAuditChip("Agent 待确认", "warn", alignment.detail),
	    row.accuracy === undefined || row.accuracy === null ? runAuditChip("待指标", "warn", "QA 已完成但正式准确率或官方指标还未产生") : runAuditChip(row.official_metric ? "官方指标" : "已判分", "ok", `${row.official_metric || "judge"} ${percent(row.accuracy)}`),
    modelFailed > 0 ? runAuditChip(`模型失败 ${modelFailed}`, "bad", "存在模型/API 失败行") : runAuditChip("模型无失败", "ok"),
    unknownAnswers > 0 ? runAuditChip(`未知回答 ${unknownAnswers}`, "warn", "存在空回答或 unknown 回答") : runAuditChip("回答非空", "ok"),
    retrievalErrors > 0 ? runAuditChip(`检索错误 ${retrievalErrors}`, "bad", "存在检索失败行") : runAuditChip("检索无错误", "ok"),
    fallbackTotal > 0 ? runAuditChip(`兜底 ${fallbackTotal}`, "warn", "存在 archive/source fallback，上下文不完全来自正式记忆检索") : runAuditChip("无原文兜底", "ok"),
    missingArtifacts > 0 ? runAuditChip(`缺产物 ${missingArtifacts}`, "warn", "部分报告/日志/快照文件不存在") : runAuditChip("产物齐全", "ok"),
  ];
  if (pendingRows > 0) warnings.splice(2, 0, runAuditChip(`未判分 ${pendingRows}`, "warn", "存在 UNSCORED 行"));
  const questionText = row.questions && row.questions !== "-" ? String(row.questions).split(",").length : "-";
  target.innerHTML = `
    <section class="run-audit-card primary">
      <div class="run-audit-head">
        <div>
          <span class="label">运行审计</span>
          <h4>${escapeHtml(row.id || candidateRecord.id || "当前结果")}</h4>
        </div>
        <div class="run-audit-chips">${warnings.join("")}</div>
      </div>
      <div class="run-audit-grid">
        ${runAuditMetric("回答模型", row.answer_model)}
        ${runAuditMetric("判分模型", row.judge_model)}
        ${runAuditMetric("向量模型", row.embedding_model)}
        ${runAuditMetric(RETRIEVAL_COUNT_LABEL, row.top_k)}
        ${runAuditMetric("提示词", row.prompt_mode)}
        ${runAuditMetric("官方指标", row.official_metric || "-")}
        ${runAuditMetric("配置来源", row.config_source)}
      </div>
    </section>
    <details class="run-audit-fold ${alignment.tone}">
      <summary>
        <span class="label">Agent 对齐</span>
        <strong>${escapeHtml(alignment.title)}</strong>
      </summary>
      <section class="run-audit-card ${alignment.tone}">
        <div class="run-audit-head">
          <div>
            <span class="label">自定义 Agent / VikingBoat 对齐</span>
            <h4>${escapeHtml(alignment.title)}</h4>
          </div>
          <div class="run-audit-chips">${alignment.chips.join("")}</div>
        </div>
        <p class="evidence-contract-error">${escapeHtml(alignment.detail)}</p>
        <div class="run-audit-grid">
          ${alignment.metrics.map(([label, value, detail]) => runAuditMetric(label, value, detail)).join("")}
        </div>
      </section>
    </details>
    <details class="run-audit-fold">
      <summary>
        <span class="label">上下文</span>
        <strong>范围与上下文</strong>
      </summary>
      <section class="run-audit-card">
        <div class="run-audit-section-title">
          <span>范围与上下文</span>
          <small>用于判断分数是否来自目标记忆链路。</small>
        </div>
        <div class="run-audit-grid">
          ${runAuditMetric("空间", row.account)}
          ${runAuditMetric("记忆目录", row.workspace)}
          ${runAuditMetric("样本", row.sample)}
          ${runAuditMetric("选题", questionText, row.questions && row.questions !== "-" ? "已指定题目" : "未指定或全量")}
          ${runAuditMetric("检索模式", row.retrieval_mode)}
          ${runAuditMetric("平均记忆命中", row.avg_memory_hits)}
          ${runAuditMetric("记忆命中总数", row.memory_hits)}
          ${runAuditMetric("工具循环", reportBoolLabel(row.openviking_tool_loop), `tools ${row.openviking_tool_set || "-"}`)}
          ${runAuditMetric("读取原文", reportBoolLabel(row.openviking_content_read))}
          ${runAuditMetric("Query Expansion", reportBoolLabel(row.query_expansion))}
          ${runAuditMetric("Lexical Fallback", reportBoolLabel(row.lexical_fallback))}
          ${runAuditMetric("Archive Fallback", reportBoolLabel(row.archive_fallback), fallbackTotal ? `${fallbackTotal} hits` : "")}
          ${runAuditMetric("Memory File Read", reportBoolLabel(row.memory_file_read))}
        </div>
      </section>
    </details>
    <details class="run-audit-fold">
      <summary>
        <span class="label">排查</span>
        <strong>异常与产物</strong>
      </summary>
      <section class="run-audit-card">
        <div class="run-audit-section-title">
          <span>异常与产物</span>
          <small>优先排查红色/黄色 chip。</small>
        </div>
        <div class="run-audit-grid">
          ${runAuditMetric("模型失败", modelFailed)}
          ${runAuditMetric("Unknown/空回答", unknownAnswers)}
          ${runAuditMetric("检索错误", retrievalErrors)}
          ${runAuditMetric("未判分", pendingRows)}
          ${runAuditMetric("结果文件", outputFile || row.output_file || candidateRecord.output_file || "-")}
          ${runAuditMetric("Run 目录", runDir || row.run_dir || candidateRecord.run_dir || "-")}
        </div>
      </section>
    </details>
  `;
}

async function loadRunDetail(runDir, outputFile, datasetFormat = "") {
  if ($("runDetailPanel")) $("runDetailPanel").hidden = false;
  $("questionDetailPane").innerHTML = "";
  let detail = null;
  let record = {};
  let summary = {};
  const formatHint = normalizeDatasetFormat(datasetFormat);
  if (runDir) {
    detail = await api(`/api/run-detail?run_dir=${encodeURIComponent(runDir)}`);
    record = detail.record || {};
    summary = detail.summary || record.summary || {};
    const candidateRecord = {...record, run_dir: record.run_dir || runDir, output_file: record.output_file || outputFile, summary};
    state.selectedRunDir = runDir || state.selectedRunDir || "";
    state.selectedRunDatasetFormat = fallbackDatasetFormatForRecord(candidateRecord, formatHint);
    state.selectedRunRecord = candidateRecord;
    rememberBenchmarkRecord(candidateRecord, state.selectedRunDatasetFormat);
    if (state.selectedRunDatasetFormat !== "locomo") {
      markDatasetOutputFile(candidateRecord.output_file || outputFile || "", state.selectedRunDatasetFormat);
    }
    rememberEvidenceScope(candidateRecord, candidateRecord.output_file || outputFile);
  } else {
    state.selectedRunDir = runDir || state.selectedRunDir || "";
    const run = (state.recentRuns || []).find((item) => item.output_file === outputFile || item.run_dir === runDir);
    if (run) {
      state.selectedRunRecord = run;
      state.selectedRunDatasetFormat = fallbackDatasetFormatForRecord(run, formatHint);
      rememberBenchmarkRecord(run, state.selectedRunDatasetFormat);
      if (state.selectedRunDatasetFormat !== "locomo") {
        markDatasetOutputFile(outputFile || run.output_file || "", state.selectedRunDatasetFormat);
      }
      rememberEvidenceScope(run, outputFile || run.output_file || "");
    }
  }
  if (outputFile) {
    if (state.selectedRunDatasetFormat === "locomo" && /\.csv$/i.test(outputFile)) {
      markLocomoOutputFile(outputFile);
      await loadRunQuestions(outputFile);
    }
  }
  if (runDir) {
    const summaryJson = summary.summary_json || {};
    state.selectedRunSummary = summary;
    const fallbackTotal = Number(summaryJson.archive_fallback_total ?? summary.archive_fallback_total ?? 0);
    const avgMemoryInjectionTime = summary.avg_memory_injection_time_s ?? summaryJson.avg_memory_injection_time_s;
    const totalMemoryInjectionTime = summary.total_memory_injection_time_s ?? summaryJson.total_memory_injection_time_s;
    const avgQaTime = summary.avg_qa_time_s ?? summaryJson.avg_qa_time_s ?? summary.avg_time;
    const totalQaTime = summary.total_qa_time_s ?? summaryJson.total_qa_time_s;
    const avgEndToEndTime = summary.avg_end_to_end_time_s ?? summaryJson.avg_end_to_end_time_s;
    const answerEm = summary.official_answer_em ?? summaryJson.official_answer_em ?? summary.answer_em ?? summaryJson.answer_em;
    const answerF1 = summary.official_answer_f1 ?? summaryJson.official_answer_f1 ?? summary.answer_f1 ?? summaryJson.answer_f1;
    const kpis = [
      ["类型", agentTypeLabel(record.agent_type || agentTypeForKind(record.kind || ""))],
      ["状态", detail.status || record.status || "-"],
      ["题数", summary.rows ?? "-"],
      ["准确率", summary.accuracy == null ? "待判分" : percent(summary.accuracy)],
      ["数据集", datasetTypeLabel(state.selectedRunDatasetFormat || summaryDatasetFormat(summary))],
      ["召回方式", summaryJson.retrieval_mode ?? "-"],
      ["记忆命中", summaryJson.memory_hit_total ?? summary.memory_hit_total ?? "-"],
    ];
    if ((summary.official_metric || summaryJson.official_metric) && (summary.official_score ?? summaryJson.official_score) != null) {
      kpis.splice(4, 0, ["官方指标", `${summary.official_metric || summaryJson.official_metric} · ${percent(summary.official_score ?? summaryJson.official_score)}`]);
    }
    if (summary.exact_match_reference != null) {
      kpis.splice(5, 0, ["精确匹配", `${summary.simple_correct ?? "-"} / ${summary.rows ?? "-"} · ${percent(summary.exact_match_reference)}`]);
    }
    if (fallbackTotal > 0) {
      kpis.push(["原文兜底", fallbackTotal]);
    }
    if (avgMemoryInjectionTime != null) {
      kpis.push(["平均注入时间", formatSecondsMetric(avgMemoryInjectionTime)]);
    }
    if (totalMemoryInjectionTime != null) {
      kpis.push(["总注入时间", formatSecondsMetric(totalMemoryInjectionTime)]);
    }
    if (avgQaTime != null) {
      kpis.push(["平均 QA 时间", formatSecondsMetric(avgQaTime)]);
    }
    if (totalQaTime != null) {
      kpis.push(["总 QA 时间", formatSecondsMetric(totalQaTime)]);
    }
    if (avgEndToEndTime != null) {
      kpis.push(["平均端到端", formatSecondsMetric(avgEndToEndTime)]);
    }
    if (answerEm != null || answerF1 != null) {
      kpis.push(["HotpotQA EM/F1", `${answerEm == null ? "-" : percent(answerEm)} / ${answerF1 == null ? "-" : percent(answerF1)}`]);
    }
    renderKpis("runDetailKpis", kpis);
    const artifactStatus = detail.artifact_status || {};
    const auditRecord = {...record, run_dir: record.run_dir || runDir, output_file: record.output_file || outputFile, summary};
    const resolvedOutputFile = outputFile || record.output_file || "";
    renderRunAudit(detail, auditRecord, summary, runDir, resolvedOutputFile);
    await loadFailureAttribution(resolvedOutputFile);
    await loadEvidenceContract(resolvedOutputFile, auditRecord);
    $("runArtifactList").innerHTML = [
      ["结果文件", record.output_file || outputFile || "", artifactStatus.output_file],
      ["结果目录", record.run_dir || runDir || "", artifactStatus.run_dir],
      ["日志", record.log_file || "", artifactStatus.log_file],
      ["任务清单", record.manifest_file || "", artifactStatus.manifest_file],
      ["配置快照", runDir ? `${runDir}/config_snapshot.json` : "", artifactStatus.config_snapshot],
      ["运行摘要", artifactStatus.summary?.path || "", artifactStatus.summary],
      ["LongMemEval 官方摘要", artifactStatus.longmemeval_official_summary?.path || "", artifactStatus.longmemeval_official_summary],
      ["HotpotQA 回答摘要", artifactStatus.hotpotqa_answer_summary?.path || "", artifactStatus.hotpotqa_answer_summary],
      ["Markdown 报告", runDir ? `${runDir}/report.md` : "", artifactStatus.report],
      ["报告文件", runDir ? `${runDir}/report.html` : "", artifactStatus.report_html],
    ].filter(([, value, info]) => value && (info?.exists ?? true)).map(([label, value]) => `
      <article class="path-row">
        <span>${escapeHtml(label)}</span>
        <code>${escapeHtml(value)}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(value)}">复制</button>
      </article>
    `).join("") || "<p>这个结果没有可显示文件。</p>";
    bindCopyButtons("#runArtifactList");
    await loadConfigSnapshot(runDir).catch(() => {
      $("configSnapshotResult").innerHTML = "<p>这个结果没有配置快照。</p>";
    });
    updateJudgeAndReportActionButtons();
    updateWorkflowGuide();
  }
}

async function loadConfigSnapshot(runDir) {
  const data = await api(`/api/config-snapshot?run_dir=${encodeURIComponent(runDir)}`);
  $("configSnapshotResult").innerHTML = `
    <div class="path-row">
      <span>配置文件</span>
      <code>${escapeHtml(data.path || "")}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(data.path || "")}">复制</button>
    </div>
    <pre>${escapeHtml(JSON.stringify(data.config || data, null, 2)).slice(0, 6000)}</pre>
  `;
  bindCopyButtons("#configSnapshotResult");
}

async function exportRunReport() {
  const runDir = state.selectedRunDir;
  if (!runDir) return toast("请先选择一个历史结果");
  if ($("runReportDetails")) $("runReportDetails").hidden = false;
  const data = await api(`/api/report?run_dir=${encodeURIComponent(runDir)}`);
  state.lastReportFile = data.report_html_file || data.report_file || "";
  renderJudgeReadinessPanel();
  const text = String(data.text || "");
  const reportRows = Array.from(text.matchAll(/^- ([^:\n]+): `?([^`\n]+)`?/gm)).map((m) => [m[1], m[2]]);
  const reportField = (label) => (reportRows.find(([key]) => key === label) || [label, "-"])[1];
  const gateRows = Array.from(text.matchAll(/^- (Run completion|VikingBoat parameter alignment|VikingBot parameter alignment|Memory import integrity|Import log failures|QA coverage|Judge completion|Model final failures|QA log warnings): `([^`]+)` · ([^\n]+)/gm))
    .map((m) => ({label: m[1], status: m[2], detail: m[3]}));
  const digestRows = [
    ["门禁", reportField("Gate status")],
    ["结论", reportField("Gate verdict")],
    ["审计", reportField("Audit status")],
    ["应测题数", reportField("Dataset expected questions")],
    ["结果行", reportField("Rows")],
    ["缺失题数", reportField("Missing questions")],
    ["可重跑失败题", reportField("Retryable failed questions")],
    ["待判分", reportField("Pending Judge rows") !== "-" ? reportField("Pending Judge rows") : reportField("Pending Judge")],
    ["正式判分", reportField("Formal Judge score")],
    ["模型重试", reportField("Model/API retry warnings")],
    ["重试行数", reportField("Model retry rows")],
    ["完整样本", reportField("Complete samples")],
    ["导入日志", reportField("Import log failures")],
    ["QA 日志", reportField("QA log warnings")],
    ["提交消息", reportField("Submitted messages")],
    ["抽取记忆", reportField("Extracted memories")],
  ];
  const wrongCount = (text.match(/^### \d+\. `/gm) || []).length;
  const clusterLines = text.split("\n").filter((line) => line.startsWith("- ") && line.includes(" cases")).slice(0, 4);
  const reportHtmlHref = artifactHref(data.report_html_file || "");
  $("runReportResult").innerHTML = `
    <div class="path-row">
      <span>报告</span>
      <code>${escapeHtml(data.report_file || "")}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(data.report_file || "")}">复制</button>
    </div>
    ${data.report_html_file ? `
      <div class="path-row">
        <span>打开</span>
        <code>${escapeHtml(data.report_html_file || "")}</code>
        <button class="path-copy" type="button" data-copy="${escapeHtml(data.report_html_file || "")}">复制</button>
        ${reportHtmlHref ? `<a class="path-link" href="${escapeHtml(reportHtmlHref)}" target="_blank" rel="noreferrer">浏览器打开</a>` : ""}
        <button class="path-open" type="button" data-path="${escapeHtml(data.report_html_file || "")}">本机打开</button>
      </div>
    ` : ""}
    <div class="report-digest">
      <div class="report-digest-head">
        <strong>报告摘要</strong>
        <span>${wrongCount ? `${wrongCount} 个错题示例` : "暂无错题示例"}</span>
      </div>
      <div class="report-kv">
        ${digestRows.map(([label, value]) => `
          <article>
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
          </article>
        `).join("")}
      </div>
      ${gateRows.length ? `
        <div class="report-gate-list">
          ${gateRows.map((row) => `
            <article class="${escapeHtml(row.status)}">
              <span>${escapeHtml(row.status)}</span>
              <strong>${escapeHtml(row.label)}</strong>
              <p>${escapeHtml(row.detail)}</p>
            </article>
          `).join("")}
        </div>
      ` : ""}
      <div class="report-clusters">
        ${clusterLines.length ? clusterLines.map((line) => `<p>${escapeHtml(line.replace(/^-\\s*/, ""))}</p>`).join("") : "<p>没有错题聚类。</p>"}
      </div>
    </div>
    <details class="report-raw">
      <summary>完整报告文本</summary>
      <pre>${escapeHtml(text.slice(0, 12000))}</pre>
    </details>
  `;
  $("runReportDetails")?.setAttribute("open", "");
  $("runReportResult")?.scrollIntoView({behavior: "smooth", block: "start"});
  bindCopyButtons("#runReportResult");
  bindOpenButtons("#runReportResult");
  updateWorkflowGuide();
  toast("报告已生成");
}

async function loadLongMemBaselineComparison() {
  const path = projectPath("docs", "longmemeval_baseline_comparison_20260531.md");
  const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
  const text = String(data.text || "");
  const rows = text.split("\n").filter((line) => line.startsWith("| `"));
  const findings = text.split("\n").filter((line) => line.startsWith("- ")).slice(0, 8);
  $("longMemBaselineResult").innerHTML = `
    <div class="path-row">
      <span>Report</span>
      <code>${escapeHtml(path)}</code>
      <button class="path-copy" type="button" data-copy="${escapeHtml(path)}">复制</button>
    </div>
    <div class="report-digest">
      <div class="report-digest-head">
        <strong>LongMemEval 基线对比</strong>
        <span>truncated → full-memory → clean-answer</span>
      </div>
      <div class="baseline-table">
        ${rows.map((line) => {
          const cells = line.split("|").slice(1, -1).map((cell) => cell.trim().replace(/^`|`$/g, ""));
          return `<article><strong>${escapeHtml(cells[0] || "-")}</strong><span>${escapeHtml(cells[2] || "-")}</span><small>${escapeHtml(cells[3] || "-")} · pending ${escapeHtml(cells[4] || "-")}</small></article>`;
        }).join("")}
      </div>
      <div class="report-clusters">
        ${findings.map((line) => `<p>${escapeHtml(line.replace(/^-\s*/, ""))}</p>`).join("")}
      </div>
    </div>
    <details class="report-raw">
      <summary>查看完整 Markdown</summary>
      <pre>${escapeHtml(text)}</pre>
    </details>
  `;
  bindCopyButtons("#longMemBaselineResult");
}

async function loadLatestLongMemResults() {
  const runs = [
    {
      label: "100 题 subset aggregate",
      runDir: runPath("manual_longmemeval_subset_100"),
      csv: runPath("manual_longmemeval_subset_100", "local_agent", "local_agent_results.csv"),
      summary: "92/100 exact reference · 正式判分待全量",
    },
    {
      label: "50 题判分小样本核验",
      runDir: runPath("manual_longmemeval_acronym_judge_smoke_50"),
      csv: runPath("manual_longmemeval_acronym_judge_smoke_50", "local_agent", "local_agent_results.csv"),
      summary: "50/50 正式判分 · 100.0%",
    },
    {
      label: "20 题判分小样本核验",
      runDir: runPath("manual_longmemeval_numericalias_judge_smoke_20"),
      csv: runPath("manual_longmemeval_numericalias_judge_smoke_20", "local_agent", "local_agent_results.csv"),
      summary: "20/20 正式判分 · 100.0%",
    },
  ];
  $("longMemBaselineResult").innerHTML = `
    <div class="report-digest">
      <div class="report-digest-head">
        <strong>最新 LongMemEval 结果</strong>
        <span>100 题 + 独立判分抽样</span>
      </div>
      <div class="latest-run-grid">
        ${runs.map((run) => `
          <article class="latest-run-card" data-run-dir="${escapeHtml(run.runDir)}" data-output-file="${escapeHtml(run.csv)}">
            <span>${escapeHtml(run.label)}</span>
            <strong>${escapeHtml(run.summary)}</strong>
            <code>${escapeHtml(run.csv)}</code>
            <div class="panel-actions">
              <button class="secondary latest-run-open" type="button">打开任务</button>
              <button class="secondary path-copy" type="button" data-copy="${escapeHtml(run.csv)}">复制结果文件</button>
            </div>
          </article>
        `).join("")}
      </div>
    </div>
  `;
  document.querySelectorAll("#longMemBaselineResult .latest-run-open").forEach((button) => {
    button.addEventListener("click", (event) => {
      const card = event.currentTarget.closest(".latest-run-card");
      loadRunDetail(card.dataset.runDir || "", card.dataset.outputFile || "", "longmemeval").catch((e) => toast(e.message));
    });
  });
  bindCopyButtons("#longMemBaselineResult");
  toast("已加载最新 LongMemEval 结果");
}

async function findLatestCsvByAgent(agentType) {
  const data = await api("/api/runs?limit=160");
  const runs = data.runs || [];
  const match = runs.find((run) => {
    const type = run.agent_type || agentTypeForKind(run.kind || "");
    return type === agentType && run.output_file;
  });
  if (!match) throw new Error(`没有找到 ${agentTypeLabel(agentType)} 的结果文件`);
  return match;
}

async function useLatestRunForDiff(agentType, targetId) {
  const run = await findLatestCsvByAgent(agentType);
  $(targetId).value = run.output_file || "";
  if (targetId === "diffCandidate") {
    const format = fallbackDatasetFormatForRecord(run);
    if (format === "locomo") {
      markLocomoOutputFile(run.output_file || state.outputFile);
    } else if (format) {
      markDatasetOutputFile(run.output_file || state.outputFile, format);
    } else {
      state.outputFile = run.output_file || state.outputFile;
    }
  }
  toast(`已填入 ${agentTypeLabel(agentType)} 最新结果文件`);
  return run;
}

async function loadRunQuestions(csvPath) {
  const data = await api(`/api/csv-preview?path=${encodeURIComponent(csvPath)}&limit=240`);
  const rows = data.rows || [];
  $("runQuestionList").innerHTML = rows.map((row, index) => `
    <article class="memory-hit" data-question-id="${escapeHtml(row.question_id || "")}" data-row-index="${index}" data-csv-path="${escapeHtml(csvPath)}">
      <strong>${escapeHtml(row.result || row.simple_grade || "待判分")} · ${escapeHtml(row.sample_id || "-")} · ${escapeHtml(row.question_id || `row-${index + 1}`)} · C${escapeHtml(row.category || "")}</strong>
      <p>${escapeHtml(row.question || "-")}</p>
    </article>
  `).join("") || "<p>这个结果没有可显示的问题。</p>";
  document.querySelectorAll("#runQuestionList .memory-hit").forEach((card) => {
    card.addEventListener("click", () => openQuestionDetail(card.dataset.csvPath || "", card.dataset.questionId || "", card.dataset.rowIndex || "").catch((e) => toast(e.message)));
  });
  if (rows.length) {
    const first = rows[0] || {};
    await openQuestionDetail(csvPath, first.question_id || "", "0");
  } else {
    $("questionDetailPane").innerHTML = "<p class=\"muted-list-note\">这个结果没有可显示的问题详情。</p>";
  }
}

async function openQuestionDetail(csvPath, questionId, rowIndex) {
  const qs = new URLSearchParams({path: csvPath});
  if (questionId) qs.set("question_id", questionId);
  else qs.set("index", rowIndex);
  const data = await api(`/api/question-detail?${qs.toString()}`);
  const row = data.row || {};
  const memories = data.relevant_memory || [];
  const judgeResult = data.judge?.result || "待判分";
  const judgeClass = String(judgeResult).toUpperCase() === "CORRECT" ? "correct" : (String(judgeResult).toUpperCase() === "WRONG" ? "wrong" : "pending");
  $("questionDetailPane").innerHTML = `
    <div class="question-report">
      <header class="question-report-head">
        <div>
          <span>${escapeHtml(row.question_id || `row-${data.index + 1}`)}</span>
          <h4>${escapeHtml(row.question || "-")}</h4>
          ${locomoCategoryBadge(row.category)}
        </div>
        <strong class="judge-chip ${judgeClass}">${escapeHtml(judgeResult)}</strong>
      </header>
      <div class="answer-grid detail-answer-grid">
        <section><span>标准答案</span><p>${escapeHtml(row.answer || "-")}</p></section>
        <section><span>模型回答</span><p>${escapeHtml(row.response || "-")}</p></section>
      </div>
      <section class="judge-detail ${judgeClass}">
        <span>判分理由</span>
        <p>${escapeHtml(data.judge?.reasoning || "尚未判分。")}</p>
      </section>
      ${renderDiagnosticPanel(data, row)}
      <section class="detail-meta-grid">
        <article><span>样本</span><strong>${escapeHtml(row.sample_id || "-")}</strong></article>
        <article><span>召回证据</span><strong>${escapeHtml(memories.length)}</strong></article>
        <article><span>Token 估算</span><strong>${escapeHtml(row.injection_tokens_est || "-")}</strong></article>
      </section>
      <section class="question-detail-section">
        <h4>相关记忆</h4>
        <div class="evidence-grid">
          ${memories.length ? memories.map((item, index) => renderEvidenceCard(item, index)).join("") : "<p>没有证据字段或没有召回记忆。</p>"}
        </div>
      </section>
      <details class="context-details">
        <summary>上下文预览</summary>
        <pre>${escapeHtml(data.context || row.context_preview || "-")}</pre>
      </details>
    </div>
  `;
  bindCopyButtons("#questionDetailPane");
  bindOpenButtons("#questionDetailPane");
}

async function runDiff() {
  const base = $("diffBase")?.value.trim() || "";
  const candidate = $("diffCandidate")?.value.trim() || "";
  if (!base || !candidate) return toast("请先填写两个结果文件");
  const data = await api(`/api/run-diff?base=${encodeURIComponent(base)}&candidate=${encodeURIComponent(candidate)}`);
  const transitionRows = Object.entries(data.transitions || {}).sort((a, b) => b[1] - a[1]);
  const categoryRows = Object.entries(data.category_transitions || {}).sort((a, b) => {
    const total = (obj) => Object.values(obj || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    return total(b[1]) - total(a[1]);
  });
  revealReportAnalysisPanel("runDiffResult");
  $("runDiffResult").innerHTML = `
    <div class="result-kpis compact">
      <div class="kpi"><span>变好</span><strong>${escapeHtml(data.improved)}</strong></div>
      <div class="kpi"><span>变差</span><strong>${escapeHtml(data.regressed)}</strong></div>
      <div class="kpi"><span>变化</span><strong>${escapeHtml(data.changed)}</strong></div>
      <div class="kpi"><span>共同题</span><strong>${escapeHtml(data.shared_rows)}</strong></div>
    </div>
    <div class="diff-summary">
      <section>
        <h4>评分迁移</h4>
        ${transitionRows.length ? transitionRows.map(([name, count]) => `<p><span>${escapeHtml(name)}</span><strong>${escapeHtml(count)}</strong></p>`).join("") : "<p>没有评分迁移。</p>"}
      </section>
      <section>
        <h4>Category 变化</h4>
        ${categoryRows.length ? categoryRows.slice(0, 8).map(([category, values]) => `
          <p><span>C${escapeHtml(category)}</span><strong>${escapeHtml(Object.entries(values).map(([k, v]) => `${k}: ${v}`).join(" · "))}</strong></p>
        `).join("") : "<p>没有 category 变化。</p>"}
      </section>
    </div>
    ${(data.changes || []).slice(0, 20).map((change) => `
      <article class="diff-change">
        <strong>${escapeHtml(change.type)} · ${escapeHtml(change.key)}</strong>
        <small>${escapeHtml(change.before?.response || "-")} → ${escapeHtml(change.after?.response || "-")}</small>
      </article>
    `).join("") || "<p>没有评分变化。</p>"}
  `;
}

function revealReportAnalysisPanel(panelId) {
  const panel = $(panelId);
  const details = panel?.closest("details");
  if (details) {
    details.hidden = false;
    details.open = true;
  }
}

async function loadWrongClusters() {
  const input = $("diffCandidate")?.value.trim() || $("judgeInput")?.value.trim() || state.outputFile;
  if (!input) return toast("请先选择结果文件");
  const data = await api(`/api/wrong-clusters?path=${encodeURIComponent(input)}`);
  const analysis = data.analysis || {};
  const clusters = analysis.failure_clusters?.clusters || [];
  const attribution = analysis.failure_attribution || {};
  revealReportAnalysisPanel("wrongClusterResult");
  $("wrongClusterResult").innerHTML = `
    <div class="result-kpis compact">
      <div class="kpi"><span>错误</span><strong>${escapeHtml(analysis.wrong ?? 0)}</strong></div>
      <div class="kpi"><span>未判</span><strong>${escapeHtml(analysis.unresolved ?? 0)}</strong></div>
      <div class="kpi"><span>聚类</span><strong>${escapeHtml(clusters.length)}</strong></div>
      <div class="kpi"><span>可重跑</span><strong>${escapeHtml(attribution.retryable_rows ?? 0)}</strong></div>
    </div>
    <div class="embedded-failure-attribution">
      ${attribution.buckets?.length ? `
        <div class="failure-bucket-list compact">
          ${attribution.buckets.slice(0, 5).map((bucket) => `
            <article class="failure-bucket ${failureSeverityTone(bucket.severity)}">
              <div class="failure-bucket-head">
                <div>
                  <span>${escapeHtml(failureOwnerLabel(bucket.owner))} · ${bucket.retryable ? "可重跑" : "需分析"}</span>
                  <strong>${escapeHtml(bucket.label || bucket.mode || "Failure")} · ${escapeHtml(bucket.count ?? 0)} 题</strong>
                  <p>${escapeHtml(bucket.reason || "")}</p>
                </div>
              </div>
            </article>
          `).join("")}
        </div>
      ` : ""}
    </div>
    ${clusters.map((cluster) => `
      <article class="diff-change">
        <strong>${escapeHtml(cluster.label)} · ${escapeHtml(cluster.count)} 题</strong>
        <small>${escapeHtml((cluster.examples || []).map((ex) => `${ex.sample_id}: ${ex.question}`).join(" | "))}</small>
      </article>
    `).join("") || "<p>暂无错题聚类；可能还没判分，或没有错误样本。</p>"}
  `;
}

function currentResultCsv() {
  const candidate = ($("diffCandidate")?.value || "").trim();
  if (candidate && looksLocomoArtifact(candidate) && !looksNonLocomoArtifact(candidate)) return candidate;
  return currentLocomoResultCsv();
}

function renderQuickTestStatus(data, runnerKind) {
  const examples = (data.examples || []).slice(0, 5).map((item) => {
    const qid = item.question_id || "";
    const question = item.question || "";
    return `${qid}${question ? ` · ${question}` : ""}`;
  });
  const sampleLabel = inferSingleSampleFromQuestionIds(data.question_ids || [], data.examples || []);
  $("quickTestStatus").innerHTML = `
    <p><strong>${escapeHtml(data.count || 0)} 题</strong>${sampleLabel ? ` · ${escapeHtml(sampleLabel)}` : ""} 已加入当前测试</p>
    ${examples.length ? `<p>${escapeHtml(examples.join(" / "))}</p>` : ""}
  `;
}

async function selectQuestionIds(questionIds, sampleValue = "all") {
  const resolvedSampleValue = resolveLocomoSampleOptionValue(sampleValue, questionIds);
  if ($("sample").value !== resolvedSampleValue) {
    $("sample").value = resolvedSampleValue;
    await loadQuestions();
  } else if (!state.questions.length || !locomoQuestionsMatchScope()) {
    await loadQuestions();
  }
  state.selectedQuestions = new Set(questionIds);
  renderQuestions();
}

async function loadQuestionSet(mode, options = {}) {
  const qs = new URLSearchParams({
    mode,
    path: $("data").value.trim(),
    sample: options.sample ?? ($("sample").value || "all"),
  });
  if (options.csv) qs.set("csv", options.csv);
  return api(`/api/question-set?${qs.toString()}`);
}

async function runGeneratedQuestionSet(mode, runnerKind, options = {}) {
  const data = await loadQuestionSet(mode, options);
  const questionIds = data.question_ids || [];
  if (!questionIds.length) {
    renderQuickTestStatus(data, runnerKind);
    return toast("没有找到可测试的问题");
  }
  const inferredSampleValue = inferSingleSampleFromQuestionIds(questionIds, data.examples || []);
  const sampleValue = options.sample ?? (inferredSampleValue || "all");
  await selectQuestionIds(questionIds, sampleValue);
  renderQuickTestStatus(data, runnerKind);
  showView("evalView");
  const agentCfg = agentModelConfig();
  return startTask(runnerKind, {
    name: options.name || `locomo ${mode} quick test`,
    sample: sampleValue,
    questions: questionIds.join(","),
    answer_base_url: agentCfg.baseUrl,
    answer_model: agentCfg.model,
    answer_token: agentCfg.token,
  });
}

async function runFullMemoryQa() {
  const dataset = currentLocomoDataset();
  if (!dataset) return toast("请先读取 LoCoMo 数据集");
  const busyMessage = locomoQaLaunchPendingMessage();
  if (busyMessage) return toast(busyMessage);
  state.locomoQaLaunchSource = "full";
  state.locomoQaLaunchPending = true;
  refreshLocomoQaActionLabels();
  const qaKind = locomoQaTaskKind();
  const workspace = effectiveOpenVikingWorkspace(qaKind);
  if (workspace) {
    $("ovWorkspace").value = workspace;
    if ($("memoryWorkspace")) $("memoryWorkspace").value = workspace;
  }
  try {
    const sample = $("sample")?.value || "all";
    if (sample !== "all" && !state.questions.length) {
      await loadQuestions();
    }
    state.selectedQuestions.clear();
    renderQuestions();
    refreshLocomoQaActionLabels();
    const launchButton = $("runOpenVikingFullQa");
    if (launchButton) launchButton.disabled = true;
    const scope = currentLocomoSampleScope();
    const scopeLabel = scope.isAll ? "全部 LoCoMo" : `${scope.label} 当前 conv 全量`;
    const questionCount = scope.isAll ? Number(dataset.questions || 0) : Number(scope.questionCount || state.questions.length || 0);
    $("quickTestStatus").innerHTML = `
      <p><strong>本次运行：${escapeHtml(scopeLabel)}</strong></p>
      <p>预计题数：${escapeHtml(questionCount ? `${formatInt(questionCount)} 题` : "未知")} · 使用当前账户的 ${escapeHtml(memoryBackendLabel(currentMemoryBackend()))} 记忆空间。</p>
    `;
    showView("evalView");
    const agentCfg = agentModelConfig();
    return await startTask(qaKind, {
      sample,
      questions: "",
      ...(scope.isAll ? {full_locomo_run: true} : {}),
      name: `locomo ${scope.isAll ? "full" : scope.label} ${memoryBackendLabel(currentMemoryBackend())} QA ${questionCount || ""}`.trim(),
      answer_base_url: agentCfg.baseUrl,
      answer_model: agentCfg.model,
      answer_token: agentCfg.token,
    });
  } finally {
    state.locomoQaLaunchPending = false;
    if (state.locomoQaLaunchSource === "full") state.locomoQaLaunchSource = "";
    refreshLocomoQaActionLabels();
  }
}

async function runJudgeForCurrentResult(options = {}) {
  const input = currentLocomoResultCsv();
  if (!input) return toast("请先运行或选择 LoCoMo 结果文件");
  const validation = await preflightJudge();
  if (!validation?.ok) return toast("判分预检未通过，请先看检查结果");
  const result = await api(`/api/results?path=${encodeURIComponent(input)}`).catch(() => null);
  const summary = result?.summary || {};
  const pending = Number(options.estimatedPending ?? pendingCount(summary));
  if (pending >= 20 && !options.confirmed && state.judgeConfirmInput !== input) {
    document.querySelector(".judge-confirm")?.remove();
    renderJudgeConfirmation(input, summary, options);
    return toast(`判分涉及 ${pending} 行，请二次确认`);
  }
  state.judgeConfirmInput = "";
  const judgeCfg = judgeModelConfig();
  return startTask("judge", {
    input,
    name: options.name || "judge",
    judge_base_url: judgeCfg.baseUrl,
    judge_model: judgeCfg.model,
    judge_token: judgeCfg.token,
    ...(options.filterPayload || {}),
  });
}

async function retryFailedOpenVikingQa() {
  const input = currentLocomoResultCsv();
  if (!input) return toast("请先运行或选择 LoCoMo 结果文件");
  const running = state.currentLocomoTask;
  if (isTaskActive(running) && running.output_file === input) {
    return toast("当前问答还在写结果文件，完成后再重跑失败题");
  }
  const diagnostics = await renderQaDiagnostics(input);
  const failedRows = Number(diagnostics.retryable_failed_rows || 0);
  const failedQuestions = Number(diagnostics.retryable_failed_questions || 0);
  if (!failedQuestions) return toast("当前结果没有模型/API/检索失败题");
  const workspace = effectiveOpenVikingWorkspace("openviking_qa");
  if (!workspace) return toast("请先填写 OpenViking 记忆目录");
  const agentCfg = agentModelConfig();
  return startTask("openviking_qa_retry_failed", {
    input,
    data: $("data").value.trim(),
    workspace,
    account: $("ovAccount")?.value.trim() || "default",
    ov_user_id: $("memoryUserId")?.value.trim() || "default",
    ov_agent_id: $("memoryAgentId")?.value.trim() || "default",
    host: $("ovHost")?.value.trim() || "127.0.0.1",
    port: $("ovPort")?.value.trim() || "1933",
    top_k: 30,
    answer_base_url: agentCfg.baseUrl,
    answer_model: agentCfg.model,
    answer_token: agentCfg.token,
    name: `重跑失败问答 ${failedQuestions} 题/${failedRows} 行`,
  });
}

async function retryMissingOpenVikingQa() {
  const input = currentLocomoResultCsv();
  if (!input) return toast("请先运行或选择 LoCoMo 结果文件");
  const running = state.currentLocomoTask;
  if (isTaskActive(running) && running.output_file === input) {
    return toast("当前问答还在写结果文件，完成后再补跑缺失题");
  }
  const diagnostics = await renderQaDiagnostics(input);
  const missingIds = diagnostics.missing_question_ids || [];
  if (!missingIds.length) return toast("当前结果没有缺失题");
  const workspace = effectiveOpenVikingWorkspace("openviking_qa");
  if (!workspace) return toast("请先填写 OpenViking 记忆目录");
  $("quickTestStatus").innerHTML = `
    <p><strong>补跑缺失题</strong> · ${escapeHtml(missingIds.length)} 题</p>
    <p>${escapeHtml(missingIds.slice(0, 8).join(" / "))}${missingIds.length > 8 ? " ..." : ""}</p>
  `;
  showView("evalView");
  const agentCfg = agentModelConfig();
  return startTask("openviking_qa_retry_missing", {
    input,
    sample: "all",
    question_ids: missingIds.join(","),
    questions: missingIds.join(","),
    name: `locomo 补跑并合并缺失题 ${missingIds.length}`,
    data: $("data").value.trim(),
    workspace,
    account: $("ovAccount")?.value.trim() || "default",
    ov_user_id: $("memoryUserId")?.value.trim() || "default",
    ov_agent_id: $("memoryAgentId")?.value.trim() || "default",
    host: $("ovHost")?.value.trim() || "127.0.0.1",
    port: $("ovPort")?.value.trim() || "1933",
    top_k: 30,
    answer_base_url: agentCfg.baseUrl,
    answer_model: agentCfg.model,
    answer_token: agentCfg.token,
  });
}

async function runJudgeSmoke(limit = 3) {
  const input = currentLocomoResultCsv();
  if (!input) return toast("请先运行测试或填写 LoCoMo 结果文件");
  const preview = await api(`/api/pending-preview?path=${encodeURIComponent(input)}&limit=${encodeURIComponent(String(limit))}`);
  const indexes = (preview.rows || []).map((row) => row._row_index).filter((value) => value !== undefined && value !== "");
  if (!indexes.length) return toast("当前结果没有待判样本可抽查");
  return runJudgeForCurrentResult({
    estimatedPending: indexes.length,
    filterPayload: {only_pending: true, row_indexes: indexes.join(",")},
    name: `judge validation ${indexes.length} pending`,
  });
}

async function preflightJudge() {
  const input = currentLocomoResultCsv();
  if (!input) {
    renderJudgeReadinessPanel();
    toast("请先运行或选择 LoCoMo 结果文件");
    return {ok: false};
  }
  const payload = {
    kind: "judge",
    input,
    data: $("data").value.trim(),
    output_dir: state.config.output_dir || "",
  };
  const data = await api("/api/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.lastValidation = data;
  state.lastJudgeValidation = data;
  const judgePreflightBox = $("judgePreflightBox");
  const checksHtml = (data.checks || []).map((item) => `
    <span class="check ${item.ok ? "ok" : "bad"}">
      ${item.ok ? "通过" : "失败"} · ${escapeHtml(item.name)} · ${escapeHtml(item.message)}
    </span>
  `).join("");
  judgePreflightBox.innerHTML = checksHtml;
  judgePreflightBox.hidden = !checksHtml;
  if (input) {
    const result = await api(`/api/results?path=${encodeURIComponent(input)}`).catch(() => null);
    if (result?.summary) {
      state.lastJudgeSummary = result.summary;
      renderJudgeEstimate(result.summary);
      renderJudgeReadinessPanel(result.summary);
    } else {
      renderJudgeReadinessPanel();
    }
  }
  updateWorkflowGuide();
  toast(data.ok ? "判分预检通过" : "判分预检未通过");
  return data;
}

function bindNavButtons() {
  document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
    if (button.dataset.bound === "1") return;
    button.dataset.bound = "1";
    button.addEventListener("click", () => {
      const targetView = button.dataset.view || "";
      const options = isStandaloneBenchmarkView(targetView) ? {benchmarkStage: "import"} : {};
      options.userTriggered = true;
      showView(targetView, options);
    });
  });
}

function sidebarContractEntries(contract = state.uiContract || state.config?.ui_contract || {}) {
  return Array.isArray(contract.sidebar) ? contract.sidebar.filter((item) => item && item.view && item.label) : [];
}

function applyUiContract(contract = {}) {
  state.uiContract = contract && typeof contract === "object" ? contract : {};
  document.documentElement.dataset.uiContractVersion = String(state.uiContract.version || "");
  const entries = sidebarContractEntries(state.uiContract);
  if (!entries.length) return;
  const group = document.querySelector(".side-nav .nav-group");
  if (!group) return;
  const existing = [...group.querySelectorAll(".nav-item[data-view]")].map((button) => [button.dataset.view, button.textContent.trim()]);
  const expected = entries.map((item) => [String(item.view), String(item.label)]);
  if (JSON.stringify(existing) !== JSON.stringify(expected)) {
    const activeView = VIEW_NAV_PARENT[document.body.dataset.activeView] || document.body.dataset.activeView || "openvikingView";
    group.innerHTML = `
      <span>评测入口</span>
      ${entries.map((item) => `
        <button class="nav-item ${item.view === activeView ? "active" : ""}" data-view="${escapeHtml(item.view)}" title="${escapeHtml(item.purpose || "")}">${escapeHtml(item.label)}</button>
      `).join("")}
    `;
  } else {
    const buttons = [...group.querySelectorAll(".nav-item[data-view]")];
    entries.forEach((item) => {
      const button = buttons.find((candidate) => candidate.dataset.view === String(item.view));
      if (!button) return;
      button.textContent = String(item.label);
      if (item.purpose) button.title = String(item.purpose);
    });
  }
  bindNavButtons();
}

function bind() {
  bindNavButtons();
  document.querySelectorAll("[data-chat-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.chatTab || "memory";
      document.querySelectorAll("[data-chat-tab]").forEach((item) => {
        item.classList.toggle("active", item.dataset.chatTab === tab);
      });
      document.querySelectorAll("[data-chat-panel]").forEach((panel) => {
        panel.classList.toggle("active", panel.dataset.chatPanel === tab);
      });
    });
  });
  document.querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => {
      const options = button.dataset.flowKey ? {benchmarkStage: button.dataset.flowKey} : {};
      options.userTriggered = true;
      showView(button.dataset.viewJump, options);
    });
  });
  document.querySelectorAll("[data-external-href]").forEach((button) => {
    button.addEventListener("click", () => {
      const href = String(button.dataset.externalHref || "").trim();
      if (!href) return;
      window.open(href, "_blank", "noopener,noreferrer");
    });
  });
  document.querySelectorAll("[data-chat-template]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = $("chatInput");
      if (!input) return;
      input.value = button.dataset.chatTemplate || "";
      input.focus();
      renderChatDebugStrip();
    });
  });
  $("accountSelect")?.addEventListener("change", () => {
    const account = $("accountSelect").value || "default";
    state.lastArchiveRecord = null;
    state.lastChatContextData = null;
    state.chatContextPreviewKey = "";
    loadAccountConfigFromBackend(account)
      .catch(() => null)
      .then(() => {
        applyAccountConfig(account);
        state.lastArchivedMessageCount = 0;
        renderArchiveStatus();
        renderChatDebugStrip();
        if (document.querySelector("#chatView.view-panel.active")) {
          loadChatDefaultContextPreview({force: true}).catch((e) => renderChatContextPlaceholder(`上下文预览失败：${e.message}`));
        }
        refreshImportedMemories().catch(() => {});
        refreshTasks().catch(() => {});
        if (document.querySelector("#runsView.view-panel.active")) refreshRuns().catch(() => {});
        toast(`已切换空间：${account}`);
      });
  });
  $("createAccount")?.addEventListener("click", handleCreateAccountClick);
  $("deleteAccount")?.addEventListener("click", () => deleteCurrentAccount().catch((e) => toast(e.message)));
  $("accountNameInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") createAccount().catch((e) => toast(e.message));
    if (event.key === "Escape") setAccountCreateExpanded(false);
  });
  document.querySelectorAll("#workflowGuide .workflow-step").forEach((step) => {
    step.addEventListener("click", () => showView(step.dataset.targetView, {workflowKey: step.dataset.workflowKey}));
  });
  $("loadDataset")?.addEventListener("click", () => loadDataset().catch((e) => toast(e.message)));
  $("sample")?.addEventListener("change", () => loadQuestions().catch((e) => toast(e.message)));
  $("questionSearch")?.addEventListener("input", renderQuestions);
  $("questionCategory")?.addEventListener("change", renderQuestions);
  $("selectAllQuestions")?.addEventListener("click", () => {
    (state.filteredQuestions.length ? state.filteredQuestions : filteredQuestions()).forEach((q) => state.selectedQuestions.add(q.question_id));
    renderQuestions();
  });
  $("clearQuestions")?.addEventListener("click", () => {
    state.selectedQuestions.clear();
    renderQuestions();
  });
  $("probeOpenViking")?.addEventListener("click", () => probeOpenViking().catch((e) => toast(e.message)));
  $("importSample")?.addEventListener("change", () => {
    renderImportPaths();
    renderImportReadinessPanel();
    updateBackendUi();
    mirrorImportSampleToQa({allowAll: true}).catch((e) => toast(e.message));
    refreshImportedMemories().catch(() => {});
  });
  $("ovWorkspace")?.addEventListener("input", () => {
    renderImportPaths();
    persistCurrentAccountConfig();
  });
  $("ovWorkspace")?.addEventListener("change", () => {
    if ($("memoryWorkspace")) $("memoryWorkspace").value = $("ovWorkspace").value;
    persistCurrentAccountConfig();
  });
  $("generateImportWorkspace")?.addEventListener("click", () => {
    generateWorkspaceForCurrentAccount();
  });
  $("ovAccount")?.addEventListener("input", () => {
    const account = $("ovAccount").value.trim() || "default";
    saveAccountList([...readAccountList(), account]);
    renderAccountSelect(account);
    renderImportPaths();
    if ($("memoryAccount")) $("memoryAccount").value = $("ovAccount").value;
    persistCurrentAccountConfig();
    refreshImportedMemories().catch(() => {});
  });
  ["ovHost", "ovPort", "judgeBaseUrl", "judgeModel", "chatTopK", "systemAgentBaseUrl", "systemAgentModel", "systemJudgeBaseUrl", "systemJudgeModel", "systemMemoryBaseUrl", "systemMemoryModel", "echomemRoot", "memoryUserId", "memoryAgentId"].forEach((id) => {
    $(id)?.addEventListener("change", persistCurrentAccountConfig);
    $(id)?.addEventListener("input", () => {
      if (["judgeBaseUrl", "judgeModel", "systemAgentBaseUrl", "systemAgentModel", "systemJudgeBaseUrl", "systemJudgeModel"].includes(id)) {
        invalidateHotpotQaModelReadiness();
      }
      syncSystemModelFieldsToLegacy();
      saveAccountConfig(currentAccount(), currentAccountConfigPatch());
      updateSystemConfigSummary();
      renderQaReadinessPanel();
      renderJudgeReadinessPanel();
      renderImportReadinessPanel();
      if (id === "chatTopK") renderChatDebugStrip();
    });
  });
  ["judgeToken", "systemAgentToken", "systemJudgeToken", "systemMemoryToken"].forEach((id) => {
    $(id)?.addEventListener("input", () => {
      invalidateHotpotQaModelReadiness();
      syncSystemModelFieldsToLegacy();
      saveAccountConfig(currentAccount(), currentAccountConfigPatch());
      updateSystemConfigSummary();
      renderQaReadinessPanel();
      renderJudgeReadinessPanel();
      renderImportReadinessPanel();
    });
    $(id)?.addEventListener("change", () => {
      invalidateHotpotQaModelReadiness();
      syncSystemModelFieldsToLegacy();
      persistCurrentAccountConfig();
      renderQaReadinessPanel();
      renderJudgeReadinessPanel();
      renderImportReadinessPanel();
    });
  });
  $("ovApiKey")?.addEventListener("input", () => {
    saveAccountConfig(currentAccount(), currentAccountConfigPatch());
    updateSystemConfigSummary();
    renderQaReadinessPanel();
    renderImportReadinessPanel();
  });
  $("ovApiKey")?.addEventListener("change", () => {
    persistCurrentAccountConfig();
    renderQaReadinessPanel();
    renderImportReadinessPanel();
  });
  $("judgeToken")?.addEventListener("change", renderJudgeReadinessPanel);
  $("judgeInput")?.addEventListener("input", () => {
    state.lastJudgeSummary = null;
    state.lastJudgeValidation = null;
    renderJudgeReadinessPanel();
  });
  $("judgeInput")?.addEventListener("change", () => {
    state.lastJudgeSummary = null;
    state.lastJudgeValidation = null;
    renderJudgeReadinessPanel();
  });
  $("memoryBackendSelect")?.addEventListener("change", () => {
    maybeRegenerateWorkspaceForBackend(currentAccount(), currentMemoryBackend());
    persistCurrentAccountConfig();
    renderImportPaths();
    updateWorkspaceMode();
    updateBackendUi();
    refreshTasks().catch(() => {});
    toast(`当前账户已切换到 ${memoryBackendLabel(currentMemoryBackend())}`);
  });
  $("runSystemPreflight")?.addEventListener("click", () => runSystemPreflight().catch((e) => toast(e.message)));
  $("hotpotQaCheckModels")?.addEventListener("click", () => refreshHotpotQaModelReadiness(true).catch((e) => toast(e.message)));
  $("testAgentModel")?.addEventListener("click", () => testSystemModel("agent").catch((e) => {
    setModelPreflightStatus("agent", `失败 · ${e.message}`, "bad");
    toast(e.message);
  }));
  $("testJudgeModel")?.addEventListener("click", () => testSystemModel("judge").catch((e) => {
    setModelPreflightStatus("judge", `失败 · ${e.message}`, "bad");
    toast(e.message);
  }));
  $("testMemoryModel")?.addEventListener("click", () => testSystemModel("memory").catch((e) => {
    setModelPreflightStatus("memory", `失败 · ${e.message}`, "bad");
    toast(e.message);
  }));
  $("runAdapterDoctor")?.addEventListener("click", () => runAdapterDoctor(["adapterDoctorPanel", "adapterDoctorReadmePanel"]).catch((e) => toast(e.message)));
	  $("runAdapterDoctorReadme")?.addEventListener("click", () => runAdapterDoctor(["adapterDoctorPanel", "adapterDoctorReadmePanel"]).catch((e) => toast(e.message)));
	  $("runHandoffAudit")?.addEventListener("click", () => runHandoffAudit(["handoffAuditPanel", "handoffAuditReadmePanel"]).catch((e) => toast(e.message)));
	  $("runHandoffAuditReadme")?.addEventListener("click", () => runHandoffAudit(["handoffAuditPanel", "handoffAuditReadmePanel"]).catch((e) => toast(e.message)));
	  $("runHandoffDashboard")?.addEventListener("click", () => runHandoffDashboard(["handoffDashboardPanel", "handoffDashboardReadmePanel"]).catch((e) => toast(e.message)));
	  $("runHandoffDashboardReadme")?.addEventListener("click", () => runHandoffDashboard(["handoffDashboardPanel", "handoffDashboardReadmePanel"]).catch((e) => toast(e.message)));
	  $("runDeliveryBoundary")?.addEventListener("click", () => runDeliveryBoundaryGate(["deliveryBoundaryPanel", "deliveryBoundaryReadmePanel"]).catch((e) => toast(e.message)));
	  $("runDeliveryBoundaryReadme")?.addEventListener("click", () => runDeliveryBoundaryGate(["deliveryBoundaryPanel", "deliveryBoundaryReadmePanel"]).catch((e) => toast(e.message)));
	  $("runAgentAlignment")?.addEventListener("click", () => runAgentAlignment(["agentAlignmentPanel", "agentAlignmentReadmePanel", "agentAlignmentWorkbenchPanel"]).catch((e) => toast(e.message)));
  $("runAgentAlignmentReadme")?.addEventListener("click", () => runAgentAlignment(["agentAlignmentPanel", "agentAlignmentReadmePanel", "agentAlignmentWorkbenchPanel"]).catch((e) => toast(e.message)));
  $("runAgentAlignmentWorkbench")?.addEventListener("click", () => runAgentAlignment(["agentAlignmentPanel", "agentAlignmentReadmePanel", "agentAlignmentWorkbenchPanel"]).catch((e) => toast(e.message)));
  $("runAccountIsolation")?.addEventListener("click", () => runAccountIsolation(["accountIsolationGatePanel", "accountIsolationReadmePanel"]).catch((e) => toast(e.message)));
  $("runAccountIsolationReadme")?.addEventListener("click", () => runAccountIsolation(["accountIsolationGatePanel", "accountIsolationReadmePanel"]).catch((e) => toast(e.message)));
  $("runGithubLaunchKitReadme")?.addEventListener("click", () => runGithubLaunchKit(["githubLaunchKitReadmePanel"]).catch((e) => toast(e.message)));
  $("runReadiness")?.addEventListener("click", () => runReadiness(["readinessPanel", "readinessReadmePanel"]).catch((e) => toast(e.message)));
  $("runReadinessReadme")?.addEventListener("click", () => runReadiness(["readinessPanel", "readinessReadmePanel"]).catch((e) => toast(e.message)));
  $("runAcceptanceMatrix")?.addEventListener("click", () => runAcceptanceMatrix(["acceptanceMatrixPanel", "acceptanceMatrixReadmePanel"]).catch((e) => toast(e.message)));
  $("runAcceptanceMatrixReadme")?.addEventListener("click", () => runAcceptanceMatrix(["acceptanceMatrixPanel", "acceptanceMatrixReadmePanel"]).catch((e) => toast(e.message)));
  $("runSmokePlan")?.addEventListener("click", () => runSmokePlan(["smokePlanPanel", "smokePlanReadmePanel"]).catch((e) => toast(e.message)));
  $("runSmokePlanReadme")?.addEventListener("click", () => runSmokePlan(["smokePlanPanel", "smokePlanReadmePanel"]).catch((e) => toast(e.message)));
  $("runHandoffPackage")?.addEventListener("click", () => runHandoffPackage(["handoffPackagePanel", "handoffPackageReadmePanel"]).catch((e) => toast(e.message)));
  $("runHandoffPackageReadme")?.addEventListener("click", () => runHandoffPackage(["handoffPackagePanel", "handoffPackageReadmePanel"]).catch((e) => toast(e.message)));
  $("runSetupPack")?.addEventListener("click", () => runSetupPack(["setupPackPanel", "setupPackReadmePanel"]).catch((e) => toast(e.message)));
  $("runSetupPackReadme")?.addEventListener("click", () => runSetupPack(["setupPackPanel", "setupPackReadmePanel"]).catch((e) => toast(e.message)));
  $("runEchoMemContract")?.addEventListener("click", () => runEchoMemContract(["echomemContractPanel", "echomemContractReadmePanel"]).catch((e) => toast(e.message)));
  $("runEchoMemContractReadme")?.addEventListener("click", () => runEchoMemContract(["echomemContractPanel", "echomemContractReadmePanel"]).catch((e) => toast(e.message)));
  $("refreshImportedMemories")?.addEventListener("click", () => refreshImportedMemories().catch((e) => toast(e.message)));
  $("commitImport")?.addEventListener("click", () => runWithUiActionLock(
    "locomoImportLaunch",
    ["commitImport"],
    () => startTask(locomoImportTaskKind()).catch((e) => toast(e.message)),
    "导入任务正在启动，请勿重复点击",
  ));
  $("checkImportIntegrity")?.addEventListener("click", () => checkImportIntegrity().catch((e) => toast(e.message)));
  $("newCleanAccount")?.addEventListener("click", () => newCleanAccount().catch((e) => toast(e.message)));
  $("toggleContextPanel")?.addEventListener("click", toggleContextPanel);
  $("sendChat")?.addEventListener("click", () => sendChat().catch((e) => toast(e.message)));
  $("archiveChat")?.addEventListener("click", () => archiveChat("manual_button").catch((e) => toast(e.message)));
  $("clearChat")?.addEventListener("click", () => {
    state.chatMessages = [];
    state.lastArchivedMessageCount = 0;
    state.lastArchiveRecord = null;
    state.lastChatContextData = null;
    state.chatContextPreviewKey = "";
    clearChatDraft(currentAccount());
    $("chatMeta").innerHTML = "";
    $("chatTokenKpis").innerHTML = "";
    renderChat();
    renderChatDebugStrip();
    loadChatDefaultContextPreview({force: true}).catch((e) => renderChatContextPlaceholder(`上下文预览失败：${e.message}`));
  });
  $("previewContext")?.addEventListener("click", () => previewContext().catch((e) => toast(e.message)));
  $("chatInput")?.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    e.preventDefault();
    sendChat().catch((err) => toast(err.message));
  });
  $("runEval")?.addEventListener("click", () => startTask("local_agent").catch((e) => toast(e.message)));
  $("runOpenVikingQa")?.addEventListener("click", () => runWithUiActionLock(
    "locomoQaLaunch",
    ["runOpenVikingQa", "runOpenVikingFullQa"],
    () => runSelectedMemoryQa().catch((e) => toast(e.message)),
    "问答任务正在启动，请勿重复点击",
  ));
  $("runOpenVikingFullQa")?.addEventListener("click", () => runWithUiActionLock(
    "locomoQaLaunch",
    ["runOpenVikingQa", "runOpenVikingFullQa"],
    () => runFullMemoryQa().catch((e) => toast(e.message)),
    "问答任务正在启动，请勿重复点击",
  ));
  $("loadLargeQuestionPage")?.addEventListener("click", () => loadLargeQuestionPage(0).catch((e) => toast(e.message)));
  $("longMemRefreshDatasets")?.addEventListener("click", () => loadDatasetRegistry().catch((e) => toast(e.message)));
  $("longMemValidate")?.addEventListener("click", () => validateLongMemDataset().catch((e) => toast(e.message)));
  $("longMemInjectMemory")?.addEventListener("click", () => injectLongMemMemory().catch((e) => toast(e.message)));
  $("longMemLoadPreview")?.addEventListener("click", () => loadLongMemQuestionPreview().catch((e) => toast(e.message)));
  $("longMemQuestionSearch")?.addEventListener("input", renderLongMemQuestionSelection);
  $("longMemSelectVisible")?.addEventListener("click", selectVisibleLongMemQuestions);
  $("longMemClearQuestions")?.addEventListener("click", clearLongMemQuestionSelection);
  $("longMemSample")?.addEventListener("change", () => {
    state.selectedLongMemQuestions.clear();
    loadLongMemQuestionPreview().catch(() => renderLongMemQuestionSelection());
  });
  $("longMemRunSmoke")?.addEventListener("click", () => runLongMemDiagnostic().catch((e) => toast(e.message)));
  $("longMemLatestResults")?.addEventListener("click", () => {
    showView("runsView");
    loadLatestLongMemResults().catch((e) => toast(e.message));
  });
  $("longMemLatestResultsJudge")?.addEventListener("click", () => {
    showView("runsView", {benchmarkStage: "report"});
    loadLatestLongMemResults().catch((e) => toast(e.message));
  });
  document.querySelectorAll(".generic-load-example").forEach((button) => {
    button.addEventListener("click", () => loadGenericExample(button.dataset.benchmark).catch((e) => toast(e.message)));
  });
  document.querySelectorAll(".generic-validate").forEach((button) => {
    button.addEventListener("click", () => validateGenericBenchmark(button.dataset.benchmark).catch((e) => toast(e.message)));
  });
  document.querySelectorAll(".generic-preview").forEach((button) => {
    button.addEventListener("click", () => previewGenericBenchmark(button.dataset.benchmark).catch((e) => toast(e.message)));
  });
  document.querySelectorAll(".generic-select-visible").forEach((button) => {
    button.addEventListener("click", () => selectVisibleBenchmarkQuestions(button.dataset.benchmark));
  });
  document.querySelectorAll(".generic-clear-selection").forEach((button) => {
    button.addEventListener("click", () => clearBenchmarkQuestionSelection(button.dataset.benchmark));
  });
  document.querySelectorAll(".generic-use-full-count").forEach((button) => {
    button.addEventListener("click", () => useFullBenchmarkCount(button.dataset.benchmark));
  });
  document.querySelectorAll(".generic-use-formal-preset").forEach((button) => {
    button.addEventListener("click", () => useFormalBenchmarkPreset(button.dataset.benchmark, {
      countValue: button.dataset.count || "0",
    }));
  });
  document.querySelectorAll(".generic-question-search").forEach((input) => {
    input.addEventListener("input", () => renderBenchmarkQuestionSelection(input.dataset.benchmark));
  });
  document.querySelectorAll(".generic-run-adapter").forEach((button) => {
    button.addEventListener("click", () => runGenericBenchmark(button.dataset.benchmark).catch((e) => toast(e.message)));
  });
  $("runPreviousWrong")?.addEventListener("click", () => runWithUiActionLock(
    "locomoQaLaunch",
    ["runOpenVikingQa", "runOpenVikingFullQa", "runPreviousWrong", "runTimeQuestions", "retryMissingQa", "retryFailedQa"],
    () => {
      const csv = currentResultCsv();
      if (!csv) return toast("请先选择或运行一个结果文件");
      return runGeneratedQuestionSet("wrong_csv", locomoQaTaskKind(), {csv, name: "locomo 上轮错题重跑"}).catch((e) => toast(e.message));
    },
    "问答任务正在启动，请勿重复点击",
  ));
  $("runTimeQuestions")?.addEventListener("click", () => runWithUiActionLock(
    "locomoQaLaunch",
    ["runOpenVikingQa", "runOpenVikingFullQa", "runPreviousWrong", "runTimeQuestions", "retryMissingQa", "retryFailedQa"],
    () => runGeneratedQuestionSet("time", locomoQaTaskKind(), {
      sample: $("sample").value || "all",
      name: "locomo 时间题问答",
    }).catch((e) => toast(e.message)),
    "问答任务正在启动，请勿重复点击",
  ));
  $("refreshMemoryBrowser")?.addEventListener("click", () => refreshMemoryBrowser().catch((e) => toast(e.message)));
  $("memoryQuery")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") refreshMemoryBrowser().catch((err) => toast(err.message));
  });
  $("refreshRuns")?.addEventListener("click", () => refreshRuns().catch((e) => toast(e.message)));
  $("refreshRunsMini")?.addEventListener("click", () => refreshRuns().catch((e) => toast(e.message)));
  $("taskCurrentAccountOnly")?.addEventListener("change", () => refreshTasks().catch((e) => toast(e.message)));
  $("refreshRecentLocomoRuns")?.addEventListener("click", () => renderRecentLocomoRuns({force: true, loading: true}).catch((e) => toast(e.message)));
  $("runsCurrentAccountOnly")?.addEventListener("change", () => refreshRuns().catch((e) => toast(e.message)));
  $("useLatestLocalAsBase")?.addEventListener("click", () => useLatestRunForDiff("local_reference_agent", "diffBase").catch((e) => toast(e.message)));
  $("copyLocalToCandidate")?.addEventListener("click", () => {
    const diffBase = $("diffBase");
    const diffCandidate = $("diffCandidate");
    if (!diffBase || !diffCandidate) return toast("当前页面没有结果对比输入框");
    diffCandidate.value = diffBase.value.trim();
    toast("已复制为当前结果");
  });
  $("compareCsvRuns")?.addEventListener("click", () => runDiff().catch((e) => toast(e.message)));
  $("runDiff")?.addEventListener("click", () => runDiff().catch((e) => toast(e.message)));
  $("loadLongMemBaseline")?.addEventListener("click", () => loadLongMemBaselineComparison().catch((e) => toast(e.message)));
  $("loadLongMemLatest")?.addEventListener("click", () => loadLatestLongMemResults().catch((e) => toast(e.message)));
  $("loadWrongClusters")?.addEventListener("click", () => loadWrongClusters().catch((e) => toast(e.message)));
  $("exportRunReport")?.addEventListener("click", () => exportRunReport().catch((e) => toast(e.message)));
  $("compareSelectedRuns")?.addEventListener("click", () => compareSelectedRuns().catch((e) => toast(e.message)));
  $("clearSelectedRuns")?.addEventListener("click", clearSelectedRuns);
  $("refreshNativeBaseline")?.addEventListener("click", () => refreshNativeOpenVikingBaseline().catch((e) => toast(e.message)));
  $("pinSelectedNativeBaseline")?.addEventListener("click", () => pinSelectedNativeOpenVikingBaseline().catch((e) => toast(e.message)));
  $("autoPinNativeBaseline")?.addEventListener("click", () => autoPinNativeOpenVikingBaseline().catch((e) => toast(e.message)));
  $("compareWithNativeBaseline")?.addEventListener("click", () => compareRunsWithNativeBaseline().catch((e) => toast(e.message)));
  $("preflightJudge")?.addEventListener("click", () => preflightJudge().catch((e) => toast(e.message)));
  $("retryMissingQa")?.addEventListener("click", () => runWithUiActionLock(
    "locomoQaLaunch",
    ["runOpenVikingQa", "runOpenVikingFullQa", "runPreviousWrong", "runTimeQuestions", "retryMissingQa", "retryFailedQa"],
    () => retryMissingOpenVikingQa().catch((e) => toast(e.message)),
    "问答任务正在启动，请勿重复点击",
  ));
  $("retryFailedQa")?.addEventListener("click", () => runWithUiActionLock(
    "locomoQaLaunch",
    ["runOpenVikingQa", "runOpenVikingFullQa", "runPreviousWrong", "runTimeQuestions", "retryMissingQa", "retryFailedQa"],
    () => retryFailedOpenVikingQa().catch((e) => toast(e.message)),
    "问答任务正在启动，请勿重复点击",
  ));
  $("runJudgeInline")?.addEventListener("click", () => runWithUiActionLock(
    "locomoJudgeLaunch",
    ["runJudgeInline"],
    () => runJudgeForCurrentResult().catch((e) => toast(e.message)),
    "判分任务正在启动，请勿重复点击",
  ));
  $("refreshResult")?.addEventListener("click", () => refreshLocomoResultAction("refreshResult"));
  $("refreshJudgeResult")?.addEventListener("click", () => refreshLocomoResultAction("refreshJudgeResult"));
  $("refreshTasks")?.addEventListener("click", () => refreshTasks().catch((e) => toast(e.message)));
  $("stopAllTasksImport")?.addEventListener("click", () => stopAllTasks().catch((e) => toast(e.message)));
  $("stopAllTasksEval")?.addEventListener("click", () => stopAllTasks().catch((e) => toast(e.message)));
  $("stopAllTasksJudge")?.addEventListener("click", () => stopAllTasks().catch((e) => toast(e.message)));
}

bind();
normalizeLegacyLabels();
const bootView = initialViewFromUrl();
if (bootView) showView(bootView, {preserveScroll: true});
updateWorkspaceMode();
renderArchiveStatus();
syncContextPanelDefaultForViewport();
startTaskUiTimers();
loadConfig().catch((e) => {
  setConnection(false, "初始化失败");
  toast(e.message);
});
