#!/usr/bin/env python3
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "static" / "generated-reports" / "echomemory_platform_integration_scheme_20260626.html"
OUT_MIRROR = ROOT / "static" / "generated-reports" / "echomemory_platform_integration_scheme_20260626.html"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def code(path: str) -> str:
    return f"<code>{esc(path)}</code>"


def section(title: str, body: str) -> str:
    return f"""
    <section class="section">
      <h2>{esc(title)}</h2>
      {body}
    </section>
    """


def callout(kind: str, title: str, body: str) -> str:
    return f"""
    <div class="callout {esc(kind)}">
      <h3>{esc(title)}</h3>
      {body}
    </div>
    """


def li(text: str) -> str:
    return f"<li>{text}</li>"


def table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{esc(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"""
    <div class="tablewrap">
      <table>
        <thead><tr>{head}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>
    """


def render_html() -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory 平台接入与注入检索方案</title>
  <style>
    :root {{
      --bg:#f5f5f7;
      --panel:#ffffff;
      --panel-2:#fbfbfd;
      --text:#1d1d1f;
      --muted:#6e6e73;
      --line:#d2d2d7;
      --blue:#0071e3;
      --green:#1d9b5f;
      --amber:#9a6700;
      --red:#b3261e;
      --shadow:0 10px 30px rgba(0,0,0,.05);
      --radius:16px;
      --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
      --sans:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      background:var(--bg);
      color:var(--text);
      font:15px/1.68 var(--sans);
    }}
    main {{
      max-width:1180px;
      margin:0 auto;
      padding:28px 18px 64px;
    }}
    .hero,.section,.footer {{
      background:var(--panel);
      border:1px solid var(--line);
      border-radius:var(--radius);
      box-shadow:var(--shadow);
    }}
    .hero {{
      padding:28px 24px;
      margin-bottom:16px;
      background:linear-gradient(180deg,#fff,#f8fbff);
    }}
    .section {{
      padding:22px 18px;
      margin-top:16px;
    }}
    .footer {{
      padding:18px;
      margin-top:16px;
    }}
    h1,h2,h3 {{
      margin:0 0 12px;
      line-height:1.24;
    }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:22px; }}
    h3 {{ font-size:17px; }}
    p {{ margin:0 0 12px; }}
    ul,ol {{
      margin:8px 0 12px 20px;
      padding:0;
    }}
    li {{ margin:6px 0; }}
    code {{
      font:12.5px/1.5 var(--mono);
      background:#f2f2f4;
      padding:2px 6px;
      border-radius:6px;
      word-break:break-word;
    }}
    .muted {{ color:var(--muted); }}
    .small {{ font-size:13px; }}
    .tags {{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      margin-bottom:14px;
    }}
    .tag {{
      display:inline-block;
      padding:4px 10px;
      border-radius:999px;
      background:#eef4ff;
      color:#1849a9;
      font-size:12px;
      font-weight:700;
    }}
    .grid-2 {{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
      gap:14px;
    }}
    .grid-3 {{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
      gap:12px;
    }}
    .stat {{
      border:1px solid var(--line);
      border-radius:12px;
      padding:14px;
      background:var(--panel-2);
    }}
    .stat .label {{
      font-size:12px;
      color:var(--muted);
      margin-bottom:6px;
    }}
    .stat .value {{
      font-size:25px;
      font-weight:700;
      line-height:1.1;
      margin-bottom:8px;
    }}
    .callout {{
      border:1px solid var(--line);
      border-left:4px solid var(--blue);
      border-radius:12px;
      padding:14px 14px 12px;
      background:linear-gradient(180deg,#fff,#fafafa);
      margin:12px 0;
    }}
    .callout.green {{ border-left-color:var(--green); }}
    .callout.amber {{ border-left-color:var(--amber); }}
    .callout.red {{ border-left-color:var(--red); }}
    .flow {{
      display:grid;
      gap:10px;
      margin-top:10px;
    }}
    .node {{
      border:1px solid var(--line);
      border-radius:12px;
      background:var(--panel-2);
      padding:12px 14px;
    }}
    .node .title {{
      font-weight:700;
      margin-bottom:4px;
    }}
    .arrow {{
      text-align:center;
      color:var(--muted);
      font-size:18px;
      line-height:1;
    }}
    .tablewrap {{
      overflow:auto;
      margin:12px 0;
    }}
    table {{
      width:100%;
      min-width:920px;
      border-collapse:collapse;
      font-size:14px;
    }}
    th,td {{
      text-align:left;
      padding:12px 12px;
      border-bottom:1px solid var(--line);
      vertical-align:top;
    }}
    th {{
      background:#fafafc;
      color:var(--muted);
      font-weight:600;
    }}
    @media (max-width:720px) {{
      main {{ padding:16px 12px 48px; }}
      .hero,.section {{ padding:18px 14px; }}
      h1 {{ font-size:24px; }}
      h2 {{ font-size:20px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="tags">
        <span class="tag">EchoMemory</span>
        <span class="tag">LoCoMo</span>
        <span class="tag">平台接入</span>
        <span class="tag">注入 / 检索 / 任务 / 报告</span>
      </div>
      <h1>EchoMemory 平台接入与注入检索方案</h1>
      <p class="muted">这份页面回答四个问题：一，EchoMemory 在平台里怎么注入记忆；二，问答时怎么检索；三，平台怎么把它接成可运行任务；四，结果和进度怎么回收到 UI 和报告里。</p>
      <div class="grid-3">
        <div class="stat">
          <div class="label">平台仓库</div>
          <div class="value">locomo-eval-web</div>
          <div class="small">{esc(str(ROOT))}</div>
        </div>
        <div class="stat">
          <div class="label">报告生成时间</div>
          <div class="value">{esc(generated_at)}</div>
          <div class="small">当前代码基线梳理</div>
        </div>
        <div class="stat">
          <div class="label">当前结论</div>
          <div class="value">平台适配已成链</div>
          <div class="small">重点在命令构建、状态刷新、前端展示统一</div>
        </div>
      </div>
    </section>

    {section("1. 先给结论",
      callout("green", "一句话结论",
        "<p>平台现在不是“临时跑 EchoMemory 脚本”，而是已经把 EchoMemory 接成一个完整 backend：有运行时发现、有任务构建、有导入 summary、有 QA CSV、有 judge 汇总、有进度状态回流到页面。</p>"
      )
      + callout("amber", "真正需要稳定的边界",
        "<ol>"
        + li("运行时边界：怎么找到 EchoMemory 根目录、怎么注入 embedding/chat token。")
        + li("任务边界：导入和 QA 分别要传哪些参数，默认值是什么。")
        + li("产物边界：导入 summary、QA CSV、judge_summary、workspace token 日志长什么样。")
        + li("展示边界：后端返回哪些 progress 字段，前端用哪些字段显示“已完成多少 session”。")
        + "</ol>"
      )
      + table(
        ["问题", "当前实现", "关键文件"],
        [
          ["怎么注入记忆", "LoCoMo 导入脚本逐 session create_session -> add_message -> commit_session", f"{code('/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:1973')} / {code('/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:2017')}"],
          ["怎么检索", "QA 固定走 sdk.search，平台层和脚本层都把 retrieval_mode 钉死为 search", f"{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:95')} / {code('/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:84')} / {code('/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:1462')}"],
          ["怎么发任务", "server -> task_orchestrator -> plugin_service -> echomemory plugin tasks", f"{code('/Users/chx/locomo-eval-web/server.py:6276')} / {code('/Users/chx/locomo-eval-web/memory/services/task_orchestrator.py:194')} / {code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/plugin.py:83')}"],
          ["怎么回 UI", "task_progress 解析 run.log + echomemory_import_summary.json，前端读取 completed_sessions 等字段", f"{code('/Users/chx/locomo-eval-web/memory/tasking.py:544')} / {code('/Users/chx/locomo-eval-web/memory/tasking.py:638')} / {code('/Users/chx/locomo-eval-web/web/static/app.js:14466')}"],
        ]
      )
    )}

    {section("2. EchoMemory 怎么注入记忆",
      callout("blue", "注入的真实执行方式",
        "<p>平台并不是直接往某个 JSON 文件里写数据，而是调用 EchoMemory SDK 的会话接口。导入流程按 LoCoMo 的 session 粒度进行：</p>"
        "<ol>"
        + li(f"先由任务构建器拼出导入命令：{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:169')}。")
        + li(f"脚本读取数据集后，为每个 session 调用 {code('sdk.create_session')}：{code('/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:1973')}。")
        + li(f"逐条 turn 调用 {code('sdk.add_message')} 写入：{code('/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:1980')}。")
        + li(f"session 写完后调用 {code('sdk.commit_session')} 触发长期记忆抽取和索引：{code('/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:926')} / {code('/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:939')}。")
        + li("每个 session 的状态、完整性、warning、估算 token，会被写入 echomemory_import_summary.json。")
        + "</ol>"
      )
      + table(
        ["阶段", "在平台里的含义", "代码位置"],
        [
          ["create_session", "为当前 LoCoMo session 建一个 EchoMemory session 容器", code("/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:1973")],
          ["add_message", "把每条对话 turn 作为消息写入 EchoMemory", code("/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:1980")],
          ["commit_session", "触发抽取 atom / overview / abstract / embedding / 索引", code("/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:2017")],
          ["build_import_summary", "把本次导入的进度和完整性汇总成 summary 文件", code("/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:178")],
          ["summary write", "把 summary 落盘给 UI 和 runs 列表消费", code("/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:3140")],
        ]
      )
      + callout("amber", "导入等待策略是怎么控制的",
        "<p>任务构建层会根据是否是 develop root、是否 defer artifact wait，自动决定导入等待参数。默认逻辑在 {tasks}</p>"
        "<ul>"
        + li(f"{code('--import-wait-mode')}：控制 full / fast。")
        + li(f"{code('--commit-wait-s')}、{code('--flush-call-timeout-s')}、{code('--flush-attempts')}：控制 commit 返回后还要等多久。")
        + li(f"{code('--defer-artifact-wait')}：允许导入先返回 succeeded，异步产物继续追平。")
        + li(f"平台对 develop root 会强制更保守的 full wait：{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:106')} 到 {code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:164')}。")
        + "</ul>"
        .replace("{tasks}", code("/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:169"))
      )
      + callout("green", "注入产物是什么",
        "<p>平台接入并不要求直接读 EchoMemory 内部所有文件，而是优先依赖这三类产物：</p>"
        "<ul>"
        + li(f"{code('echomemory_import_summary.json')}：导入状态总表。")
        + li(f"workspace 下的 {code('metrics/llm_tokens/*.jsonl')}：导入期间和 QA 期间的 token / latency 日志。")
        + li("EchoMemory account 目录下的 memory / structured / atoms / overview / abstract 等真实长期记忆产物。")
        + "</ul>"
      )
    )}

    {section("3. EchoMemory 怎么检索",
      callout("blue", "平台口径：现在只认 search",
        "<p>无论前端有没有传别的 retrieval_mode，平台插件层和 QA 脚本层都会把 EchoMemory 检索钉到 {search}</p>"
        "<ul>"
        + li("插件任务构建器：" + code("/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:95") + " 直接 " + code('return "search"') + "。")
        + li(f"QA 脚本内部：{code('/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:84')} 同样固定成 search。")
        + li("这意味着当前正式口径下，不做 find-only、本地文件拼接式 retrieval，也不做 both 混合模式。")
        + "</ul>"
        .replace("{search}", code("sdk.search"))
      )
      + table(
        ["环节", "当前做法", "代码位置"],
        [
          ["检索模式归一", "把 retrieval_mode 统一改成 search", f"{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:95')} / {code('/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:84')}"],
          ["真实检索调用", "按 top_k 调用 sdk.search(query, ctx, budget)", code("/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:1462")],
          ["结果落表", "相关命中写到 relevant_memory 字段", code("/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:2898")],
          ["token 落表", "答案 token 写到 answer_total_tokens", code("/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:2976")],
          ["汇总输出", "summary.json 汇总 QA 结果", code("/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:3288")],
        ]
      )
      + callout("amber", "检索后如何生成答案",
        "<p>命中的 EchoMemory 结果不会直接返回给平台，而是先组装成 prompt，再走 answer model。当前脚本通过 {call_openai} 调模型，最后把 answer、relevant_memory、answer_total_tokens 一并写入 CSV。</p>"
        '<p class="small">这也是为什么平台既能看准确率，也能看 token 消耗：检索和答案两条链路都已经有结构化落盘。</p>'
        .replace("{call_openai}", code("call_openai"))
      )
      + callout("red", "当前限制",
        "<ul>"
        + li("正式评测口径下，EchoMemory 没有暴露一个和 OpenViking 完全等价的 tool-loop 检索面。")
        + li("因此平台为了公平和稳定，现阶段把 EchoMemory QA 固定成“search + answer model”，而不是多工具多轮读取。")
        + li("如果以后要扩展成 search + read_many + grep 的多轮链路，需要先补 EchoMemory plugin 的 agent_workbench / memory tools 契约。")
        + "</ul>"
      )
    )}

    {section("4. 平台怎么对接 EchoMemory",
      "<div class='flow'>"
      "<div class='node'><div class='title'>A. 前端选 backend</div><div>用户在页面上把 memory backend 切到 <code>echomemory</code>，前端表单把 workspace / account / sample / model 等参数送到 <code>/api/tasks</code>。</div></div>"
      "<div class='arrow'>↓</div>"
      "<div class='node'><div class='title'>B. server.py 组装运行时</div><div>{runtime} 负责解析 EchoMemory 根目录、embedding/chat token；{preflight} 负责在创建任务前做模型可用性预检。</div></div>"
      "<div class='arrow'>↓</div>"
      "<div class='node'><div class='title'>C. task orchestrator 分派任务</div><div>{orchestrator} 根据 kind 是否属于 EchoMemory，设置环境变量，例如 <code>DASHSCOPE_API_KEY</code>、<code>ECHOMEM_CHAT_API_KEY</code>、<code>ECHOMEM_AUTO_COMMIT_THRESHOLD=0</code>。</div></div>"
      "<div class='arrow'>↓</div>"
      "<div class='node'><div class='title'>D. EchoMemory plugin 构建命令</div><div>{plugin} 根据任务种类构建 import / QA / generic QA 命令。</div></div>"
      "<div class='arrow'>↓</div>"
      "<div class='node'><div class='title'>E. 结果与状态回流</div><div>{tasking} 读 run.log 和 summary，{refresh} 把磁盘状态同步回 task，前端再从 <code>/api/tasks</code> 和 <code>/api/runs</code> 读出来显示。</div></div>"
      "</div>"
      .replace("{runtime}", code("/Users/chx/locomo-eval-web/server.py:5907"))
      .replace("{preflight}", code("/Users/chx/locomo-eval-web/memory/services/task_orchestrator.py:217"))
      .replace("{orchestrator}", code("/Users/chx/locomo-eval-web/memory/services/task_orchestrator.py:194"))
      .replace("{plugin}", code("/Users/chx/locomo-eval-web/memory/plugins/echomemory/plugin.py:83"))
      .replace("{tasking}", code("/Users/chx/locomo-eval-web/memory/tasking.py:638"))
      .replace("{refresh}", code("/Users/chx/locomo-eval-web/server.py:958"))
      + table(
        ["对接层", "职责", "关键文件"],
        [
          ["运行时发现", "找到 EchoMemory root，判断是不是可用 SDK，检测 token 是否配置", f"{code('/Users/chx/locomo-eval-web/memory/services/runtime_status.py:148')} / {code('/Users/chx/locomo-eval-web/server.py:1249')}"],
          ["任务创建", "创建 echomemory_import / echomemory_qa / echomemory_generic_qa", f"{code('/Users/chx/locomo-eval-web/server.py:6276')} / {code('/Users/chx/locomo-eval-web/memory/services/task_factory.py:20')}"],
          ["命令构建", "把 payload 变成实际 python 命令", f"{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:169')} / {code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:257')} / {code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:447')}"],
          ["状态刷新", "从 run_dir / manifest / summary / output_file 回填任务状态", code("/Users/chx/locomo-eval-web/server.py:958")],
          ["进度解析", "从 log 和 summary 解析 session 进度", f"{code('/Users/chx/locomo-eval-web/memory/tasking.py:544')} / {code('/Users/chx/locomo-eval-web/memory/tasking.py:638')}"],
          ["结果汇总", "把 judge_summary 合并回 run summary", f"{code('/Users/chx/locomo-eval-web/memory/runs.py:268')} / {code('/Users/chx/locomo-eval-web/memory/reports.py:89')}"],
        ]
      )
    )}

    {section("5. 前端看到的进度和报告是怎么来的",
      callout("green", "导入进度不是瞎猜，是后端给结构化字段",
        "<p>后端 {progress_fn} 现在会返回这几个关键字段：</p>"
        "<ul>"
        + li(f"{code('submitted_sessions')}：已经提交 commit 的 session 数。")
        + li(f"{code('completed_sessions')}：真正完成的 session 数。")
        + li(f"{code('finalizing_sessions_done')} / {code('finalizing_sessions_total')}：正在归档阶段时的已完成数。")
        + li(f"{code('session_label')} / {code('current_import')}：当前跑到哪个 session、哪条消息。")
        + "</ul>"
        "<p>前端则在 {ui_line} 一带优先使用这些字段，而不是只看 {current_total}</p>"
        .replace("{progress_fn}", code("/Users/chx/locomo-eval-web/memory/tasking.py:638"))
        .replace("{ui_line}", code("/Users/chx/locomo-eval-web/web/static/app.js:14466"))
        .replace("{current_total}", code("progress.current/progress.total"))
      )
      + table(
        ["UI 场景", "数据来源", "代码位置"],
        [
          ["工作区状态条", "renderWorkspaceStatusStrip 读 active task progress", code("/Users/chx/locomo-eval-web/web/static/app.js:1737")],
          ["任务标题文案", "taskProgressLabel 生成“已完成多少 session”", code("/Users/chx/locomo-eval-web/web/static/app.js:2528")],
          ["任务详情 log", "轮询 /api/tasks/<id>/log", code("/Users/chx/locomo-eval-web/web/static/app.js:3071")],
          ["runs 列表", "调用 /api/runs 并合并 judge_summary", f"{code('/Users/chx/locomo-eval-web/web/static/app.js:11418')} / {code('/Users/chx/locomo-eval-web/memory/runs.py:268')}"],
        ]
      )
      + callout("amber", "为什么之前会出现 0/19、满进度条、running/已中断来回跳",
        "<ul>"
        + li("只读 log tail 时，早期 commit completed 行可能被挤出窗口，导致 current 短暂回退。")
        + li("manifest 还写着 running，但实际进程已经结束，UI 会误以为任务还活着。")
        + li("前端如果只用 progress.current，而不看 completed_sessions / finalizing_sessions_done，就会把“正在处理当前 session”误显示成“已完成”。")
        + "</ul>"
        "<p class='small'>这也是这次修复重点放在 {cache}、{summary_progress}、{refresh} 三处的原因。</p>"
        .replace("{cache}", code("/Users/chx/locomo-eval-web/memory/tasking.py:466"))
        .replace("{summary_progress}", code("/Users/chx/locomo-eval-web/memory/tasking.py:544"))
        .replace("{refresh}", code("/Users/chx/locomo-eval-web/server.py:958"))
      )
    )}

    {section("6. 直接可执行的接入方案",
      "<ol>"
      + li("<strong>第一步，确认运行时。</strong> 先让平台通过 runtime_status 找到正确的 EchoMemory 根目录和 token 配置。重点看 " + code("/Users/chx/locomo-eval-web/memory/services/runtime_status.py:148") + " 与 " + code("/Users/chx/locomo-eval-web/server.py:5907") + "。")
      + li("<strong>第二步，固定任务参数。</strong> 导入只走 " + code("echomemory_import") + "，QA 只走 " + code("echomemory_qa") + "，检索模式固定 " + code("search") + "。避免引入未对齐的 local/both 模式。")
      + li("<strong>第三步，导入产物最小契约。</strong> 必须保证 " + code("echomemory_import_summary.json") + " 始终存在并且字段稳定，否则进度条、runs 列表、完整性检查都会失真。")
      + li("<strong>第四步，QA 产物最小契约。</strong> 必须保证 CSV 里有 " + code("relevant_memory") + "、" + code("answer_total_tokens") + "、question/response/result 这些字段。")
      + li("<strong>第五步，状态刷新先于显示。</strong> 所有 " + code("/api/tasks") + "、" + code("/api/tasks/<id>") + " 返回前，都先跑 " + code("refresh_task_runtime_state") + "。")
      + li("<strong>第六步，前端只认 completed_sessions 口径。</strong> 会话进度显示不要再直接拿 " + code("progress.current") + " 当“已完成数”。")
      + "</ol>"
      + callout("red", "不建议的做法",
        "<ul>"
        + li("不要让前端自己推测 EchoMemory 导入完成。")
        + li("不要在平台里同时维护多套 EchoMemory 检索口径。")
        + li("不要把 judge token、answer token、embedding/chat token 混成一套不分角色的默认值。")
        + li("不要绕过 plugin/task_orchestrator，直接在 server.py 里硬拼某个 EchoMemory 版本的命令。")
        + "</ul>"
      )
    )}

    {section("7. 当前平台里已经确认没问题的部分",
      "<ul>"
      + li("EchoMemory 任务已经通过 plugin-service 接入，不是散落脚本。")
      + li("任务创建时会单独补齐 EchoMemory embedding/chat token，并做模型预检。")
      + li("导入 summary、QA summary、judge_summary 都已经有统一回收路径。")
      + li("task_progress 到前端显示这条链路，已经有专门字段支撑 session 级进度。")
      + li("相关测试已通过：task orchestrator、tasking progress、backend profiles。")
      + "</ul>"
      + callout("amber", "仍需持续盯住的点",
        "<ul>"
        + li("EchoMemory workspace 下 token sidecar 文件是否稳定；如果会重建或损坏，token 汇总会丢。")
        + li("include_inactive 的 /api/tasks 在历史任务很多时会偏重。")
        + li("老 run 的 manifest 状态、实际进程状态、summary 状态可能不一致，需要 refresh 修正。")
        + li("如果后续切换 EchoMemory develop 与 v010，不要默认它们的 commit 等待策略和目录布局完全相同。")
        + "</ul>"
      )
    )}

    <section class="footer">
      <h2>8. 关键代码索引</h2>
      <p class="small muted">这组路径可以直接顺着看完整链路。</p>
      <ul>
        <li>{code('/Users/chx/locomo-eval-web/server.py:5907')}：EchoMemory runtime env 解析</li>
        <li>{code('/Users/chx/locomo-eval-web/server.py:958')}：任务运行状态刷新</li>
        <li>{code('/Users/chx/locomo-eval-web/memory/services/runtime_status.py:148')}：EchoMemory runtime status</li>
        <li>{code('/Users/chx/locomo-eval-web/memory/services/task_orchestrator.py:194')}：统一任务编排</li>
        <li>{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/plugin.py:83')}：EchoMemory plugin 入口</li>
        <li>{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:169')}：导入命令构建</li>
        <li>{code('/Users/chx/locomo-eval-web/memory/plugins/echomemory/tasks.py:257')}：LoCoMo QA 命令构建</li>
        <li>{code('/Users/chx/locomo-eval-web/scripts/echomemory_locomo_import.py:1973')}：create_session / add_message / commit_session 实际执行</li>
        <li>{code('/Users/chx/locomo-eval-web/scripts/echomemory_memory_qa.py:1462')}：sdk.search 实际检索</li>
        <li>{code('/Users/chx/locomo-eval-web/memory/tasking.py:638')}：导入 / QA 进度解析</li>
        <li>{code('/Users/chx/locomo-eval-web/web/static/app.js:14466')}：前端 session 进度展示</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""


def main() -> None:
    html_text = render_html()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_text, encoding="utf-8")
    OUT_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    OUT_MIRROR.write_text(html_text, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
