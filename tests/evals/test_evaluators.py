"""确定性评估器单测:零 LLM 调用、同义词匹配、expected 缺省跳过。"""

from unittest.mock import patch

from evals.evaluators import section_coverage, ticker_match
from evals.sections import find_section


class TestFindSection:
    def test_exact_hit(self):
        assert find_section("偿债能力", "...偿债能力较强...") is True

    def test_synonym_hit(self):
        # spec「章节命中 SHALL 经同义词词典匹配」:"偿债分析" 命中 "偿债能力"
        assert find_section("偿债能力", "下面进行偿债分析...") is True

    def test_english_synonym_hit(self):
        assert find_section("盈利能力", "ROE 维持在 25%") is True

    def test_miss(self):
        assert find_section("技术面", "公司盈利良好") is False

    def test_unknown_section_falls_back_to_literal(self):
        # 词典没有的章节名,退化为字面匹配
        assert find_section("冷门章节", "包含冷门章节四个字") is True
        assert find_section("冷门章节", "完全没有相关内容") is False

    def test_risk_alone_no_longer_matches_risk_warning(self):
        # 裸 "风险" 已从「风险提示」同义词移除:无风险利率不应误判覆盖
        assert find_section("风险提示", "无风险利率下行,风险收益比改善") is False
        assert find_section("风险提示", "风险提示:市场波动加大") is True

    def test_ascii_synonym_uses_word_boundary(self):
        # 纯 ASCII 词条按词边界匹配:OPENAI/PIPELINE 不含独立 PE
        assert find_section("估值", "公司与 OPENAI 合作,PIPELINE 自动化") is False
        assert find_section("估值", "当前 PE 25 倍,PB 3 倍") is True
        assert find_section("盈利能力", "ROE 维持 25%") is True

    def test_ascii_synonym_adjacent_to_cjk_matches(self):
        # \b 在 Unicode 下把 CJK 当词字符,改用 ASCII 词字符环视后紧贴写法仍命中
        assert find_section("估值", "PE为25倍") is True
        assert find_section("盈利能力", "ROE达25%") is True
        assert find_section("估值", "PB仅1.2倍") is True
        # 边界语义不退化:OPENAI/PIPELINE 仍不命中
        assert find_section("估值", "公司与OPENAI合作") is False


class TestSectionCoverage:
    def test_full_coverage(self):
        report = "偿债能力分析:良好。盈利能力:ROE 高。技术面:均线上扬。风险提示:注意波动。"
        result = section_coverage(
            report, {"must_cover": ["偿债能力", "盈利能力", "技术面", "风险提示"]}
        )
        assert result == {"name": "section_coverage", "value": 1.0, "comment": None}

    def test_partial_coverage_with_synonym(self):
        # "偿债分析" 命中 "偿债能力";"风险提示" 缺失
        report = "偿债分析:良好。盈利能力:ROE 高。技术面:均线上扬。"
        result = section_coverage(
            report, {"must_cover": ["偿债能力", "盈利能力", "技术面", "风险提示"]}
        )
        assert result["value"] == 0.75
        assert "风险提示" in result["comment"]

    def test_missing_must_cover_returns_none(self):
        assert section_coverage("任何报告", {}) is None
        assert section_coverage("任何报告", {"ticker": "600519"}) is None

    def test_none_report_scores_zero_when_must_cover_present(self):
        result = section_coverage(None, {"must_cover": ["偿债能力"]})
        assert result["value"] == 0.0


class TestTickerMatch:
    def test_match(self):
        assert ticker_match("600519", {"ticker": "600519"})["value"] == 1.0

    def test_mismatch(self):
        assert ticker_match("000001", {"ticker": "600519"})["value"] == 0.0

    def test_missing_ticker_in_expected_returns_none(self):
        assert ticker_match("600519", {}) is None

    def test_none_ticker_scores_zero(self):
        assert ticker_match(None, {"ticker": "600519"})["value"] == 0.0


class TestTickerMatchNormalization:
    """#55:ticker_match 比较经归一（交易所后缀/空白/大小写）——后缀变体不再误判 0 分。"""

    def test_suffix_variant_matches(self):
        assert ticker_match("600519.SH", {"ticker": "600519"})["value"] == 1.0
        assert ticker_match("600519", {"ticker": "600519.SH"})["value"] == 1.0

    def test_case_and_whitespace_normalized(self):
        assert ticker_match(" 600519.sh ", {"ticker": "600519"})["value"] == 1.0

    def test_real_mismatch_still_zero(self):
        assert ticker_match("000001.SZ", {"ticker": "600519.SH"})["value"] == 0.0


class TestSectionCoverageDedup:
    """#55:must_cover 重复项去重——重复的缺失项不得虚增分母压低覆盖率,
    缺失清单亦不得重复罗列。"""

    def test_duplicate_missing_counts_once(self):
        # 「估值」缺失且重复:原实现分母 3、缺失 2 → 0.333;去重后 1/2=0.5
        result = section_coverage("含风险提示章节", {"must_cover": ["估值", "估值", "风险提示"]})
        assert result["value"] == 0.5
        assert result["comment"] == "缺失章节: 估值"

    def test_duplicate_found_scores_full(self):
        result = section_coverage(
            "有估值也有风险提示", {"must_cover": ["估值", "估值", "风险提示"]}
        )
        assert result["value"] == 1.0


class TestNoLlmCall:
    def test_deterministic_evaluators_never_call_llm(self):
        # spec「确定性评估器 SHALL NOT 发起任何 LLM 调用」
        with patch("litellm.completion") as mock_llm:
            section_coverage("偿债能力 盈利能力", {"must_cover": ["偿债能力", "盈利能力"]})
            ticker_match("600519", {"ticker": "600519"})
        mock_llm.assert_not_called()

    def test_deterministic_evaluators_source_has_no_llm_import(self):
        # 源码级守卫:确定性评估器任何 LLM 依赖引入都会变红
        import inspect

        import evals.evaluators
        import evals.sections

        for module in (evals.evaluators, evals.sections):
            source = inspect.getsource(module)
            assert "litellm" not in source
            assert "call_llm" not in source
