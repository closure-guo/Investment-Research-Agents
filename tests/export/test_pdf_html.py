"""pdf_exporter HTML 构建路径测试（不依赖 weasyprint——markdown_to_pdf 内部才惰性导入）。

issue #239B 排版保真：列表 <ul>/<ol>、内联 <strong>/<em>、HTML 折叠降级。
端到端 PDF 生成见 test_pdf.py（importorskip weasyprint，CI 有字体与依赖）。
"""

from finance_agent.export.pdf_exporter import _sections_to_html


def test_sections_to_html_lists_and_inline():
    html = _sections_to_html("- **方向**: watch\n- 风险\n\n正文 **加粗** 段落")
    assert "<ul><li><strong>方向</strong>: watch</li><li>风险</li></ul>" in html
    assert "<strong>加粗</strong>" in html
    assert "**" not in html


def test_sections_to_html_ordered_list():
    html = _sections_to_html("1. 第一\n2. 第二")
    assert "<ol><li>第一</li><li>第二</li></ol>" in html


def test_sections_to_html_details_degraded():
    html = _sections_to_html("<details>\n<summary>点击展开</summary>\n正文<br>续行\n</details>")
    assert "<details" not in html and "summary>" not in html
    assert "<br" not in html
    assert "点击展开" in html and "正文" in html and "续行" in html
