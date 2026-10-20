"""Analyst prompt 的 JSON 示例必须是可解析的 AnalystReport。

incident 017 延伸：四个 analyst prompt 的 `claims` 示例曾把 `"markdown"` 写进数组、
缺少对象括号、尾逗号，示例本身不是合法 JSON——模型在「照示例输出」时会被误导。
本测试把示例当真解析并过 Pydantic，防止再次退化。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from finance_agent.models import AnalystReport

PROMPTS = (
    ("technical_analyst.md", "technical"),
    ("sentiment_analyst.md", "sentiment"),
    ("fundamental_analyst.md", "fundamental"),
    ("macro_analyst.md", "macro"),
)


def _first_json_block(text: str) -> str:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    assert match is not None, "prompt 缺少 ```json 示例块"
    return match.group(1)


@pytest.mark.parametrize(("filename", "agent_name"), PROMPTS)
def test_analyst_prompt_json_example_is_valid_report(filename: str, agent_name: str) -> None:
    text = (Path("src/finance_agent/prompts") / filename).read_text(encoding="utf-8")
    payload = json.loads(_first_json_block(text))
    report = AnalystReport.model_validate(payload)
    assert report.agent_name == agent_name
    assert report.markdown.startswith("## ")
    assert report.claims
