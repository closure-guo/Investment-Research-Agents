"""Markdown → PDF (.pdf) converter，基于 WeasyPrint（HTML → PDF）。

WeasyPrint 系统库（pango/cairo）缺失时惰性导入失败，由调用方容错（置 None）。
"""

from __future__ import annotations

import re
from pathlib import Path

from finance_agent.export.parser import parse_markdown

_CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm;
    @bottom-center { content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; }
}
body {
    font-family: "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 10.5pt; line-height: 1.6; color: #1a1a1a;
}
h1 { font-size: 20pt; text-align: center; margin: 0 0 12pt; }
h2 { font-size: 14pt; border-bottom: 1px solid #ddd; padding-bottom: 4pt; margin: 16pt 0 8pt; }
h3 { font-size: 12pt; margin: 12pt 0 6pt; }
p { margin: 6pt 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 1px solid #999; padding: 4pt 8pt; font-size: 9.5pt; text-align: left; }
th { background: #f0f0f0; }
img { max-width: 100%; display: block; margin: 8pt auto; }
hr { border: none; border-top: 1px solid #ccc; margin: 12pt 0; }
"""


def _inline_to_html(text: str) -> str:
    """escape 后转内联样式（issue #239B）：`**bold**`→<strong>、`*italic*`→<em>。"""
    from html import escape

    escaped = escape(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def _sections_to_html(markdown_text: str) -> str:
    from html import escape

    parts: list[str] = []
    for sec in parse_markdown(markdown_text):
        if sec.type == "heading":
            parts.append(f"<h{min(sec.level, 6)}>{escape(sec.text)}</h{min(sec.level, 6)}>")
        elif sec.type == "paragraph":
            parts.append(f"<p>{_inline_to_html(sec.text)}</p>")
        elif sec.type == "list":
            tag = "ol" if sec.ordered else "ul"
            items = "".join(f"<li>{_inline_to_html(item)}</li>" for item in sec.items)
            parts.append(f"<{tag}>{items}</{tag}>")
        elif sec.type == "table":
            rows_html = []
            for i, row in enumerate(sec.rows):
                tag = "th" if i == 0 else "td"
                cells = "".join(f"<{tag}>{_inline_to_html(c)}</{tag}>" for c in row)
                rows_html.append(f"<tr>{cells}</tr>")
            parts.append(f"<table>{''.join(rows_html)}</table>")
        elif sec.type == "separator":
            parts.append("<hr/>")
        elif sec.type == "image":
            path = Path(sec.image_path)
            if path.exists():
                parts.append(f'<img src="{path.resolve().as_uri()}" alt="{escape(sec.text)}"/>')
    return "\n".join(parts)


def markdown_to_pdf(markdown_text: str, output_path: str, stock_name: str = "") -> str:
    """Convert markdown report to PDF document.

    Parameters
    ----------
    markdown_text : str
        Full markdown report.
    output_path : str
        Destination file path.
    stock_name : str
        Stock name（当前仅占位，标题沿用 markdown 自带 H1）.

    Returns
    -------
    str
        The output_path.
    """
    from weasyprint import HTML  # 惰性导入：系统库缺失时抛 ImportError，由调用方容错

    body = _sections_to_html(markdown_text)
    html = (
        f"<html><head><meta charset='utf-8'/><style>{_CSS}</style></head><body>{body}</body></html>"
    )
    HTML(string=html).write_pdf(output_path)
    return output_path
