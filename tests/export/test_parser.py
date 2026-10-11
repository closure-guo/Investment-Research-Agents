"""export/parser.py 单元测试。"""

from finance_agent.export.parser import Section, parse_markdown, split_by_chapters, strip_md_inline


def test_parse_heading():
    text = "# Title\n\n## Subtitle\n\n### Section"
    sections = parse_markdown(text)

    assert len(sections) == 3
    assert sections[0] == Section(type="heading", level=1, text="Title")
    assert sections[1] == Section(type="heading", level=2, text="Subtitle")
    assert sections[2] == Section(type="heading", level=3, text="Section")


def test_parse_table():
    text = "| A | B |\n|---|---|\n| 1 | 2 |"
    sections = parse_markdown(text)

    assert len(sections) == 1
    assert sections[0].type == "table"
    assert sections[0].rows == [["A", "B"], ["1", "2"]]


def test_parse_paragraph():
    text = "This is a paragraph.\n\nAnother paragraph."
    sections = parse_markdown(text)

    assert len(sections) == 2
    assert sections[0].type == "paragraph"
    assert "This is a paragraph." in sections[0].text
    assert sections[1].type == "paragraph"
    assert "Another paragraph." in sections[1].text


def test_parse_separator():
    text = "Line\n\n---\n\nAnother"
    sections = parse_markdown(text)

    types = [s.type for s in sections]
    assert "separator" in types


def test_split_by_chapters():
    sections = [
        Section(type="heading", level=2, text="Ch1"),
        Section(type="paragraph", text="p1"),
        Section(type="heading", level=2, text="Ch2"),
        Section(type="paragraph", text="p2"),
    ]
    chapters = split_by_chapters(sections)

    assert len(chapters) == 2
    assert chapters[0][0] == "Ch1"
    assert chapters[0][1][0].text == "p1"
    assert chapters[1][0] == "Ch2"
    assert chapters[1][1][0].text == "p2"


class TestListBlocks:
    """issue #239B：列表块不再拍平为段落。"""

    def test_consecutive_list_lines_aggregate_into_single_section(self):
        text = "前言\n\n- **方向**: watch\n- **置信度**: 55%\n- 风控观测\n\n后文"
        sections = parse_markdown(text)
        lists = [s for s in sections if s.type == "list"]
        assert len(lists) == 1
        assert lists[0].items == ["**方向**: watch", "**置信度**: 55%", "风控观测"]
        # 不再拍平进段落
        assert not any(s.type == "paragraph" and "方向" in s.text for s in sections)

    def test_ordered_list(self):
        sections = parse_markdown("1. 第一条\n2. 第二条")
        assert sections[0].type == "list"
        assert sections[0].ordered is True
        assert sections[0].items == ["第一条", "第二条"]

    def test_unordered_flag_default(self):
        sections = parse_markdown("- 甲\n- 乙")
        assert sections[0].type == "list"
        assert sections[0].ordered is False

    def test_list_stops_at_non_list_line(self):
        sections = parse_markdown("- 甲\n普通段落行")
        assert [s.type for s in sections] == ["list", "paragraph"]
        assert sections[0].items == ["甲"]

    def test_indented_continuation_lines_are_items(self):
        sections = parse_markdown("- 甲\n  - 乙\n- 丙")
        assert sections[0].type == "list"
        assert sections[0].items == ["甲", "乙", "丙"]


class TestHtmlDegradation:
    """issue #239B：<details>/<summary>/<br> 降级，HTML 字面不进正文。"""

    def test_details_wrapper_dropped_summary_becomes_heading(self):
        text = "<details>\n<summary>点击展开：风险明细</summary>\n- 条目一<br>条目二\n</details>"
        sections = parse_markdown(text)
        headings = [s for s in sections if s.type == "heading"]
        assert len(headings) == 1
        assert headings[0].level == 3
        assert headings[0].text == "点击展开：风险明细"
        # <br> → 换行 → 两个列表项
        lists = [s for s in sections if s.type == "list"]
        assert len(lists) == 1
        assert lists[0].items == ["条目一", "条目二"]
        # HTML 字面不残留
        assert not any("<details" in (s.text or "") for s in sections)
        assert not any("summary" in (s.text or "") for s in sections)

    def test_br_becomes_line_break_in_paragraph(self):
        sections = parse_markdown("第一行<br>第二行<br/>第三行")
        assert sections[0].type == "paragraph"
        assert "第一行" in sections[0].text
        assert "第二行" in sections[0].text
        assert "第三行" in sections[0].text
        assert "<br" not in sections[0].text

    def test_details_and_summary_on_same_line(self):
        """真实报告形态：<details><summary>T</summary> 同行。"""
        markdown = "<details><summary>点击展开完整图表</summary>\n- 图表条目\n</details>"
        sections = parse_markdown(markdown)
        headings = [s for s in sections if s.type == "heading"]
        assert len(headings) == 1 and headings[0].text == "点击展开完整图表"
        lists = [s for s in sections if s.type == "list"]
        assert lists and lists[0].items == ["图表条目"]
        assert not any(
            "<details" in (x.text or "") or "summary" in (x.text or "") for x in sections
        )

    def test_summary_without_details_still_degrades(self):
        sections = parse_markdown("<summary>独立摘要行</summary>")
        assert sections[0].type == "heading"
        assert sections[0].text == "独立摘要行"


def test_strip_md_inline():
    assert strip_md_inline("**加粗** 与 *斜体*") == "加粗 与 斜体"
    assert strip_md_inline("普通文本") == "普通文本"
    # 不成对的 ** 保守保留原文
    assert strip_md_inline("不成对 ** 保留") == "不成对 ** 保留"
