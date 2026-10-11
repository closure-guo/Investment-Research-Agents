"""TDD tests for metrics/efficiency.py — 运营效率 4 指标。

指标：
1. 存货周转率（次）— AKShare 预计算（年报）
2. 应收账款周转率（次）— 自算 = 营业收入 / 应收账款平均余额
3. 总资产周转率（次）— 自算 = 营业收入 / 资产总计
4. 应付账款周转率（次）— 自算 = 营业成本 / 应付账款

fixture 手算（2024）：
- 应收账款周转率 = 营业收入(1000) / ((应收账款(40)+上年应收(35))/2) = 1000/37.5 = 26.67
- 应付账款周转率 = 营业成本(600) / 应付账款(60) = 10
- 总资产周转率 = 营业收入(1000) / 资产总计(1000) = 1.0
"""

from math import isclose

from finance_agent.metrics.efficiency import calc_efficiency


class TestCalcEfficiency:
    def test_returns_all_metrics(self, balance_sheet, income_statement, indicators):
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        expected_keys = {
            "存货周转率",
            "存货周转率_source",
            "应收账款周转率",
            "总资产周转率",
            "应付账款周转率",
        }
        assert set(result.keys()) == expected_keys

    def test_each_metric_has_all_years(self, balance_sheet, income_statement, indicators):
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        for name, values in result.items():
            assert set(values.keys()) == {"2024", "2023", "2022"}, f"{name} years mismatch"

    def test_inventory_turnover_2024(self, balance_sheet, income_statement, indicators):
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        # AKShare 预计算值
        assert isclose(result["存货周转率"]["2024"], 6.32, rel_tol=1e-2)

    def test_total_asset_turnover_2024(self, balance_sheet, income_statement, indicators):
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        # 自算 = 营业收入/资产总计 = 1000/1000 = 1.0
        assert isclose(result["总资产周转率"]["2024"], 1.0, rel_tol=1e-2)

    def test_ap_turnover_2024(self, balance_sheet, income_statement, indicators):
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        # 应付账款周转率 = 营业成本/应付账款 = 600/60 = 10
        assert isclose(result["应付账款周转率"]["2024"], 10.0, rel_tol=1e-2)

    def test_ap_turnover_2023(self, balance_sheet, income_statement, indicators):
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        assert isclose(result["应付账款周转率"]["2023"], 550 / 50, rel_tol=1e-2)

    def test_ar_turnover_2024(self, balance_sheet, income_statement, indicators):
        """应收账款周转率 = 营业收入 / 应收账款平均余额 = 1000 / ((40+35)/2) = 26.67"""
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        assert isclose(result["应收账款周转率"]["2024"], 1000 / 37.5, rel_tol=1e-2)

    def test_ar_turnover_2023(self, balance_sheet, income_statement, indicators):
        """应收账款周转率 = 营业收入 / 应收账款平均余额 = 900 / ((35+30)/2) = 27.69"""
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        assert isclose(result["应收账款周转率"]["2023"], 900 / 32.5, rel_tol=1e-2)

    def test_ar_turnover_oldest_year(self, balance_sheet, income_statement, indicators):
        """最老年份无上年数据，用当年应收账款直接算 = 800 / 30 = 26.67"""
        result = calc_efficiency(balance_sheet, income_statement, indicators)
        assert isclose(result["应收账款周转率"]["2022"], 800 / 30.0, rel_tol=1e-2)

    def test_zero_accounts_payable(self):
        import pandas as pd

        bs = pd.DataFrame({"报告日": ["20241231"], "资产总计": [1000.0], "应付账款": [0.0]})
        is_ = pd.DataFrame({"报告日": ["20241231"], "营业收入": [1000.0], "营业成本": [600.0]})
        ind = pd.DataFrame(
            {
                "日期": ["2024-12-31"],
                "存货周转率(次)": [6.0],
                "应收账款周转率(次)": [None],
                "总资产周转率(次)": [1.0],
            }
        )
        result = calc_efficiency(bs, is_, ind)
        assert result["应付账款周转率"]["2024"] is None

    def test_date_alignment_indicators_ascending(self):
        """indicators 升序 + BS 降序 + 年份不完全重叠 → 按日期匹配，非行号。

        模拟生产环境：BS/IS 含 2025 但 indicators 只到 2024。
        当前 bug 会按行号取到错误年份的数据。
        """
        import pandas as pd

        bs = pd.DataFrame(
            {
                "报告日": ["20251231", "20241231", "20231231", "20221231"],
                "资产总计": [1200.0, 1000.0, 900.0, 800.0],
                "应收账款": [50.0, 40.0, 35.0, 30.0],
                "应付账款": [70.0, 60.0, 50.0, 45.0],
                "存货": [120.0, 100.0, 90.0, 80.0],
            }
        )
        is_ = pd.DataFrame(
            {
                "报告日": ["20251231", "20241231", "20231231", "20221231"],
                "营业收入": [1200.0, 1000.0, 900.0, 800.0],
                "营业成本": [720.0, 600.0, 550.0, 500.0],
            }
        )
        # 升序（生产环境真实顺序），只有 2022-2024
        ind = pd.DataFrame(
            {
                "日期": ["2022-12-31", "2023-12-31", "2024-12-31"],
                "存货周转率(次)": [6.0, 6.5, 7.0],
            }
        )

        result = calc_efficiency(bs, is_, ind)

        # 2024 应取 indicators 的 7.0，不是行号错位取的 6.0
        assert isclose(result["存货周转率"]["2024"], 7.0, rel_tol=1e-2)
        # 2023 应取 6.5，不是行号错位取的 6.5（碰巧对了但逻辑错）
        assert isclose(result["存货周转率"]["2023"], 6.5, rel_tol=1e-2)
        # 2022 应取 6.0
        assert isclose(result["存货周转率"]["2022"], 6.0, rel_tol=1e-2)

    def test_missing_year_self_calculated(self):
        """indicators 无 2025 数据时，自算 营业成本/平均存货。

        2025: 营业成本=720, 存货(2025)=120, 存货(2024)=100
        平均存货 = (120+100)/2 = 110
        存货周转率 = 720/110 = 6.545
        """
        import pandas as pd

        bs = pd.DataFrame(
            {
                "报告日": ["20251231", "20241231", "20231231"],
                "资产总计": [1200.0, 1000.0, 900.0],
                "应收账款": [50.0, 40.0, 35.0],
                "应付账款": [70.0, 60.0, 50.0],
                "存货": [120.0, 100.0, 90.0],
            }
        )
        is_ = pd.DataFrame(
            {
                "报告日": ["20251231", "20241231", "20231231"],
                "营业收入": [1200.0, 1000.0, 900.0],
                "营业成本": [720.0, 600.0, 550.0],
            }
        )
        # indicators 只有 2023-2024，无 2025
        ind = pd.DataFrame(
            {
                "日期": ["2023-12-31", "2024-12-31"],
                "存货周转率(次)": [6.5, 7.0],
            }
        )

        result = calc_efficiency(bs, is_, ind)

        # 2025 自算: 720 / ((120+100)/2) = 6.545
        assert isclose(result["存货周转率"]["2025"], 720 / 110, rel_tol=1e-2)

    def test_source_annotation(self):
        """返回值包含来源标注：official（indicators 有数据）vs calc（自算）。"""
        import pandas as pd

        bs = pd.DataFrame(
            {
                "报告日": ["20251231", "20241231", "20231231"],
                "资产总计": [1200.0, 1000.0, 900.0],
                "应收账款": [50.0, 40.0, 35.0],
                "应付账款": [70.0, 60.0, 50.0],
                "存货": [120.0, 100.0, 90.0],
            }
        )
        is_ = pd.DataFrame(
            {
                "报告日": ["20251231", "20241231", "20231231"],
                "营业收入": [1200.0, 1000.0, 900.0],
                "营业成本": [720.0, 600.0, 550.0],
            }
        )
        ind = pd.DataFrame(
            {
                "日期": ["2023-12-31", "2024-12-31"],
                "存货周转率(次)": [6.5, 7.0],
            }
        )

        result = calc_efficiency(bs, is_, ind)
        source = result.get("存货周转率_source", {})

        # 2025: indicators 无数据 → calc
        assert source["2025"] == "calc"
        # 2024: indicators 有数据 → official
        assert source["2024"] == "official"
        # 2023: indicators 有数据 → official
        assert source["2023"] == "official"


class TestFindIndicatorEmptyFrame:
    """issue #243-2：无列空 DataFrame 不得抛 KeyError（_find_indicator 守卫）。"""

    def test_columnless_empty_frame_returns_none(self):
        import pandas as pd

        from finance_agent.metrics.efficiency import _find_indicator

        # fetch_indicators 失败降级返回 pd.DataFrame()（无列）时不得 KeyError
        assert _find_indicator(pd.DataFrame(), "2024") is None

    def test_empty_frame_with_date_column_returns_none(self):
        import pandas as pd

        from finance_agent.metrics.efficiency import _find_indicator

        assert _find_indicator(pd.DataFrame({"日期": []}), "2024") is None

    def test_calc_efficiency_with_columnless_indicators(self):
        """calc_efficiency 收到无列空框应走 calc 降级而非崩溃。"""
        import pandas as pd

        bs = pd.DataFrame(
            {
                "报告日": ["20241231"],
                "资产总计": [1000.0],
                "应收账款": [40.0],
                "应付账款": [60.0],
                "存货": [100.0],
            }
        )
        is_ = pd.DataFrame(
            {
                "报告日": ["20241231"],
                "营业收入": [1000.0],
                "营业成本": [600.0],
            }
        )
        result = calc_efficiency(bs, is_, pd.DataFrame())
        assert result["存货周转率_source"]["2024"] == "calc"
