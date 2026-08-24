#!/usr/bin/env bash
set -Eeuo pipefail

JOB_ID="${1:-}"
HARNESS_ROOT="${HARNESS_ROOT:-/opt/memory-eval-harness-latest}"
DATA_DIR="${DATA_DIR:-/opt/memory-eval-web/data}"
RESULTS_DIR="${RESULTS_DIR:-/opt/memory-eval-harness-latest/results}"
CODEX_HOME="${CODEX_HOME:-/root/.codex}"
export CODEX_HOME

if [[ -z "$JOB_ID" ]]; then
  JOB_ID="$(
    python3 - "$DATA_DIR/jobs.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    jobs = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("")
    raise SystemExit

active = [
    job for job in jobs
    if job.get("status") in {"queued", "running"}
]
if active:
    print(active[-1].get("id", ""))
elif jobs:
    print(jobs[-1].get("id", ""))
PY
  )"
fi

if [[ -z "$JOB_ID" ]]; then
  echo "没有找到任务。用法：codex-monitor [任务ID]" >&2
  exit 2
fi

JOB_DIR="$RESULTS_DIR/$JOB_ID"
PROMPT_FILE="$(mktemp)"
trap 'rm -f "$PROMPT_FILE"' EXIT

cat >"$PROMPT_FILE" <<EOF
你是服务器评测运维助手。请只读分析任务 $JOB_ID，不要修改任何文件、代码、配置或容器。

检查范围：
- 当前任务状态、source、commit、develop 基线和 progress；
- Docker 中 memory-eval-web、memory-eval-runner、EchoMem 容器状态；
- $JOB_DIR 下的 summary.json、config.json、import_results.csv、qa_results.csv、judge_results.csv、diagnosis.json；
- container.log、backend_logs.json、echomem.logs.*、echomem.inspect.*；
- 是否存在 atom_persistence_failed、version mismatch、空召回、QA/Judge 异常、模型鉴权/限流、Docker 或 GitHub 问题。

输出简体中文，按以下格式：
1. 当前阶段和是否仍在执行
2. 代码版本是否正确
3. 注入、QA、Judge 是否完整
4. 准确率和 81 题分母
5. 空召回和原子记忆持久化异常
6. 最可能根因，区分 EchoMem、测试平台、依赖、模型服务、Docker/服务器
7. 是否需要修改 EchoMem
8. 下一步建议

证据不足时明确写“证据不足”，不要猜测。
EOF

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Codex CLI 已安装，但尚未配置 OPENAI_API_KEY。"
  echo "任务：$JOB_ID"
  echo "先执行：export OPENAI_API_KEY='你的 OpenAI API Key'"
  echo "然后重新执行：codex-monitor $JOB_ID"
  echo
  echo "本地任务状态摘要："
  python3 - "$DATA_DIR/jobs.json" "$JOB_ID" <<'PY'
import json
import sys
from pathlib import Path

try:
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"无法读取 jobs.json: {exc}")
    raise SystemExit
job = next((item for item in jobs if item.get("id") == sys.argv[2]), None)
if not job:
    print("找不到任务")
else:
    print(json.dumps({
        key: job.get(key)
        for key in (
            "id", "status", "message", "source_label", "commit_sha",
            "develop_commit_sha", "merge_commit_sha", "progress", "summary",
        )
    }, ensure_ascii=False, indent=2))
PY
  exit 3
fi

exec codex exec \
  --cd "$HARNESS_ROOT" \
  --skip-git-repo-check \
  --sandbox read-only \
  --ephemeral \
  --color never \
  <"$PROMPT_FILE"
