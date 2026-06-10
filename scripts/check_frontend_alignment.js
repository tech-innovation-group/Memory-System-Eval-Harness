#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const appPath = path.join(root, "web", "static", "app.js");
const indexPath = path.join(root, "web", "static", "index.html");
const roadmapPath = path.join(root, "web", "static", "product-roadmap.html");
const contractPath = path.join(root, "web", "ui_contract.json");
const appText = fs.readFileSync(appPath, "utf8");
const indexText = fs.readFileSync(indexPath, "utf8");
const roadmapText = fs.readFileSync(roadmapPath, "utf8");
const uiContract = JSON.parse(fs.readFileSync(contractPath, "utf8"));
const contractAgentLabel = uiContract.agent?.label || "MemoryBench Agent";
const contractSidebar = (uiContract.sidebar || []).map((item) => [item.view, item.label]);
const contractBackendIds = (uiContract.memory_backends || []).map((item) => item.id).sort();
const contractPublicStatic = (uiContract.delivery_boundary?.public_static_files || []).slice().sort();

function extractFunctionBefore(name, nextName) {
  const marker = `function ${name}`;
  const start = appText.indexOf(marker);
  if (start < 0) throw new Error(`missing function ${name}`);
  const nextMarker = `function ${nextName}`;
  const end = appText.indexOf(nextMarker, start + marker.length);
  if (end < 0) throw new Error(`missing next function ${nextName} after ${name}`);
  return appText.slice(start, end).trim();
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const source = [
  "function escapeHtml(value) { return String(value ?? ''); }",
  "function reportBoolLabel(value) { const text = String(value ?? '').trim().toLowerCase(); if (text === 'native' || text === 'native_vikingbot_cli') return 'native'; if (value === true || value === 'true') return 'on'; if (value === false || value === 'false') return 'off'; return '-'; }",
  "function runAuditChip(label, status = 'ok', detail = '') { return { label, status, detail }; }",
  extractFunctionBefore("auditSwitchOn", "auditSwitchOff"),
  extractFunctionBefore("auditSwitchOff", "firstNumber"),
  extractFunctionBefore("firstNumber", "alignmentCheckChip"),
  extractFunctionBefore("alignmentCheckChip", "vikingbotRunAlignment"),
  extractFunctionBefore("vikingbotRunAlignment", "evidenceContractBackend"),
  "result = { vikingbotRunAlignment };",
].join("\n\n");

const sandbox = { result: null };
vm.runInNewContext(source, sandbox, { filename: "frontend-alignment-check.vm.js" });
const { vikingbotRunAlignment } = sandbox.result;

const alignedRow = {
  prompt_mode: "vikingboat_compat",
  top_k: "30",
  openviking_tool_loop: true,
  openviking_tool_set: "vikingbot_native_safe",
  group_chat: false,
  vikingbot_identity_mode: "sender_session",
  vikingbot_channel: "cli",
  memory_user_strategy: "sender_sample_namespace",
  initial_agent_memory: true,
  query_expansion: false,
  lexical_fallback: false,
  archive_fallback: false,
  memory_file_read: false,
  raw_turn_fallback: false,
  initial_search_limit: 30,
  initial_score_threshold: 0.1,
  tool_search_limit: 20,
  tool_min_score: 0.35,
};

const aligned = vikingbotRunAlignment(alignedRow);
assert(aligned.comparable === true, "aligned custom agent should be comparable");
assert(aligned.tone === "ok", "aligned custom agent should render ok tone");

const lowTopK = vikingbotRunAlignment({
  ...alignedRow,
  top_k: "4",
  initial_search_limit: 4,
  tool_search_limit: 4,
});
assert(lowTopK.comparable === false, "top-k below VikingBoat-aligned threshold should not be comparable");

const withFallback = vikingbotRunAlignment({
  ...alignedRow,
  archive_fallback: true,
});
assert(withFallback.comparable === false, "archive/source fallback should break comparability");

const native = vikingbotRunAlignment({
  prompt_mode: "native_vikingbot_cli",
  top_k: "native_vikingbot_internal",
  openviking_tool_loop: "native",
  openviking_tool_set: "native_vikingbot_cli",
  openviking_content_read: "native",
  group_chat: false,
  vikingbot_identity_mode: "sender_session",
  vikingbot_channel: "cli",
  memory_user_strategy: "sender_sample_namespace",
});
assert(native.comparable === true, "historical OpenViking reference run should remain comparable");

assert(appText.includes("Agent 可对比"), "run audit should expose comparable chip");
assert(appText.includes("自定义 Agent / VikingBoat 对齐"), "run audit should expose alignment card title");
assert(indexText.includes(contractAgentLabel), "chat view should present the custom agent from ui_contract.json");
assert(indexText.includes("Public UI Contract"), "README view should expose the public UI contract in the delivery boundary");
assert(indexText.includes("web/ui_contract.json"), "README view should name web/ui_contract.json as the public contract");
assert(indexText.includes("locomoFlowNav"), "LoCoMo评测 should expose the four-step flow navigation");
assert(indexText.includes("当前数据集评测流程"), "LoCoMo评测 should label the shared dataset evaluation flow");
assert(indexText.includes("locomoWorkbenchTrack"), "LoCoMo评测 should expose the four-block task track container");
assert(indexText.includes("LoCoMo 四块任务轨道"), "LoCoMo评测 should label the current task track");
assert(indexText.includes("importProgressText"), "LoCoMo import page should keep a visible progress summary");
assert(!indexText.includes("importStageRail"), "LoCoMo import page should not expose the verbose internal import stage rail");
assert(indexText.includes("importReadinessPanel"), "LoCoMo import page should show current backend readiness");
assert(indexText.includes("qaReadinessPanel"), "LoCoMo QA page should show readiness and comparability before running QA");
assert(indexText.includes("judgeReadinessPanel"), "LoCoMo Judge page should show readiness and report status before judging");
assert(indexText.includes("/path/to/memory_workspace"), "LoCoMo import workspace placeholder should be backend-neutral");
assert(appText.includes("LoCoMo 可比"), "chat debug strip should expose LoCoMo comparability status");
assert(appText.includes("renderLocomoWorkbenchTrack"), "frontend should render the LoCoMo task track from runtime state");
assert(appText.includes("renderImportReadinessPanel"), "frontend should render import readiness from current backend/account state");
assert(appText.includes("renderQaReadinessPanel"), "frontend should render QA readiness from selected backend/account/questions");
assert(appText.includes("renderJudgeReadinessPanel"), "frontend should render Judge readiness from result/model/report state");
assert(appText.includes("VIKINGBOAT_LITE_TOP_K = 30"), "QA readiness should expose VikingBoat-aligned top-k default");
assert(appText.includes("VIKINGBOAT_LITE_TOOL_SEARCH_LIMIT = 20"), "QA readiness should expose VikingBoat-aligned tool search default");
assert(appText.includes("EchoMemory find/search evidence"), "chat debug strip should describe EchoMemory agent workbench context source");
assert(appText.includes("OpenViking user/agent memory"), "chat debug strip should describe OpenViking agent workbench context source");
assert(indexText.includes("agentAlignmentPanel"), "System Config should expose the Agent alignment gate panel");
assert(indexText.includes("agentAlignmentReadmePanel"), "README should expose the Agent alignment gate panel");
assert(indexText.includes("runAgentAlignment"), "Agent alignment gate should have a refresh control");
assert(appText.includes("/api/agent-alignment"), "frontend should call the Agent alignment gate API");
assert(appText.includes("renderAgentAlignment"), "frontend should render Agent alignment gate results");
assert(indexText.includes("accountIsolationGatePanel"), "System Config should expose the Account isolation gate panel");
assert(indexText.includes("accountIsolationReadmePanel"), "README should expose the Account isolation gate panel");
assert(indexText.includes("runAccountIsolation"), "Account isolation gate should have a refresh control");
assert(appText.includes("/api/account-isolation"), "frontend should call the Account isolation gate API");
assert(appText.includes("renderAccountIsolationGate"), "frontend should render Account isolation gate results");

assert(contractSidebar.length === 9, "ui_contract sidebar must contain the nine requested task entries");
assert(JSON.stringify(contractBackendIds) === JSON.stringify(["echomemory", "openviking"]), "ui_contract must expose only OpenViking and EchoMemory");
assert(
  JSON.stringify(contractPublicStatic) === JSON.stringify([
    "web/static/app.js",
    "web/static/index.html",
    "web/static/product-roadmap.html",
    "web/static/styles.css",
  ]),
  "ui_contract public_static_files must expose only the four public UI files"
);
assert(
  String(uiContract.delivery_boundary?.historical_static_policy || "").includes("experiment history"),
  "ui_contract must explain that extra web/static HTML files are experiment history, not public entrypoints"
);
const navMatches = [
  ...indexText.matchAll(/<button\s+class="nav-item(?:\s+active)?"\s+data-view="([^"]+)"[^>]*>(.*?)<\/button>/g),
].map((match) => [match[1], match[2].replace(/<[^>]*>/g, "").trim()]);
assert(
  JSON.stringify(navMatches) === JSON.stringify(contractSidebar),
  `sidebar nav must stay as the nine requested task entries; got ${JSON.stringify(navMatches)}`
);

const retiredBackendPattern = new RegExp(["h", "i", "g", "o"].join(""), "i");
const retiredLocomoLabel = ["LoCoMo", "工作台"].join(" ");
assert(!retiredBackendPattern.test(indexText + appText + roadmapText), "active UI must not mention retired backend names");
assert(
  roadmapText.includes("左侧导航固定为九个任务入口"),
  "roadmap should describe the fixed nine-entry sidebar instead of adding extra navigation items"
);
assert(
  roadmapText.includes("不可妥协约束"),
  "roadmap should expose hard constraints for the 20k-star overhaul"
);
assert(
  roadmapText.includes("只保留两个记忆后端"),
  "roadmap should state that only OpenViking and EchoMemory are in current scope"
);
assert(
  roadmapText.includes("MemoryBench Agent 可比标准"),
  "roadmap should define the custom agent comparability standard"
);
assert(
  roadmapText.includes("Agent Alignment Gate"),
  "roadmap should describe the API-backed Agent alignment gate"
);
assert(
  roadmapText.includes("Public UI Contract"),
  "roadmap should explain the public UI contract layer"
);
assert(
  roadmapText.includes("双后端覆盖矩阵"),
  "roadmap should make the OpenViking/EchoMemory coverage boundary explicit"
);
assert(
  roadmapText.includes("唯一当前要求双后端可复现的正式主链路"),
  "roadmap should state that LoCoMo is the current dual-backend formal path"
);
assert(
  (indexText + appText).includes("MemoryBench OpenViking memory-QA") || (indexText + appText).includes("OpenViking MemoryBench memory-QA"),
  "non-LoCoMo benchmark tasks should use the formal MemoryBench memory-QA label"
);
assert(
  (indexText + appText).includes("官方原 benchmark 指标") || (indexText + appText).includes("官方原指标"),
  "non-LoCoMo benchmark pages should separate MemoryBench scores from official benchmark metrics"
);
assert(
  !(indexText + appText).includes("OpenViking generic smoke"),
  "active UI should not keep the old generic smoke label for formal MemoryBench benchmark tasks"
);
assert(
  !roadmapText.includes(retiredLocomoLabel),
  "roadmap should use the sidebar label LoCoMo评测 instead of the retired sidebar wording"
);

console.log("frontend MemoryBench alignment checks passed");
