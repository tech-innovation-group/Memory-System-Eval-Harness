#!/usr/bin/env python3
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path("/Users/chx/locomo-eval-web")
SRC = ROOT / "docs" / "echomemory_mm_cvpr_condensed_submission_draft_v3_20260615.md"
OUT = ROOT / "web" / "static" / "generated-reports" / "echomemory_mm_cvpr_condensed_submission_draft_v3_20260615.html"


def flush_list(list_type: str | None, items: list[str]) -> str:
    if not list_type or not items:
        return ""
    tag = "ol" if list_type == "ol" else "ul"
    body = "".join(f"<li>{item}</li>" for item in items)
    return f"<{tag}>{body}</{tag}>"


def render_inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return text


def render_table(lines: list[str]) -> str:
    rows = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [render_inline(cell.strip()) for cell in stripped[1:-1].split("|")]
        rows.append(cells)
    if len(rows) < 2:
        return "".join(f"<p>{render_inline(line)}</p>" for line in lines)
    header = rows[0]
    body = rows[2:] if len(rows) > 2 else []
    thead = "<thead><tr>" + "".join(f"<th>{cell}</th>" for cell in header) + "</tr></thead>"
    tbody_rows = []
    for row in body:
        tbody_rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    tbody = "<tbody>" + "".join(tbody_rows) + "</tbody>"
    return f"<div class='table-wrap'><table>{thead}{tbody}</table></div>"


def render_markdown(md_text: str) -> str:
    lines = md_text.splitlines()
    parts: list[str] = []
    paragraph: list[str] = []
    list_type: str | None = None
    list_items: list[str] = []
    table_lines: list[str] = []
    hero_open = False
    section_open = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(s.strip() for s in paragraph).strip()
            if text:
                parts.append(f"<p>{render_inline(text)}</p>")
            paragraph = []

    def flush_table() -> None:
        nonlocal table_lines
        if table_lines:
            parts.append(render_table(table_lines))
            table_lines = []

    def flush_all() -> None:
        nonlocal list_type, list_items
        flush_paragraph()
        flush_table()
        if list_items:
            parts.append(flush_list(list_type, list_items))
            list_type = None
            list_items = []

    def open_section(title: str) -> None:
        nonlocal hero_open, section_open
        flush_all()
        if hero_open:
            parts.append("</div>")
            hero_open = False
        if section_open:
            parts.append("</section>")
        parts.append(f"<section class='section'><h2>{render_inline(title)}</h2>")
        section_open = True

    def open_hero(title: str) -> None:
        nonlocal hero_open
        flush_all()
        parts.append(f"<div class='hero'><h1>{render_inline(title)}</h1>")
        hero_open = True

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_all()
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            if list_items:
                parts.append(flush_list(list_type, list_items))
                list_type = None
                list_items = []
            table_lines.append(stripped)
            continue

        if table_lines:
            flush_table()

        if stripped.startswith("## "):
            open_section(stripped[3:])
            continue

        if stripped.startswith("### "):
            flush_all()
            parts.append(f"<h3>{render_inline(stripped[4:])}</h3>")
            continue

        if stripped.startswith("# "):
            open_hero(stripped[2:])
            continue

        m_num = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m_num:
            flush_paragraph()
            if list_type not in (None, "ol"):
                parts.append(flush_list(list_type, list_items))
                list_items = []
            list_type = "ol"
            list_items.append(render_inline(m_num.group(1)))
            continue

        m_bullet = re.match(r"^-\s+(.*)$", stripped)
        if m_bullet:
            flush_paragraph()
            if list_type not in (None, "ul"):
                parts.append(flush_list(list_type, list_items))
                list_items = []
            list_type = "ul"
            list_items.append(render_inline(m_bullet.group(1)))
            continue

        if stripped.startswith("> "):
            flush_all()
            parts.append(f"<blockquote>{render_inline(stripped[2:])}</blockquote>")
            continue

        paragraph.append(stripped)

    flush_all()
    if hero_open:
        parts.append("</div>")
    if section_open:
        parts.append("</section>")
    return "\n".join(parts)


def build_page(body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory-MM Condensed Submission Draft v3</title>
  <style>
    :root{{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#617184; --line:#dbe3ee;
      --blue:#2563eb; --blue-soft:#eef4ff; --green:#0f9f6e; --amber:#b7791f; --shadow:0 10px 28px rgba(15,23,42,.08);
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.78 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1100px;margin:0 auto;padding:28px 18px 54px}}
    .hero,.section{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:28px 30px;margin-bottom:16px}}
    .section{{padding:20px 24px;margin-bottom:16px}}
    h1,h2,h3{{margin:0 0 12px;line-height:1.2}}
    h1{{font-size:32px}}
    h2{{font-size:22px}}
    h3{{font-size:17px;color:#20314d}}
    p{{margin:0 0 12px}}
    ul,ol{{margin:10px 0 14px 20px;padding:0}}
    li{{margin:6px 0}}
    code{{background:#f3f6fb;border:1px solid #e4ebf5;border-radius:6px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    blockquote{{margin:12px 0;padding:10px 14px;border-left:4px solid var(--blue);background:#f8fbff;color:#42556e;border-radius:8px}}
    .topnote{{margin-bottom:14px;color:var(--muted)}}
    .meta{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
    .tag{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;background:var(--blue-soft);color:var(--blue)}}
    .table-wrap{{overflow:auto;margin:14px 0 16px}}
    table{{width:100%;border-collapse:collapse;font-size:14px}}
    th,td{{border:1px solid var(--line);padding:10px 12px;vertical-align:top;text-align:left}}
    th{{background:#f8fbff}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topnote">
      <div class="meta">
        <span class="tag">condensed draft v3</span>
        <span class="tag">30-paper map</span>
        <span class="tag">21-case real-code subset</span>
        <span class="tag">main-results snapshot</span>
        <span class="tag">three-clock time</span>
        <span class="tag">coverage-aware gating</span>
        <span class="tag">type-aware second pass</span>
      </div>
    </div>
    {body}
  </div>
</body>
</html>
"""


def main() -> None:
    md_text = SRC.read_text(encoding="utf-8")
    body = render_markdown(md_text)
    OUT.write_text(build_page(body), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
