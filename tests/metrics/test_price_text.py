"""price_text 量纲归类器 + 价格承诺账本直测（refactor-price-check-dimension-classifier，C1）。

归类是「什么数字是什么量纲」的唯一实现；历史误报语境（#254/#337/#566/#579/#582/
#599 六族）以归类类别直测钉死——新增豁免 SHALL 以归类规则表达，不再开 token 表。
"""

from finance_agent.metrics.price_text import (
    PRICE_CANDIDATE,
    alias_occurrences,
    classify,
    extract_commitments,
)


def _cl(text, span=None, scale=100.0):
    aliases = alias_occurrences(text)
    if span is None:
        # 默认取第一个数字 occurrence
        import re

        m = re.search(r"\d+(?:\.\d+)?", text)
        assert m, text
        span = (m.start(), m.end())
    return classify(text, span, aliases=aliases, scale=scale)


class TestClassifyDimensions:
    def test_direction_bound_price_is_candidate(self):
        """「跌破 15.0」方向词绑定 → 价位候选。"""
        assert _cl("若收盘跌破 15.0 无条件清仓") == PRICE_CANDIDATE

    def test_percent_suffix_non_price(self):
        """百分比后缀 → 非价格语境（600015 的 18.35% 阈值族）。"""
        assert _cl("单季净利降幅收敛至 18.35% 以下") != PRICE_CANDIDATE

    def test_peg_keyword_window_non_price(self):
        """PEG 词窗（#588）→ 非价格语境。"""
        assert _cl("PEG≈2.07 处于历史中枢下方") != PRICE_CANDIDATE

    def test_multiply_sign_non_price(self):
        """×量纲乘号（605033）→ 非价格语境。"""
        assert _cl("约为 2×日波动以上") != PRICE_CANDIDATE

    def test_enum_marker_structural(self):
        """编号列表记号「3. 」（301220）→ 结构记号。"""
        assert _cl('…清空"\n3. "2026 年报') != PRICE_CANDIDATE

    def test_var_context_non_price(self):
        """VaR 风险度量语境（688072）→ 非价格语境。"""
        assert _cl("VaR95单日6.6%") != PRICE_CANDIDATE

    def test_t_plus_structural(self):
        """T+N 周期记号 → 结构记号。"""
        assert _cl("T+20 个交易日内考核") != PRICE_CANDIDATE

    def test_index_name_structural(self):
        """指数名前缀数字（沪深300）→ 结构记号。"""
        assert _cl("相对沪深300 超额收益") != PRICE_CANDIDATE

    def test_ratio_chain_structural(self):
        """比率比较链（beta 0.8628<1，600198）→ 结构记号。"""
        assert _cl("经 beta 0.8628<1 校准后") != PRICE_CANDIDATE

    def test_date_structural(self):
        """日期 → 结构记号。"""
        assert _cl("截至 2026-09-30 的持仓") != PRICE_CANDIDATE

    def test_off_scale(self):
        """指数点位量级（1170 vs 基准 100）→ 量纲外。"""
        assert _cl("回撤至 1170 附近", scale=100.0) != PRICE_CANDIDATE

    def test_sub_unit_ratio(self):
        """0-1 小数（置信度形态）→ 子单元比值。"""
        assert _cl("置信度约 0.85") != PRICE_CANDIDATE

    def test_meta_discussion(self):
        """自引用讨论句（打回/误读元话语）→ 非价位。"""
        text = "上一稿被误读为数值价位 60，本次已修正表述"
        assert _cl(text) != PRICE_CANDIDATE


class TestAliasOccurrences:
    def test_ma_boundary_guard(self):
        """MA5 不命中 MA50（后随数字）；中文前邻不受影响（#254）。"""
        assert alias_occurrences("ma50压力") == []
        assert alias_occurrences("ema5金叉") == []
        assert len(alias_occurrences("穿ma60确认")) == 1


class TestExtractCommitments:
    def test_ledger_carries_direction_and_dirs(self):
        """账本条目携带最近方向词与窗口完整方向集（复合回踩豁免消费）。

        同值双 occurrence 各自绑定最近方向词（第一处 down、第二处 up），
        方向集共享完整 {down, up}（复合回踩豁免按值取集）。
        """
        text = "先跌破 16.73 确认，再站稳 16.73 之上"
        out = extract_commitments(text, scale=100.0, aliases=[])
        assert len(out) == 2
        assert out[0].direction == "down" and out[1].direction == "up"  # 各自最近词胜出
        for c in out:
            assert {"up", "down"} <= set(c.dirs)  # 方向集共享（按值聚合）

    def test_ledger_excludes_non_price(self):
        """归类为非价位的数字不进账本（18.35% / T+20 / (1)）。"""
        out = extract_commitments(
            "降幅 18.35%，T+20 内（1）修复，跌破 15.0 清仓", scale=100.0, aliases=[]
        )
        assert [c.value for c in out] == [15.0]

    def test_ledger_order_follows_text(self):
        """账本顺序 = 文本出现顺序（消费方摘录/门禁序确定性）。"""
        out = extract_commitments("跌破 15.0 清仓；站上 18.2 加仓", scale=100.0, aliases=[])
        assert [c.value for c in out] == [15.0, 18.2]
        assert out[0].direction == "down" and out[1].direction == "up"

    def test_scale_window_bounds_ledger(self):
        """量纲外数值不进账本（±10 倍窗口）。"""
        out = extract_commitments("回撤至 1170 附近观望", scale=100.0, aliases=[])
        assert out == []

    def test_pre_scan_dirs_not_dimension_filtered(self):
        """预扫方向集不做量纲过滤（688072 gen1：回撤关键词不误杀 down 方向）。

        「回撤」在关键词表（非价格语境）——但同一数值的方向集收集与量纲分类
        正交：另一 occurrence 同值可借其方向集获复合豁免。
        """
        text = "回撤至610以下且站稳610之上"
        out = extract_commitments(text, scale=1000.0, aliases=[])
        # 610 在 1000 基准 ±10 倍窗口内 → 进账本且方向集含 down+up
        assert any(c.value == 610 and {"down", "up"} <= set(c.dirs) for c in out)

    def test_no_mutation_of_inputs(self):
        """账本提取不改输入文本（纯函数）。"""
        text = "跌破 15.0 清仓"
        extract_commitments(text, scale=100.0, aliases=[])
        assert text == "跌破 15.0 清仓"
