# EchoMemory Nano

这是一个故意做小的 `nano` 版本，用来帮助理解 EchoMemory / Temporal Graph Memory 的最小工作原理。

它只保留四步：

1. `append_turn`
2. `extract_atoms`
3. `build_graph`
4. `search`

对应大系统里的抽象：

- `turns` ~= session stream / `messages.jsonl`
- `atoms` ~= `.structured/atoms`
- `graph_nodes` ~= `fact / event / entity`
- `search()` ~= 最简化 retrieval

## 如果只看一个文件

优先看：

- `nano_minimal_stream_dual_backbone.py`
  - 新的最小教学版，故意只保留六个概念：
    `append-only stream`、`atom extraction`、`three-clock time`、
    `temporal tree`、`relation graph`、`readiness + planner`。
  - 这个文件的目标不是做 benchmark，而是让人 5 分钟内看懂：
    为什么 EchoMemory 不该被理解成“一个大向量库”，
    为什么时间题和关系题要走不同 backbone，
    以及为什么 `qa_ready` 是 correctness 约束。
  - 如果你是第一次给别人解释这条论文线，建议从它开始，再往下看
    `nano_canonical_echomemory_tg.py` 和 `nano_paper_method_tgmm.py`。

## 文件

- `nano_minimal_stream_dual_backbone.py`
  - 新的最小入口文件。
  - 它比 `canonical` 更短，比 `whiteboard` 更完整。
  - 只演示：
    `stream -> atoms -> temporal_tree + relation_graph -> planned retrieval`。
- `nano_temporal_graph.py`
  - 第一版极简实现。
  - 重点解释 `session -> atoms -> fact/event/entity -> retrieval`。
- `nano_whiteboard_echomemory.py`
  - 新的最短白板版。
  - 只保留五件事：
    `append-only messages`、`extract atoms`、`temporal tree`、`relation graph`、`tree-first / graph-first routing`。
  - 如果你想第一次给别人解释“为什么 EchoMemory 不等于一个大向量库”，先看这个。
- `nano_stream_graph_memory.py`
  - 第二版 tiny prototype。
  - 比第一版多了 `edge`、`query planner`、`temporal_next`，更接近现在真实 `echomem` 想走的方向。
- `nano_multimodal_temporal_graph.py`
  - 第三版 tiny prototype。
  - 用最小代价解释“截图/图像证据怎么接进 temporal graph”。
  - 重点是 `image_evidence` 节点、`OCR/tag/caption`、以及 visual query planner。
- `nano_readiness_temporal_graph.py`
  - 第四版 tiny prototype。
  - 更贴近现在 EchoMemory 的真实问题：`readiness gate`、`story time vs write time`、`graph-first temporal retrieval`。
- `nano_canonical_echomemory_tg.py`
  - 新的 canonical 单文件版。
  - 把现在真实 EchoMemory 最关键的四件事揉到一起：
    `append-only stream`、`story time`、`query-time anchor`、
    `temporal_tree`、`qa_ready gate`、`graph-first retrieval`。
  - 如果你只想先看一个最像真实系统主结构的 nano，先看这个。
- `nano_unified_mm_tg.py`
  - 新的 unified 单文件版。
  - 在 canonical 版基础上再把多模态和研究叙事也揉进来：
    `text stream`、`image evidence`、`story time vs mention time`、
    `qa readiness`、`temporal graph retrieval`、`visual question routing`。
  - 如果你想一口气看懂 “text-first TG” 和 “未来 CVPR 的 MM 方向” 怎么连起来，先看这个。
- `nano_paper_method_tgmm.py`
  - 新的“论文方法节”单文件版。
  - 专门把五件事揉在一起：
    `three-clock time`、`typed blocks`、`temporal graph`、
    `image_evidence`、`readiness gate`。
  - 如果你想给别人解释 “EchoMemory-TG / EchoMemory-MM 论文里的方法长什么样”，先看这个。
- `nano_paper_method_tgmm_ablation.py`
  - 对论文方法节 nano 做小型 ablation。
  - 对比 `flat facts only`、`typed blocks only`、`full TG+MM`。
- `nano_graph_first_ablation.py`
  - 对 planner 做小型 ablation。
  - 对比 `lexical baseline`、`graph-first`、`graph-path`。
  - 重点解释：为什么 temporal / relational / visual query 不能只靠文本匹配，而应该让 graph 成为主证据入口。
- `nano_explicit_planner_tg.py`
  - 把 `Planner / Retriever / EvidenceComposer` 三层显式拆开。
  - 重点不是提分，而是把主仓未来该怎么从 `SearchService` 中拆出来讲清楚。
- `nano_explicit_planner_ablation.py`
  - 比较“混合式检索”与“显式 planner 分层”。
  - 重点解释：显式 planner 不是架构洁癖，而是会改变 temporal / relation 题的证据路径。
- `nano_modular_maincode_upgrade.py`
  - 新的 modular 升级版单文件实现。
  - 刻意模拟未来主代码拆层后的边界：
    `IntentClassifier`、`QueryPlanner`、`TemporalTreeReader`、
    `GraphReader`、`AtomReader`、`EvidenceFusion`、`SelfCheckPolicy`、
    `SearchOrchestrator`。
  - 如果你想理解“为什么下一步该把 SearchService 拆开，以及最小合理拆法是什么”，先看这个。
- `nano_tree_graph_dual_backbone.py`
  - 新的 dual-backbone 单文件版。
  - 把 `stream memory`、`temporal tree`、`relation graph`、`image evidence`
    放进同一个最小系统里。
  - 重点解释：下一步 EchoMemory 更像应该演化成
    `tree + graph` 双主干记忆，而不只是“在向量检索旁边再补一点 graph”。
- `nano_dual_backbone_teaching.py`
  - 新的教学版单文件实现。
  - 刻意比 benchmark 脚本更短、更线性，只保留：
    `append-only messages`、`atoms`、`temporal tree`、`relation graph`、
    `planner`、`readiness gate`。
  - 如果你想给别人解释“dual-backbone 到底怎么工作”，先看这个。
- `nano_dual_backbone_ablation.py`
  - 对 dual-backbone 方案做最直接的小型 ablation。
  - 对比 `tree-only`、`graph-only`、`dual-backbone`。
  - 重点解释：时间树和关系图解决的是不同失败模式，二者组合比单独使用更稳。
- `nano_dual_backbone_selfcheck_v2.py`
  - 新的 dual-backbone v2 单文件版。
  - 在 `tree + graph + readiness gate` 之上，再补一个更像真实系统的
    `retrieval self-check`：
    先走 primary backbone，再检查证据形状；若不够，再补 supporting backbone；
    还是不够，就答 `unknown`。
  - 如果你想给别人解释“为什么 retrieval 之后还需要 answer-time policy”，先看这个。
- `nano_graph_second_pass_contract_ablation.py`
  - 一个更贴近当前主仓改动的小型 method ablation。
  - 不测“整体 QA 准确率是不是都提高了”，而是更窄地测：
    当 planner 要求 temporal query 同时具备 `temporal_tree + event` 证据时，
    `self-check` 触发的一次 `graph second-pass` 能不能把证据 contract 补完整。
  - 如果你想把最近主仓里加的 `self-check -> graph second-pass` 讲清楚，先看这个。
- `nano_coverage_aware_gating_ablation.py`
  - 一个更贴近当前主仓 `coverage-aware gating` 改动的小型 ablation。
  - 对比两种策略：
    `confidence-only gating` 和 `coverage-aware gating`。
  - 重点不是表面关键词命中，而是当 primary backbone 已经给出高分命中时，
    系统是否会因为“分高”而过早停止，导致 planned evidence contract 仍不完整。
  - 如果你想把最近主仓里加的 `coverage_gap` / `missing_types` 逻辑讲清楚，先看这个。
- `nano_type_aware_second_pass_ablation.py`
  - 一个更贴近当前主仓 `missing_types -> second pass sources` 改动的小型 ablation。
  - 对比三种策略：
    `one_pass`、`graph-only second pass`、`type-aware second pass`。
  - 重点不是“有没有做 second pass”，而是：
    当 contract 缺的是 `event`、`fact`、`temporal_tree`、`image_evidence` 等不同层时，
    系统能不能补对 reader，而不是永远只补 graph。
  - 如果你想把最近主仓里加的 type-aware supporting retrieval 讲清楚，先看这个。
- `nano_multimodal_contract_ablation.py`
  - 一个更像 CVPR 路线需要的 visual evidence ablation。
  - 对比两种策略：
    `one-pass multimodal retrieval` 和 `contract-aware multimodal retrieval`。
  - 重点不是“图像关键词有没有命中”，而是：
    当视觉题需要 `image_evidence + entity`、`image_evidence + fact`、`image_evidence + event`
    这些不同证据组合时，系统能不能补齐 owner / linked fact / linked event，
    而不是只停在一个看起来相关的 screenshot hit 上。
  - 如果你想给别人解释“为什么 image evidence 要成为一等记忆对象，而不只是 OCR 附件”，先看这个。
- `nano_anchored_temporal_ablation.py`
  - 一个更聚焦的时间题小型 ablation。
  - 只测带 `query_time` 的 anchored temporal queries。
  - 对比 `tree-only`、`graph-only`、`dual-backbone` 在相对时间题上的稳定性。
- `nano_three_clock_temporal_ablation.py`
  - 一个更聚焦的时间语义小型 ablation。
  - 对比三种记忆设计：
    `write-time only`、`event+mention split`、`three-clock`.
  - 重点不是有没有查到关键词，而是：
    当 query 明确在问“事情真正发生在什么时候”时，
    系统会不会把 `write_time / mention_time / event_time` 混成一个字段。
  - 如果你想给别人解释“为什么时间字段不能只留一个 created_at”，先看这个。
- `nano_generalizable_stream_graph_contract.py`
  - 新的 generalized 方法原型单文件版。
  - 刻意把四个最关键的结构性改进揉到一起：
    `three-clock time`、`temporal tree`、`relation graph`、
    `contract-driven second pass`。
  - 重点不是刷某个数据集，而是验证：
    当 query family 不同时，系统能不能先走对 primary backbone，
    再根据缺失证据类型补对 supporting reader。
  - 如果你想给别人解释“哪些改动是泛化 memory 改进，而不是 benchmark hack”，先看这个。
- `nano_memory_os_dual_backbone.py`
  - 新的统一方法 nano。
  - 把现在最像“论文方法节主干”的机制揉在同一个文件里：
    `write governance`、`three-clock time`、`readiness receipt`、
    `active/superseded lifecycle`、`temporal tree`、`relation graph`、
    `contract-aware second pass`。
  - 它的定位不是取代更早的教学版，而是回答：
    如果现在要把 EchoMemory 的核心方法压成一个最小 yet coherent 的系统，
    最少要保留哪些结构，才能既解释清楚，也能做一个小实验。
  - 如果你想给别人解释“从 LongMemEval / MemOS / RAPTOR / GraphReader / Self-RAG
    最后到底落成了什么代码骨架”，先看这个。
- `nano_reference_impl_v14.py`
  - 单文件参考实现。
  - 它比 `nano_memory_os_dual_backbone.py` 更像“给新读者看的干净入口”：
    只保留 `stream`、`atom`、`topic_dossier`、`temporal_tree`、`graph`、`readiness`、
    `contract-aware second pass` 七个核心概念。
  - 如果你想先读一版最清楚的骨架，再看其他实验脚本，这个最合适。
- `nano_reference_impl_v17.py`
  - 新的统一参考实现。
  - 它比 `v14/v15/v16` 更收束，专门把七个关键层放进一个干净入口：
    `stream`、`atoms`、`topic dossier`、`temporal blocks`、`graph`、
    `readiness`、`contract-aware retrieval`。
  - 这版刻意不针对任何 benchmark 写关键词模版，而是只保留 query family
    级别的路由和证据约束。
  - 如果你想给别人解释“EchoMemory 最后到底应该长成什么样”，先看这个。
- `nano_v17_core_ablation.py`
  - 新的 v17 对齐 ablation。
  - 只测两条最核心的结构性问题：
    `three-clock time` 和 `topic dossier`。
  - 三个变体分别是：
    `full_v17`、`write_time_only`、`atom_only_no_dossier`。
  - 它的意义不是再做一套 benchmark，而是用最小实验直接说明：
    如果把时间塌成 write time，temporal / retrospective 题会坏；
    如果拿掉中层 dossier，longitudinal 题会坏。
- `nano_topic_dossier_ablation.py`
  - 新的中层 topic memory ablation。
  - 专门解释一个现在主仓还没有被单独讲透的问题：
    当同一主题跨多个 session 持续更新时，
    只有 `global overview` 和 `flat atoms` 往往都不够，
    还需要一个 topic-centered 中层对象，也就是 `topic dossier`。
  - 对比三种模式：
    `overview_only`、`atom_only`、`topic_dossier`。
  - 重点不是刷某个 benchmark，而是验证：
    中层 topic memory 能否在 longitudinal / mixed-topic query 上
    保持同一主题的 timeline coherence。
  - 如果你想给别人解释“overview 和 episode 之间还缺什么”，先看这个。
- `nano_relation_backbone_ablation.py`
  - 一个和 temporal ablation 成对的关系题小型 ablation。
  - 只测 relation-heavy questions。
  - 对比 `tree-only`、`graph-only`、`dual-backbone` 在关系题上的稳定性。
- `nano_readiness_ablation_experiment.py`
  - 对第四版 tiny prototype 做小型 ablation。
  - 对比 `baseline`、`temporal_graph`、`full` 三套系统。

## 运行

```bash
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_temporal_graph.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_whiteboard_echomemory.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_stream_graph_memory.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_temporal_graph.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_temporal_graph.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_unified_mm_tg.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_experiment.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_graph_first_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_tg.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_modular_maincode_upgrade.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_teaching.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_selfcheck_v2.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_graph_second_pass_contract_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_coverage_aware_gating_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_type_aware_second_pass_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_contract_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_three_clock_temporal_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_generalizable_stream_graph_contract.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_memory_os_dual_backbone.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_topic_dossier_ablation.py
python3 /Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation.py
```

运行后会生成：

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_demo_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_whiteboard_echomemory_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_stream_graph_demo_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_demo_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_temporal_graph_demo_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_canonical_echomemory_tg_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_unified_mm_tg_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_report.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm_ablation_report.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_graph_first_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_graph_first_ablation_report.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_tg_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_tg_report.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_explicit_planner_ablation_report.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_modular_maincode_upgrade_output.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_modular_maincode_upgrade_20260614.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone_output.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_tree_graph_dual_backbone_report.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_teaching_output.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_teaching_20260613.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_ablation_report.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_selfcheck_v2_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_selfcheck_v2_20260614.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_graph_second_pass_contract_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/echomemory_nano_graph_second_pass_contract_ablation_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_coverage_aware_gating_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_coverage_aware_gating_ablation_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_type_aware_second_pass_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_type_aware_second_pass_ablation_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_contract_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_multimodal_contract_ablation_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_anchored_temporal_ablation_20260614.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_three_clock_temporal_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_three_clock_temporal_ablation_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_generalizable_stream_graph_contract_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_generalizable_stream_graph_contract_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_memory_os_dual_backbone_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_memory_os_dual_backbone_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_topic_dossier_ablation_results.json`
- `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_topic_dossier_ablation_20260615.html`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_results.json`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_relation_backbone_ablation_report.html`

## 第一版 nano 故意没有做的事

- 没有真实 LLM 抽取
- 没有 vector retrieval
- 没有多 session cursor
- 没有 graph edge diffusion
- 没有复杂 planner

## 第二版 tiny prototype 额外解释的东西

- 为什么需要 `event -> fact -> entity` 这几类边
- 为什么时间题更应该优先打到 `event` 层
- 为什么后续会需要一个 recall planner，而不是所有 query 都一把梭搜全文

## 第三版 multimodal tiny prototype 额外解释的东西

- 为什么 CVPR 路线必须把图像/截图变成一等记忆对象
- 为什么 visual query 不能只靠 text memory
- 为什么 `image_evidence -> event/fact/entity` 的连接关系很重要
- 为什么 OCR / caption / tags 可以作为最简单的视觉记忆载体

## 这个 nano 版最适合拿来看什么

- 为什么 EchoMemory 不是“把对话直接塞向量库”
- 为什么 temporal graph 比纯 atom 更容易解释时间题
- `fact / event / entity` 这三类节点各自做什么
- `image_evidence` 节点在未来 multimodal temporal graph 里做什么
- 为什么 “消息已经写进去” 不等于 “现在就适合 QA”
- 为什么 relative time 应该在 memory 层就解析成 story time
- 为什么 `event_time / mention_time / write_time` 应该分开保存，而不是只留一个时间戳
- 为什么 `temporal_next` 这类边必须真的进入检索主流程
- 为什么只靠 graph 还不够，还需要一个更适合时间导航的 temporal tree
- 为什么 `messages_persisted -> atoms_ready -> graph_ready -> qa_ready`
  这条状态推进，才是长期记忆系统而不只是“能写文件”的关键
- 为什么 `dual-backbone` 之后还不够，还需要一个 `retrieval self-check`
  来决定：直接答、补支持证据，还是返回 `unknown`
