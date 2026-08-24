# 数据集下载回退设计意图

## 目标

`shared/dataset_io.py` 负责 benchmark 数据集的本地查找与自动下载。每个数据集
配置一串候选下载地址（`urls`），按顺序尝试，直到某个地址下载成功并通过校验；
全部失败才报错并提示手动下载。这样在官方源不可达（网络限制、服务器下线）时，
评测仍能自动从镜像源拿到数据集。

## 接口

- `DATASET_SOURCES[benchmark] = {"filename": str, "urls": list[str]}`
- `resolve_dataset_path(benchmark, explicit_path="") -> str`
  - `--dataset` 指定路径时直接返回，不触发下载。
  - 本地文件已存在时直接返回本地路径，不联网。
  - 否则按 `urls` 顺序逐个下载；某源下载成功且校验通过即返回本地路径。
  - 全部失败时抛 `RuntimeError`，列出每个源各自的失败原因。

## 行为约束与边界条件

- **下载后校验**：`≤ 64MB` 的文件做完整 JSON 解析（`read_dataset`），捕获截断/
  损坏；更大的文件只做轻量结构检查（非空、以 `[`/`{` 开头、以 `]`/`}` 结尾），
  避免把大数据集整体加载进内存造成内存峰值。轻量校验可能放过个别非标准尾部
  垃圾，代价是牺牲部分严格性换取低内存——大数据集后续评测加载时仍会走完整解析。
- **失败清理**：任一候选源失败时删除 `.part` 临时文件，继续尝试下一个源。
- **全部失败**：不遗留 `.part` 文件，错误信息汇总所有源的失败原因。
- 每源下载超时 `120s`（沿用原行为）。

## 候选源

- **hotpotqa**：官方 `curtis.ml.cmu.edu` → GitHub 镜像（经 `ghfast.top` 加速代理
  与 GitHub 直连，多个镜像仓库，内容 SHA 一致）。
- **longmemeval**：官方 HuggingFace → ModelScope 镜像（`evalscope/longmemeval-cleaned`，
  与官方同源）→ HuggingFace 国内镜像。
- **locomo**：单源（GitHub raw），保持原地址。
