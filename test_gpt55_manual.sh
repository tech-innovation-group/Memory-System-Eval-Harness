#!/bin/bash
# OpenViking + GPT-5.5 快速测试脚本
# 用法: ./test_gpt55_manual.sh [10|199|1540]

set -e

# 配置
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON_BIN:-python3}"
DATASET="${LOCOMO_DATA:-$ROOT/dataset/locomo10.json}"
OPENVIKING_URL="${OPENVIKING_URL:-http://localhost:1933}"
WORKSPACE="${OPENVIKING_WORKSPACE:-$ROOT/workspace/openviking_workspace}"
API_KEY="${OPENVIKING_API_KEY:-}"
JUDGE_API_KEY="${JUDGE_API_KEY:-${OPENAI_API_KEY:-}}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ -z "$JUDGE_API_KEY" ]; then
    echo "❌ 错误: JUDGE_API_KEY 或 OPENAI_API_KEY 未设置"
    exit 1
fi

# 确定测试规模
TEST_SIZE="${1:-10}"
if [ "$TEST_SIZE" = "10" ]; then
    RANDOM_COUNT="--random-count 10"
    SAMPLE="--sample conv-30"
    OUT_DIR="$ROOT/runs/echomem_gpt55_10q_$TIMESTAMP"
    echo "📊 运行 10 题快速测试"
elif [ "$TEST_SIZE" = "199" ]; then
    RANDOM_COUNT=""
    SAMPLE="--sample conv-30"
    OUT_DIR="$ROOT/runs/echomem_gpt55_conv30_full_$TIMESTAMP"
    echo "📊 运行完整 conv-30 测试 (199 题)"
elif [ "$TEST_SIZE" = "1540" ]; then
    RANDOM_COUNT=""
    SAMPLE=""
    OUT_DIR="$ROOT/runs/echomem_gpt55_full_1540_$TIMESTAMP"
    echo "📊 运行全量测试 (1540 题)"
else
    echo "❌ 错误: 参数必须是 '10', '199' 或 '1540'"
    echo "用法: $0 [10|199|1540]"
    exit 1
fi

echo ""
echo "=== 配置信息 ==="
echo "数据集: $DATASET"
echo "模型: gpt-5.5"
echo "OpenViking: $OPENVIKING_URL"
echo "输出目录: $OUT_DIR"
echo ""

# 检查 OpenViking 服务
echo "🔍 检查 OpenViking 服务..."
if ! curl -s "$OPENVIKING_URL/health" > /dev/null 2>&1; then
    echo "❌ 错误: OpenViking 服务未运行"
    echo "请启动服务: cd /path/to/openviking && python server.py --port 1933"
    exit 1
fi
echo "✅ OpenViking 服务正常"
echo ""

# 步骤 1: 导入数据
NAMESPACE="echomem_gpt55_$TIMESTAMP"
echo "📥 步骤 1/3: 导入数据到 namespace: $NAMESPACE"
$PYTHON "$ROOT/scripts/benchmark_adapter.py" \
  --dataset "$DATASET" \
  --format auto \
  --out-dir "$ROOT/runs/echomem_ov_import_$TIMESTAMP" \
  --mode execute \
  --allow-writes \
  --memory-mode isolated_instance \
  --ov-base-url "$OPENVIKING_URL" \
  --namespace "$NAMESPACE" \
  $SAMPLE

echo "⏳ 等待 commit 完成..."
sleep 5
echo "✅ 数据导入完成"
echo ""

# 步骤 2: 运行评测
echo "🤖 步骤 2/3: 运行 VikingBot + GPT-5.5 评测..."
$PYTHON "$ROOT/scripts/run_vikingbot_eval.py" \
  --dataset "$DATASET" \
  --out-dir "$OUT_DIR" \
  $SAMPLE \
  $RANDOM_COUNT \
  --random-seed 42 \
  --engine openviking_memory \
  --openviking-url "$OPENVIKING_URL" \
  --workspace "$WORKSPACE" \
  --account default \
  --answer-base-url https://codexcs.ysaikeji.cn/v1 \
  --answer-model gpt-5.5 \
  --answer-token "$API_KEY" \
  --top-k 8 \
  --lexical-fallback \
  --timeout-s 180 \
  --parallel 1

echo ""
echo "✅ 评测完成！"
echo ""

# 步骤 3: 运行 Judge
echo "⚖️ 步骤 3/3: 运行 Judge 评估..."
$PYTHON "$ROOT/scripts/local_judge.py" \
  --input "$OUT_DIR/vikingbot_eval.csv" \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --model deepseek-v4-flash \
  --token "$JUDGE_API_KEY" \
  --timeout 180

echo ""
echo "🎉 测试完成！"
echo ""
echo "📁 结果文件: $OUT_DIR/vikingbot_eval.csv"
echo "📊 摘要文件: $OUT_DIR/summary.json"
echo ""
echo "查看结果:"
echo "  cat $OUT_DIR/summary.json"
echo "  head -20 $OUT_DIR/vikingbot_eval.csv"
