# Server Codex Monitoring

服务器已安装 OpenAI Codex CLI，入口为：

```bash
codex --version
codex-monitor <任务ID>
```

## 首次配置

Codex CLI 当前未保存登录凭证。使用 root 或专用运维账号配置：

```bash
export OPENAI_API_KEY='你的 OpenAI API Key'
```

不要把 Key 写入 Git、任务结果、Docker 日志或飞书消息。若需要常驻服务，
建议放在权限为 `600` 的 `/root/.codex-monitor.env`，再由 systemd 的
`EnvironmentFile` 加载。

## 监控任务

不带任务 ID 时，优先分析当前正在运行或排队的任务：

```bash
codex-monitor
```

指定任务：

```bash
codex-monitor a20038eedf17
```

Codex 使用 `read-only` 沙箱，只能检查测试平台、Docker 状态和结果文件，
不会修改 EchoMem、PR、测试配置或容器。默认检查：

- `jobs.json` 和任务进度；
- Web、Runner、EchoMem、Embedding 服务；
- `summary.json`、`config.json`、导入、QA、Judge 和诊断文件；
- `atom_persistence_failed`、CAS 版本冲突、空召回和模型服务异常；
- 任务实际 commit、develop 基线和 PR 合并结果。

## 常用命令

```bash
docker ps
docker logs --tail 100 memory-eval-web
tail -n 20 /opt/memory-eval-web/data/feishu-events.jsonl
curl -fsS http://127.0.0.1:8081/
```

Codex 只负责分析和解释。实际的重试、重启和评测任务仍由 Web 测试平台执行。
