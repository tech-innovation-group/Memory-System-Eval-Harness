# UI Function Audit Report

## 总体结论
本轮巡检确认项目主流程基本可用，`记忆导入`、`问答测试`、`判分`、`报告与对比`、`系统配置` 的页面切换和主要按钮状态大体正常。已自动修复一批低风险 UI/交互问题，主要是路径展示、按钮/输入框尺寸、`evalView` 顶部宽度锁定、题目表格压缩和 HotpotQA 空值异常防护。

最新一轮自动审计已覆盖 `1440×900`、`1280×800`、`1920×1080` 三档尺寸。结果显示 `judgeView` 和 `runsView` 已无残留问题；`evalView` 的 `element-overflow` 已消失；当前剩余问题全部收敛为跨页面一致存在的 `path-layout`，本质是路径节点仍偏长、偏占视觉面积，而不是页面崩坏或控制台错误。

补充功能烟测结果：已在浏览器里实际验证 `chatView`、`openvikingView`、`evalView`、`judgeView`、`runsView`、`longMemEvalView`、`hotpotQaView`、`systemConfigView` 的页面切换与安全交互；聊天输入、导入范围下拉、问答页 checkbox 切换、判分页刷新均可正常响应，烟测过程中未采集到新的 console error / warn。

## P0
无。

## P1
无。

## P2

### 1. 多个页面仍存在长路径展示问题
- 页面：对话页、记忆导入、问答测试、LongMemEval、HotpotQA、系统配置、评测数据集入口
- 问题描述：审计持续识别到路径相关元素（日志、产物、配置路径、历史记录）仍然占据较大视觉面积，部分虽然已做 truncate，但在若干卡片和说明块里仍会形成“路径主视觉”。
- 复现方式：在不同页面查看路径卡、日志路径和结果文件路径。
- 影响：页面显得拥挤，路径会抢视觉焦点，并削弱不同模块之间的层次感。
- 建议修复方式：统一把路径收成单行省略，并确保复制/打开操作固定在右侧。
- 是否已修复：部分已修复。导入日志路径、部分结果路径、系统配置输入和聊天页的账户目录代码块已继续收窄，但还有残留。

## P3

### 2. 若干页面仍被审计脚本标记为路径布局项
- 页面：chatView、openvikingView、evalView、longMemEvalView、hotpotQaView、systemConfigView、evolvingEventsView、proAgentBenchView、tauBenchView
- 问题描述：这些页面当前仍有少量 `path-layout` 标记。
- 复现方式：运行 `scripts/ui_function_audit.mjs`。
- 影响：不一定是可见崩坏，更多是密度和省略策略还不够统一。
- 建议修复方式：逐页收紧路径节点，统一 `code`、`path-row`、日志和结果列表的省略规则。
- 是否已修复：未完全修复。

## 已修复项
- `evalView` 顶部区域不会再被旧规则压窄。
- 点击“问答测试”后，顶部公共区不会比“记忆导入”更窄。
- `evalView` 的题目表格不再触发 `element-overflow`。
- `judgeView` 先前的 404 / `console-error` 告警在最新审计里已消失。
- `runsView` 在三档桌面尺寸下均为 `0 issue`。
- 页面切换、聊天输入、LoCoMo 导入范围下拉、问答页 Tool Calling checkbox、判分页刷新等基础交互已做浏览器烟测，未发现新的明显功能错误。
- `HotpotQA` 的空值异常已补防护。
- `static/styles.css` 和 `web/static/styles.css` 已同步。
- `static/app.js` 和 `web/static/app.js` 已同步空值修复。
- 相关缓存版本号已更新，便于浏览器加载新资源。

## 未修复项
- 多页面长路径省略还没有完全统一。
- 审计脚本仍会把部分路径节点标成问题，需要继续逐页收口。

## 修改文件
- `/Users/chx/locomo-eval-web/static/styles.css`
- `/Users/chx/locomo-eval-web/web/static/styles.css`
- `/Users/chx/locomo-eval-web/static/app.js`
- `/Users/chx/locomo-eval-web/web/static/app.js`
- `/Users/chx/locomo-eval-web/static/index.html`
- `/Users/chx/locomo-eval-web/web/static/index.html`

## 本地验证
- `node --check /Users/chx/locomo-eval-web/static/app.js`
- `node --check /Users/chx/locomo-eval-web/web/static/app.js`
- `diff -q /Users/chx/locomo-eval-web/static/styles.css /Users/chx/locomo-eval-web/web/static/styles.css`
- `node /Users/chx/locomo-eval-web/scripts/ui_function_audit.mjs`
- 浏览器复查 `openvikingView`、`evalView`、`judgeView`、`runsView`、`hotpotQaView`
- 浏览器烟测 `chatView` 输入、`openvikingView` 下拉切换、`evalView` checkbox 切换、`judgeView` 刷新按钮

## 下一步建议
继续把长路径展示规则统一到全站公共模块，优先处理 `path-row`、`memory-hit p code`、`runArtifactList`、`system-config-shell` 这四类节点；功能层面可暂时把 `judgeView` / `runsView` 视为本轮已稳定页面。
