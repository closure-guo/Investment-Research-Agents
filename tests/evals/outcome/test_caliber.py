"""口径常量钉死（§1.8 唯一权威定义；常量漂移 = 口径漂移）。"""

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

    assert caliber.NEUTRAL_BAND == DEFAULT_NEUTRAL_BAND
