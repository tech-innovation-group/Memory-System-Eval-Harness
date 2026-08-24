# EchoMem 改进钩子目录

给「EchoMem 记忆后端」把 D1–D7 维度的发现映射到**具体的代码/配置落点**。每条按
「方案 → 预期收益 → 风险/代价 → 验证方式」组织，并给出可操作的钩子（文件路径 + 配置项）。
非 EchoMem 后端请只使用 `analysis-dimensions.md` 的通用方向，不要套用本目录的路径。

> 现状基线（写方案时以此为准，避免基于不存在的环节给建议）：
> - 生效引擎是 **atomic_engine**（`EchoMem/echo_workspace/config.json` `engine.enabled=["atomic_engine"]`），
>   echo0 的 L0→L1→L2 分层是遗留代码，**未启用**。
> - **打字期 prefill 预热（max_tokens=1 暖 KV）在当前 develop 分支不存在**；现有替代物是
>   `req_coordinator/prefetch.py` 的 PrefetchCache/Gate/Merger。
> - 全局 rerank 默认关闭，且 atomic gateway 的 `rerank()` 未实现。

---

## R1 检索精度：开启/落地 rerank（重排）

- **适用**：D4 evidence_mismatch / evidence_unused 占比高，或低分证据挤占 top_k。
- **方案**：实现 `model_gateway_adapter.py` 的 `rerank()`（当前 `:756` 抛
  `NotImplementedError`），配置 `recall.model.rerank.enabled=true` + 重排模型
  （dashscope qwen3-rerank 已在 config 预留），对融合结果做 cross-encoder 重排。
- **收益**：top_k 内排序更准，精度↑；可用**更小 top_k 达到同等精度**（同时省 token）。
- **风险/代价**：每次查询多一次重排调用 → 检索延迟↑、成本↑（需评估是否值得，见 R4）。
- **验证**：同一数据集 rerank 开/关 对比 accuracy 与 p50/p95 检索延迟。

## R2 检索延迟：query→结果缓存

- **适用**：D2 长尾（p95/p50 高）、同 session 内重复/近重复查询（dynamic 多轮工具查询）。
- **方案**：在 `memrouter/recall/local_recall_orchestrator.py` 的 recall 入口加
  query（+scope/filter）→ 结果哈希缓存（TTL 可参考 `req_coordinator/prefetch.py` 的
  PrefetchCache 模式）；prefetch 结果已有 per-turn revision 合并缓存，可扩展为跨轮复用。
- **收益**：重复查询零检索延迟、省 embedding 调用。
- **风险/代价**：记忆更新后缓存过期问题 → 需与 commit 版本号联动失效。
- **验证**：对比同 session 内重复 query 的 retrieval_latency；命中率指标。

## R3 检索延迟/精度：top_k 与召回路数调参

- **适用**：D5 召回数逼近 top_k、D1 token 高、或检索延迟受多路召回影响。
- **方案**：`atomic_engine/core/recall/atom_retriever.py` 的语义/关键词/结构化 top_k
  （现各 50）、`recall_default_limit`（现 25）、`search.fusion.max_results`（现 12）
  按数据集复杂度下调；动态 top_k（简单 query 少取）。
- **收益**：token↓、延迟↓；若配合 R1 重排，精度可维持甚至提升。
- **风险/代价**：top_k 过小可能漏召回 → 需用 failure mode 验证不新增 empty_retrieval。
- **验证**：多组 top_k 对比 accuracy / tokens_per_correct / 检索延迟。

## R4 检索精度/延迟：时间线检索与时间感知（temporal 失败）

- **适用**：D4 temporal_reasoning 占比高、Cat2（时间类，以 dataset 题目复核）准确率低。
- **方案**：`atom_retriever.py` 已做 query 日期 token → `find_by_time_range` 时间线召回；
  若失败集中在时间推理，排查：(a) 抽取时原子是否携带可信时间戳（`[time=...]` 标记），
  (b) 多个时间冲突的原子如何合并（见 R5），(c) 排序是否时间感知（近期 vs 历史权重）。
- **收益**：时序题精度↑（locomo 样本该类别占比 42% 失败）。
- **风险/代价**：时间感知排序可能干扰普通事实题 → 按 category 分策略。
- **验证**：按 category 拆 accuracy；抽查 temporal 失败题的 `retrieval_items` 是否含正确时间证据。

## R5 精度：原子去重与版本仲裁（evidence_mismatch）

- **适用**：D4 evidence_mismatch 占比高（同一事实多个冲突版本）。
- **方案**：`atomic_engine/core/extractor/atom_merge_engine.py` 的合并决策
  （ADD/UPDATE/REPLACE/INVALIDATE）已存在；若冲突证据仍进 top_k，检查仲裁阈值
  与 INVALIDATE 是否真正生效，并考虑注入时对同一 subject 只保留最新/最可信版本。
- **收益**：冲突证据减少 → evidence_mismatch↓、模型被误导概率↓。
- **风险/代价**：仲裁过激进会丢信息 → 保留来源 URI 便于追溯。
- **验证**：统计失败题命中证据的 subject 重复/冲突情况。

## R6 注入：预算/格式/条数联动

- **适用**：D1 token 高、D5 注入过多、D4 evidence_unused。
- **方案**：`entrypoints/plugins/echoagent/server.py` 的 transform 预算拆分
  （history 0.4 / memory 余量）与 `limit=5` 条数、`context_assembly.py` 的
  `<relevant-memories>` 注入格式；`mapper.py:130 inject_context_message` 注入位置
  （system 之后 index 1）。可改为：按可用 token 动态条数、按 score 截断、低相关记忆
  转「按需查询」而非注入。
- **收益**：prompt token↓、证据信噪比↑。
- **风险/代价**：注入过少可能漏关键证据 → 用 failure mode 验证不新增 evidence_unused/missing。
- **验证**：对比不同 limit/预算的 prompt_tokens 与 accuracy。

## R7 注入延迟：抽取窗口与后处理降频

- **适用**：D3 注入慢（locomo 样本每 session commit ~23.5s，抽取占主体）。
- **方案**：
  - 抽取窗口 `raw_atom_extractor.py`（`_DEFAULT_WINDOW_SIZE=12`）与 granularity 三档
    （turn/window/session）按会话长度/复杂度调优；
  - `repair_attempts`（解析失败重试）是纯 token/时间浪费点，调高抽取 prompt 质量以减少 repair；
  - `atomic_memory_engine.py:294` 的**每次 commit 全量 vector_compact** 是隐藏成本，
    改为按增量/定期/异步 compact。
- **收益**：注入吞吐↑、记忆就绪更快、抽取 token↓。
- **风险/代价**：窗口过大会稀释原子粒度；compact 降频需保证检索一致性。
- **验证**：对比 import_results.elapsed_s 与 backend_logs commit 阶段耗时、`model_token_counts`。

## R8 Token：抽取/仲裁/embedding 成本控制

- **适用**：D1 抽取侧 token 高（若经 EchoMem 日志可见 `model_token_counts`）。
- **方案**：抽取 `max_tokens=8192`、仲裁 `max_tokens=256`、field/neighbor embedding 批量
  与 `model_gateway_adapter.py` 的 EmbedCacheState（共享单例 + single-flight，已存在）——
  确认缓存命中率；对低价值窗口跳抽取（concreteness/salience 阈值）。
- **收益**：每 commit 抽取 token↓。
- **风险/代价**：阈值过严丢记忆 → 用 `work_counts` 观察原子产量。
- **验证**：`atomic_pipeline_completed` 日志的 `model_token_counts` / `provider_diagnostics`。

## R9 打字期/首字：prefetch 完善与 prefill 预热回归

- **适用**：D6/D7 或用户关注 TTFT；`prefetch_committed` 恒 false、cached_tokens≈0。
- **方案**：
  - 现有 `req_coordinator/prefetch.py` 的 PrefetchRetrievalGate（debounce/max_wait 判定、
    novelty/specificity 打分）与 PrefetchCache（TTL 600s）已能在打字期预取；
  - 若目标是**暖 KV cache 的 max_tokens=1 prefill**，该机制当前**已移除**
    （历史参考 `EchoMem` 仓库 `prefetch-develop` 分支的 `prefill_pipeline.py`；
    EchoAgent 侧 `prefill_committed` 现恒为 false）——需要评估是否重新引入，牵涉
    EchoAgent `ai-handler.service.ts` / `session-stream-finalizer.service.ts` 的配合。
- **收益**：长会话 TTFT↓、cached_tokens↑。
- **风险/代价**：跨项目改动（EchoMem + EchoAgent）、预热请求的额外成本。
- **验证**：用支持 TTFT 的插件（echo_agent/echoagent_live）跑 dynamic，对比 ttft_ms/cached_tokens。

## R10 意图路由：rule → LLM（可选）

- **适用**：查询意图多样、rule 关键字路由误判导致召回偏差。
- **方案**：`config` `search.intent.backend` 从 `rule` 切 `llm`
  （`LLMSearchIntentRecognizer`，max_tokens 512）——**每次查询多一次 LLM 调用**，属成本换精度的权衡。
- **收益**：复杂/口语化 query 意图识别更准。
- **风险/代价**：延迟+token↑，需与 R2 缓存配合。
- **验证**：开/关对比 accuracy 与检索延迟。

---

## 钩子速查（文件路径）

| 钩子 | 路径 |
|---|---|
| 检索四路召回 + 打分 + 时间线 | `EchoMem/src/echomem/index_engine/engine/atomic_engine/core/recall/atom_retriever.py` |
| 检索编排（router→engine→compose 打点） | `EchoMem/src/echomem/memrouter/recall/local_recall_orchestrator.py` |
| 全局重排 + 截断 | `EchoMem/src/echomem/memrouter/recall/local_composer.py` |
| 意图识别（rule/llm） | `EchoMem/src/echomem/index_engine/engine/atomic_engine/core/recall/intent_recognizer.py` |
| 抽取管道（窗口/repair） | `EchoMem/src/echomem/index_engine/engine/atomic_engine/core/extractor/raw_atom_extractor.py` |
| 原子合并仲裁 | `EchoMem/src/echomem/index_engine/engine/atomic_engine/core/extractor/atom_merge_engine.py` |
| commit 编排 + vector_compact | `EchoMem/src/echomem/index_engine/engine/atomic_engine/application/atomic_memory_engine.py` |
| 打字期预取（缓存/门控/合并） | `EchoMem/src/echomem/req_coordinator/prefetch.py` |
| 预取与 transform 注入服务 | `EchoMem/src/echomem/req_coordinator/local_retrieval_service.py` |
| 注入格式/预算/位置 | `EchoMem/src/echomem/entrypoints/plugins/echoagent/{server.py, mapper.py, context_assembly.py, token_utils.py}` |
| embedding/模型网关 + 缓存 | `EchoMem/src/echomem/index_engine/engine/atomic_engine/infra/model_gateway_adapter.py` |
| 运行时配置（模型/召回/预取） | `EchoMem/echo_workspace/config.json`、`EchoMem/src/echomem/runtime/config.py` |
| 召回遥测（candidate 来源） | `EchoMem/echo_workspace/engines/atomic_engine/.../log/recall_log/*.jsonl`（经 getlog 拉取） |
| EchoAgent 侧注入/指标 | `EchoAgent/dev/backend/src/modules/session/{ai-handler.service.ts, session-memory-engine.service.ts, session-stream-finalizer.service.ts, metrics/metric-definitions.ts}` |
