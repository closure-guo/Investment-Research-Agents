"""section 词典冻结与交叉一致性（harden-eval-implementation-decoupling）。"""

import re
from pathlib import Path

from evals.dataset_seed import load_items
from evals.sections import SECTION_SYNONYMS, SECTION_SYNONYMS_VERSION, find_section

# 冻结快照：词典 keys 与版本（内容变更必须显式递增版本并更新此快照）
_FROZEN_VERSION = 1
_FROZEN_KEYS = (
    "宏观环境",
    "基本面",
    "偿债能力",
    "盈利能力",
    "技术面",
    "市场情绪",
    "估值",
    "风险提示",
    "交易建议",
)


class TestFrozen:
    def test_version_and_keys_frozen(self):
        assert SECTION_SYNONYMS_VERSION == _FROZEN_VERSION
        assert tuple(SECTION_SYNONYMS) == _FROZEN_KEYS

    def test_synonym_lists_nonempty_and_unique(self):
        for key, syns in SECTION_SYNONYMS.items():
            assert syns, key
            assert len(syns) == len(set(syns)), f"{key} 有重复同义词"

    def test_dataset_must_cover_covered_by_dictionary(self):
        """评估期望章节（dataset must_cover）必须全部被词典 key 覆盖。"""
        for item in load_items(pool="baseline") + load_items(pool="rotating"):
            for section in (item.get("expected_output") or {}).get("must_cover", []):
                assert section in SECTION_SYNONYMS, f"must_cover 章节未入词典: {section}"
                assert find_section(section, f"## {section}"), f"{section} 字面命中失败"

    def test_prompt_report_section_words_covered(self):
        """prompt 中「## X分析」形态的章节词应被词典覆盖（allowlist 放行已知例外）。"""
        # quick_mode「股票深度分析」是操作模式标题（深度分析 vs 只读查询），
        # 不是报告正文章节——与「分析要点/方法论」同性质，放行
        allowlist = {"分析要点", "分析方法论", "股票深度分析"}
        uncovered = []
        for md in Path("src/finance_agent/prompts").glob("*.md"):
            text = md.read_text(encoding="utf-8")
            for word in re.findall(r"##\s*([\u4e00-\u9fff]{2,6}分析)[^\n]*", text):
                if word in allowlist:
                    continue
                if not any(s in word for syns in SECTION_SYNONYMS.values() for s in syns):
                    uncovered.append(f"{md.name}: {word}")
        assert not uncovered, f"prompt 章节词未被词典覆盖: {uncovered}"
