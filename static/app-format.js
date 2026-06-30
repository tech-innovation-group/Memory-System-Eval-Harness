(function (global) {
  if (!global.MemoryBenchAppState || !global.MemoryBenchConfig || !global.MemoryBenchCore) {
    throw new Error("MemoryBench app-state.js and app-core.js must load before app-format.js");
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[<>&"]/g, (c) => ({
      "<": "&lt;",
      ">": "&gt;",
      "&": "&amp;",
      '"': "&quot;",
    }[c]));
  }

  function percent(n) {
    return n == null || Number.isNaN(n) ? "待判分" : `${Math.round(n * 1000) / 10}%`;
  }

  function formatInt(value) {
    if (value == null || value === "" || Number.isNaN(Number(value))) return "-";
    return Number(value).toLocaleString();
  }

  function normalizeDisplayDate(value) {
    if (value == null) return null;
    if (value instanceof Date) {
      return Number.isNaN(value.getTime()) || value.getTime() <= 0 ? null : value;
    }
    const raw = String(value).trim();
    if (!raw || raw === "0") return null;
    const numeric = Number(raw);
    if (Number.isFinite(numeric)) {
      if (numeric <= 0) return null;
      const ms = numeric < 1e12 ? numeric * 1000 : numeric;
      const numericDate = new Date(ms);
      if (Number.isNaN(numericDate.getTime()) || numericDate.getTime() <= 0) return null;
      return numericDate;
    }
    const date = new Date(value);
    if (!(date instanceof Date) || Number.isNaN(date.getTime()) || date.getTime() <= 0) return null;
    return date;
  }

  function formatDateTimeLocal(value) {
    const date = normalizeDisplayDate(value);
    if (!date) return "-";
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
    const date = normalizeDisplayDate(value);
    if (!date) return "-";
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

  function compactTimestamp(value) {
    const date = normalizeDisplayDate(value);
    if (!date) return "-";
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

  function compactText(value, limit = 120) {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    const max = Math.max(8, Number(limit) || 120);
    if (!text || text.length <= max) return text;
    return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
  }

  function normalizeVisibleMemoryBackendName(value) {
    return String(value ?? "")
      .replace(/EchoMem\s*\/\s*EchoMemory/g, "EchoMemory")
      .replace(/EchoMem\/EchoMemory/g, "EchoMemory")
      .replace(/\bEchoMem\b/g, "EchoMemory");
  }

  function runCompareKey(run = {}) {
    return String(run.run_dir || run.id || run.name || run.output_file || "");
  }

  global.MemoryBenchFormat = Object.assign(global.MemoryBenchFormat || {}, {
    escapeHtml,
    percent,
    formatInt,
    normalizeDisplayDate,
    formatDateTimeLocal,
    formatDateTime,
    compactTimestamp,
    compactText,
    formatDuration,
    formatSecondsMetric,
    normalizeVisibleMemoryBackendName,
    runCompareKey,
  });
})(window);
