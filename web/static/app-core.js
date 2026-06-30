(function (global) {
  if (!global.MemoryBenchAppState || !global.MemoryBenchConfig || !global.MemoryBenchBootstrap?.$) {
    throw new Error("MemoryBench app-state.js must load before app-core.js");
  }

  const $ = global.MemoryBenchBootstrap.$;
  const state = global.MemoryBenchAppState;
  const { UI_ACTION_LOCKS } = global.MemoryBenchConfig;

  function isTaskRunningStatus(task = {}) {
    const status = String(task?.status || "").toLowerCase();
    return status === "queued" || status === "running";
  }

  function taskManifestStatus(task = {}) {
    return String(task?.manifest_status || task?.manifestStatus || "").toLowerCase();
  }

  function isManifestRunningTask(task = {}) {
    return taskManifestStatus(task) === "running";
  }

  function isImportTaskInBackground(task = {}) {
    return isMemoryImportKind(task?.kind || "") && (isTaskActive(task) || isManifestRunningTask(task));
  }

  function activeLocomoQaTask() {
    const current = state.currentLocomoTask;
    if (current && isTaskActive(current)) return current;
    const running = state.currentRunningTask;
    if (running && isMemoryQaKind(running.kind || "") && isTaskActive(running)) return running;
    return null;
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
    const root = normalizeSlashes(state.config?.repo || state.config?.root || "");
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

  function readStoredBool(key, fallback = false) {
    try {
      const value = window.localStorage.getItem(key);
      if (value === "true") return true;
      if (value === "false") return false;
    } catch {}
    return fallback;
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
  const IMPORT_SINGLE_SESSION_SUFFIX = "__single_session_test";

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

  function locomoSampleScopeFromTask(task = null) {
    if (!task) return currentLocomoSampleScope();
    const sampleValue = taskSampleFilter(task) || "all";
    const dataset = currentLocomoDataset();
    if (sampleValue === "all") {
      return {
        value: "all",
        isAll: true,
        label: LOCOMO_ALL_SESSIONS_LABEL,
        optionText: LOCOMO_ALL_SESSIONS_LABEL,
        questionCount: Number(dataset?.questions || 0),
      };
    }
    const sampleRows = Array.isArray(dataset?.sample_rows) ? dataset.sample_rows : [];
    const matchedRow = sampleRows.find((row) => String(row.index) === sampleValue || String(row.sample_id || "") === sampleValue);
    const label = String(matchedRow?.sample_id || sampleValue).trim() || sampleValue;
    return {
      value: sampleValue,
      isAll: false,
      label,
      optionText: matchedRow ? `${matchedRow.index} · ${label} · ${matchedRow.questions || 0} 题` : label,
      questionCount: Number(matchedRow?.questions || 0),
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
    const importBusy = isImportTaskInBackground(state.currentImportTask || {});
    const imported = currentImportedMemoryStatus();
    const existingImportInWorkspace = !importBusy && Number(imported.summary_count || 0) > 0;
    const sampleName = selection.sampleId || selection.baseValue || "当前 conv";
    const commitButton = $("commitImport");
    if (commitButton) {
      commitButton.disabled = !locomoReady || importBusy || existingImportInWorkspace;
      commitButton.textContent = selection.smoke ? "运行单 session 验证" : "开始导入";
      commitButton.title = existingImportInWorkspace
        ? "请先点“自动生成目录”，或手动切换到新的记忆目录。"
        : importBusy
        ? "导入任务运行中，请稍候"
        : (
          selection.smoke
            ? `只向 ${backendLabel} 写入 ${sampleName} 的 1 段 session，用于快速验证注入链路`
            : `把当前选择的对话写入 ${backendLabel}`
      );
    }
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
    const generatedReportsDir = root ? `${root}/generated-reports` : "";
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
    if (generatedReportsDir) {
      if (value === generatedReportsDir) return "/generated-reports/";
      if (value.startsWith(`${generatedReportsDir}/`)) {
        return `/generated-reports/${value.slice(generatedReportsDir.length + 1).split("/").map(encodeURIComponent).join("/")}`;
      }
    }
    const reportsMarker = "/generated-reports/";
    const reportsIndex = value.indexOf(reportsMarker);
    if (reportsIndex >= 0) return `/generated-reports/${value.slice(reportsIndex + reportsMarker.length).split("/").map(encodeURIComponent).join("/")}`;
    return "";
  }

  function readLastImport() {
    return readScopedLastImport(currentAccount());
  }

  function readScopedLastImport(account = currentAccount()) {
    const normalizedAccount = safeAccountSlug(account);
    try {
      const scopedKey = `${LAST_IMPORT_KEY}.${normalizedAccount}`;
      const scoped = JSON.parse(window.localStorage.getItem(scopedKey) || "{}");
      if (scoped && Object.keys(scoped).length) return scoped;
      if (normalizedAccount === "default") return JSON.parse(window.localStorage.getItem(LAST_IMPORT_KEY) || "{}");
      return {};
    } catch {
      return {};
    }
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
    if (/^echomem(?:_workspace)?_/i.test(name)) return "echomemory";
    if (/^openviking_workspace_/i.test(name)) return "openviking";
    return "";
  }

  function compactPath(value = "", head = 34, tail = 34) {
    const text = String(value || "").trim();
    if (!text) return "-";
    if (text.length <= head + tail + 3) return text;
    return `${text.slice(0, head)}...${text.slice(-tail)}`;
  }

  function displayPath(value = "") {
    const text = String(value || "").trim();
    if (!text) return "";
    const normalized = normalizeSlashes(text);
    const repoRoot = normalizeSlashes(state.config?.repo || state.config?.root || "");
    const runsDir = normalizeSlashes(state.config?.runs_dir || state.config?.output_dir || "");
    const homeDir = normalizeSlashes(state.config?.home || "");
    if (repoRoot && normalized === repoRoot) return ".";
    if (repoRoot && normalized.startsWith(`${repoRoot}/`)) return `./${normalized.slice(repoRoot.length + 1)}`;
    if (runsDir && normalized === runsDir) return "./runs";
    if (runsDir && normalized.startsWith(`${runsDir}/`)) return `./runs/${normalized.slice(runsDir.length + 1)}`;
    if (homeDir && normalized.startsWith(`${homeDir}/`)) return `./${normalized.slice(homeDir.length + 1)}`;
    if (normalized.startsWith("/Users/chx/")) return `./${normalized.slice("/Users/chx/".length)}`;
    return text;
  }

  function shellQuote(value = "") {
    const text = String(value || "");
    return `'${text.replace(/'/g, "'\"'\"'")}'`;
  }

  function currentMemoryBackend() {
    return normalizeMemoryBackend($("memoryBackendSelect")?.value || readAccountConfig(currentAccount()).memoryBackend || "openviking");
  }

  function normalizeWorkspacePath(value = "") {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  function importRecordBackend(record = {}, fallback = currentMemoryBackend()) {
    const explicit = normalizeMemoryBackend(
      record?.meta?.config?.backend
      || record?.config?.backend
      || record?.backend
      || ""
    );
    if (explicit) return explicit;
    return String(record?.kind || "").startsWith("echomemory_") ? "echomemory" : normalizeMemoryBackend(fallback);
  }

  function importRecordWorkspace(record = {}) {
    return String(
      record?.meta?.config?.workspace
      || record?.config?.workspace
      || record?.workspace
      || ""
    ).trim();
  }

  function currentWorkspaceScopedLastImport(backend = currentMemoryBackend()) {
    const lastImport = readLastImport();
    const expectedBackend = normalizeMemoryBackend(backend);
    const currentWorkspace = normalizeWorkspacePath(currentConfiguredWorkspace(expectedBackend));
    const lastBackend = normalizeMemoryBackend(lastImport.backend || expectedBackend);
    const lastWorkspace = normalizeWorkspacePath(lastImport.workspace || "");
    if (lastBackend !== expectedBackend) return {};
    if (currentWorkspace && lastWorkspace && currentWorkspace !== lastWorkspace) return {};
    return lastImport;
  }

  function importRecordMatchesCurrentWorkspace(record = {}, backend = currentMemoryBackend()) {
    const expectedBackend = normalizeMemoryBackend(backend);
    if (importRecordBackend(record, expectedBackend) !== expectedBackend) return false;
    const currentWorkspace = normalizeWorkspacePath(currentConfiguredWorkspace(expectedBackend));
    if (!currentWorkspace) return true;
    const recordWorkspace = normalizeWorkspacePath(importRecordWorkspace(record));
    return !recordWorkspace || recordWorkspace === currentWorkspace;
  }

  function clearImportedMemoryStatusForWorkspace(workspace = "", account = currentAccount()) {
    state.importedMemoryStatus = {
      workspace,
      account: safeAccountSlug(account || currentAccount()),
      sample_id: currentImportNamespace().sampleId || "",
      session_count: 0,
      summary_count: 0,
      complete_count: 0,
      latest_summary_path: "",
      latest_integrity: "",
    };
  }

  function currentImportedMemoryStatus() {
    const imported = state.importedMemoryStatus || {};
    const current = safeAccountSlug(currentAccount());
    const importedAccount = safeAccountSlug(imported.account || "");
    if (importedAccount && importedAccount !== current) return {};
    const currentWorkspace = normalizeWorkspacePath(currentConfiguredWorkspace(currentMemoryBackend()));
    const importedWorkspace = normalizeWorkspacePath(imported.workspace || "");
    if (currentWorkspace && importedWorkspace && importedWorkspace !== currentWorkspace) return {};
    return imported;
  }

  function locomoImportDisplayState(lastImport = currentWorkspaceScopedLastImport(), imported = currentImportedMemoryStatus()) {
    const importedComplete = Number(imported.complete_count || 0) > 0;
    const summaryCount = Number(imported.summary_count || 0);
    const integrity = String(lastImport.integrity || "").toLowerCase();
    const sameWorkspace = !imported.workspace || !lastImport.workspace || String(imported.workspace) === String(lastImport.workspace);
    const latestSummaryPath = String(imported.latest_summary_path || "").trim();
    const currentSummaryPath = String(lastImport.output_file || "").trim();
    const running = isImportTaskInBackground(state.currentImportTask || {});
    const summaryMatched = Boolean(currentSummaryPath && latestSummaryPath && latestSummaryPath === currentSummaryPath);
    const currentRunComplete = !running && (
      (summaryMatched && importedComplete)
      || (sameWorkspace && integrity === "complete" && (!latestSummaryPath || !currentSummaryPath || summaryMatched))
    );
    const historicalComplete = !currentRunComplete && importedComplete;
    const historicalSeen = !currentRunComplete && summaryCount > 0;
    return {
      currentRunComplete,
      historicalComplete,
      historicalSeen,
      importedComplete,
      summaryCount,
      sameWorkspace,
      latestSummaryPath,
      currentSummaryPath,
    };
  }

  function locomoImportCompleteState(lastImport = currentWorkspaceScopedLastImport(), imported = currentImportedMemoryStatus()) {
    return locomoImportDisplayState(lastImport, imported).currentRunComplete;
  }

  function setImportedMemoryRunningStatus({workspace = "", account = "", sampleId = ""} = {}) {
    state.importedMemoryStatus = {
      workspace,
      account: safeAccountSlug(account || currentAccount()),
      sample_id: sampleId || currentImportNamespace().sampleId || "",
      session_count: 0,
      summary_count: 0,
      complete_count: 0,
      latest_summary_path: "",
      latest_integrity: "",
    };
  }

  function chatDraftKey(account = currentAccount()) {
    return `${CHAT_DRAFT_PREFIX}${safeAccountSlug(account)}`;
  }

  function loadChatDraft(account = currentAccount()) {
    try {
      const raw = window.localStorage.getItem(chatDraftKey(account));
      const data = raw ? JSON.parse(raw) : [];
      return Array.isArray(data) ? data : [];
    } catch {
      return [];
    }
  }

  function saveChatDraft(account = currentAccount(), messages = state.chatMessages) {
    try {
      window.localStorage.setItem(chatDraftKey(account), JSON.stringify(messages || []));
    } catch {}
  }

  function clearChatDraft(account = currentAccount()) {
    try {
      window.localStorage.removeItem(chatDraftKey(account));
    } catch {}
  }

  global.MemoryBenchCore = Object.assign(global.MemoryBenchCore || {}, {
    isTaskRunningStatus,
    taskManifestStatus,
    isManifestRunningTask,
    isImportTaskInBackground,
    activeLocomoQaTask,
    normalizeSlashes,
    relativeDatasetPath,
    datasetPathVariants,
    datasetPathMatches,
    readStoredBool,
    preferredLocomoDatasetPath,
    uiActionLocked,
    runWithUiActionLock,
    LOCOMO_ALL_SESSIONS_LABEL,
    currentLocomoSampleScope,
    locomoSampleScopeFromTask,
    currentImportSampleScope,
    IMPORT_SINGLE_SESSION_SUFFIX,
    parseImportSampleSelection,
    locomoQaSampleOptionLabel,
    locomoImportSampleOptionLabel,
    refreshImportActionLabels,
    projectPath,
    runPath,
    artifactHref,
    readLastImport,
    readScopedLastImport,
    normalizeMemoryBackend,
    memoryBackendLabel,
    memoryBackendShortLabel,
    importTaskKindForBackend,
    importScriptForBackend,
    genericQaTaskKindForBackend,
    importWriteSurfaceForBackend,
    workspaceBackendNameHint,
    compactPath,
    displayPath,
    shellQuote,
    currentMemoryBackend,
    normalizeWorkspacePath,
    importRecordBackend,
    importRecordWorkspace,
    currentWorkspaceScopedLastImport,
    importRecordMatchesCurrentWorkspace,
    clearImportedMemoryStatusForWorkspace,
    currentImportedMemoryStatus,
    locomoImportDisplayState,
    locomoImportCompleteState,
    setImportedMemoryRunningStatus,
    chatDraftKey,
    loadChatDraft,
    saveChatDraft,
    clearChatDraft,
  });
})(window);
