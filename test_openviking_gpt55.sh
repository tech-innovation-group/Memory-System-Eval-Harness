#!/bin/bash
# OpenViking + GPT-5.5 快速测试脚本
# 用法: ./test_openviking_gpt55.sh [10|full]

set -e

# 配置
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON_BIN:-python3}"
DATASET="${LOCOMO_DATA:-$ROOT/dataset/locomo10.json}"
OPENVIKING_URL="${OPENVIKING_URL:-http://localhost:1933}"
WORKSPACE="${OPENVIKING_WORKSPACE:-$ROOT/workspace/openviking_workspace}"
ANSWER_MODEL="${ANSWER_MODEL:-gpt-5.5}"
SAMPLE="conv-30"

# 检查 API Key
if [ -z "$OPENAI_API_KEY" ]; then
    echo "❌ 错误: OPENAI_API_KEY 未设置"
    echo "请运行: export OPENAI_API_KEY=sk-your-key"
    exit 1
fi

# 检查 OpenViking 服务
echo "🔍 检查 OpenViking 服务..."
if ! curl -s "$OPENVIKING_URL/health" > /dev/null 2>&1; then
    echo "❌ 错误: OpenViking 服务未运行"
    echo "请启动服务: cd /path/to/openviking && python server.py --port 1933"
    exit 1
fi
echo "✅ OpenViking 服务正常"

# 确定测试规模
TEST_SIZE="${1:-10}"
if [ "$TEST_SIZE" = "10" ]; then
    RANDOM_COUNT="--random-count 10"
    OUT_DIR="$ROOT/runs/echomem_vikingbot_gpt55_10q_$(date +%Y%m%d_%H%M%S)"
    echo "📊 运行 10 题快速测试"
elif [ "$TEST_SIZE" = "full" ]; then
    RANDOM_COUNT=""
    OUT_DIR="$ROOT/runs/echomem_vikingbot_gpt55_conv30_full_$(date +%Y%m%d_%H%M%S)"
    echo "📊 运行完整 conv-30 测试 (199 题)"
else
    echo "❌ 错误: 参数必须是 '10' 或 'full'"
    echo "用法: $0 [10|full]"
    exit 1
fi

echo ""
echo "=== 配置信息 ==="
echo "数据集: $DATASET"
echo "模型: $ANSWER_MODEL"
echo "OpenViking: $OPENVIKING_URL"
echo "输出目录: $OUT_DIR"
echo ""

# 步骤 1: 导入数据（如果需要）
NAMESPACE="echomem_gpt55_$(date +%Y%m%d)"
echo "📥 步骤 1/3: 检查数据导入状态..."

# 检查 namespace 是否已存在
if curl -s "$OPENVIKING_URL/api/v1/namespaces" | grep -q "$NAMESPACE"; then
    echo "✅ 数据已导入到 namespace: $NAMESPACE"
else
    echo "📥 导入数据到 OpenViking..."
    $PYTHON scripts/benchmark_adapter.py \
        --dataset "$DATASET" \
        --format auto \
        --out-dir "/Users/chx/locomo-eval-web/runs/echomem_ov_import_$(date +%Y%m%d_%H%M%S)" \
        --mode execute \
        --allow-writes \
        --memory-mode isolated_instance \
        --ov-base-url "$OPENVIKING_URL" \
        --namespace "$NAMESPACE" \
        --sample "$SAMPLE"

    echo "⏳ 等待 commit 完成..."
    sleep 5
    echo "✅ 数据导入完成"
fi

echo ""

# 步骤 2: 运行 VikingBot 评测
echo "🤖 步骤 2/3: 运行 VikingBot + GPT-5.5 评测..."
$PYTHON scripts/run_vikingbot_eval.py \
    --dataset "$DATASET" \
    --out-dir "$OUT_DIR" \
    --sample "$SAMPLE" \
    $RANDOM_COUNT \
    --openviking-url "$OPENVIKING_URL" \
    --workspace "$WORKSPACE" \
    --account default \
    --answer-base-url "https://api.openai.com/v1" \
    --answer-model "$ANSWER_MODEL" \
    --answer-token "$OPENAI_API_KEY" \
    --top-k 8 \
    --timeout-s 120

echo ""
echo "✅ 评测完成！"
echo ""

# 步骤 3: 分析结果
echo "📊 步骤 3/3: 分析结果..."
$PYTHON -c "
import csv
import json

csv_path = '$OUT_DIR/vikingbot_eval.csv'
summary_path = '$OUT_DIR/summary.json'

# 读取结果
with open(csv_path, 'r', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

with open(summary_path, 'r', encoding='utf-8') as f:
    summary = json.load(f)

# 统计
correct = sum(1 for r in rows if r['simple_grade'] == 'CORRECT')
needs_judge = sum(1 for r in rows if r['simple_grade'] == 'NEEDS_JUDGE')
total = len(rows)

print('=== 测试结果 ===')
print(f'总题数: {total}')
print(f'Exact Match: {correct}/{total} ({correct/total*100:.1f}%)')
print(f'需要 Judge: {needs_judge}/{total} ({needs_judge/total*100:.1f}%)')
print()
print('=== Token 统计 ===')
print(f'总 Prompt Tokens: {summary[\"answer_prompt_tokens\"]:,}')
print(f'总 Completion Tokens: {summary[\"answer_completion_tokens\"]:,}')
print(f'总 Tokens: {summary[\"answer_total_tokens\"]:,}')
print(f'平均 Tokens/题: {summary[\"answer_total_tokens\"] / total:.0f}')
print()
print('=== 性能统计 ===')
print(f'总耗时: {summary[\"duration_s\"]:.1f} 秒')
print(f'平均耗时/题: {summary[\"duration_s\"] / total:.2f} 秒')
print(f'平均检索数: {summary[\"avg_retrieval_count\"]:.1f}')
print()
print('=== 成本估算 ===')
input_cost = summary['answer_prompt_tokens'] / 1_000_000 * 2.5
output_cost = summary['answer_completion_tokens'] / 1_000_000 * 10.0
total_cost = input_cost + output_cost
print(f'Input 成本: \${input_cost:.4f}')
print(f'Output 成本: \${output_cost:.4f}')
print(f'总成本: \${total_cost:.4f}')
print()
print(f'📁 结果文件: {csv_path}')
print(f'📊 摘要文件: {summary_path}')
"

echo ""
echo "🎉 测试完成！"
echo ""
echo "下一步:"
echo "1. 查看详细结果: cat $OUT_DIR/vikingbot_eval.csv"
echo "2. 对比 Local Agent: 查看 /Users/chx/locomo-eval-web/runs/echomem_test_10q/"
echo "3. 运行 Judge: python scripts/local_judge.py --input $OUT_DIR/vikingbot_eval.csv"
