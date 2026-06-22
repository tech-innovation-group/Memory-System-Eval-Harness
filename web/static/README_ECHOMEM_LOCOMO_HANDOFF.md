# EchoMemory 接入与界面测试说明

这份文档只保留当前真实可用的流程。

## 1. 接入边界

- `OpenViking` 走服务地址 + 端口。
- `EchoMemory` 不走独立服务端口，当前平台是直接调用本地 SDK。
- 只要 EchoMemory fork 还保留兼容的 SDK 接口，平台通常不用单独重写检索逻辑。

当前平台实际会调用这些接口：

- `open_runtime(...)`
- `EchoMemSDK(...)`
- `sdk.create_session(...)`
- `sdk.add_message(...)`
- `sdk.commit_session(...)`
- `sdk.find(...)`
- `sdk.search(...)`

## 2. 启动平台

先复制环境模板，然后启动本地服务：

```bash
cp env.echomem.example .env.local
source .env.local
./preflight.sh
./start.sh
```

默认地址：

```text
http://127.0.0.1:19181/
```

## 3. 别人新拉的 EchoMemory 怎么接进来

如果别人拿到的是一份新的 EchoMemory 代码，不是复用你本机已有目录，按下面做：

1. 先 clone 到本地目录：

```bash
git clone <their-echo-memory-repo> /absolute/path/to/echo_memory
```

2. 在那份 EchoMemory 根目录准备 Python 环境：

```bash
cd /absolute/path/to/echo_memory
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

3. 回到评测平台目录，填写：

- `ECHOMEM_ROOT`
- `ECHOMEM_WORKSPACE`
- `ECHOMEM_ACCOUNT`
- `ECHOMEM_USER_ID`
- `ECHOMEM_AGENT_ID`
- `ECHOMEM_CHAT_BASE_URL`
- `ECHOMEM_CHAT_MODEL`
- `ECHOMEM_CHAT_API_KEY`
- `JUDGE_BASE_URL`
- `JUDGE_MODEL`
- `JUDGE_TOKEN`

4. 如果不是用 `$ECHOMEM_ROOT/.venv/bin/python`，再补：

```bash
export ECHOMEM_PYTHON=/absolute/path/to/python
```

5. 再启动平台：

```bash
source .env.local
./preflight.sh
./start.sh
```

## 4. 页面里怎么跑一轮

推荐第一次先跑一个小闭环：`conv-30 + 小样本 QA + Judge + 报告`。

1. 进入 `系统配置`，把记忆后端切到 `EchoMemory`。
2. 填好 `EchoMemory 源码根目录`、`Memory User ID`、`Memory Agent ID`。
3. 填好 `Agent 模型`、`判分模型`、`记忆注入模型`。这些配置会按当前账户保存在浏览器里。
4. 回到 `LoCoMo 评测`，先选 `conv-30`，点 `导入所选对话`。
5. 导入结束后点一次 `检查导入完整性`。
6. 进入 `问答测试`，先勾 5 到 10 道题跑小样本，确认结果正常后再扩到当前会话全量。
7. 进入 `判分`，直接点 `判分当前结果`。
8. 进入 `导出报告`，选择这次结果生成 HTML 报告。

重点看四件事：

- 是否有正常回答
- 是否能看到相关记忆
- 判分是否完成
- 报告里准确率和错题分析是否正常

## 5. 什么情况下才需要改平台代码

正常情况下，改配置就够。

只有下面这些情况才需要改平台代码：

- EchoMemory 目录结构变了，先只改 `ECHOMEM_ROOT` 或 Python 路径。
- SDK 的方法签名变了，再改 `scripts/echomemory_locomo_import.py` 和 `scripts/echomemory_memory_qa.py`。
- 检索返回结构变了，优先改 `scripts/echomemory_memory_qa.py`。
