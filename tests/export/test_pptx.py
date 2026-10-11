"""export/pptx_exporter.py 单元测试。"""

from pathlib import Path

from finance_agent.export.pptx_exporter import markdown_to_pptx


def test_pptx_generation(tmp_path):
    markdown = "## Chapter 1\n\nContent here.\n\n## Chapter 2\n\nMore content.\n"
    output = tmp_path / "test.pptx"
    result = markdown_to_pptx(markdown, str(output), "Test Stock")

    assert Path(result).exists()


def test_pptx_contains_disclaimer(tmp_path):
    markdown = "## Chapter 1\n\nHello world."
    output = tmp_path / "test.pptx"
    markdown_to_pptx(markdown, str(output))

    assert Path(output).exists()


def _iter_text_frames(prs):
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                yield shape.text_frame


def test_pptx_list_items_no_literal_asterisks(tmp_path):
    """issue #239B：pptx 列表项缩进渲染，文本经 strip_md_inline 无星号残留。"""
    markdown = "## 观点\n\n- **方向**: watch\n- **置信度**: 55%"
    output = tmp_path / "list.pptx"
    markdown_to_pptx(markdown, str(output), "Test Stock")

    from pptx import Presentation

    prs = Presentation(str(output))
    texts = [p.text for tf in _iter_text_frames(prs) for p in tf.paragraphs]
    assert any("方向: watch" in t for t in texts)
    assert not any("**" in t for t in texts)
    # 列表项有缩进层级
    levels = [p.level for tf in _iter_text_frames(prs) for p in tf.paragraphs if p.text]
    assert any(lv >= 1 for lv in levels)


def test_pptx_paragraph_stripped_inline(tmp_path):
    markdown = "## 概述\n\n这是**加粗**段落。"
    output = tmp_path / "para.pptx"
    markdown_to_pptx(markdown, str(output), "Test Stock")

    from pptx import Presentation

    prs = Presentation(str(output))
    texts = [p.text for tf in _iter_text_frames(prs) for p in tf.paragraphs]
    assert any("这是加粗段落。" in t for t in texts)
    assert not any("**" in t for t in texts)
