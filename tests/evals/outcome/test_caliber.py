"""口径常量钉死（§1.8 唯一权威定义；常量漂移 = 口径漂移）。

口径为预登记值：env 覆盖（如 `OUTCOME_DEFAULT_HORIZON_DAYS`）属口径变更，须先重预登记
（metrics.md §1.8）再动常量——本文件的数值/身份断言即该约定的 tripwire。
"""

from evals.outcome import caliber


def test_caliber_values_pinned():
    assert caliber.PRIMARY_WINDOW_DAYS == 20
    assert caliber.AUX_WINDOWS == (5, 10)
    assert caliber.BENCHMARK_CODE == "000300.SH"
    assert caliber.NEUTRAL_BAND == 0.02
    assert caliber.MIN_SETTLED_FOR_WINRATE == 10
    assert caliber.FULL_CONCLUSION_SAMPLE == 30
    assert caliber.LEAKAGE_PROBE_THRESHOLD == 0.60


def test_neutral_band_aliases_production_definition():
    from finance_agent.outcome.track_record.judgment import DEFAULT_NEUTRAL_BAND

    assert caliber.NEUTRAL_BAND is DEFAULT_NEUTRAL_BAND


def test_primary_window_aliases_production_definition():
    """主评估窗口不另造定义：身份断言（`is`）钉住唯一实现源，防两处 20 独立漂移。"""
    from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS

    assert caliber.PRIMARY_WINDOW_DAYS == 20
    assert caliber.PRIMARY_WINDOW_DAYS is DEFAULT_HORIZON_DAYS
