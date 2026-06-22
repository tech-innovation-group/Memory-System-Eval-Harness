# Web UI 使用指南 - 当前可用流程

Web 地址：

```text
<WEB_BASE_URL>/
```

当前版本已经去掉原生机器人评测入口。可用流程是：

```text
EchoMem/LoCoMo 数据集
  -> Local Agent 批量评测
  -> Judge
  -> Runs 分析 / report

LoCoMo 数据集
  -> OpenViking 导入
  -> commit_session
  -> 导入完整性检查
  -> 记忆浏览 / Agent 对话查看 relevant-memory
```

不要把真实 API key 写进本文档或发给外部测试者。

## 1. 打开页面

浏览器访问：

```text
<WEB_BASE_URL>/
```

如果服务没有启动：

```bash
cd <repo-root>
python3 server.py --host 127.0.0.1 --port 19181
```

## 2. 选择数据集

进入左侧 `数据集`。

数据集路径填：

```text
dataset/locomo10.json
```

点击 `读取`。

预期：

- format 显示 `locomo`
- samples 为 `10`
- questions 为 `1540`
- conversation 包含 `conv-26`, `conv-30`, `conv-41` 等

## 3. 导入 OpenViking 记忆

进入左侧 `OpenViking`。

配置：

```text
Host: 127.0.0.1
Port: 1933
Workspace: <openviking-workspace>
Account: default
```

如果希望每次干净测试，可以勾选“自动生成并填入新 workspace 路径”。

操作：

1. 选择 `conv-30`。
2. 点击 `导入并 Commit`。
3. 等待导入完成。
4. 查看 `导入完整性检查`。

完整性检查通过的标准：

- submitted / expected 一致
- commit 后 pending 为 `0`
- session 目录存在
- history 文件存在
- memory 文件存在
- 状态为 `complete`

如果不是 `complete`，先不要进入后续分析，优先检查 OpenViking 服务、workspace、commit 日志。

## 4. 运行 Local Agent 批量评测

进入左侧 `批量评测`。

操作：

1. `测试 Conversation` 选择 `conv-30`。
2. 在题目列表中选择 5-10 道题，或者搜索后勾选具体题目。
3. 点击 `开始 Local Agent 测试`。

当前 Local Agent 是本地只读检索基线：

- 不调用 OpenViking 写入
- 不污染历史记忆
- 输出 `local_agent_results.csv`
- 会记录 relevant-memory、token 估算和 Agent Response

注意：当前页面没有 `Random Count` 输入框。快速测试请手动选择 5-10 道题。

## 5. 运行 Judge

Local Agent 测试完成后，页面会自动填入 `结果 CSV`。

配置 Judge：

```text
Judge Base URL: 你的 Judge endpoint
Judge 模型: gpt-5.5
Judge Token: 在页面密码框中填写，不要写入文档
```

建议先点击：

```text
Judge 前 3 条 pending
```

确认正常后再点击：

```text
Judge 全部 pending
```

说明：

- 未 Judge 时准确率显示 `待 Judge`
- 正式准确率以 Judge 写入的 `CORRECT/WRONG` 为准
- exact match 只是参考

## 6. 查看结果

进入左侧 `Runs 分析`。

可以做：

- 查看 Recent Runs
- 打开每道题详情
- 查看 Agent Response / Gold / Evidence / Context / Judge Reasoning
- 导出 report
- wrong answer 聚类
- 手动填两个 CSV 做 diff

## 7. 记忆浏览与 Agent 对话

### 记忆浏览

进入左侧 `记忆浏览`。

配置 workspace/account 后点击刷新，可以查看：

- session 列表
- memory timeline
- memory markdown 文件内容

### Agent 对话

进入左侧 `Agent 对话`。

用于手动测试 OpenViking relevant-memory 召回。默认只读，不写历史记忆。

适合问：

```text
Why did Jon decide to start his dance studio?
What did Jon want his ideal dance studio to look like?
When did Jon lose his banker job?
How do Jon and Gina both like to destress?
```

右侧会显示 Relevant Memory。如果没有证据，优先检查：

- 是否导入了对应 conversation
- workspace/account 是否一致
- OpenViking relevant-memory API 是否返回 memory

## 8. 外部测试者应回传的产物

请回传：

- harness 版本或压缩包
- OpenViking commit hash
- 是否启用 graph memory
- dataset 路径和 sample 范围
- workspace 路径
- `openviking_import_summary.json`
- `local_agent_results.csv`
- Judge 后 CSV
- `report.md`
- `run.log`

不要回传真实 API key。

## 9. 常见问题

### 页面打不开

```bash
curl <WEB_BASE_URL>/health
```

如果失败，重启：

```bash
cd <repo-root>
python3 server.py --host 127.0.0.1 --port 19181
```

### OpenViking 检测失败

确认：

- OpenViking 服务是否运行
- host/port 是否正确
- workspace 是否可写
- account 是否一致

### 导入完整性失败

优先看：

- run.log
- commit task 状态
- pending_after_commit
- session/history 文件
- user/default/memories 文件

### 没有准确率

没有运行 Judge 时是正常的。先跑 Judge smoke，再跑全部 Judge。

## 10. 推荐快速 smoke

最小测试：

1. 数据集读取 `echomem_memrouter_locomo10.json`
2. Local Agent 跑 `conv-30` 5-10 题
3. Judge 前 3 条 pending
4. 导入 `conv-30` 到 OpenViking
5. 检查导入完整性
6. 在 Agent 对话中问一个 `conv-30` 相关问题，看 Relevant Memory

这套流程能验证当前系统最关键的输入、输出、记忆存储和检索闭环。
