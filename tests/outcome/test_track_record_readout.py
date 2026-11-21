"""track-record 读模型门控（C6①：docs/architecture-debt/c6-small-items.md 小件①）。

§1.9「10 样本红线」生产唯一真相源 + 统一门控 helper。此前 5 处各自为政：
api.py 两处字面复制（注释自认「按字面同步」）+ report/significance/segments 各写
一遍；真相源在 evals/outcome/caliber（生产不可 import evals）。本模块立生产侧
唯一常量，evals 侧反向 re-export（先例：caliber 对 judgment NEUTRAL_BAND）。
"""

import pytest

from finance_agent.outcome.track_record import readout


class TestMinSettledGate:
    def test_min_settled_is_ten(self):
        """§1.9 预登记红线值：10（openspec settlement-significance-stats 单一总纲）。"""
        assert readout.MIN_SETTLED_FOR_WINRATE == 10

    def test_gate_rate_below_threshold_returns_none(self):
        """settled < 10 → 胜率门控为 None（不产出读数）。"""
        assert readout.gate_rate(0.62, settled=9) is None
        assert readout.gate_rate(0.62, settled=0) is None

    def test_gate_rate_at_threshold_passes(self):
        """settled == 10 → 读数放行（红线是「< 10」，含 10 放行）。"""
        assert readout.gate_rate(0.62, settled=10) == pytest.approx(0.62)

    def test_gate_rate_none_rate_stays_none(self):
        """rate 本身 None（如全 neutral）→ 门控后仍 None。"""
        assert readout.gate_rate(None, settled=50) is None
