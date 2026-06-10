# LoCoMo Long Context Agent 测试方案

目标：先基于 LoCoMo 搭建一个可复现的长期对话记忆评测流程。主路径参考 MemoryAgentBench 的 Long Context Agents 思路：把 LoCoMo 对话整理成 Context Pack，再由本地 agent 在当前请求上下文内检索和回答。默认不写外部记忆系统、Neo4j 或向量库，因此不会污染长期记忆。

参考实现方向：[MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) 中的 Long Context Agents 把历史上下文直接提供给 agent 进行问答；本项目的 `scripts/local_memory_agent.py` 对 LoCoMo 使用同类思路，但把每次测试的 Context Pack、检索命中和结果 CSV 都落到当前 run 目录，方便 Web 追踪和复盘。

## 当前平台形态

- Web 入口当前只暴露 LoCoMo。
- 主 runner 是 `Local Long Context Agent`，脚本为 `scripts/local_memory_agent.py`。
- `Context Pack Preview` 生成注入计划和统一 QA CSV，脚本为 `scripts/benchmark_adapter.py`。

## 注入方式

1. 读取数据集样本。
2. 把 conversation、messages、history、events、sessions 等字段归一成事件列表。
3. 为每个样本生成本地 memory store：`local_memory_store.json`。
4. 每道题只从当前 run 的本地 memory store 检索相关事件。
5. 将 top-k relevant memory 放入回答逻辑和输出 CSV。

这等价于“把记忆注入到当前上下文”，不是写入长期记忆系统。

## 怎么测试

### Smoke

1. 打开 Web。
2. 选择数据集卡片。
3. 点 `读取数据集`。
4. 选择某个 conv/sample 或保留全量。
5. 点 `Agent Smoke`。
6. 查看任务日志、结果 CSV、summary、relevant memory。

每个 run 产物：

- `local_agent_results.csv`
- `local_memory_store.json`
- `relevant_memory.json`
- `summary.json`
- `manifest.json`
- `config_snapshot.json`

### Full

- 把 `问题数` 留空或设成目标数量。
- 对 LoCoMo 可先跑 5 题、50 题、再全量。

### Judge

本地 agent 会先做简单字符串判定：

- 命中 gold answer：`CORRECT`
- 无法可靠判断：`NEEDS_JUDGE`

`NEEDS_JUDGE` 不显示为 0% 准确率，应进入单独 Judge 阶段。Judge 会更新当前 CSV 的 `result` 和 `reasoning` 列，再刷新准确率、token 和失败分析。

## 如何保证不污染记忆

默认 Long Context Agent 路线满足：

- 不调用外部记忆系统登录。
- 不调用外部记忆写入接口。
- 不写 Neo4j。
- 不写向量库。
- 只在 `runs/<run_id>/local_agent/` 或临时输出目录写 JSON/CSV。
- 每个 run 使用独立 namespace。

可审计证据：

- `local_memory_store.json` 中 `pollution_guard.external_memory_write=false`。
- `storage=local run directory only`。
- `manifest.json` 记录 runner、数据集路径、输出路径和配置 hash。

## LoCoMo 覆盖

- 支持真实 LoCoMo JSON。
- 支持指定某个 conv/sample 或全量。
- 支持 Agent smoke/full、Judge 和 relevant memory 展示。

## 预估时间

在不调用外部模型的 Long Context Agent 路线下：

- 平台启动和预检：2 到 5 分钟。
- LoCoMo smoke：1 分钟以内。
- 100 题本地 agent：通常 1 分钟以内。
- LoCoMo 全量本地 agent：数分钟级，取决于上下文长度和机器 IO。
- 加外部 Judge：按 judge 模型延迟估算，100 题约 10 到 40 分钟。

## 推荐下一步

1. 用 Long Context Agent 建立稳定 baseline。
2. 对 `NEEDS_JUDGE` 样本单独跑 Judge。
3. 用失败样本聚类和结果 diff 找上下文工程问题。
4. 再迭代 prompt、检索 top-k、样本范围和数据格式适配。
