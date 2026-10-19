"""拥挤度指标纯函数单测（delta add-crowding-regime-positioning T1；先红后绿）。

口径（metrics.md §1.14 / design.md「指标口径」，实施裁决见计划 Task 2）：

- 参与集 = 快照行中「非停牌 且 ``amount`` 有限且 > 0」的行（停牌与零成交剔除；
  裁决：**停牌剔除于全部四个指标**，含涨跌家数——故全市场成交额的求和域同为参与集）；
- 小市值组 = 参与集按流通市值升序的前 ``ceil(n × SMALL_CAP_QUANTILE)`` 只
  （流通市值并列按 code 升序 tie-break，保证同输入同输出）；
- 小市值占比 = 组内成交额之和 ÷ 参与集成交额之和；``pct_chg == 0`` 计「平」；
- 不可计算（universe 空 / 参与集空 / 参与集任一标的缺 cap / 任一参与标的
  ``amount`` 或 ``pct_chg`` 非有限值）→ ``None``——不折算 0、不估算冒充；
- 分位 = ``#{x≤v}/n``（窗口含当日值）；样本 < ``min_history`` → ``None``。

数值断言全部手写算术（见各用例注释），不复用被测实现的计算。
"""

from __future__ import annotations

import pytest

from finance_agent.outcome.regime.indicators import (
    HISTORY_WINDOW,
    MIN_HISTORY,
    SMALL_CAP_QUANTILE,
    compute_indicators,
    percentile_rank,
)


def _row(
    code: str,
    *,
    amount: float | None,
    pct_chg: float | None,
    is_suspended: int = 0,
    limit_up_sealed: int = 0,
    limit_down_sealed: int = 0,
) -> dict:
    """最小快照行（只含指标计算消费的列）。"""
    return {
        "code": code,
        "amount": amount,
        "pct_chg": pct_chg,
        "is_suspended": is_suspended,
        "limit_up_sealed": limit_up_sealed,
        "limit_down_sealed": limit_down_sealed,
    }


class TestConstants:
    def test_module_constants_verbatim(self):
        """口径常量逐字钉死（计划 Task 2 Interfaces / metrics.md §1.14）。"""
        assert SMALL_CAP_QUANTILE == 0.3
        assert HISTORY_WINDOW == 250
        assert MIN_HISTORY == 60


class TestSmallCapGroupAndShare:
    def test_small_cap_group_and_share(self):
        """最小记录集对照手算（brief 关键用例，按裁决修正求和域）。

        停牌 C 从全部四指标剔除 → 参与集 {A, B, D}：
        - 小市值组：ceil(3 × 0.3) = 1 只 → cap 最小的 A（1.0）；
        - 小市值占比 = A 成交额 10 ÷ 参与集成交额 (10+30+100=140) = 10/140；
        - 涨跌平 = 1.0↑ / -1.0↓ / 9.9↑ → (2, 1, 0)（C 的 pct_chg=0 不参与，停牌剔除）；
        - 涨停 1（D 封板）、跌停 0；全市场成交额 = 140。

        注：brief 片段写作 ``10.0 / 200.0``（分母含停牌 C 的 60）——该断言早于
        「停牌从全部四指标剔除」裁决（片段内联注释即该未决问题），按裁决以
        参与集为求和域；真实数据中停牌行 amount=0，两种读法等价。
        """
        universe = [
            _row("A", amount=10.0, pct_chg=1.0),
            _row("B", amount=30.0, pct_chg=-1.0),
            _row("C", amount=60.0, pct_chg=0.0, is_suspended=1),
            _row("D", amount=100.0, pct_chg=9.9, limit_up_sealed=1),
        ]
        caps = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 10.0}

        out = compute_indicators(universe, caps)

        assert out is not None
        assert out["small_cap_share"] == pytest.approx(10.0 / 140.0)
        assert (out["up_count"], out["down_count"], out["flat_count"]) == (2, 1, 0)
        assert (out["limit_up_count"], out["limit_down_count"]) == (1, 0)
        assert out["total_amount"] == pytest.approx(140.0)

    def test_zero_turnover_excluded_and_flat_counted(self):
        """零成交剔除（且零成交标的**不要求 cap**）；``pct_chg == 0`` 计「平」。

        参与集 = {B(50, 0.0), C(50, -0.5)}（A 零成交剔除，未提供 cap 也不报缺）：
        - 组：ceil(2 × 0.3) = 1 → cap 最小的 B（2.0）→ 占比 = 50/100 = 0.5；
        - 平 1（B）、跌 1（C）、涨 0；全市场成交额 = 100。
        """
        universe = [
            _row("A", amount=0.0, pct_chg=0.0),
            _row("B", amount=50.0, pct_chg=0.0),
            _row("C", amount=50.0, pct_chg=-0.5),
        ]
        out = compute_indicators(universe, {"B": 2.0, "C": 10.0})

        assert out is not None
        assert out["small_cap_share"] == pytest.approx(0.5)
        assert (out["up_count"], out["down_count"], out["flat_count"]) == (0, 1, 1)
        assert out["total_amount"] == pytest.approx(100.0)

    def test_suspended_row_does_not_require_cap(self):
        """停牌标的剔除于参与集 → 其缺 cap 不触发 None（剔除域 = 要求 caps 的域）。"""
        universe = [
            _row("A", amount=10.0, pct_chg=1.0),
            _row("S", amount=0.0, pct_chg=0.0, is_suspended=1),
        ]
        out = compute_indicators(universe, {"A": 1.0})
        assert out is not None
        assert out["total_amount"] == pytest.approx(10.0)

    def test_group_size_is_ceil(self):
        """组大小 = ceil(n × 0.3)：n=10 → 3 只；n=3 → 1 只（ceil(0.9)=1）。"""
        universe = [_row(f"C{i}", amount=1.0, pct_chg=0.5) for i in range(10)]
        caps = {f"C{i}": float(i + 1) for i in range(10)}  # 升序 cap = 升序 code 尾号
        out = compute_indicators(universe, caps)
        assert out is not None
        # 前 3 小市值（cap 1..3）成交额 3 ÷ 总成交额 10 = 0.3
        assert out["small_cap_share"] == pytest.approx(0.3)

    def test_quantile_kwarg_overrides_group_boundary(self):
        """``quantile`` 可覆盖（日批经配置传入）：0.5 → n=3 组 2 只（ceil(1.5)=2）。"""
        universe = [
            _row("A", amount=10.0, pct_chg=1.0),
            _row("B", amount=30.0, pct_chg=1.0),
            _row("C", amount=60.0, pct_chg=1.0),
        ]
        out = compute_indicators(universe, {"A": 1.0, "B": 2.0, "C": 3.0}, quantile=0.5)
        assert out is not None
        assert out["small_cap_share"] == pytest.approx((10.0 + 30.0) / 100.0)

    @pytest.mark.parametrize("quantile", [0.0, 1.5])
    def test_quantile_out_of_range_rejected(self, quantile):
        with pytest.raises(ValueError, match="quantile"):
            compute_indicators([_row("A", amount=10.0, pct_chg=1.0)], {"A": 1.0}, quantile=quantile)

    def test_group_tie_break_by_code_ascending(self):
        """流通市值并列 → code 升序 tie-break（确定性：同输入同输出）。

        n=3 → 组 1 只；两条 cap=1.0 并列，取 code 较小的 ``600001``：
        占比 = 30 ÷ (30+20+50) = 0.3。
        """
        universe = [
            _row("600002", amount=20.0, pct_chg=0.5),
            _row("600001", amount=30.0, pct_chg=0.5),
            _row("600003", amount=50.0, pct_chg=0.5),
        ]
        out = compute_indicators(universe, {"600001": 1.0, "600002": 1.0, "600003": 5.0})
        assert out is not None
        assert out["small_cap_share"] == pytest.approx(0.3)

    def test_limit_down_counted(self):
        """跌停计数取 ``limit_down_sealed``；涨跌家数不受封板状态影响。"""
        universe = [
            _row("A", amount=10.0, pct_chg=-9.9, limit_down_sealed=1),
            _row("B", amount=20.0, pct_chg=-3.0),
        ]
        out = compute_indicators(universe, {"A": 1.0, "B": 2.0})
        assert out is not None
        assert (out["limit_up_count"], out["limit_down_count"]) == (0, 1)
        assert (out["up_count"], out["down_count"], out["flat_count"]) == (0, 2, 0)


class TestUncomputableReturnsNone:
    def test_empty_universe_returns_none(self):
        assert compute_indicators([], {}) is None

    def test_all_suspended_returns_none(self):
        universe = [
            _row("A", amount=0.0, pct_chg=0.0, is_suspended=1),
            _row("B", amount=0.0, pct_chg=0.0, is_suspended=1),
        ]
        assert compute_indicators(universe, {"A": 1.0, "B": 2.0}) is None

    def test_missing_cap_for_participant_returns_none(self):
        """参与集任一标的缺 cap → None（覆盖不全即不可计算，不缩样本冒充）。"""
        universe = [
            _row("A", amount=10.0, pct_chg=1.0),
            _row("B", amount=20.0, pct_chg=-1.0),
        ]
        assert compute_indicators(universe, {"A": 1.0}) is None

    def test_non_finite_cap_returns_none(self):
        universe = [_row("A", amount=10.0, pct_chg=1.0)]
        assert compute_indicators(universe, {"A": float("nan")}) is None

    def test_missing_amount_for_active_row_returns_none(self):
        """非停牌行 amount 非有限值 → 不可计算（不按 0 折算、不静默剔除）。"""
        universe = [_row("A", amount=None, pct_chg=1.0)]
        assert compute_indicators(universe, {"A": 1.0}) is None

    def test_missing_pct_chg_for_active_row_returns_none(self):
        """非停牌行 pct_chg 非有限值 → 无法分类涨跌平（不静默归入任一类）。"""
        universe = [_row("A", amount=10.0, pct_chg=None)]
        assert compute_indicators(universe, {"A": 1.0}) is None


class TestPercentileRank:
    def test_count_le_over_n(self):
        """百分位 = ``#{x≤v}/n``（含等值）。values {1,2,3,4}，v=2 → 2/4 = 0.5。"""
        assert percentile_rank([1.0, 2.0, 3.0, 4.0], 2.0, min_history=1) == pytest.approx(0.5)

    def test_equal_values_counted_as_le(self):
        """等值计入 ``≤``：values {1,2,2,3}，v=2 → 3/4。"""
        assert percentile_rank([1.0, 2.0, 2.0, 3.0], 2.0, min_history=1) == pytest.approx(0.75)

    def test_above_all_and_below_all(self):
        assert percentile_rank([1.0, 2.0, 3.0], 10.0, min_history=1) == pytest.approx(1.0)
        assert percentile_rank([1.0, 2.0, 3.0], 0.0, min_history=1) == pytest.approx(0.0)

    @pytest.mark.parametrize("size", [59, 60, 61])
    def test_min_history_boundary(self, size):
        """样本 59 → None；60/61 → 有分位（门槛含等号：n ≥ min_history）。"""
        values = [1.0] * size
        out = percentile_rank(values, 1.0, min_history=60)
        if size < 60:
            assert out is None
        else:
            assert out == pytest.approx(1.0)

    def test_default_min_history_is_60(self):
        assert percentile_rank([1.0] * 59, 1.0) is None
        assert percentile_rank([1.0] * 60, 1.0) == pytest.approx(1.0)

    def test_empty_values_returns_none(self):
        assert percentile_rank([], 1.0) is None
