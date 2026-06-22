# Progress Report 2026-05-31 07:45

## Current Focus

继续把 LoCoMo / OpenViking / LongMemEval 评测台往可用的实验 harness 靠拢。本轮重点是视觉风格回调和结果详情可读性，不改变原来的蓝灰主色，只借鉴更克制、正式、研究平台式的信息组织。

## Completed In This Interval

1. 视觉风格回调
   - 保留原蓝灰主色，不继续引入清华紫或大面积紫色风格。
   - 侧边栏、页眉、流程条、面板、结果卡片统一为更克制的研究平台后台风格。
   - 减少渐变和装饰感，强化边框、留白、标题层级和信息分区。

2. 结果卡片可读性
   - 将结果预览卡片拆成 Judge 状态、Question、Gold、Agent Response、Judge Reasoning、Evidence。
   - Judge 按钮移到卡片头部，pending 样本更容易直接触发判定。
   - 未 Judge 的样本显示明确的 `Judge Status`，避免把未评分误解成准确率为 0。
   - 卡片 metadata 现在集中展示 sample、question、category、query time、token 估算。

3. 问题详情页增强
   - 详情页增加 Sample / Question ID / Category / Tokens 的元信息区。
   - Evidence / Relevant Memory 保持卡片化展示，避免直接暴露整段 JSON。
   - Judge Reasoning 保持显眼展示，用于定位每道题为什么正确或错误。

4. 运行状态
   - 服务地址仍为 `<WEB_BASE_URL>/`。
   - `/health` 返回正常。

## Dataset / Evaluation Status

- LoCoMo:
  - 已有 smoke run 和 formal Judge 结果。
  - 之前 smoke formal Judge 为 1/5，说明 OpenViking/Local Agent 问答路径可跑，但 LoCoMo 答案质量还需要继续优化。

- LongMemEval:
  - 已接入 100 题 local long-context 测试路径。
  - 修复 full-memory 导入后，100 题 exact reference 从 7/100 提升到 18/100。
  - clean answer 规则后，100 题 exact reference 提升到 64/100。
  - date alias / evidence boost 后，100 题 exact reference 达到 77/100。
  - 20 题 formal Judge smoke 已达到 20/20。

## Validation

- `curl <WEB_BASE_URL>/health` 正常。
- 下一步继续跑 `node --check static/app.js` 和 Python 编译检查，确认本轮 UI 改动没有脚本错误。

## Next Steps

1. 继续扩展 formal Judge 覆盖，从 LongMemEval 20 题扩大到更大的独立切片。
2. 分析 LongMemEval 100 题剩余 exact miss，优先处理时间类、数量类、聚合类问题。
3. 继续优化 Agent 对话测试台，使 relevant memory、query、answer 和只读隔离状态更清楚。
4. 做一版更清晰的 run report 入口，让用户不用在 Runs 页面里找路径。
