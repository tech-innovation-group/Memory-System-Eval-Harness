export ECHOMEM_BASE_URL=http://127.0.0.1:8010
export ECHOMEM_AUTH_KEY=ek_69595a8c65a441afb69ee94f85916024
export LOCOMO_DATASET=dataset/locomo10.json
export LOCOMO_CLIENT_STATE=./workspace/locomo-conv30-client-state
export LOCOMO_ACCOUNT=tenant_b9ff7ef7b76b
export LOCOMO_USER_ID=user_292f658fd474
export LOCOMO_AGENT_ID=default
export ANSWER_BASE_URL=https://api.deepseek.com/v1
export ANSWER_MODEL=deepseek-v4-flash
export ANSWER_TOKEN=sk-023416efc890499691741f0ab51935e7
export JUDGE_BASE_URL=https://api.deepseek.com/v1
export JUDGE_MODEL=deepseek-v4-flash
export JUDGE_TOKEN=sk-023416efc890499691741f0ab51935e7

RUN_DIR="$PWD/runs/locomo-conv30-http-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR/qa-smoke"

/c/Users/jiao/software/anaconda/anacond/envs/echomem_develop/python benchmark/locomo/echomemory/run_eval.py \
  --dataset "$LOCOMO_DATASET" \
  --out-dir "$RUN_DIR/qa-smoke" \
  --sample conv-30 \
  --questions conv-30_qa0 \
  --echomem-transport http \
  --echomem-base-url "$ECHOMEM_BASE_URL" \
  --workspace "$LOCOMO_CLIENT_STATE" \
  --account "$LOCOMO_ACCOUNT" \
  --user-id "$LOCOMO_USER_ID" \
  --agent-id "$LOCOMO_AGENT_ID" \
  --identity-mode sample_question \
  --prompt-mode one_shot \
  --retrieval-mode search \
  --evidence-policy blackbox \
  --retrieval-source-mode echo_http_native \
  --top-k 25 \
  --score-threshold 0.1 \
  --qa-memory-injection \
  --no-search-overview-enrichment \
  --no-vikingboat-tool-loop \
  --no-initial-tool-prefetch \
  --answer-base-url "$ANSWER_BASE_URL" \
  --answer-model "$ANSWER_MODEL" \
  --answer-token "$ANSWER_TOKEN" \
  --judge-base-url "$JUDGE_BASE_URL" \
  --judge-model "$JUDGE_MODEL" \
  --judge-token "$JUDGE_TOKEN" \
  --judge-every 1 \
  --qa-parallelism 1 \
  --judge-parallel 1
