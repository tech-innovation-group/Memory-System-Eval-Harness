#!/usr/bin/env python3
from __future__ import annotations

import html
import re
from pathlib import Path


ROOT = Path("/Users/chx/locomo-eval-web")
SRC = ROOT / "docs" / "echomemory_mm_cvpr_main_submission_draft_v18_20260617.md"
OUT = ROOT / "web" / "static" / "generated-reports" / "echomemory_mm_cvpr_main_submission_draft_v18_20260617.html"


def flush_list(list_type: str | None, items: list[str]) -> str:
    if not list_type or not items:
        return ""
    tag = "ol" if list_type == "ol" else "ul"
    return f"<{tag}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{tag}>"


def render_inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return text


def is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        token = cell.replace("-", "").replace(":", "").replace(" ", "")
        if token:
            return False
    return True


def render_table(rows: list[list[str]]) -> str:
    if len(rows) < 2:
        return "".join(f"<p>{render_inline(' | '.join(row))}</p>" for row in rows)
    header = rows[0]
    body = rows[2:] if len(rows) >= 2 and is_table_separator(rows[1]) else rows[1:]
    thead = "".join(f"<th>{render_inline(cell)}</th>" for cell in header)
    tbody_parts = []
    for row in body:
        cells = "".join(f"<td>{render_inline(cell)}</td>" for cell in row)
        tbody_parts.append(f"<tr>{cells}</tr>")
    return (
        "<table><thead><tr>"
        + thead
        + "</tr></thead><tbody>"
        + "".join(tbody_parts)
        + "</tbody></table>"
    )


def render_markdown(md_text: str) -> str:
    lines = md_text.splitlines()
    parts: list[str] = []
    paragraph: list[str] = []
    list_type: str | None = None
    list_items: list[str] = []
    table_rows: list[list[str]] = []
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
        nonlocal table_rows
        if table_rows:
            parts.append(render_table(table_rows))
            table_rows = []

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
        stripped = raw.strip()

        if is_table_line(raw):
            flush_paragraph()
            if list_items:
                parts.append(flush_list(list_type, list_items))
                list_type = None
                list_items = []
            table_rows.append(split_table_cells(raw))
            continue

        if not stripped:
            flush_all()
            continue

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
            flush_table()
            if list_type not in (None, "ol"):
                parts.append(flush_list(list_type, list_items))
                list_items = []
            list_type = "ol"
            list_items.append(render_inline(m_num.group(1)))
            continue

        m_bullet = re.match(r"^-\s+(.*)$", stripped)
        if m_bullet:
            flush_paragraph()
            flush_table()
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


def build_page(title: str, body: str) -> str:
    chips = "".join(
        f"<span class='tag'>{html.escape(tag)}</span>"
        for tag in ("main submission", "v18", "paper-facing")
    )
    return f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>{html.escape(title)}</title>
  <style>
    :root{{--bg:#f6f8fc;--panel:#fff;--text:#18212f;--muted:#617184;--line:#dbe3ee;--blue:#2563eb;--blue-soft:#eef4ff;--shadow:0 10px 28px rgba(15,23,42,.08);}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.78 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1100px;margin:0 auto;padding:28px 18px 54px}}
    .hero,.section{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:28px 30px;margin-bottom:16px}}
    .section{{padding:20px 24px;margin-bottom:16px}}
    h1,h2,h3{{margin:0 0 12px;line-height:1.2}}
    h1{{font-size:32px}} h2{{font-size:22px}} h3{{font-size:17px;color:#20314d}}
    p{{margin:0 0 12px}}
    ul,ol{{margin:10px 0 14px 20px;padding:0}}
    li{{margin:6px 0}}
    code{{background:#f3f6fb;border:1px solid #e4ebf5;border-radius:6px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    blockquote{{margin:12px 0;padding:10px 14px;border-left:4px solid var(--blue);background:#f8fbff;color:#42556e;border-radius:8px}}
    .meta{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}}
    .tag{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;background:var(--blue-soft);color:var(--blue)}}
    table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0 14px}}
    th,td{{border-top:1px solid var(--line);text-align:left;vertical-align:top;padding:10px 8px}}
    th{{background:#fbfcfe;color:var(--muted);font-size:12px;text-transform:uppercase}}
  </style>
</head>
<body>
  <div class='wrap'>
    <div class='meta'>{chips}</div>
    {body}
  </div>
</body>
</html>"""


def main() -> None:
    body = render_markdown(SRC.read_text(encoding="utf-8"))
    OUT.write_text(build_page(SRC.stem.replace("_", " "), body), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
