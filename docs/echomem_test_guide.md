# EchoMemory 测试指南

这份文档只保留当前还真实有效的内容。

它回答四件事：

1. 怎么把 EchoMemory 接进测试平台
2. 怎么在界面里跑 `LongMemEval` 和 `EvolvingEvents`
3. 当前哪些结果已经真实跑过
4. 当前平台和 EchoMemory 0.1.0 的限制在哪里

## 1. 接入方式

`OpenViking` 和 `EchoMemory` 的接法不同。

### OpenViking

- 服务地址 + 端口接入

### EchoMemory

- 本地 SDK 接入
- 不是“给一个服务端口就行”

EchoMemory 需要：

- `ECHOMEM_ROOT`
- `ECHOMEM_PYTHON` 或 `$ECHOMEM_ROOT/.venv/bin/python`
- `ECHOMEM_WORKSPACE`
- `ECHOMEM_ACCOUNT`
- `ECHOMEM_USER_ID`
- `ECHOMEM_AGENT_ID`

平台会直接调用 EchoMemory 本地 SDK，而不是自己重写一套记忆检索逻辑。

兼容接口默认按下面这组判断：

- `open_runtime(...)`
- `EchoMemSDK(...)`
- `create_session(...)`
- `add_message(...)`
- `commit_session(...)`
- `find(...)`
- `search(...)`

## 2. 如果别人是新拉的 EchoMemory 代码

先在对方机器上准备 EchoMemory 源码目录，例如：

```bash
git clone <their-echo-memory-repo> /absolute/path/to/echo_memory
cd /absolute/path/to/echo_memory
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

推荐至少满足下面任一目录结构：

- 根目录下有 `pyproject.toml` 和 `echomem/`
- 或根目录下有 `packages/echomem/src` 和 `packages/echofs/src`

然后在测试平台目录：

```bash
cp env.echomem.example .env.local
source .env.local
./preflight.sh
./start.sh
```

关键配置：

- `ECHOMEM_ROOT=/absolute/path/to/echo_memory`
- `ECHOMEM_PYTHON=/absolute/path/to/python`（如果不是默认 `.venv/bin/python`）
- `ECHOMEM_WORKSPACE=/absolute/path/to/workspace`
- `ECHOMEM_ACCOUNT=default`
- `ECHOMEM_USER_ID=default`
- `ECHOMEM_AGENT_ID=default`

## 3. 数据集现状

### 仓内可直接跑

- `dataset/longmemeval.sample.json`
- `dataset/evolvingevents.sample.json`

### 仓内 full 状态

- `dataset/full/longmemeval_s_cleaned.json`：存在
- `dataset/full/evolvingevents.json`：当前不存在

所以：

- `LongMemEval` 可以直接做真实 full 样本测试
- `EvolvingEvents` 当前仓里只能直接代表 sample

如果拿到上游 `chunks.json + qa_pairs.json`，可以转成：

```bash
python3 scripts/prepare_evolvingevents_full.py \
  --chunks /path/to/chunks.json \
  --qa /path/to/qa_pairs.json \
  --out dataset/full/evolvingevents.json
```

## 4. 界面里怎么跑

### LongMemEval

建议顺序：

1. 打开 `系统配置`
2. 选择 `EchoMemory`
3. 填好 EchoMemory 根目录、workspace、账号、模型
4. 打开 `LongMemEval 评测`
5. 先跑少量题，或指定单个 sample / questions
6. 跑完后再 `Judge`
7. 导出 HTML 报告

不要第一次就直接跑所有题。

原因不是平台偷懒，而是 `EchoMemory 0.1.0` 的长样本后处理会很慢。

### EvolvingEvents

当前建议：

1. 先跑 sample
2. 确认链路通了
3. 如果要 formal full，再补 `dataset/full/evolvingevents.json`

## 5. 已经真实跑过的证据

### EvolvingEvents sample

- run: `echomemory_generic_qa_20260615_155307_567cd6`
- 结果：`2/2 = 100%`

### LongMemEval full 单题

- run: `echomemory_generic_qa_20260615_130316_1b05ee`
- 结果：`0/1`
- 例子：标准答案 `$400,000`，模型回答 `$350,000`

### LongMemEval full 3 题复跑

- run: `echomemory_generic_qa_20260615_155532_1df346`
- 第一题导入 `550` 条消息后，长时间停在 `atom_extraction / commit:indexing`
- 这是当前最关键的后端瓶颈证据

## 6. 当前平台已经修过的问题

为了让结果更像正式 benchmark，而不是“看上去在跑”：

- 长任务进度已显示真实阶段，如 `commit:atom_extraction`、`commit:indexing`
- 用户停止任务后，状态不再误写成普通失败
- 普通日志不再被误判成 `401/API error`
- 报告页面已明确区分 `sample` 和 `full`

## 7. 当前仍然存在的限制

### 平台侧

- 还有很多历史命名仍然带 `LoCoMo`
- 一些 launch-kit / readiness 文案仍偏 LoCoMo 口径

### EchoMemory 0.1.0 后端侧

- 长样本 `atom_extraction` 很慢
- 会出现 `Atomic extraction output appears truncated`
- full LongMemEval 还不能视为已经稳定跑完

## 8. 当前最有用的报告

主报告：

- `generated-reports/echomemory_v010_longmemeval_evolvingevents_20260615.html`

可以直接在浏览器打开，也可以通过本地服务访问。
