# OpenViking + GPT-5.5 完整测试流程

**目标**: 使用 GPT-5.5 对 EchoMem 数据进行完整评测

---

## 📋 前置准备

### 1. 环境检查

```bash
# 检查 OpenViking 服务
curl <OPENVIKING_BASE_URL>/health

# 预期输出: {"status":"ok","healthy":true,...}
```

### 2. 设置 API Key

```bash
# 方法 1: 临时设置（当前终端有效）
export OPENAI_API_KEY=sk-REDACTED

# 方法 2: 写入 .env 文件（永久保存）
echo 'export OPENAI_API_KEY=sk-REDACTED' >> ~/.zshrc
source ~/.zshrc

# 验证
echo $OPENAI_API_KEY
```

---

## 🚀 测试流程

### 步骤 1: 导入数据到 OpenViking

```bash
cd <repo-root>

# 导入 conv-30 数据
<python-bin> scripts/benchmark_adapter.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir <repo-root>/runs/echomem_ov_import_$(date +%Y%m%d_%H%M%S) \
  --mode execute \
  --allow-writes \
  --memory-mode isolated_instance \
  --ov-base-url <OPENVIKING_BASE_URL> \
  --namespace echomem_gpt55_$(date +%Y%m%d) \
  --sample conv-30
```

**预期输出**:
```json
{
  "pollution_guard": {
    "write_to_openviking": true,
    "guard_reason": "ok"
  },
  "written_samples": 1,
  "status": "IMPORT_DONE"
}
```

**等待 5-10 秒让 OpenViking commit 完成**

---

### 步骤 2: 运行 VikingBot + GPT-5.5 评测

#### 选项 A: 10 题快速测试（推荐先做）

```bash
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_gpt55_10q_$(date +%Y%m%d_%H%M%S) \
  --sample conv-30 \
  --random-count 10 \
  --random-seed 42 \
  --engine openviking_memory \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --account default \
  --answer-base-url https://codexcs.ysaikeji.cn/v1 \
  --answer-model gpt-5.5 \
  --answer-token sk-REDACTED \
  --top-k 8 \
  --lexical-fallback \
  --timeout-s 180
```

**预期**:
- 耗时: 约 1 分钟
- 成本: 约 $0.06
- 输出: 10 题的详细结果

---

#### 选项 B: 完整 conv-30 测试（199 题）

```bash
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_gpt55_conv30_full_$(date +%Y%m%d_%H%M%S) \
  --sample conv-30 \
  --engine openviking_memory \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --account default \
  --answer-base-url https://codexcs.ysaikeji.cn/v1 \
  --answer-model gpt-5.5 \
  --answer-token sk-REDACTED \
  --top-k 8 \
  --lexical-fallback \
  --timeout-s 180
```

**预期**:
- 耗时: 约 18 分钟
- 成本: 约 $1.20
- 输出: 199 题的详细结果

---

#### 选项 C: 全量测试（1540 题，所有 10 个 conversation）

```bash
# 先导入所有数据
<python-bin> scripts/benchmark_adapter.py \
  --dataset dataset/locomo10.json \
  --format auto \
  --out-dir <repo-root>/runs/echomem_ov_import_all_$(date +%Y%m%d_%H%M%S) \
  --mode execute \
  --allow-writes \
  --memory-mode isolated_instance \
  --ov-base-url <OPENVIKING_BASE_URL> \
  --namespace echomem_gpt55_full_$(date +%Y%m%d)

# 等待 30 秒

# 运行全量评测
<python-bin> scripts/run_vikingbot_eval.py \
  --dataset dataset/locomo10.json \
  --out-dir <repo-root>/runs/echomem_gpt55_full_1540_$(date +%Y%m%d_%H%M%S) \
  --engine openviking_memory \
  --openviking-url <OPENVIKING_BASE_URL> \
  --workspace <openviking-workspace> \
  --account default \
  --answer-base-url https://codexcs.ysaikeji.cn/v1 \
  --answer-model gpt-5.5 \
  --answer-token sk-REDACTED \
  --top-k 8 \
  --lexical-fallback \
  --timeout-s 180 \
  --parallel 2
```

**预期**:
- 耗时: 约 2.4 小时
- 成本: 约 $9.24
- 输出: 1540 题的详细结果

---

### 步骤 3: 运行 Judge 评估

```bash
# 使用 DeepSeek-V4-Flash 作为 Judge
<python-bin> scripts/local_judge.py \
  --input <repo-root>/runs/echomem_gpt55_10q_*/vikingbot_eval.csv \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --model deepseek-v4-flash \
  --token sk-REDACTED \
  --timeout 180
```

**注意**: 将 `echomem_gpt55_10q_*` 替换为实际的输出目录名

**预期输出**:
```
Grading completed: X/10 correct, accuracy: XX.XX%
```

---

### 步骤 4: 查看结果

```bash
# 设置输出目录（替换为实际目录）
OUTPUT_DIR="<repo-root>/runs/echomem_gpt55_10q_20260531_201500"

# 查看摘要
cat $OUTPUT_DIR/summary.json

# 查看详细结果（前 20 行）
head -20 $OUTPUT_DIR/vikingbot_eval.csv

# 统计准确率
python3 -c "
import csv
with open('$OUTPUT_DIR/vikingbot_eval.csv', 'r', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
    correct = sum(1 for r in rows if r['result'] == 'CORRECT')
    wrong = sum(1 for r in rows if r['result'] == 'WRONG')
    total = len(rows)
    print(f'总题数: {total}')
    print(f'正确: {correct}/{total} ({correct/total*100:.1f}%)')
    print(f'错误: {wrong}/{total} ({wrong/total*100:.1f}%)')
"
```

---

## 📊 结果分析

### 自动生成分析报告

```bash
<python-bin> -c "
import csv
import json

# 读取结果
csv_path = '$OUTPUT_DIR/vikingbot_eval.csv'
summary_path = '$OUTPUT_DIR/summary.json'

with open(csv_path, 'r', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

with open(summary_path, 'r') as f:
    summary = json.load(f)

# 统计
correct = sum(1 for r in rows if r['result'] == 'CORRECT')
wrong = sum(1 for r in rows if r['result'] == 'WRONG')
needs_judge = sum(1 for r in rows if r['result'] not in ['CORRECT', 'WRONG'])
total = len(rows)

print('=' * 70)
print('VikingBot + GPT-5.5 测试结果')
print('=' * 70)
print()
print(f'总题数: {total}')
print(f'正确: {correct}/{total} ({correct/total*100:.1f}%)')
print(f'错误: {wrong}/{total} ({wrong/total*100:.1f}%)')
print(f'未判断: {needs_judge}/{total}')
print()
print('Token 统计:')
print(f'  总 Prompt Tokens: {summary[\"answer_prompt_tokens\"]:,}')
print(f'  总 Completion Tokens: {summary[\"answer_completion_tokens\"]:,}')
print(f'  总 Tokens: {summary[\"answer_total_tokens\"]:,}')
print(f'  平均 Tokens/题: {summary[\"answer_total_tokens\"] / total:.0f}')
print()
print('性能统计:')
print(f'  总耗时: {summary[\"duration_s\"]:.1f} 秒')
print(f'  平均耗时/题: {summary[\"duration_s\"] / total:.2f} 秒')
print()
print('成本估算 (假设 GPT-5.5 定价类似 GPT-4o):')
input_cost = summary['answer_prompt_tokens'] / 1_000_000 * 2.5
output_cost = summary['answer_completion_tokens'] / 1_000_000 * 10.0
total_cost = input_cost + output_cost
print(f'  Input 成本: \${input_cost:.4f}')
print(f'  Output 成本: \${output_cost:.4f}')
print(f'  总成本: \${total_cost:.4f}')
"
```

---

## 🔧 故障排查

### 问题 1: OpenViking 连接失败

```bash
# 检查服务
curl <OPENVIKING_BASE_URL>/health

# 如果失败，启动服务
cd /path/to/openviking
python server.py --port 1933
```

### 问题 2: API 调用失败

```bash
# 测试 API
curl -X POST https://codexcs.ysaikeji.cn/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-REDACTED" \
  -d '{
    "model": "gpt-5.5",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
  }'

# 预期: 返回正常的 JSON 响应
```

### 问题 3: 导入数据失败

```bash
# 检查 workspace 权限
ls -la <openviking-workspace>

# 检查 namespace
curl <OPENVIKING_BASE_URL>/api/v1/namespaces
```

### 问题 4: Python 路径错误

```bash
# 确认 Python 路径
which python3.12
<python-bin> --version

# 如果路径不对，修改为正确的路径
```

---

## 📝 快速测试脚本

### 一键运行 10 题测试

```bash
#!/bin/bash
# 保存为 test_gpt55.sh

set -e

# 配置
PYTHON="<python-bin>"
DATASET="dataset/locomo10.json"
OPENVIKING_URL="<OPENVIKING_BASE_URL>"
WORKSPACE="<openviking-workspace>"
API_KEY="sk-REDACTED"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "🚀 开始测试..."

# 步骤 1: 导入数据
echo "📥 步骤 1/3: 导入数据..."
$PYTHON scripts/benchmark_adapter.py \
  --dataset "$DATASET" \
  --format auto \
  --out-dir "<repo-root>/runs/echomem_ov_import_$TIMESTAMP" \
  --mode execute \
  --allow-writes \
  --memory-mode isolated_instance \
  --ov-base-url "$OPENVIKING_URL" \
  --namespace "echomem_gpt55_$TIMESTAMP" \
  --sample conv-30

echo "⏳ 等待 commit..."
sleep 5

# 步骤 2: 运行评测
echo "🤖 步骤 2/3: 运行 GPT-5.5 评测..."
OUTPUT_DIR="<repo-root>/runs/echomem_gpt55_10q_$TIMESTAMP"
$PYTHON scripts/run_vikingbot_eval.py \
  --dataset "$DATASET" \
  --out-dir "$OUTPUT_DIR" \
  --sample conv-30 \
  --random-count 10 \
  --random-seed 42 \
  --engine openviking_memory \
  --openviking-url "$OPENVIKING_URL" \
  --workspace "$WORKSPACE" \
  --answer-base-url https://codexcs.ysaikeji.cn/v1 \
  --answer-model gpt-5.5 \
  --answer-token "$API_KEY" \
  --top-k 8 \
  --lexical-fallback \
  --timeout-s 180

# 步骤 3: 运行 Judge
echo "⚖️ 步骤 3/3: 运行 Judge..."
$PYTHON scripts/local_judge.py \
  --input "$OUTPUT_DIR/vikingbot_eval.csv" \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --model deepseek-v4-flash \
  --token sk-REDACTED \
  --timeout 180

echo ""
echo "✅ 测试完成！"
echo "📁 结果目录: $OUTPUT_DIR"
```

**使用方法**:
```bash
chmod +x test_gpt55.sh
./test_gpt55.sh
```

---

## 📚 参考文档

- 完整配置指南: `<repo-root>/docs/openviking_gpt55_setup_guide.md`
- 测试报告: `<repo-root>/docs/test_final_report_with_judge_20260531.md`
- EchoMem 指南: `<repo-root>/docs/echomem_test_guide.md`

---

**最后更新**: 2026-05-31  
**模型**: GPT-5.5 (2026年4月23日发布)
