"""Simple markdown parser for export converters.

Parses markdown text into structured Sections that can be consumed by
docx_exporter and pptx_exporter.

issue #239B 排版保真：列表块（list Section）、HTML 折叠标签降级
（<details>/<summary>/<br>）、内联标记保留 + strip_md_inline 归一工具——
解析器不篡改文本，渲染器按自身能力选择富文本或纯文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Section:
    type: str  # "heading" | "paragraph" | "table" | "separator" | "image" | "list"
    level: int = 0
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)
    image_path: str = ""
    items: list[str] = field(default_factory=list)  # list 专用：条目文本（保留行内标记）
    ordered: bool = False  # list 专用：有序列表（1. 前缀）


# 无序列表前缀：- / * / +（后跟空格）；有序：N. 或 N）
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_ORDERED_PREFIX_RE = re.compile(r"^\d+[.)]$")

# <summary>T</summary>（可与 <details> 同行，真实报告形态）
_SUMMARY_ANY_RE = re.compile(r"<summary[^>]*>(.*?)</summary>", re.IGNORECASE)
# details/summary 包装标签（纯包装，剥离不留痕）
_TAG_WRAPPER_RE = re.compile(r"</?(?:details|summary)[^>]*>", re.IGNORECASE)
# 行内 <br> 变体
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _degrade_html(lines: list[str]) -> list[str]:
    """HTML 折叠标签降级（issue #239B）：`<summary>T</summary>` → `### T` 标题行；
    details/summary 包装标签 anywhere 剥离；`<br>` 变体 → 换行。列表行的
    <br> 续行补原列表标记保持条目归属（`- a<br>b` → `- a` + `- b`）。"""
    out: list[str] = []
    for line in lines:
        m = _SUMMARY_ANY_RE.search(line)
        if m:
            out.append(f"### {m.group(1).strip()}")
            rest = _TAG_WRAPPER_RE.sub("", line[: m.start()] + line[m.end() :]).strip()
            if rest:
                out.append(rest)
            continue
        if _BR_RE.search(line):
            stripped_tags = _TAG_WRAPPER_RE.sub("", line)
            parts = _BR_RE.sub("\n", stripped_tags).split("\n")
            list_match = _LIST_ITEM_RE.match(line)
            if list_match and len(parts) > 1:
                marker = list_match.group(2)
                out.append(parts[0])
                out.extend(f"{marker} {p.strip()}" for p in parts[1:])
            else:
                out.extend(parts)
            continue
        cleaned = _TAG_WRAPPER_RE.sub("", line)
        if line.strip() and not cleaned.strip():
            continue  # 行只剩包装标签 → 丢弃
        out.append(cleaned)
    return out


def strip_md_inline(text: str) -> str:
    """去 `**bold**`/`*italic*` 行内标记的纯文本归一（issue #239B）。

    不成对的 `**` 保守保留原文（数学/乘号等场景不误伤）。
    """
    result = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    result = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", result)
    return result


def parse_markdown(text: str) -> list[Section]:
    """Parse markdown text into a list of Sections."""
    lines = _degrade_html(text.splitlines())
    sections: list[Section] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # List block（连续列表行聚合为一个 Section，行内标记保留）
        list_match = _LIST_ITEM_RE.match(line)
        if list_match:
            items: list[str] = []
            ordered = _ORDERED_PREFIX_RE.match(list_match.group(2)) is not None
            while i < len(lines):
                m = _LIST_ITEM_RE.match(lines[i])
                if not m:
                    break
                items.append(m.group(3).strip())
                i += 1
            sections.append(Section(type="list", items=items, ordered=ordered))
            continue

        # Separator
        if stripped == "---":
            sections.append(Section(type="separator"))
            i += 1
            continue

        # Heading
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            sections.append(Section(type="heading", level=level, text=heading_match.group(2)))
            i += 1
            continue

        # Image: ![alt](path)
        image_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if image_match:
            sections.append(
                Section(type="image", text=image_match.group(1), image_path=image_match.group(2))
            )
            i += 1
            continue

        # Table
        if stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            rows = _parse_table_rows(table_lines)
            if rows:
                sections.append(Section(type="table", rows=rows))
            continue

        # Paragraph (skip empty lines)
        if stripped:
            para_lines = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip():
                nxt = lines[i].strip()
                # 段落中途遇到列表行则断开（列表另起 Section）
                if _LIST_ITEM_RE.match(lines[i]):
                    break
                para_lines.append(nxt)
                i += 1
            sections.append(Section(type="paragraph", text=" ".join(para_lines)))
            continue

        # Empty line
        i += 1

    return sections


def _parse_table_rows(lines: list[str]) -> list[list[str]]:
    """Parse markdown table lines into rows of cells."""
    rows = []
    for line in lines:
        # Skip separator rows like |---|---|
        if re.match(r"^\|(?:\s*[-:]+\s*\|)+$", line):
            continue
        # Split by | and strip
        cells = [cell.strip() for cell in line.split("|")]
        # Remove empty cells from leading/trailing |
        cells = [c for c in cells if c or c == "0"]
        if cells:
            rows.append(cells)
    return rows


def split_by_chapters(sections: list[Section]) -> list[tuple[str, list[Section]]]:
    """Split sections into chapters, where each chapter starts with a level-2 heading.

    Returns list of (chapter_title, chapter_sections).
    """
    chapters: list[tuple[str, list[Section]]] = []
    current_title = ""
    current_sections: list[Section] = []

    for sec in sections:
        if sec.type == "heading" and sec.level == 2:
            if current_sections or current_title:
                chapters.append((current_title, current_sections))
            current_title = sec.text
            current_sections = []
        else:
            current_sections.append(sec)

    if current_sections or current_title:
        chapters.append((current_title, current_sections))

    return chapters
