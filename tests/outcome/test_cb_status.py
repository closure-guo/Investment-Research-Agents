"""强赎状态推导纯函数测试（delta add-convertible-bond-coverage T2/T3）。

断言面（brief T3 + spec「条款事件落库与状态推导」）：
- 天计数解析（``已触发天数/所需天数 | 上限天数``）与状态判定（counting/triggered/announced/
  undetermined），含边界：天数恰好达标（含边界判 triggered）/ 差一天判 counting；
- 倒计时按**交易日历**（跨周末/节假日自然扣除；``as_of < d ≤ last_trade_date`` 含边界）；
- 要素缺失如实标注不可判定（缺 last_trade_date 的**已公告**强赎 → undetermined + note
  指明缺失字段；天计数不可解析 → undetermined；announce_date 缺失 → undetermined），
  MUST NOT 猜测数值；日历缺失 → 倒计时 null（状态照判，不猜自然日）；
- 纯函数：同输入同输出（零 LLM、零 IO）。

口径权威：docs/evals/metrics.md §1.17。TDD：先红后绿——实现见 outcome/events/cb_status.py。
"""

from __future__ import annotations

import copy

import pytest

from finance_agent.outcome.events.cb_status import derive_redeem_status

# 2026-10-16 为周五；日历跨周末（10-17/18 非交易日）
_CALENDAR = [
    "2026-10-14",
    "2026-10-15",
    "2026-10-16",
    "2026-10-19",
    "2026-10-20",
    "2026-10-21",
    "2026-10-22",
    "2026-10-23",
    "2026-10-26",
]


def _event(**over) -> dict:
    row = {
        "code": "113046",
        "event_type": "强赎",
        "announce_date": "2026-10-15",
        "count_text": "12/15 | 30",
        "last_trade_date": "2026-10-20",
        "redeem_status": "已公告强赎",
    }
    row.update(over)
    return row


class TestDayCountParsing:
    def test_parses_days_done_and_needed(self):
        result = derive_redeem_status(_event(), as_of="2026-10-16", calendar=_CALENDAR)
        assert result["days_done"] == 12 and result["days_needed"] == 15

    def test_tolerates_spacing_and_width_variants(self):
        for text in ("12/15 | 30", "12/15|30", "12/15 | 30", "  3/15 ｜ 30 "):
            result = derive_redeem_status(
                _event(count_text=text), as_of="2026-10-16", calendar=_CALENDAR
            )
            assert result["days_done"] == int(text.split("/")[0].strip()), text

    def test_unparseable_text_leaves_days_none(self):
        """到期类文案（「临近到期 …」）不可解析 → 天数 None，MUST NOT 猜测。"""
        result = derive_redeem_status(
            _event(count_text="临近到期 <span>!</span><br>2026-10-27 最后交易"),
            as_of="2026-10-16",
            calendar=_CALENDAR,
        )
        assert result["days_done"] is None and result["days_needed"] is None


class TestStateDecisions:
    def test_announced_takes_precedence(self):
        """生命周期序 counting → triggered → announced：已公告强赎优先级最高。"""
        result = derive_redeem_status(
            _event(count_text="12/15 | 30", redeem_status="已公告强赎"),
            as_of="2026-10-16",
            calendar=_CALENDAR,
        )
        assert result["state"] == "announced"
        assert result["note"] is None

    def test_count_met_is_triggered_inclusive_boundary(self):
        """天数**恰好达标**判 triggered（含边界）。"""
        result = derive_redeem_status(
            _event(count_text="15/15 | 30", redeem_status=""),
            as_of="2026-10-16",
            calendar=_CALENDAR,
        )
        assert result["state"] == "triggered"

    def test_one_day_short_is_counting(self):
        result = derive_redeem_status(
            _event(count_text="14/15 | 30", redeem_status=""),
            as_of="2026-10-16",
            calendar=_CALENDAR,
        )
        assert result["state"] == "counting"

    def test_counting_when_as_of_before_announce(self):
        """公告日之后的判断才计状态：as_of < announce_date 时不得判触发。"""
        result = derive_redeem_status(
            _event(count_text="15/15 | 30", redeem_status="", announce_date="2026-10-20"),
            as_of="2026-10-16",
            calendar=_CALENDAR,
        )
        assert result["state"] == "counting"

    def test_declined_redemption_uses_count_state(self):
        """「公告不强赎」无专用状态位：沿用天计数判定（不强赎事实保留于源行）。"""
        result = derive_redeem_status(
            _event(count_text="12/15 | 30", redeem_status="公告不强赎"),
            as_of="2026-10-16",
            calendar=_CALENDAR,
        )
        assert result["state"] == "counting"


class TestCountdown:
    def test_trading_days_across_weekend(self):
        """as_of 周五 → last_trade 下周二：跨周末只数交易日（10-19、10-20 = 2）。"""
        result = derive_redeem_status(_event(), as_of="2026-10-16", calendar=_CALENDAR)
        assert result["trading_days_to_last"] == 2

    def test_longer_window_hand_counted(self):
        """手算对照：as_of 10-16 → last_trade 10-26，日历内 10-19/20/21/22/23/26 = 6。"""
        result = derive_redeem_status(
            _event(last_trade_date="2026-10-26"), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["trading_days_to_last"] == 6

    def test_boundary_same_day_is_zero(self):
        result = derive_redeem_status(
            _event(last_trade_date="2026-10-16"), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["trading_days_to_last"] == 0

    def test_past_last_trade_date_is_zero_not_negative(self):
        result = derive_redeem_status(
            _event(last_trade_date="2026-10-15"), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["trading_days_to_last"] == 0

    def test_calendar_missing_leaves_countdown_none(self):
        """日历不可得 → 倒计时 null + note（状态照判，不猜自然日）。"""
        result = derive_redeem_status(_event(), as_of="2026-10-16", calendar=[])
        assert result["state"] == "announced"
        assert result["trading_days_to_last"] is None
        assert "交易日历" in str(result["note"])

    def test_last_trade_date_beyond_calendar_leaves_countdown_none(self):
        """日历未覆盖 last_trade_date → 倒计时 null + note（不按残缺日历少报天数）。"""
        result = derive_redeem_status(
            _event(last_trade_date="2026-10-28"), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["trading_days_to_last"] is None
        assert "交易日历" in str(result["note"])


class TestUndetermined:
    def test_announced_without_last_trade_date_is_undetermined(self):
        """spec 场景「缺少最后交易日的强赎公告 → 不可判定并注明缺失字段」。"""
        result = derive_redeem_status(
            _event(last_trade_date=None), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["state"] == "undetermined"
        assert "last_trade_date" in str(result["note"])
        assert result["trading_days_to_last"] is None

    def test_unparseable_count_without_announcement_is_undetermined(self):
        result = derive_redeem_status(
            _event(count_text=None, redeem_status=""), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["state"] == "undetermined"
        assert "count_text" in str(result["note"])

    def test_missing_announce_date_is_undetermined(self):
        result = derive_redeem_status(
            _event(announce_date=None), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["state"] == "undetermined"
        assert "announce_date" in str(result["note"])

    def test_invalid_dates_treated_as_missing(self):
        result = derive_redeem_status(
            _event(announce_date="不是日期"), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["state"] == "undetermined"
        assert "announce_date" in str(result["note"])

    def test_invalid_last_trade_date_is_undetermined_for_announced(self):
        result = derive_redeem_status(
            _event(last_trade_date="2026/13/45"), as_of="2026-10-16", calendar=_CALENDAR
        )
        assert result["state"] == "undetermined"

    def test_invalid_as_of_raises(self):
        """``as_of`` 非规范 ISO → 上抛（调用方缺陷，不静默降级）。"""
        with pytest.raises(ValueError):
            derive_redeem_status(_event(), as_of="16/10/2026", calendar=_CALENDAR)


class TestPurity:
    def test_same_input_same_output_and_no_mutation(self):
        event = _event()
        snapshot = copy.deepcopy(event)
        first = derive_redeem_status(event, as_of="2026-10-16", calendar=_CALENDAR)
        second = derive_redeem_status(event, as_of="2026-10-16", calendar=list(_CALENDAR))
        assert first == second
        assert event == snapshot, "纯函数不得改写入参"

    def test_result_shape_is_frozen(self):
        result = derive_redeem_status(_event(), as_of="2026-10-16", calendar=_CALENDAR)
        assert set(result) == {
            "state",
            "days_done",
            "days_needed",
            "trading_days_to_last",
            "note",
        }
