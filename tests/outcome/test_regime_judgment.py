"""regime 判定 + 配置加载单测（delta add-crowding-regime-positioning T1；先红后绿）。

判定契约（spec crowding-regime「regime 判定与总仓位系数」）：

- 触发指标 = 小市值组成交额占比分位、全市场成交额分位；任一 ≥ danger（默认 0.9）
  → danger；否则 ≥ elevated（默认 0.8）→ elevated；否则 normal；
- 映射 normal 1.0 / elevated 0.5 / danger 0.0（全部可配置）；
- 行缺失 → ``{"state":"normal","multiplier":1.0,"reasons":["no_regime_data"]}``；
- 分位为 null（样本不足）不触发且 reasons 注明 ``insufficient_history:<指标>``；
- 判定 MUST NOT 调用 LLM；同输入同输出。

配置契约（范式照抄 ``execution/config.py``）：缺文件回落内置默认 + WARN；
文件存在但损坏（JSON 非法 / 缺键 / 类型不符 / 取值非法）→ ``ValueError`` fail loud。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from finance_agent.outcome.regime.config import RegimeConfig, load_regime_config
from finance_agent.outcome.regime.judgment import judge, staleness

_CONFIG_LOGGER = "finance_agent.outcome.regime.config"


def _valid_payload() -> dict:
    """design.md 配置样例的完整键集（缺键用例从完整载荷上删，不靠宽容解析）。"""
    return {
        "thresholds": {"elevated": 0.8, "danger": 0.9},
        "multipliers": {"normal": 1.0, "elevated": 0.5, "danger": 0.0},
        "small_cap_quantile": 0.3,
        "history_window": 250,
        "min_history": 60,
        "stale_days": 7,
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _row(**overrides: object) -> dict:
    """判定的最小指标行（键与 ``regime_daily`` 列同名）。"""
    base: dict = {
        "trade_date": "2026-10-17",
        "small_cap_share": 0.0714,
        "up_count": 2500,
        "down_count": 2300,
        "flat_count": 200,
        "limit_up_count": 60,
        "limit_down_count": 5,
        "total_amount": 8.0e11,
        "small_cap_share_pctile": 0.5,
        "total_amount_pctile": 0.5,
        "computed_at": "2026-10-17T15:35:00",
    }
    base.update(overrides)
    return base


class TestJudgeStates:
    def test_danger_by_small_cap_pctile(self):
        """spec 场景「过热判定」：0.93 ≥ 0.9 → danger + 0.0；reasons 含指标名与读数。"""
        out = judge(_row(small_cap_share_pctile=0.93), RegimeConfig())
        assert out["state"] == "danger"
        assert out["multiplier"] == 0.0
        assert "small_cap_share_pctile=0.93>=0.9" in out["reasons"]

    def test_elevated_by_total_amount_pctile(self):
        out = judge(_row(total_amount_pctile=0.85), RegimeConfig())
        assert out["state"] == "elevated"
        assert out["multiplier"] == 0.5
        assert "total_amount_pctile=0.85>=0.8" in out["reasons"]

    def test_normal_when_all_below_elevated(self):
        out = judge(_row(small_cap_share_pctile=0.79, total_amount_pctile=0.5), RegimeConfig())
        assert out["state"] == "normal"
        assert out["multiplier"] == 1.0

    def test_danger_wins_over_elevated(self):
        """一指标 danger、另一指标 elevated → danger（状态取最严）。"""
        out = judge(_row(small_cap_share_pctile=0.95, total_amount_pctile=0.85), RegimeConfig())
        assert out["state"] == "danger"
        assert out["multiplier"] == 0.0

    @pytest.mark.parametrize(
        ("pctile", "expected_state", "expected_multiplier"),
        [
            (0.7999, "normal", 1.0),  # elevated 阈值下方
            (0.8, "elevated", 0.5),  # 边界含等号：≥ elevated
            (0.8999, "elevated", 0.5),  # danger 阈值下方
            (0.9, "danger", 0.0),  # 边界含等号：≥ danger
        ],
    )
    def test_threshold_boundaries(self, pctile, expected_state, expected_multiplier):
        out = judge(_row(small_cap_share_pctile=pctile), RegimeConfig())
        assert out["state"] == expected_state
        assert out["multiplier"] == expected_multiplier

    def test_none_row_means_no_data(self):
        assert judge(None, RegimeConfig()) == {
            "state": "normal",
            "multiplier": 1.0,
            "reasons": ["no_regime_data"],
        }

    def test_insufficient_history_does_not_trigger_and_is_noted(self):
        """分位 null → 不触发、reasons 注明；另一触发指标照常参与判定。

        两触发指标皆 null 时保持 normal；``insufficient_history`` 逐指标注明。
        """
        out = judge(_row(small_cap_share_pctile=None, total_amount_pctile=None), RegimeConfig())
        assert out["state"] == "normal"
        assert out["multiplier"] == 1.0
        assert "insufficient_history:small_cap_share_pctile" in out["reasons"]
        assert "insufficient_history:total_amount_pctile" in out["reasons"]

        mixed = judge(_row(small_cap_share_pctile=None, total_amount_pctile=0.95), RegimeConfig())
        assert mixed["state"] == "danger"

    def test_breadth_readings_enter_reasons(self):
        """Breadth 类不参与触发但进 reasons（design「Breadth 类进 reasons/展示」）。"""
        out = judge(_row(), RegimeConfig())
        assert out["state"] == "normal"
        assert "up_count=2500" in out["reasons"]
        assert "down_count=2300" in out["reasons"]
        assert "limit_up_count=60" in out["reasons"]
        assert "limit_down_count=5" in out["reasons"]
        assert not any("flat_count" in reason for reason in out["reasons"])

    def test_reproducible_same_input_same_output(self):
        row = _row(small_cap_share_pctile=0.9)
        assert judge(row, RegimeConfig()) == judge(row, RegimeConfig())

    def test_custom_thresholds_and_multipliers(self):
        config = RegimeConfig(
            elevated_threshold=0.5,
            danger_threshold=0.6,
            multiplier_elevated=0.7,
            multiplier_danger=0.1,
        )
        assert judge(_row(small_cap_share_pctile=0.55), config)["multiplier"] == 0.7
        assert judge(_row(small_cap_share_pctile=0.65), config)["multiplier"] == 0.1


class TestStaleness:
    def test_age_and_stale_boundaries(self):
        """``stale = age_days > stale_days``（spec：``age_days > 7``；7 自然日不陈旧、8 陈旧）。"""
        now = date(2026, 10, 18)
        assert staleness("2026-10-18", now=now) == (0, False)
        assert staleness("2026-10-11", now=now) == (7, False)
        assert staleness("2026-10-10", now=now) == (8, True)

    def test_custom_stale_days(self):
        now = date(2026, 10, 18)
        assert staleness("2026-10-13", now=now, stale_days=3) == (5, True)
        assert staleness("2026-10-15", now=now, stale_days=3) == (3, False)

    def test_malformed_date_raises_value_error(self):
        with pytest.raises(ValueError):
            staleness("2026-13-99", now=date(2026, 10, 18))


class TestConfigDefaults:
    def test_builtin_defaults_verbatim(self):
        """内置默认与 design.md 口径逐字一致（全部可配置）。"""
        config = RegimeConfig()
        assert config.elevated_threshold == 0.8
        assert config.danger_threshold == 0.9
        assert config.multiplier_normal == 1.0
        assert config.multiplier_elevated == 0.5
        assert config.multiplier_danger == 0.0
        assert config.small_cap_quantile == 0.3
        assert config.history_window == 250
        assert config.min_history == 60
        assert config.stale_days == 7

    def test_shipped_config_file_matches_builtin_defaults(self):
        """仓库自带 ``config/regime.json`` 与内置默认逐字一致（部署产物同口径）。"""
        default_path = Path(__file__).resolve().parents[2] / "config" / "regime.json"
        assert default_path.is_file()
        assert load_regime_config(default_path) == RegimeConfig()


class TestConfigLoading:
    def test_missing_file_falls_back_with_warning(self, tmp_path, caplog):
        missing = tmp_path / "not-there.json"
        with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
            config = load_regime_config(missing)
        assert config == RegimeConfig()
        assert any("不存在" in record.message for record in caplog.records)

    def test_valid_file_overrides(self, tmp_path):
        payload = _valid_payload()
        payload["thresholds"] = {"elevated": 0.7, "danger": 0.95}
        payload["multipliers"] = {"normal": 1.0, "elevated": 0.6, "danger": 0.2}
        payload["small_cap_quantile"] = 0.25
        payload["history_window"] = 120
        payload["min_history"] = 30
        payload["stale_days"] = 3
        path = _write(tmp_path / "regime.json", payload)
        assert load_regime_config(path) == RegimeConfig(
            elevated_threshold=0.7,
            danger_threshold=0.95,
            multiplier_normal=1.0,
            multiplier_elevated=0.6,
            multiplier_danger=0.2,
            small_cap_quantile=0.25,
            history_window=120,
            min_history=30,
            stale_days=3,
        )

    def test_env_path_used_when_no_explicit_path(self, tmp_path, monkeypatch):
        payload = _valid_payload()
        payload["thresholds"] = {"elevated": 0.6, "danger": 0.7}
        path = _write(tmp_path / "env-regime.json", payload)
        monkeypatch.setenv("REGIME_CONFIG_PATH", str(path))
        config = load_regime_config()
        assert (config.elevated_threshold, config.danger_threshold) == (0.6, 0.7)
        assert config.small_cap_quantile == 0.3  # 未改动键同文件值（= 内置默认）

    def test_explicit_path_beats_env(self, tmp_path, monkeypatch):
        explicit_payload = _valid_payload()
        explicit_payload["thresholds"] = {"elevated": 0.55, "danger": 0.65}
        env_payload = _valid_payload()
        env_payload["thresholds"] = {"elevated": 0.11, "danger": 0.22}
        explicit = _write(tmp_path / "explicit.json", explicit_payload)
        other = _write(tmp_path / "env.json", env_payload)
        monkeypatch.setenv("REGIME_CONFIG_PATH", str(other))
        assert load_regime_config(explicit).elevated_threshold == 0.55

    def test_unparseable_json_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="不可解析"):
            load_regime_config(path)

    def test_root_not_object_raises(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="根节点"):
            load_regime_config(path)

    def test_missing_key_raises(self, tmp_path):
        payload = _valid_payload()
        del payload["multipliers"]
        path = _write(tmp_path / "missing.json", payload)
        with pytest.raises(ValueError, match="multipliers"):
            load_regime_config(path)

    def test_missing_nested_key_raises(self, tmp_path):
        payload = _valid_payload()
        del payload["thresholds"]["danger"]
        path = _write(tmp_path / "missing-nested.json", payload)
        with pytest.raises(ValueError, match="thresholds.danger"):
            load_regime_config(path)

    def test_type_mismatch_raises(self, tmp_path):
        payload = _valid_payload()
        payload["thresholds"]["elevated"] = "0.8"
        path = _write(tmp_path / "types.json", payload)
        with pytest.raises(ValueError, match="数值"):
            load_regime_config(path)

    def test_bool_is_not_number(self, tmp_path):
        payload = _valid_payload()
        payload["thresholds"]["elevated"] = True
        path = _write(tmp_path / "bool.json", payload)
        with pytest.raises(ValueError, match="数值"):
            load_regime_config(path)

    @pytest.mark.parametrize("value", [-0.1, 1.5])
    def test_multiplier_out_of_range_raises(self, tmp_path, value):
        payload = _valid_payload()
        payload["multipliers"]["normal"] = value
        path = _write(tmp_path / "mult.json", payload)
        with pytest.raises(ValueError, match="multipliers.normal"):
            load_regime_config(path)

    def test_threshold_out_of_range_raises(self, tmp_path):
        payload = _valid_payload()
        payload["thresholds"]["danger"] = 1.2
        path = _write(tmp_path / "th.json", payload)
        with pytest.raises(ValueError, match="thresholds.danger"):
            load_regime_config(path)

    def test_elevated_above_danger_raises(self, tmp_path):
        payload = _valid_payload()
        payload["thresholds"] = {"elevated": 0.95, "danger": 0.9}
        path = _write(tmp_path / "order.json", payload)
        with pytest.raises(ValueError, match="elevated"):
            load_regime_config(path)

    def test_quantile_out_of_range_raises(self, tmp_path):
        for value in (0.0, 1.5):
            payload = _valid_payload()
            payload["small_cap_quantile"] = value
            path = _write(tmp_path / "q.json", payload)
            with pytest.raises(ValueError, match="small_cap_quantile"):
                load_regime_config(path)

    def test_min_history_above_window_raises(self, tmp_path):
        payload = _valid_payload()
        payload["history_window"] = 30
        payload["min_history"] = 60
        path = _write(tmp_path / "window.json", payload)
        with pytest.raises(ValueError, match="min_history"):
            load_regime_config(path)

    def test_negative_stale_days_raises(self, tmp_path):
        payload = _valid_payload()
        payload["stale_days"] = -1
        path = _write(tmp_path / "stale.json", payload)
        with pytest.raises(ValueError, match="stale_days"):
            load_regime_config(path)

    def test_non_integer_window_raises(self, tmp_path):
        payload = _valid_payload()
        payload["history_window"] = 250.0
        path = _write(tmp_path / "float-window.json", payload)
        with pytest.raises(ValueError, match="history_window"):
            load_regime_config(path)
