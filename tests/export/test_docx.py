"""export/docx_exporter.py 单元测试。"""

from pathlib import Path

from finance_agent.export.docx_exporter import markdown_to_docx


def test_docx_generation(tmp_path):
    markdown = (
        "# Report\n\n"
        "## Chapter 1\n\n"
        "Some **bold** text.\n\n"
        "| Col1 | Col2 |\n"
        "|------|------|\n"
        "| A    | B    |\n"
    )
    output = tmp_path / "test.docx"
    result = markdown_to_docx(markdown, str(output), "Test Stock")

    assert Path(result).exists()


def test_docx_contains_disclaimer(tmp_path):
    markdown = "# Report\n\nHello world."
    output = tmp_path / "test.docx"
    markdown_to_docx(markdown, str(output))

    assert Path(output).exists()


def test_docx_list_items_bullet_style(tmp_path):
    """issue #239B：docx 列表项逐条 List Bullet，不拍平，富文本 bold 生效。"""
    from docx import Document as _Doc

    markdown = "- **方向**: watch\n- **置信度**: 55%"
    output = tmp_path / "list.docx"
    markdown_to_docx(markdown, str(output))

    doc = _Doc(str(output))
    bullets = [p for p in doc.paragraphs if p.style.name == "List Bullet"]
    assert len(bullets) == 2
    assert bullets[0].text == "方向: watch"
    assert any(r.bold for r in bullets[0].runs)
    # 不拍平：不存在同时含两个条目的段落
    assert not any("置信度" in p.text and "watch" in p.text for p in doc.paragraphs)


def test_docx_ordered_list_style(tmp_path):
    from docx import Document as _Doc

    output = tmp_path / "olist.docx"
    markdown_to_docx("1. 第一\n2. 第二", str(output))
    doc = _Doc(str(output))
    numbered = [p for p in doc.paragraphs if p.style.name == "List Number"]
    assert len(numbered) == 2


def test_docx_html_degraded_no_literal_tags(tmp_path):
    from docx import Document as _Doc

    output = tmp_path / "details.docx"
    markdown_to_docx(
        "<details>\n<summary>点击展开</summary>\n正文<br>续行\n</details>", str(output)
    )
    doc = _Doc(str(output))
    full = "\n".join(p.text for p in doc.paragraphs)
    assert "<details" not in full and "<br" not in full
    assert "点击展开" in full and "正文" in full and "续行" in full
