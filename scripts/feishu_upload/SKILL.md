---
name: upload-eval-to-feishu
description: >
  上传 Memory-System-Eval-Harness 评测结果到共享飞书多维表格。
  当用户完成一次 locomo/hotpotqa/longmemeval 评测，需要把结果上传到飞书共享表格时使用此 skill。
  输入：评测结果目录路径（如 benchmarks/locomo/results/20260807_173103_910765）。
---

# 上传评测结果到飞书

## 使用场景

完成一次评测运行后，将结果上传到团队共享的飞书多维表格，用于跨运行横向对比。

## 执行步骤

当用户要求上传评测结果时，按以下步骤执行：

### 1. 确认结果目录

确认评测结果目录路径（若用户未提供则询问）。目录下应包含 `summary.json` 和 `config.json`。

### 2. 提取评测数据

运行数据提取脚本，获取结构化的指标 JSON：

```bash
python "Memory-System-Eval-Harness/scripts/feishu_upload/scripts/extract_eval_result.py" "<结果目录路径>"
```

脚本输出一个 JSON 对象，key 为飞书表格列名（中文），value 为对应指标值。
所有浮点数自动四舍五入到 4 位小数。
将输出保存为变量，后续组装上传请求体时使用。

**注意**：脚本输出中不包含「上传人」和「备注」字段，这两个字段在步骤 3 中向用户收集后合并进去。

### 3. 收集飞书凭证

向用户依次询问以下信息（使用 AskUserQuestion 或直接对话）：

- 飞书 App ID（自建应用 App ID）
- 飞书 App Secret（自建应用 App Secret）
- 多维表格 App Token（从飞书多维表格 URL 中获取）
- 多维表格 Table ID（从飞书多维表格 URL 中获取）
- 飞书用户名（用于标识是谁上传的）
- 备注（可选，用户可跳过）

凭证获取方式参见 `references/feishu_setup_guide.md`。

### 4. 获取飞书 access_token

用 curl 调用飞书 API 获取 tenant_access_token：

```bash
curl -s -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
  -H "Content-Type: application/json" \
  -d '{"app_id": "<App ID>", "app_secret": "<App Secret>"}'
```

从响应 JSON 中解析 `tenant_access_token` 字段。

### 5. 打包结果目录为 zip

将评测结果目录打包为 zip 文件（文件名与目录名相同，如 `20260807_173103_910765.zip`）：

```bash
cd "<结果目录的父目录>" && python -c "
import shutil, sys
shutil.make_archive('<结果目录名>', 'zip', '<结果目录名>')
"
```

### 6. 上传 zip 到飞书获取 file_token

使用飞书云文档「上传素材」接口上传 zip 文件：

```bash
curl -s -X POST \
  "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all" \
  -H "Authorization: Bearer <tenant_access_token>" \
  -F "file_name=<zip文件名>" \
  -F "parent_type=bitable_image" \
  -F "parent_node=<app_token>" \
  -F "size=<zip文件字节数>" \
  -F "file=@<zip文件路径>"
```

从响应 JSON 中解析 `data.file_token` 字段。后续在记录的「附件」列中引用此 file_token。

### 7. 查重

从步骤 2 的提取结果中取出「运行ID」字段值，用 curl 查询表格中是否已存在相同记录：

```bash
curl -s -X GET \
  "https://open.feishu.cn/open-apis/bitable/v1/apps/<app_token>/tables/<table_id>/records?filter=CurrentValue.[运行ID]=\"<run_id>\"" \
  -H "Authorization: Bearer <tenant_access_token>"
```

- 若响应中 `data.items` 非空（已有记录）：询问用户选择「覆盖更新」还是「跳过」
  - 选择「覆盖更新」：记录 `record_id`（从响应 `data.items[0].record_id` 获取），执行步骤 8b
  - 选择「跳过」：结束流程
- 若响应中 `data.items` 为空（不存在）：执行步骤 8a

### 8a. 创建新记录

将步骤 2 提取的 JSON 与步骤 3 收集的「上传人」「备注」以及步骤 6 的「附件」合并为一个 fields 对象，用 curl 创建记录：

```bash
curl -s -X POST \
  "https://open.feishu.cn/open-apis/bitable/v1/apps/<app_token>/tables/<table_id>/records" \
  -H "Authorization: Bearer <tenant_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"fields": <合并后的字段JSON>}'
```

**合并方式**：将步骤 2 的 JSON 输出解析为 dict，添加 `"上传人": "<飞书用户名>"`、`"备注": "<备注或空字符串>"` 和 `"附件": [{"file_token": "<file_token>"}]`，然后作为 `fields` 的值。

**null 值处理**：飞书 API 不接受 null 值的字段，上传前需将值为 null 的字段从 fields 中移除。

### 8b. 更新已有记录（用户选择覆盖时）

```bash
curl -s -X PUT \
  "https://open.feishu.cn/open-apis/bitable/v1/apps/<app_token>/tables/<table_id>/records/<record_id>" \
  -H "Authorization: Bearer <tenant_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"fields": <合并后的字段JSON>}'
```

### 9. 展示结果

检查响应 JSON 中的 `code` 字段：

- `code: 0`：上传成功，向用户展示飞书表格链接 `https://feishu.cn/base/<app_token>`
- `code != 0`：上传失败，向用户展示 `msg` 字段中的错误信息

## 表格初始化（首次使用）

若飞书多维表格尚未创建列结构，执行此流程：

1. 向用户收集飞书凭证（同步骤 3 的 App ID、App Secret、App Token、Table ID）
2. 获取 tenant_access_token（同步骤 4）
3. 读取 `references/table_fields.json` 文件
4. 对每个字段定义，用 curl 调用飞书 API 创建列。数字字段（type=2）需同时设置 `property.formatter` 为 `0.0000` 以显示 4 位小数：

   ```bash
   # 文本/单选/日期/复选框/附件等非数字字段
   curl -s -X POST \
     "https://open.feishu.cn/open-apis/bitable/v1/apps/<app_token>/tables/<table_id>/fields" \
     -H "Authorization: Bearer <tenant_access_token>" \
     -H "Content-Type: application/json" \
     -d '{"field_name": "<列名>", "type": <类型数字>}'

   # 数字字段（type=2）需额外设置 formatter
   curl -s -X POST \
     "https://open.feishu.cn/open-apis/bitable/v1/apps/<app_token>/tables/<table_id>/fields" \
     -H "Authorization: Bearer <tenant_access_token>" \
     -H "Content-Type: application/json" \
     -d '{"field_name": "<列名>", "type": 2, "property": {"formatter": "0.0000"}}'
   ```

5. 创建完成后提示用户可以开始上传评测结果

飞书字段类型数字对照：1=文本, 2=数字, 3=单选, 5=日期, 7=复选框, 17=附件

## 注意事项

- 飞书凭证仅在本次会话期间使用，不落盘、不写入任何文件
- `extract_eval_result.py` 只读本地文件输出 JSON，不做任何网络请求，不接触凭证
- 所有浮点数自动四舍五入到 4 位小数
- 上传前必须移除值为 null 的字段，飞书 API 不接受 null
- 数字字段创建时必须设置 `property.formatter` 为 `0.0000`，否则默认只显示 1 位小数
- 上传记录时数字值必须以 JSON number 类型发送（不能是字符串），否则飞书会存为文本
- 「上传人」「备注」不在提取脚本输出中，由 skill 向用户收集后合并
- 「附件」列通过上传 zip 到飞书云文档获取 file_token 后填入，格式为 `[{"file_token": "<token>"}]`
- 「Benchmark」列的值为 benchmark 与样本过滤器的组合（如 locomo + conv-30 → `locomo-conv-30`），无样本过滤器时仅 benchmark 名
