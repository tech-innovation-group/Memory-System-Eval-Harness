# 飞书机器人的 M1/M2/M3 入口

`压测develop` / `压测 develop` / `压测 PR33` / `压力测试 develop` / `性能测试 develop`
创建 `test_type=stress` 任务，默认执行 M1、M2、M3。`测试develop` 保持 LoCoMo QA/Judge。
无法识别的压测命令返回用法，不交给通用 LLM 分类器创建 QA 任务。

压测复用机器人源码准备流程，测试本次指定的 develop/PR 合并版本，新建专用 EchoMem
容器（4 CPU / 8 GiB），开启 DEBUG/JSON；模型配置沿用机器人当前配置，不静默换模型。
8 个独立租户各注入 LoCoMo conv-30/session_1/D1:19 的 100 份相同信息，使用相同 QA。
M1 目标并发为 64；M2/M3 复用本次 M1 的种子身份。M2/M3 使用套件正式窗口默认值。

服务端调度配置按 600 客户端并发预留：HTTP workers=2400、检索准入=600、Commit
queue=2400、Commit tenant quota=600、Commit executor=600、LLM/Embedding=2400、
Recall LLM/Embedding=600、tenant concurrency=600、tenant QPS=2400；控制面连接池为
500。64 并发与未来 600 并发复用该上限。4 CPU/8 GiB 和 Provider 配额仍是实际边界，
这些数值不是性能承诺；默认 profile 与本调优 profile 应分开比较。
任务的重试保留 stress 类型，不会回退到 LoCoMo。报告为 observation_run 的 report.html；
缺少报告视为 WRONG_ENTRYPOINT。PARTIAL/BLOCKED 是实测结论，不转换为性能通过。

## 部署

机器人应用不在本仓库中。`scripts/patch_feishu_m123_routing.py` 对机器人版本使用
单一锚点校验，遇到不兼容或已打补丁的源码会停止，不能覆盖其余业务逻辑。

```sh
python scripts/patch_feishu_m123_routing.py --input app.py --output app.m123.py
python -m unittest tests.test_feishu_m123_routing
```

执行机需要 `/opt/echomem-pr-bot/harness` 指向含本次 runner 的已验证仓库；
镜像 `echomem-m123-runner:20260918` 基于 python:3.12-slim，预装 PyYAML/tqdm。
将 app.m123.py 更新到部署源码及 Web 镜像的 `/web/app.py`；无活动任务时重启。
若重建 Web 镜像，必须包含已打补丁的部署源码，否则旧镜像会丢失路由。
租户凭据写入 runner 的 /private tmpfs；报告私有身份文件禁止通过文件下载路由公开。

2026-09-18 已更新 8.130.75.94 上 `/opt/memory-eval-web/app.py` 和运行容器。
旧源码备份为 `app.py.before-m123-routing-20260918`。历史 LoCoMo 任务不变更、不自动重跑。
