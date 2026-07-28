from html.parser import HTMLParser
import csv
from pathlib import Path

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tbody = False
        self.in_tr = False
        self.in_td = False
        self.in_span = False
        self.rows = []
        self.current_row = []
        self.span_class = ""
        self.td_text = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.in_table = True
        elif tag == "tbody" and self.in_table:
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self.in_tr = True
            self.current_row = []
        elif tag == "td" and self.in_tr:
            self.in_td = True
            self.span_class = ""
            self.td_text = ""
        elif tag == "span" and self.in_td:
            self.in_span = True
            self.span_class = attrs.get("class", "")

    def handle_endtag(self, tag):
        if tag == "span" and self.in_span:
            self.in_span = False
        elif tag == "td" and self.in_td:
            self.in_td = False
            self.current_row.append((self.td_text.strip(), self.span_class))
        elif tag == "tr" and self.in_tr:
            self.in_tr = False
            self.rows.append(list(self.current_row))
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "table":
            self.in_table = False

    def handle_data(self, data):
        if self.in_td or self.in_span:
            self.td_text += data


with open(
    r"D:\agent\echomem\echo0\membase_graph_complementarity_report.html",
    encoding="utf-8",
) as f:
    html = f.read()

p = TableParser()
p.feed(html)
report = {}
for r in p.rows:
    if len(r) >= 5 and r[0][0].isdigit():
        qnum = int(r[0][0])
        report[qnum] = {
            "qnum": qnum,
            "question": r[1][0],
            "membase": "ok" in r[2][1],
            "graph": "ok" in r[3][1],
            "union": "ok" in r[4][1],
        }

rows = []
with open(
    r"D:\code\Memory-System-Eval-Harness\runs\qa_conv30_clean\echomemory_memory_qa_results.csv",
    encoding="utf-8",
    newline="",
) as f:
    reader = csv.DictReader(f)
    for row in reader:
        qid = row.get("question_id", "")
        if qid.startswith("conv-30_qa"):
            qnum = int(qid.replace("conv-30_qa", "")) + 1
            rows.append(
                {
                    "qnum": qnum,
                    "qid": qid,
                    "question": row.get("question", ""),
                    "gold": row.get("answer", ""),
                    "response": row.get("response", ""),
                    "harness_correct": row.get("result", "").upper().strip()
                    == "CORRECT",
                    "membase_correct": report.get(qnum, {}).get("membase", False),
                    "graph_correct": report.get(qnum, {}).get("graph", False),
                    "union_correct": report.get(qnum, {}).get("union", False),
                }
            )

categories = {
    "membase_only_wrong": [],
    "graph_only_wrong": [],
    "both_right_wrong": [],
    "both_wrong": [],
    "harness_right": [],
}
for r in rows:
    if r["harness_correct"]:
        categories["harness_right"].append(r)
    elif r["membase_correct"] and not r["graph_correct"]:
        categories["membase_only_wrong"].append(r)
    elif r["graph_correct"] and not r["membase_correct"]:
        categories["graph_only_wrong"].append(r)
    elif r["membase_correct"] and r["graph_correct"]:
        categories["both_right_wrong"].append(r)
    else:
        categories["both_wrong"].append(r)

total = len(rows)
h_right = len(categories["harness_right"])
union_right = sum(1 for r in rows if r["union_correct"])
fusion_loss = sum(
    1 for r in rows if r["union_correct"] and not r["harness_correct"]
)

parts = []
parts.append("<!DOCTYPE html>")
parts.append('<html lang="zh-CN">')
parts.append("<head>")
parts.append('<meta charset="utf-8">')
parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
parts.append("<title>Harness conv-30 错误分析报告</title>")
parts.append("<style>")
parts.append(
    ":root{--bg:#f7f5f0;--surface:#fff;--border:#e5ded2;--text:#111827;--muted:#6b7280;--membase:#2563eb;--graph:#16a34a;--wrong:#dc2626;--radius:8px;}"
)
parts.append(
    "*{box-sizing:border-box;}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.6;}"
)
parts.append(
    ".page{max-width:1200px;margin:0 auto;padding:32px 20px 48px;}h1{margin:0 0 8px;font-size:28px;}.sub{color:var(--muted);font-size:14px;}"
)
parts.append(
    ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin:24px 0;}"
)
parts.append(
    ".card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px;}"
)
parts.append(
    ".card .label{color:var(--muted);font-size:12px;margin-bottom:8px;}.card .value{font-size:32px;font-weight:700;}"
)
parts.append(
    ".section{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin:24px 0;}"
)
parts.append(".section h2{margin-top:0;font-size:18px;}")
parts.append(
    "table{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px;}"
)
parts.append(
    "th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top;}"
)
parts.append("th{background:#faf9f7;font-weight:600;}tr:hover td{background:#fafaf9;}")
parts.append(
    ".badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;}"
)
parts.append(
    ".badge.ok{background:#dcfce7;color:#166534;}.badge.miss{background:#fee2e2;color:#991b1b;}.badge.warn{background:#fef3c7;color:#92400e;}"
)
parts.append(".footer{color:var(--muted);font-size:13px;margin-top:32px;}")
parts.append("</style>")
parts.append("</head>")
parts.append("<body>")
parts.append('<div class="page">')
parts.append("<h1>Harness conv-30 错误分析报告</h1>")
parts.append(
    '<div class="sub">对比 Harness 运行结果（runs/qa_conv30_clean）与 MemBase/Graph Engine 互补性报告</div>'
)

parts.append('<div class="cards">')
parts.append(
    f'<div class="card"><div class="label">Harness 准确率</div><div class="value" style="color:var(--membase)">{h_right}/{total}</div><div class="desc">{h_right/total*100:.2f}%</div></div>'
)
parts.append(
    f'<div class="card"><div class="label">Harness 错误数</div><div class="value" style="color:var(--wrong)">{total-h_right}</div><div class="desc">{(total-h_right)/total*100:.2f}%</div></div>'
)
parts.append(
    f'<div class="card"><div class="label">理论并集上限</div><div class="value">{union_right}/{total}</div><div class="desc">{union_right/total*100:.2f}%</div></div>'
)
parts.append(
    f'<div class="card"><div class="label">融合损失</div><div class="value" style="color:var(--wrong)">{fusion_loss}</div><div class="desc">并集对但 harness 错</div></div>'
)
parts.append("</div>")

parts.append('<div class="section"><h2>错误分类汇总</h2>')
parts.append(
    "<table><thead><tr><th>类型</th><th>数量</th><th>占比</th><th>说明</th></tr></thead><tbody>"
)
parts.append(
    f'<tr><td>MemBase 对 / Graph 错</td><td>{len(categories["membase_only_wrong"])}</td><td>{len(categories["membase_only_wrong"])/total*100:.1f}%</td><td>MemBase 有答案，但 harness 没采信</td></tr>'
)
parts.append(
    f'<tr><td>Graph 对 / MemBase 错</td><td>{len(categories["graph_only_wrong"])}</td><td>{len(categories["graph_only_wrong"])/total*100:.1f}%</td><td>Graph 有答案，但 harness 没采信</td></tr>'
)
parts.append(
    f'<tr><td>两者都对但 Harness 错</td><td>{len(categories["both_right_wrong"])}</td><td>{len(categories["both_right_wrong"])/total*100:.1f}%</td><td>检索到了，但答案生成或 judge 失败</td></tr>'
)
parts.append(
    f'<tr><td>两者都错</td><td>{len(categories["both_wrong"])}</td><td>{len(categories["both_wrong"])/total*100:.1f}%</td><td>两个引擎都检索不到</td></tr>'
)
parts.append("</tbody></table></div>")

parts.append('<div class="section"><h2>核心结论</h2>')
parts.append(
    f"<p>Harness 当前准确率 <strong>{h_right/total*100:.2f}%</strong>，低于 MemBase/Graph 并集上限 <strong>{union_right/total*100:.2f}%</strong>。</p>"
)
parts.append(
    f"<p>主要损失来自<strong>融合策略</strong>：{len(categories['membase_only_wrong'])+len(categories['graph_only_wrong'])} 道题只有一个引擎答对，但 harness 最终答案错误，说明排名/选择机制没有充分利用互补信息。</p>"
)
parts.append(
    f"<p>另有 {len(categories['both_right_wrong'])} 道题两个引擎都检索到了正确答案，但 harness 答案生成或 judge 失败。</p>"
)
parts.append("</div>")

section_order = [
    (
        "membase_only_wrong",
        "MemBase 对 / Graph 错（Harness 融合丢分）",
        "MemBase 已经给出正确答案，建议检查为什么 graph 低分结果或排序把 MemBase 结果挤出。",
    ),
    (
        "graph_only_wrong",
        "Graph 对 / MemBase 错（Harness 融合丢分）",
        "Graph Engine 关系/时间更准，建议提升 graph 结果在最终答案中的权重。",
    ),
    (
        "both_right_wrong",
        "两者都对但 Harness 错（生成/judge 问题）",
        "检索成功，但 LLM 答案生成或 judge 严格匹配失败。",
    ),
    (
        "both_wrong",
        "两者都错（底层检索问题）",
        "两个引擎都检索不到，需检查记忆抽取、索引或 query 理解。",
    ),
]

for key, title, desc in section_order:
    items = categories[key]
    parts.append(
        f'<div class="section"><h2>{title} <span style="font-size:14px;font-weight:400;color:var(--muted)">（{len(items)} 道）</span></h2>'
    )
    parts.append(f"<p>{desc}</p>")
    parts.append(
        "<table><thead><tr><th>Q#</th><th>Question</th><th>Gold</th><th>Harness Response</th><th>MemBase</th><th>Graph</th></tr></thead><tbody>"
    )
    for r in sorted(items, key=lambda x: x["qnum"]):
        mb = "正确" if r["membase_correct"] else "错误"
        gr = "正确" if r["graph_correct"] else "错误"
        mb_cls = "ok" if r["membase_correct"] else "miss"
        gr_cls = "ok" if r["graph_correct"] else "miss"
        parts.append(
            f'<tr><td>{r["qnum"]}</td><td>{r["question"]}</td><td>{r["gold"]}</td><td>{r["response"][:200]}</td>'
            f'<td><span class="badge {mb_cls}">{mb}</span></td><td><span class="badge {gr_cls}">{gr}</span></td></tr>'
        )
    parts.append("</tbody></table></div>")

parts.append(
    '<div class="footer">生成时间：2026-07-02 · 数据来源：runs/qa_conv30_clean/echomemory_memory_qa_results.csv</div>'
)
parts.append("</div></body></html>")

out_path = Path(r"D:\agent\echomem\echo0\harness_conv30_error_analysis.html")
out_path.write_text("".join(parts), encoding="utf-8")
print("written:", out_path)
print("summary:")
print("  harness correct:", h_right, "/", total)
print("  membase-only wrong:", len(categories["membase_only_wrong"]))
print("  graph-only wrong:", len(categories["graph_only_wrong"]))
print("  both-right wrong:", len(categories["both_right_wrong"]))
print("  both-wrong:", len(categories["both_wrong"]))
