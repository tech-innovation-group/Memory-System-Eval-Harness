#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { spawn } from "node:child_process";

const APP_URL = process.env.UI_AUDIT_BASE_URL || "http://127.0.0.1:19181/";
const CHROME_BIN = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const DEBUG_PORT = Number(process.env.UI_AUDIT_DEBUG_PORT || 19223);
const OUT_DIR = process.env.UI_AUDIT_OUT_DIR || path.join(process.cwd(), "tmp", "ui-audit");
const WAIT_AFTER_LOAD_MS = Number(process.env.UI_AUDIT_WAIT_MS || 1800);
const WAIT_AFTER_ACTION_MS = Number(process.env.UI_AUDIT_ACTION_WAIT_MS || 1200);

const VIEWS = [
  { id: "chatView", label: "chat" },
  { id: "openvikingView", label: "locomo_import" },
  { id: "evalView", label: "locomo_qa" },
  { id: "judgeView", label: "locomo_judge" },
  { id: "runsView", label: "locomo_runs" },
  { id: "longMemEvalView", label: "longmemeval" },
  { id: "hotpotQaView", label: "hotpotqa" },
  { id: "systemConfigView", label: "system_config" },
  { id: "evolvingEventsView", label: "evolvingevents" },
  { id: "proAgentBenchView", label: "proagentbench" },
  { id: "tauBenchView", label: "taubench" },
];

const VIEWPORTS = [
  { width: 1440, height: 900, label: "1440x900" },
  { width: 1280, height: 800, label: "1280x800" },
  { width: 1920, height: 1080, label: "1920x1080" },
];

const SAFE_ACTIONS = {
  chatView: ["#refreshWorkspaceShell"],
  openvikingView: ["#refreshWorkspaceShell"],
  evalView: ["#refreshWorkspaceShell", "#refreshTasks"],
  judgeView: ["#refreshJudgeResult"],
  runsView: ["#refreshRuns"],
  longMemEvalView: ["#longMemRefreshDatasets"],
  hotpotQaView: ["#hotpotQaCheckModels"],
  systemConfigView: [],
  evolvingEventsView: [],
  proAgentBenchView: [],
  tauBenchView: [],
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function waitForHttpJson(url, timeoutMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return await res.json();
    } catch {}
    await sleep(250);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
        return;
      }
      this.events.push(message);
    };
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = reject;
    });
  }

  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }

  async waitForEvent(match, timeoutMs = 10000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const idx = this.events.findIndex(match);
      if (idx >= 0) {
        const [event] = this.events.splice(idx, 1);
        return event;
      }
      await sleep(50);
    }
    throw new Error("Timed out waiting for CDP event");
  }

  drainEvents(predicate) {
    const taken = [];
    const kept = [];
    for (const event of this.events) {
      if (predicate(event)) taken.push(event);
      else kept.push(event);
    }
    this.events = kept;
    return taken;
  }

  async close() {
    for (const [id, pending] of this.pending) {
      pending.reject(new Error(`CDP closed before response for ${id}`));
    }
    this.pending.clear();
    this.ws.close();
    await new Promise((resolve) => {
      this.ws.onclose = resolve;
      setTimeout(resolve, 500);
    });
  }
}

function flattenRemoteValue(result) {
  if (!result) return null;
  if ("value" in result) return result.value;
  return result;
}

async function evaluate(client, sessionId, expression) {
  const result = await client.send(
    "Runtime.evaluate",
    {
      expression,
      awaitPromise: true,
      returnByValue: true,
    },
    sessionId,
  );
  return flattenRemoteValue(result.result);
}

async function clickIfVisible(client, sessionId, selector) {
  return await evaluate(
    client,
    sessionId,
    `(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return { clicked: false, reason: 'missing' };
      const cs = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      if (el.disabled) return { clicked: false, reason: 'disabled' };
      if (cs.display === 'none' || cs.visibility === 'hidden' || rect.width < 2 || rect.height < 2) {
        return { clicked: false, reason: 'hidden' };
      }
      el.click();
      return { clicked: true, reason: 'ok', text: (el.innerText || el.textContent || '').trim().slice(0, 80) };
    })()`,
  );
}

function eventText(event) {
  if (!event) return "";
  if (event.method === "Log.entryAdded") {
    const entry = event.params?.entry || {};
    return `${entry.level || "log"}: ${entry.text || ""}`.trim();
  }
  if (event.method === "Runtime.consoleAPICalled") {
    const args = (event.params?.args || []).map((arg) => {
      if (typeof arg.value !== "undefined") return String(arg.value);
      if (arg.description) return String(arg.description);
      return arg.type || "";
    });
    return `${event.params?.type || "console"}: ${args.join(" ")}`.trim();
  }
  if (event.method === "Runtime.exceptionThrown") {
    const details = event.params?.exceptionDetails;
    return `exception: ${details?.text || details?.exception?.description || ""}`.trim();
  }
  if (event.method === "Network.loadingFailed") {
    return `network: ${event.params?.errorText || "failed"} ${event.params?.type || ""}`.trim();
  }
  return event.method;
}

const PAGE_AUDIT_EXPRESSION = `(() => {
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
  };

  const textOf = (el) => {
    const raw = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    return raw.slice(0, 160);
  };

  const selectorOf = (el) => {
    if (!el) return '';
    const parts = [];
    let current = el;
    let depth = 0;
    while (current && depth < 4 && current.nodeType === 1) {
      let part = current.tagName.toLowerCase();
      if (current.id) part += '#' + current.id;
      if (current.classList && current.classList.length) {
        part += '.' + Array.from(current.classList).slice(0, 2).join('.');
      }
      parts.unshift(part);
      current = current.parentElement;
      depth += 1;
    }
    return parts.join(' > ');
  };

  const doc = document.documentElement;
  const all = Array.from(document.querySelectorAll('body *'));
  const overflowCandidates = [];
  const horizontalOffenders = [];
  const pathCandidates = [];
  const controlHeights = [];
  const buttonHeights = [];
  const scrollContainers = [];

  for (const el of all) {
    if (!visible(el)) continue;
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (rect.right > window.innerWidth + 4 || rect.left < -4) {
      horizontalOffenders.push({ selector: selectorOf(el), text: textOf(el), right: Math.round(rect.right), left: Math.round(rect.left), width: Math.round(rect.width) });
    }
    if (el.scrollWidth > el.clientWidth + 12 && el.clientWidth > 40 && rect.width > 40) {
      overflowCandidates.push({ selector: selectorOf(el), text: textOf(el), scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflowX: cs.overflowX, whiteSpace: cs.whiteSpace });
    }
    if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll' || cs.overflow === 'auto' || cs.overflow === 'scroll') && el.scrollHeight > el.clientHeight + 20 && rect.height > 60) {
      scrollContainers.push({ selector: selectorOf(el), text: textOf(el), clientHeight: Math.round(el.clientHeight), scrollHeight: Math.round(el.scrollHeight) });
    }
    if (el.matches('input, select, textarea')) {
      controlHeights.push({ selector: selectorOf(el), height: Math.round(rect.height) });
    }
    if (el.matches('button, .button, [role="button"]')) {
      buttonHeights.push({ selector: selectorOf(el), text: textOf(el), height: Math.round(rect.height) });
    }
    const text = textOf(el);
    if (text.includes('/') || text.includes('\\\\') || text.startsWith('./') || text.startsWith('/Users/')) {
      pathCandidates.push({ selector: selectorOf(el), text, width: Math.round(rect.width), height: Math.round(rect.height), whiteSpace: cs.whiteSpace, writingMode: cs.writingMode });
    }
  }

  const activePanel = document.querySelector('.view-panel.active');
  const pageTitle = activePanel?.dataset?.title || document.title;
  const mainScrollers = scrollContainers.filter((item) => item.selector.includes('.log') || item.selector.includes('.task-list') || item.selector.includes('.run-list') || item.selector.includes('.sample-list') || item.selector.includes('.analysis-box') || item.selector.includes('.question-detail'));

  return {
    url: location.href,
    title: document.title,
    pageTitle,
    activeView: document.body.dataset.activeView || null,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    docScrollWidth: doc.scrollWidth,
    docClientWidth: doc.clientWidth,
    bodyScrollHeight: document.body.scrollHeight,
    bodyClientHeight: document.body.clientHeight,
    horizontalPageOverflow: doc.scrollWidth > doc.clientWidth + 4,
    horizontalOffenders: horizontalOffenders.slice(0, 20),
    overflowCandidates: overflowCandidates.slice(0, 20),
    pathCandidates: pathCandidates.slice(0, 20),
    scrollContainers: mainScrollers.slice(0, 20),
    buttonHeights: buttonHeights.slice(0, 20),
    controlHeights: controlHeights.slice(0, 20),
    visibleButtons: Array.from(document.querySelectorAll('button')).filter(visible).slice(0, 30).map((el) => ({ text: textOf(el), disabled: !!el.disabled, selector: selectorOf(el) })),
    visibleInputs: Array.from(document.querySelectorAll('input, select, textarea')).filter(visible).slice(0, 30).map((el) => ({ tag: el.tagName.toLowerCase(), id: el.id || '', type: el.type || '', disabled: !!el.disabled, selector: selectorOf(el) })),
  };
})()`;

function summarizeIssues(sample) {
  const issues = [];
  if (sample.consoleErrors.length) {
    issues.push({ severity: "P1", kind: "console-error", detail: sample.consoleErrors[0] });
  }
  if (sample.networkFailures.length) {
    issues.push({ severity: "P1", kind: "network-failure", detail: sample.networkFailures[0] });
  }
  if (sample.metrics.horizontalPageOverflow) {
    issues.push({ severity: "P1", kind: "horizontal-overflow", detail: "Document scroll width exceeds viewport" });
  }
  if ((sample.metrics.horizontalOffenders || []).length) {
    issues.push({ severity: "P2", kind: "element-overflow", detail: sample.metrics.horizontalOffenders[0] });
  }
  if ((sample.metrics.pathCandidates || []).some((item) => item.height > 120 || item.writingMode.includes("vertical"))) {
    issues.push({ severity: "P2", kind: "path-layout", detail: sample.metrics.pathCandidates.find((item) => item.height > 120 || item.writingMode.includes("vertical")) });
  }
  if ((sample.metrics.scrollContainers || []).length > 3) {
    issues.push({ severity: "P3", kind: "multi-scroll", detail: `${sample.metrics.scrollContainers.length} scrollable containers detected` });
  }
  return issues;
}

async function launchChrome() {
  const userDataDir = await fs.mkdtemp(path.join(os.tmpdir(), "locomo-ui-audit-"));
  const chrome = spawn(
    CHROME_BIN,
    [
      `--remote-debugging-port=${DEBUG_PORT}`,
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--disable-extensions",
      "--disable-sync",
      "--hide-scrollbars",
      `--user-data-dir=${userDataDir}`,
      "about:blank",
    ],
    {
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let stderr = "";
  chrome.stderr.on("data", (chunk) => {
    stderr += String(chunk);
  });
  const version = await waitForHttpJson(`http://127.0.0.1:${DEBUG_PORT}/json/version`);
  return { chrome, userDataDir, version, stderrRef: () => stderr };
}

async function run() {
  await ensureDir(OUT_DIR);
  await ensureDir(path.join(OUT_DIR, "screenshots"));

  const chromeLaunch = await launchChrome();
  const client = new CdpClient(chromeLaunch.version.webSocketDebuggerUrl);
  await client.open();

  const { targetId } = await client.send("Target.createTarget", {
    url: "about:blank",
    newWindow: true,
    width: 1280,
    height: 800,
  });
  const attach = await client.send("Target.attachToTarget", { targetId, flatten: true });
  const sessionId = attach.sessionId;

  await client.send("Page.enable", {}, sessionId);
  await client.send("Runtime.enable", {}, sessionId);
  await client.send("Log.enable", {}, sessionId);
  await client.send("Network.enable", {}, sessionId);

  const results = [];

  for (const view of VIEWS) {
    for (const viewport of VIEWPORTS) {
      client.events = [];
      await client.send(
        "Emulation.setDeviceMetricsOverride",
        {
          width: viewport.width,
          height: viewport.height,
          deviceScaleFactor: 1,
          mobile: false,
          screenWidth: viewport.width,
          screenHeight: viewport.height,
        },
        sessionId,
      );

      const url = `${APP_URL}?ui_refresh=20260626auditfix01&view=${encodeURIComponent(view.id)}`;
      await client.send("Page.navigate", { url }, sessionId);
      await client.waitForEvent((event) => event.sessionId === sessionId && event.method === "Page.loadEventFired", 15000);
      await sleep(WAIT_AFTER_LOAD_MS);

      const actionResults = [];
      for (const selector of SAFE_ACTIONS[view.id] || []) {
        actionResults.push({ selector, ...(await clickIfVisible(client, sessionId, selector)) });
      }
      if (actionResults.some((item) => item.clicked)) {
        await sleep(WAIT_AFTER_ACTION_MS);
      }

      const metrics = await evaluate(client, sessionId, PAGE_AUDIT_EXPRESSION);
      const screenshot = await client.send("Page.captureScreenshot", { format: "png", fromSurface: true, captureBeyondViewport: true }, sessionId);
      const screenshotName = `${view.label}_${viewport.label}.png`;
      await fs.writeFile(path.join(OUT_DIR, "screenshots", screenshotName), Buffer.from(screenshot.data, "base64"));

      const pageEvents = client.drainEvents((event) => event.sessionId === sessionId);
      const consoleErrors = pageEvents
        .filter((event) => event.method === "Runtime.exceptionThrown" || (event.method === "Log.entryAdded" && ["error", "warning"].includes(event.params?.entry?.level)) || (event.method === "Runtime.consoleAPICalled" && ["error", "warning", "assert"].includes(event.params?.type)))
        .map(eventText)
        .filter(Boolean);
      const networkFailures = pageEvents
        .filter((event) => event.method === "Network.loadingFailed" && !event.params?.canceled)
        .map(eventText)
        .filter(Boolean);

      const sample = {
        view: view.id,
        label: view.label,
        viewport,
        url,
        screenshot: path.join("screenshots", screenshotName),
        metrics,
        actionResults,
        consoleErrors,
        networkFailures,
      };
      sample.issues = summarizeIssues(sample);
      results.push(sample);
      console.log(`[ui-audit] ${view.id} ${viewport.label} -> ${sample.issues.length} issue(s)`);
    }
  }

  const summary = {
    generatedAt: new Date().toISOString(),
    appUrl: APP_URL,
    chrome: CHROME_BIN,
    results,
  };
  await fs.writeFile(path.join(OUT_DIR, "audit-results.json"), JSON.stringify(summary, null, 2));

  await client.send("Target.closeTarget", { targetId });
  await client.close();
  chromeLaunch.chrome.kill("SIGTERM");
  console.log(`[ui-audit] wrote ${path.join(OUT_DIR, "audit-results.json")}`);
}

run().catch(async (error) => {
  console.error("[ui-audit] failed:", error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
