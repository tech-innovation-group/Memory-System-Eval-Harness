# LongMemEval 评测 - Image-2 设计 Brief

## 目标

为本地 AI 记忆评测平台设计一个专业、干净、线性的 `LongMemEval 评测` 工作台页面。

这不是营销页，不是聊天首页，也不是 BI 大屏，而是统一 AI benchmark platform 里的一个 benchmark workbench。

整体风格参考 `LoCoMo 评测` 的秩序感、工程感和浅色工作台气质，但要更简单、更线性、更容易维护。  
LongMemEval 应该像同一平台中的轻量工作流页，而不是独立 demo 页面。

用户打开后，应该沿着一条明确路径完成：

`数据准备 -> 记忆注入 -> QA 评测 -> 结果报告`

不要为了好看增加新功能。只保留这条工作流需要的模块。

---

## 整体视觉方向

- 桌面端工作台，尺寸感接近 `1440 x 1024`
- 页面背景：`#F7F5F0`
- 白色面板：`#FFFFFF`
- 次级面板：`#FAF9F7`
- 细边框：`#E5DED2`
- 强边框：`#D8CCB9`
- 主文字：`#111827`
- 辅助文字：`#6B7280`
- 弱文字：`#9CA3AF`
- 主蓝：`#2563EB`
- 成功绿：`#16A34A`
- 警告橙：`#D97706`
- 错误红：`#DC2626`
- 圆角统一 `8px`
- 模块间距统一 `16px`
- 按钮高度统一 `36px`
- 输入框 / Select 高度统一 `40px`
- 表格行高约 `44px`
- 图标统一使用 Lucide 风格 `16px` 线性图标
- 不要大阴影，最多极弱阴影
- 不要渐变
- 不要蓝紫 AI 科技风
- 不要 Hero
- 不要插画
- 不要大 KPI 卡
- 不要复杂左右多栏
- 不要 card-inside-card
- 不要路径撑开页面

整体气质更接近：

- Linear
- LangSmith
- OpenAI Console
- Vercel Dashboard

关键词：

`专业 / 克制 / 工程化 / 高可扫描性 / 紧凑 / 线性工作流 / benchmark workbench`

---

## 页面结构

页面固定使用单列线性布局，不做窄右栏。

结构顺序固定为：

1. Top Application Bar
2. Page Header
3. 4-step Workflow Stepper
4. Horizontal Run Status Bar
5. Data Preparation Panel
6. Memory Injection Panel
7. QA Evaluation Panel
8. Result Report Panel
9. Recent Tasks Panel
10. Live Log Panel

这是一条从上到下的工作台流程，不要插入额外大模块。

---

## 结构收敛原则

这个页面之所以容易失控，不是因为 LongMemEval 功能本身复杂，而是因为：

- 同一块内容被过多局部 wrapper 包裹
- panel / status / form / log / artifact 各自做了私有视觉
- 为兼容旧布局叠了太多覆盖样式

因此这次设计必须同时服务于后续前端收敛。请把页面理解成：

`统一工作台壳 + 4 个流程面板 + 2 个收尾面板`

而不是很多独立卡片模块的拼贴。

请严格遵守下面的减层规则：

- 只允许一个顶层工作台壳，不要再做二级页面壳
- Stepper 下只有一条 Status Bar，不要每个 Panel 再做一套状态卡
- 所有路径信息统一为单行 Path Row，不要生成独立路径卡
- 所有日志统一为一个固定高度 Log Console，不要拆成多种日志视觉
- Recent Tasks 必须是标准表格，不要做任务卡片墙
- Result Report 只保留结果摘要、指标、产物路径三部分
- 不要把“说明信息”“诊断信息”“注意事项”设计成多个强调色卡片
- 不要做窄右栏；如果有局部左右分区，也必须仍然处于同一个 Panel 内

这份稿子最终要帮助前端把 LongMemEval 从“多层私有 CSS 页面”收敛成“共享 Design System 页面”。

---

## 1. Top Application Bar

这是应用级工具栏，不是 Hero。

高度约 `64px`，白底，底部细边框。

左侧：

- `LongMemEval / MemoryBench`

中间状态：

- 当前账户：`default`
- 后端：`EchoMemory · 正常`
- 模型：当前模型名称

右侧按钮：

- 刷新
- 系统配置
- 查看结果

按钮要求：

- 白底细边框
- 高度 `36px`
- 图标 `16px`
- 文字 `13px`
- 图标与文字横向排列

---

## 2. Page Header

不要 Hero，只做紧凑标题区。

高度约 `64px`。

左侧：

- 标题：`LongMemEval 评测`
- 说明：`Long-context memory injection and QA evaluation.`

右侧：

- 校验数据
- 开始评测
- 停止评测

按钮规则：

- `开始评测`：蓝色主按钮，`Play`
- `停止评测`：红色描边按钮，`Square`
- `校验数据`：白底按钮，`ShieldCheck`

---

## 3. Workflow Stepper

使用 4 步紧凑 Stepper / Tabs：

1. Data Preparation
2. Memory Injection
3. QA Evaluation
4. Result Report

要求：

- 高度约 `56px`
- 白色面板
- 细边框
- 8px 圆角
- 4 个 step 等宽
- 当前 step：蓝色数字圆点 + 蓝色标题 + 底部 2px 蓝线
- 非当前 step：灰色数字圆点 + 灰色标题
- 不要大卡片 stepper
- 不要时间轴样式
- 不要数字和文字上下分离

---

## 4. Run Status Bar

Stepper 下方是一条横向状态带，高度约 `64px`。

包含 5 个小状态块：

1. Dataset
2. Memory Backend
3. Injection Progress
4. QA Progress
5. Run Status

每个状态块结构统一为：

- 图标
- 小标签
- 当前值
- 简短备注或进度文本

设计要求：

- 不要大 KPI 卡
- 不要彩色大背景
- 统一白底细边框
- 没有数据时显示 `Waiting` / `Idle`
- Progress 使用细进度条，不要粗重仪表盘
- Run Status 使用小 pill，不要大块色条
- 不要再在每个主面板内部重复放一套大状态卡

建议图标：

- Dataset：`Database`
- Memory Backend：`Server`
- Injection Progress：`UploadCloud` 或 `DatabaseZap`
- QA Progress：`MessageSquare`
- Run Status：`Activity`

---

## 5. Data Preparation Panel

这是第一步。

Panel Header：

- 图标：`Database`
- 标题：`Data Preparation`
- 副标题：`Select LongMemEval data and prepare samples for memory injection.`

右侧按钮：

- 选择数据文件
- 使用示例数据
- 校验数据

Panel Body 使用紧凑表单：

这里是 LongMemEval 第一屏最关键的配置区，应该足够清楚，但不能像传统后台表单页一样松散。

视觉上建议：

- Dataset Path 独占一行
- Split / Sample Limit / Output Workspace 同一行
- 预设动作和数据校验动作可以紧凑排布，但不要比输入区更抢眼
- Readiness Check 必须直接跟在表单下方，不要再包一层大卡片
- 文件信息、说明、提示尽量压缩成简洁行项目

字段：

- Dataset Path
- Split
- Sample Limit
- Output Workspace

推荐布局：

第一行：

- Dataset Path input
- 自定义路径 / 选择文件按钮

第二行：

- Split select
- Sample Limit input
- Output Workspace input

下方保留一组紧凑 Readiness Check：

- Dataset readable
- Schema valid
- Conversation field found
- Question field found
- Answer field found
- Workspace writable

每项表现：

- 小圆点
- label
- 简短状态文本

状态颜色：

- passed：绿色点
- warning：橙色点
- failed：红色点
- pending：灰色点

同一模块中不要再同时摆很多说明卡。  
文件路径、格式、数据集说明应收纳成紧凑路径行和少量摘要文本。

---

## 6. Memory Injection Panel

这是第二步。

Panel Header：

- 图标：`UploadCloud`
- 标题：`Memory Injection`
- 副标题：`Inject long-context memories into selected memory backend.`

右侧按钮：

- 开始注入
- 停止注入

Panel Body 分成两个逻辑区，但仍然放在同一个面板内。

左侧配置区：

- Memory Backend
- Account
- Batch Size
- Clear Existing Memory

右侧进度区：

- Injection Progress
- 当前 sample / session id
- injected / total
- speed
- ETA
- artifact path

设计要求：

- 不要大统计卡
- 进度信息要紧凑，像运行面板
- artifact path 使用单行 path row，右侧带 copy / open
- 如果无数据，显示简短 empty state：`等待导入任务`

---

## 7. QA Evaluation Panel

这是第三步。

Panel Header：

- 图标：`MessageSquare`
- 标题：`QA Evaluation`
- 副标题：`Run question answering against injected memory.`

右侧按钮：

- 开始 QA
- 停止 QA
- 导出结果

Panel Body 分三段：

第一段：配置行

- Model
- Test Count
- Evaluation Mode
- Tool Calling
- Retrieval Top-K

第二段：QA Progress

- 细进度条
- `36 / 100`
- 当前运行状态文案

第三段：Current Task Preview

- 当前 sample_id
- 当前 question_id
- Question
- Predicted Answer
- Gold Answer
- 可选展示简短 retrieved memory / evidence

设计要求：

- 当前题目预览是这块的主视觉，不是空白占位
- 不要复杂表格
- 不要把隐藏筛选、题目管理当主模块
- Answer 对比可以做成两个并排的小块

---

## 8. Result Report Panel

这是第四步。

Panel Header：

- 图标：`FileBarChart`
- 标题：`Result Report`
- 副标题：`Review metrics, output files, and generated reports.`

右侧按钮：

- 生成报告
- 查看报告
- 打开结果目录

Panel Body 分三层：

1. 最新结果摘要
2. 指标行
3. 结果产物路径

摘要内容：

- 最近一次 run 名称
- 数据集 / split
- 完成状态

指标内容：

- Total Questions
- Completed
- Accuracy / EM
- F1
- Avg Latency
- Failed

产物路径：

- result file
- summary
- report.html
- run directory

要求：

- 路径一律单行截断
- 每条路径右侧可有 copy / open
- 不要做历史 digest 卡墙
- 更完整的历史对比入口留给全局结果页

---

## 9. Recent Tasks Panel

这是辅助模块，不抢主流程。

Panel Header：

- 图标：`History` 或 `Clock3`
- 标题：`Recent Tasks`
- 副标题：保留最近任务，方便切换日志和结果

右侧按钮：

- 刷新

Panel Body 使用标准表格，不做卡片列表。

列建议：

- Task
- Stage
- Dataset
- Progress
- Status
- Started
- Duration
- Action

设计要求：

- 行高统一 `44px`
- 只保留一层表格结构
- Status 使用小 pill
- 文字过长时截断

---

## 10. Live Log Panel

页面底部保留全宽日志面板。

Panel Header：

- 图标：`Terminal`
- 标题：`Live Log`
- 副标题：固定高度滚动显示，避免拉长页面

右侧操作：

- 自动滚动
- 复制
- 清空

Panel Body：

- 顶部一条 log path row
- 下方固定高度 log console

日志区域要求：

- 高度约 `220px`
- 深色背景，例如 `#161B22`
- 边框稍深
- 浅色等宽字体
- 内部滚动
- 空状态只显示简单文案：`运行日志会显示在这里`

---

## 模块层级原则

LongMemEval 页面要像一条单线程工作流，不像一套后台系统首页。

因此要明确压缩这些东西：

- 不要左右复杂分栏
- 不要 Data Preparation 里再塞多组并列小卡
- 不要 Result Report 里做大量历史结果卡
- 不要重复表达状态
- 不要把说明文字做成大空状态
- 不要把隐藏功能做成显性主结构

一句话原则：

`主流程可见，辅助信息收敛，结构稳定，样式可复用。`

---

## 与 LoCoMo 的关系

这是“参考 LoCoMo”，不是“复制 LoCoMo”。

差异建议：

- LoCoMo：更完整、更重流程、更强工作台感
- LongMemEval：更简单、更线性、更轻、更少模块

LongMemEval 应该保留：

- 同一套设计系统
- 同一套按钮、面板、状态条、日志、路径行语言

LongMemEval 应该删掉：

- 过多二级卡片
- 过多并列解释块
- 复杂侧栏结构

---

## 最终观感目标

最终页面看起来应该像：

- 同一个 AI Benchmark Platform 中的一个 dataset workbench
- 比 LoCoMo 更轻，但完全属于同一套系统
- 让用户一眼看懂“先配数据，再注入，再 QA，再看结果”
- 让后续前端实现容易抽共享类，而不是继续堆页面专属 CSS
