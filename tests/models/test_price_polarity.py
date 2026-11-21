"""三价极性派生（add-price-polarity-contract，#606）。

polarity = 申报三价（entry/stop/target）的惯例语义：
- long：stop < entry < target（多头观点 / exit 的反弹卖出带同关系式）
- exit：清仓离场惯例——stop=entry 下方清仓位、target=entry 上方反弹减仓带
- short：做空惯例——stop=entry 上方失效位、target=entry 下方兑现位
- None：hold/watch 无三价极性语义

sell_type=None 的 sell 默认 exit（models.TradeDecision 可执行默认，
add-holdings-context：原为 short，与生产分型语义相反且 A 股散户不可执行）。
"""

import pytest

from finance_agent.models import price_polarity


class TestPricePolarity:
    def test_buy_maps_to_long(self):
        assert price_polarity("buy", None) == "long"

    def test_sell_exit_maps_to_exit(self):
        assert price_polarity("sell", "exit") == "exit"

    def test_sell_short_maps_to_short(self):
        assert price_polarity("sell", "short") == "short"

    def test_sell_undeclared_defaults_to_exit(self):
        """sell_type=None 的 sell → exit（可执行默认，与校验层打回后默认一致）。"""
        assert price_polarity("sell", None) == "exit"

    @pytest.mark.parametrize("action", ["hold", "watch"])
    def test_hold_watch_has_no_polarity(self, action):
        assert price_polarity(action, None) is None

    def test_hold_with_stray_sell_type_still_none(self):
        """非 sell 动作上残留的 sell_type 不产生极性（防 LLM 在 buy 上塞值）。"""
        assert price_polarity("hold", "exit") is None
        assert price_polarity("buy", "short") == "long"
