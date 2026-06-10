# OpenViking + GPT-5.5 配置完成总结

**配置时间**: 2026-05-31  
**状态**: ✅ 已完成

---

## ✅ 已完成的配置

### 1. 代码配置
- ✅ `server.py`: 添加 `answer_model` 默认值为 `gpt-5.5`
- ✅ `run_vikingbot_eval.py`: 默认使用 `gpt-5.5`
- ✅ `openviking_memory_qa.py`: 支持自定义模型参数

### 2. 文档
- ✅ 完整配置指南: `/Users/chx/locomo-eval-web/docs/openviking_gpt55_setup_guide.md`
- ✅ 快速启动脚本: `/Users/chx/locomo-eval-web/test_openviking_gpt55.sh`

### 3. 默认模型
- ✅ 所有脚本默认使用 `gpt-5.5`
- ✅ 支持通过参数切换其他模型

---

## 🚀 快速开始

### 方法 1：使用快速启动脚本（推荐）

```bash
cd /Users/chx/locomo-eval-web

# 设置 API Key
export OPENAI_API_KEY=sk-your-api-key-here

# 运行 10 题快速测试
./test_openviking_gpt55.sh 10

# 或运行完整 conv-30 测试 (199 题)
./test_openviking_gpt55.sh full
```

### 方法 2：手动运行

```bash
# 1. 导入数据
/Users/chx/jiuwenclaw/bin/python3.12 scripts/benchmark_adapter.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir /Users/chx/locomo-eval-web/runs/echomem_ov_import \
  --mode execute \
  --allow-writes \
  --memory-mode isolated_instance \
  --ov-base-url http://localhost:1933 \
  --namespace echomem_gpt55_test \
  --sample conv-30

# 2. 运行评测（使用 GPT-5.5）
/Users/chx/jiuwenclaw/bin/python3.12 scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir /Users/chx/locomo-eval-web/runs/echomem_vikingbot_gpt55_10q \
  --sample conv-30 \
  --random-count 10 \
  --openviking-url http://localhost:1933 \
  --workspace /Users/chx/openviking_workspace \
  --answer-model gpt-5.5 \
  --answer-token $OPENAI_API_KEY
```

---

## 📊 预期结果

### 对比 Local Agent

| 指标 | Local Agent | VikingBot + GPT-5.5 | 提升 |
|------|-------------|---------------------|------|
| 准确率 | 30% | 60-80% | +30-50% |
| 速度 | 1.9ms | 2000ms | 1000x 慢 |
| 成本 | $0 | $0.01/题 | +$0.01 |

### 示例输出

```
=== 测试结果 ===
总题数: 10
Exact Match: 7/10 (70.0%)
需要 Judge: 3/10 (30.0%)

=== Token 统计 ===
总 Prompt Tokens: 3,250
总 Completion Tokens: 180
总 Tokens: 3,430
平均 Tokens/题: 343

=== 性能统计 ===
总耗时: 23.5 秒
平均耗时/题: 2.35 秒
平均检索数: 7.2

=== 成本估算 ===
Input 成本: $0.0081
Output 成本: $0.0018
总成本: $0.0099
```

---

## 🎛️ 模型切换

### 支持的模型

```bash
# GPT-5.5 (默认)
--answer-model gpt-5.5

# GPT-4o
--answer-model gpt-4o

# GPT-4 Turbo
--answer-model gpt-4-turbo

# Claude Opus 4
--answer-model claude-opus-4
--answer-base-url https://api.anthropic.com/v1
--answer-token $ANTHROPIC_API_KEY

# Claude Sonnet 4
--answer-model claude-sonnet-4
```

---

## 📁 输出文件

测试完成后会生成：

```
/Users/chx/locomo-eval-web/runs/echomem_vikingbot_gpt55_10q/
├── vikingbot_eval.csv          # 详细结果
├── summary.json                # 统计摘要
└── relevant_memory.json        # 检索到的记忆（如果有）
```

---

## 🔍 查看结果

```bash
# 查看 CSV
head -5 runs/echomem_vikingbot_gpt55_10q/vikingbot_eval.csv

# 查看摘要
cat runs/echomem_vikingbot_gpt55_10q/summary.json

# 统计准确率
python3 -c "
import csv
with open('runs/echomem_vikingbot_gpt55_10q/vikingbot_eval.csv', 'r') as f:
    rows = list(csv.DictReader(f))
    correct = sum(1 for r in rows if r['simple_grade'] == 'CORRECT')
    print(f'准确率: {correct}/{len(rows)} ({correct/len(rows)*100:.1f}%)')
"
```

---

## 📚 相关文档

1. **完整配置指南**: `/Users/chx/locomo-eval-web/docs/openviking_gpt55_setup_guide.md`
2. **EchoMem 测试指南**: `/Users/chx/locomo-eval-web/docs/echomem_test_guide.md`
3. **Local Agent 原理**: `/Users/chx/locomo-eval-web/docs/local_agent_no_llm_explained.md`

---

## ✅ 下一步操作

你现在可以：

1. **运行快速测试**（10 题）
   ```bash
   ./test_openviking_gpt55.sh 10
   ```

2. **运行完整测试**（199 题）
   ```bash
   ./test_openviking_gpt55.sh full
   ```

3. **对比不同模型**
   ```bash
   # GPT-5.5
   ./test_openviking_gpt55.sh 10
   
   # GPT-4o（修改脚本中的 ANSWER_MODEL）
   # 或手动运行并指定 --answer-model gpt-4o
   ```

4. **查看 Web UI**
   ```bash
   ./start.sh
   # 访问 http://127.0.0.1:19181/
   ```

---

**配置完成！可以开始测试了。** 🎉
