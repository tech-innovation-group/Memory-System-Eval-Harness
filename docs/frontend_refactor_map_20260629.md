# LoCoMo 前端重构地图

## 1. 当前真正生效的入口

- `web/static/index.html`
- `web/static/app-state.js`
- `web/static/app-core.js`
- `web/static/app-format.js`
- `web/static/app.js`
- `web/static/styles.css`
- `web/static/boot.js`

`web/package.py` 和 `server.py` 都把 `web/static` 作为主静态目录，`static/` 只是 legacy 镜像。

## 2. 已经拆出来但还没真正分层完成的文件

- `web/static/app-state.js`
- `web/static/app-core.js`
- `web/static/app-format.js`
- `web/static/styles/base.css`
- `web/static/styles/components.css`
- `web/static/styles/layout.css`
- `web/static/styles/sidebar.css`
- `web/static/styles/account-topbar.css`
- `web/static/styles/views/datasets.css`
- `web/static/styles/views/metrics.css`
- `web/static/styles/views/results.css`

这些文件存在，但当前页面运行主要还是依赖大 bundle，没形成真正稳定的源码分层。

## 3. 最需要修改或重构的前端代码

### 3.1 `web/static/app.js`

这是最大风险点。它同时承载：

- 视图切换
- runsView / evalView / judgeView / openvikingView 渲染
- 任务状态
- copy/open 按钮绑定
- 报告、对比、审计、artifact、log console
- 多个 benchmark 的 fallback 逻辑

重点热点：

- `renderRunsSelectionState`
- `renderRunCard`
- `renderRunCompareSummary`
- `renderRunAudit`
- `runAuditMetric`
- `reportPathRow`
- `bindCopyButtons`
- `bindOpenButtons`

问题表现：

- 同一类组件在多个页面重复定义
- 页面级 override 叠加太多
- 规则一改就影响别的视图

### 3.2 `web/static/styles.css`

这是第二个最大风险点。当前同一类样式在这里反复出现：

- `run-audit-grid`
- `path-row`
- `task-table`
- `log-console`
- `result-kpis`
- `report-*` / `locomo-report-*`
- `evalView`、`judgeView`、`runsView` 的局部覆盖

问题表现：

- 先全局定义，再页面覆盖，再局部重复覆盖
- `runsView` 相关规则已经出现多轮追加
- 结果是维护者无法判断哪个选择器最终生效

### 3.3 `web/static/index.html`

问题不在内容多，而在视图太多、同一壳内复用太密：

- `openvikingView`
- `evalView`
- `judgeView`
- `runsView`
- `longMemEvalView`
- `evolvingEventsView`
- `hotpotQaView`

问题表现：

- 页面 DOM 很大
- 同一个 shell 里塞太多 benchmark
- 结构可读性差，容易和样式耦合

## 4. 应该抽成共享层的组件

这些组件已经跨页面出现，应该优先做成稳定公共层：

- `path-row`
- `status-pill`
- `task-table`
- `log-console`
- `result-kpis`
- `result-summary-grid`
- `artifact-list`
- `report-artifact-row`
- `run-card`
- `run-audit-grid`
- `run-audit-fold`

## 5. 应该保留为页面内特例的内容

这些东西不要再强行抽成共享规则：

- `runsView` 的 compare bar
- `runsView` 的审计详情折叠区
- `evalView` 的题目列表和当前结果区
- `judgeView` 的判分输入、失败归因、候选结果
- `openvikingView` 的导入进度与记忆写入状态

## 6. 推荐重构顺序

1. 先把 `web/static/styles.css` 中共享组件规则抽出去，停止新增页面级重复覆盖。
2. 再把 `web/static/app.js` 中 runsView / evalView 的渲染代码拆成模块。
3. 保留 `web/static/index.html` 作为壳，但减少视图内部的跨页面共享 DOM 复用。
4. 最后同步到 `static/*` 并清理 legacy 规则和重复 selector。

## 7. 现在最该先看的文件

- `static/app.js`
- `static/styles.css`
- `static/index.html`
- `web/static/app.js`
- `web/static/styles.css`
- `web/static/index.html`
- `web/static/app-core.js`
- `web/static/app-format.js`
- `web/static/app-state.js`
- `web/static/styles/components.css`
- `web/static/styles/views/results.css`

## 8. 规则堆叠最严重的具体位置

### `web/static/styles.css`

- `body[data-active-view="runsView"]` 相关规则出现很多轮，`runsView` 的布局、card、artifact、summary、compare、audit 都在重复覆盖。
- `run-audit-grid` 出现 4 处以上定义，且 runsView 里又有两轮局部覆盖。
- `path-row` 在全局、openviking、evolvingEvents、longMemEval、runsView 中都被单独改过。
- `result-kpis` 在通用层、evalView、runsView、judgeView、evolvingEvents 中多次被改写。
- `run-card` 和 `run-state-pill` 也被 repeated override，说明列表项视觉已经不能靠单一共享层维护。
- `scope-toggle` 在 evalView 和 runsView 都被针对性改过，checkbox / switch 的视觉 contract 不统一。

### `web/static/app.js`

- `renderRunCard` 已经同时负责标题、scope、状态、metrics、actions，职责过宽。
- `renderRunAudit` 把 summary、上下文、排查、artifact、对齐都拼在一个函数里。
- `renderRunCompareSummary`、`renderRunsSelectionState`、`renderEvalRecentResultPanel` 都在做页面级拼装，重复了很多 UI 结构判断。
- `bindCopyButtons` / `bindOpenButtons` 是跨页面通用，但当前还是散落在一个大文件里，不是独立组件层。

## 9. 拆分优先级表

1. `web/static/styles/components.css` 先补齐真正稳定的共享组件层。
2. `web/static/styles/views/results.css` 再吸收 runsView / judgeView / evalView 里的重复样式。
3. `web/static/app-core.js` 保留纯数据与路径 / 状态工具。
4. `web/static/app-format.js` 保留纯格式化函数。
5. `web/static/app.js` 只留视图编排和少量页面私有渲染，先从 `runsView` 开刀。
6. `static/*` 继续当 legacy mirror，同步即可，不作为新的结构设计起点。

## 10. 重复规则的证据摘要

### 10.1 `web/static/styles.css`

- `run-audit-grid` 至少出现 4 个独立定义簇，分别在基础区、局部页面覆盖和 `runsView` 的二次覆盖里重复出现。
- `path-row` 反复出现在全局共享区、`openvikingView`、`evolvingEventsView`、`longMemEvalView`、`runsView`，同一个基础组件被多次改写。
- `result-kpis` 在通用层、`evalView`、`runsView`、`judgeView`、`evolvingEventsView` 中都有专门覆盖，说明 KPI 不是统一组件。
- `run-card`、`run-state-pill`、`scope-toggle` 也都有多轮覆盖，且很容易跟页面级规则打架。
- `runsView` 相关选择器数量明显最多，`body[data-active-view="runsView"]` 出现远超其它页面，说明报告页已经成为样式堆叠最重的地方。

### 10.2 `web/static/app.js`

- `renderRunAudit` 同时拼装 summary、上下文、排查、artifact、对齐，函数职责过宽。
- `renderRunCard` 同时负责数据集标题、状态、时间、指标、行动按钮。
- `renderRunsSelectionState` 负责列表、对比选择条、占位态、按钮状态，页面编排逻辑过密。
- `renderEvalRecentResultPanel` 和 `renderJudgeViewIdleState` 也属于同类问题，都是页面级拼接而不是稳定组件。

### 10.3 `web/static/styles/*`

- `web/static/styles/base.css`、`components.css`、`layout.css`、`sidebar.css`、`account-topbar.css`、`views/*.css` 都存在，但当前主 HTML 入口没有直接加载它们。
- 这说明拆分已开始，但没有完成接管。
- 后续如果不把这些文件接回主链路，它们只会继续变成“看起来分层，实际上维护时仍然改大文件”的半成品。

## 11. 直接可执行的拆分建议

1. 把 `path-row`、`result-kpis`、`run-state-pill`、`task-table`、`log-console` 先定成共享组件 contract。
2. 把 `runsView` 的 compare / audit / artifact 相关样式和渲染逻辑抽成独立模块。
3. 把 `evalView` 的 recent result / task list / console 也做同样拆法。
4. 只保留 `judgeView`、`openvikingView` 的少量页面特例，不再向全局 selector 继续回写。

## 12. 模块命名建议

### 12.1 状态和路径工具

建议从 `web/static/app-core.js` 再细分出这类职责：

- `dataset-path-utils`
  - `normalizeSlashes`
  - `relativeDatasetPath`
  - `datasetPathVariants`
  - `datasetPathMatches`
  - `preferredLocomoDatasetPath`
- `ui-action-locks`
  - `uiActionLocked`
  - `runWithUiActionLock`
- `locomo-sample-scope`
  - `currentLocomoSampleScope`
  - `locomoSampleScopeFromTask`
  - `currentImportSampleScope`
  - `parseImportSampleSelection`
  - `locomoQaSampleOptionLabel`
  - `locomoImportSampleOptionLabel`
  - `refreshImportActionLabels`
- `memory-backend-paths`
  - `projectPath`
  - `runPath`
  - `artifactHref`
  - `readLastImport`
  - `readScopedLastImport`
  - `normalizeMemoryBackend`
  - `memoryBackendLabel`
  - `memoryBackendShortLabel`
  - `importTaskKindForBackend`
  - `importScriptForBackend`
  - `genericQaTaskKindForBackend`
  - `importWriteSurfaceForBackend`
  - `workspaceBackendNameHint`
  - `compactPath`
  - `displayPath`
  - `shellQuote`
  - `currentMemoryBackend`
  - `normalizeWorkspacePath`
  - `importRecordBackend`
  - `importRecordWorkspace`
  - `currentWorkspaceScopedLastImport`
  - `importRecordMatchesCurrentWorkspace`
  - `clearImportedMemoryStatusForWorkspace`
  - `currentImportedMemoryStatus`
  - `locomoImportDisplayState`
  - `locomoImportCompleteState`
  - `setImportedMemoryRunningStatus`
  - `chatDraftKey`
  - `loadChatDraft`
  - `saveChatDraft`
  - `clearChatDraft`

### 12.2 格式化工具

建议 `web/static/app-format.js` 只保留：

- `escapeHtml`
- `percent`
- `formatInt`
- `normalizeDisplayDate`
- `formatDateTimeLocal`
- `formatDateTime`
- `compactTimestamp`
- `formatDuration`
- `formatSecondsMetric`
- `compactText`
- `normalizeVisibleMemoryBackendName`
- `runCompareKey`

### 12.3 页面渲染模块

建议从 `web/static/app.js` 拆成：

- `app-runs.js`
  - `renderRunsSelectionState`
  - `renderRunCard`
  - `renderRunCompareSummary`
  - `renderRunAudit`
  - `runAuditMetric`
  - `reportPathRow`
  - `loadRunDetail`
  - `loadConfigSnapshot`
  - `exportRunReport`
- `app-eval.js`
  - `renderEvalRecentResultPanel`
  - `renderEvalHeaderSummary`
  - `renderEvalProgressSummary`
  - `renderEvalConsoleContext`
- `app-judge.js`
  - `renderJudgeReadinessPanel`
  - `renderJudgeViewIdleState`
  - `renderJudgeConfirmation`
  - 判分相关 summary / history / readiness
- `app-openviking.js`
  - 导入配置、进度、完整性检查、日志、记忆浏览

### 12.4 样式模块

建议从 `web/static/styles.css` 拆出：

- `styles/components.css`
  - `path-row`
  - `status-pill`
  - `task-table`
  - `log-console`
  - `result-kpis`
  - `artifact-list`
- `styles/views/results.css`
  - `runsView` 的 compare / audit / artifact / summary
- `styles/views/eval.css`
  - `evalView` 的 recent result / task list / log
- `styles/views/judge.css`
  - `judgeView` 的 readiness / history / evidence
- `styles/views/import.css`
  - `openvikingView` 的 import shell / progress / logs

## 13. 文件级责任矩阵

| 文件 | 当前角色 | 是否该拆 | 备注 |
| --- | --- | --- | --- |
| `web/static/app-state.js` | 全局状态容器 | 否 | 先保留，边界清晰 |
| `web/static/app-core.js` | 共享逻辑与路径工具 | 否 | 适合继续作为工具层 |
| `web/static/app-format.js` | 格式化工具 | 否 | 适合继续作为纯工具层 |
| `web/static/app.js` | 页面编排 + 大量视图渲染 | 是 | 需要按 runs/eval/judge/import 拆 |
| `web/static/styles.css` | 全局样式 + 页面覆盖 | 是 | 需要拆到 components/views |
| `web/static/index.html` | 单壳多视图入口 | 轻微整理 | 保留壳，但减少内部耦合 |
| `web/static/styles/components.css` | 拆分中的共享组件层 | 是，补全 | 应承接 path/log/task/kpi 等 |
| `web/static/styles/views/results.css` | runsView 方向样式 | 是，补全 | 应承接 compare/audit/artifact |
| `web/static/styles/views/datasets.css` | 数据集页样式 | 待接管 | 未来可吸收 openviking/eval 部分页面样式 |
| `web/static/styles/views/metrics.css` | 指标页样式 | 待接管 | 未来可承接 KPI 和图表类布局 |

### 13.1 最优先从 `web/static/app.js` 拆出去的块

- `runsView`：最重、重复覆盖最多
- `evalView`：和 runsView 共享大量结果列表、任务列表、console 形态
- `judgeView`：有独立审计和 readiness 逻辑，适合单独模块化
- `openvikingView`：导入流程和进度状态可以作为 import 模块收口

### 13.2 最优先从 `web/static/styles.css` 拆出去的块

- `path-row`
- `result-kpis`
- `task-table`
- `log-console`
- `run-card`
- `run-state-pill`
- `run-audit-grid`
- `report-kv-grid`
- `report-artifact-row`
- `scope-toggle`
- `report-compare-bar`
