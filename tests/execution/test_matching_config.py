"""execution/config.py 撮合参数外置加载单测（Task 2）。

覆盖：默认三档；配置/matching_engine.json 逐字一致；文件覆盖（含 spec
「滑点档位移交配置」整链：改配置最低档滑点为 0.0008 不改代码 → 低流动性档
买单成交价随之变化）；env MATCHING_CONFIG_PATH 覆盖；缺文件回落默认；
坏内容 fail loud（ValueError）；滑点分档边界与缺失兜底档。

配置策略（计划 Task 2 与派单澄清的裁决）：仅「文件不存在」回落默认（留 WARN）；
文件存在但内容坏（JSON 不可解析/结构或类型不符/负费率/tiers 非升序或
末档 quantile_max != 1.0）→ ValueError——静默回落会把操作员的配置错误藏成
「按默认口径运行」，口径漂移不可观测。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_agent.execution.config import default_matching_config, load_matching_config
from finance_agent.execution.matching import (
    MarketBar,
    MatchingConfig,
    Order,
    SlippageTier,
    match_order,
    slippage_rate_for_amount,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_CONFIG_PATH = _REPO_ROOT / "config" / "matching_engine.json"

# 计划逐字载荷（config/matching_engine.json 必须与此一致）
_VERBATIM_PAYLOAD = {
    "commission_rate": 0.00025,
    "min_commission": 5.0,
    "stamp_tax_rate": 0.0005,
    "slippage_tiers": [
        {"quantile_max": 0.2, "slippage_rate": 0.002},
        {"quantile_max": 0.6, "slippage_rate": 0.001},
        {"quantile_max": 1.0, "slippage_rate": 0.0005},
    ],
}

# 10 个全市场成交额样本：amount 的分位 = count(样本 <= amount) / 10
_MARKET_AMOUNTS = (100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0)

_DEFAULT_TIERS = (
    SlippageTier(quantile_max=0.2, slippage_rate=0.002),
    SlippageTier(quantile_max=0.6, slippage_rate=0.001),
    SlippageTier(quantile_max=1.0, slippage_rate=0.0005),
)


def _write_config(tmp_path: Path, payload: object) -> Path:
    """把 JSON 载荷写入临时配置文件并返回路径。"""
    path = tmp_path / "matching_engine.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _replaced_payload(**overrides: object) -> dict:
    """基于逐字载荷做浅覆盖（嵌套 slippage_tiers 由调用方整段替换）。"""
    payload = json.loads(json.dumps(_VERBATIM_PAYLOAD))
    payload.update(overrides)
    return payload


def _low_tier_amount_bar() -> MarketBar:
    """低流动性档 bar：amount=200 → 分位 0.2 → 首档（default 滑点 0.002）。"""
    return MarketBar(
        trade_date="2026-09-01",
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        prev_close=100.0,
        amount=200.0,
    )


# ── 默认值与随包配置 ─────────────────────────────────────────────────────────


def test_default_config_is_three_tiers_verbatim():
    """默认配置与计划逐字一致：三档 0.002/0.001/0.0005，佣金 0.00025/最低 5.0，印花税 0.0005。"""
    assert default_matching_config() == MatchingConfig(
        commission_rate=0.00025,
        min_commission=5.0,
        stamp_tax_rate=0.0005,
        slippage_tiers=_DEFAULT_TIERS,
    )


def test_shipped_config_file_is_verbatim_and_loads_to_defaults():
    """config/matching_engine.json 内容逐字一致，且加载结果 == 默认配置。"""
    assert _SHIPPED_CONFIG_PATH.exists(), f"随包配置缺失: {_SHIPPED_CONFIG_PATH}"
    payload = json.loads(_SHIPPED_CONFIG_PATH.read_text(encoding="utf-8"))
    assert payload == _VERBATIM_PAYLOAD
    assert load_matching_config(_SHIPPED_CONFIG_PATH) == default_matching_config()


def test_default_path_without_env_loads_shipped_file(monkeypatch):
    """path=None 且无 env 时走仓库默认路径（config/matching_engine.json）。"""
    monkeypatch.delenv("MATCHING_CONFIG_PATH", raising=False)
    assert load_matching_config() == default_matching_config()


# ── 覆盖：文件路径与 env ─────────────────────────────────────────────────────


def test_file_override_changes_low_tier_and_fill_price(tmp_path):
    """spec 场景整链：最低档滑点改 0.0008 → 低流动性档买单成交价 = round(100×(1+0.0008), 2)。"""
    payload = _replaced_payload()
    payload["slippage_tiers"][0]["slippage_rate"] = 0.0008
    path = _write_config(tmp_path, payload)

    config = load_matching_config(path)
    assert config.slippage_tiers[0].slippage_rate == 0.0008

    result = match_order(
        Order(code="600519", side="buy", qty=100),
        _low_tier_amount_bar(),
        config=config,
        market_amounts=_MARKET_AMOUNTS,
    )
    # 成交价 = round(100.0 × (1 + 0.0008), 2) = 100.08；成交额 10008 → 佣金 2.502 取最低 5.0
    assert result.fill_price == round(100.0 * (1 + 0.0008), 2) == 100.08
    assert result.slippage_rate == 0.0008
    assert result.fee == 5.0


def test_env_var_overrides_default_path(monkeypatch, tmp_path):
    """env MATCHING_CONFIG_PATH 优先于仓库默认路径。"""
    path = _write_config(tmp_path, _replaced_payload(min_commission=8.0))
    monkeypatch.setenv("MATCHING_CONFIG_PATH", str(path))
    assert load_matching_config().min_commission == 8.0


def test_explicit_path_overrides_env(monkeypatch, tmp_path):
    """显式 path 参数优先于 env。"""
    env_path = _write_config(tmp_path, _replaced_payload(min_commission=8.0))
    explicit_payload = _replaced_payload(min_commission=6.0)
    explicit_path = tmp_path / "explicit.json"
    explicit_path.write_text(json.dumps(explicit_payload), encoding="utf-8")
    monkeypatch.setenv("MATCHING_CONFIG_PATH", str(env_path))
    assert load_matching_config(explicit_path).min_commission == 6.0


# ── 缺文件（回落默认，不是错误）─────────────────────────────────────────────


def test_missing_file_falls_back_to_defaults(tmp_path):
    """路径不存在 → 默认配置（缺文件是部署常态，不是错误）。"""
    assert load_matching_config(tmp_path / "not_here.json") == default_matching_config()


def test_env_var_pointing_to_missing_file_falls_back_to_defaults(monkeypatch, tmp_path):
    """env 指向不存在的文件 → 默认配置（同上）。"""
    monkeypatch.setenv("MATCHING_CONFIG_PATH", str(tmp_path / "missing.json"))
    assert load_matching_config() == default_matching_config()


# ── 坏内容（fail loud）───────────────────────────────────────────────────────


def test_unparseable_json_text_raises_value_error(tmp_path):
    """坏 JSON 文本 → ValueError（消息含文件路径，便于定位）。"""
    path = tmp_path / "broken.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load_matching_config(path)
    assert str(path) in str(excinfo.value)


def test_root_not_object_raises_value_error(tmp_path):
    """JSON 根节点必须是对象（列表/标量 → ValueError）。"""
    path = _write_config(tmp_path, [1, 2, 3])
    with pytest.raises(ValueError):
        load_matching_config(path)


def test_valid_single_tier_payload_loads(tmp_path):
    """档数不强制为三：单档且 quantile_max == 1.0 的配置合法（spec「不少于三档」约束默认值）。"""
    path = _write_config(
        tmp_path,
        {
            "commission_rate": 0.00025,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.0005,
            "slippage_tiers": [{"quantile_max": 1.0, "slippage_rate": 0.0005}],
        },
    )
    assert load_matching_config(path) == MatchingConfig(
        commission_rate=0.00025,
        min_commission=5.0,
        stamp_tax_rate=0.0005,
        slippage_tiers=(SlippageTier(quantile_max=1.0, slippage_rate=0.0005),),
    )


@pytest.mark.parametrize(
    ("mutate", "marker"),
    [
        pytest.param(
            lambda p: p.update(commission_rate=-0.00025),
            "commission_rate",
            id="negative-commission",
        ),
        pytest.param(
            lambda p: p.update(stamp_tax_rate=-0.0005), "stamp_tax_rate", id="negative-stamp-tax"
        ),
        pytest.param(
            lambda p: p.update(slippage_tiers=[{"quantile_max": 1.0, "slippage_rate": -0.001}]),
            "slippage_rate",
            id="negative-slippage",
        ),
        pytest.param(
            lambda p: p.update(slippage_tiers=[{"quantile_max": 0.0, "slippage_rate": 0.001}]),
            "quantile_max",
            id="non-positive-quantile",
        ),
        pytest.param(
            lambda p: p.update(
                slippage_tiers=[
                    {"quantile_max": 0.6, "slippage_rate": 0.001},
                    {"quantile_max": 0.2, "slippage_rate": 0.002},
                    {"quantile_max": 1.0, "slippage_rate": 0.0005},
                ]
            ),
            "升序",
            id="unordered-tiers",
        ),
        pytest.param(
            lambda p: p.update(
                slippage_tiers=[
                    {"quantile_max": 0.2, "slippage_rate": 0.002},
                    {"quantile_max": 0.2, "slippage_rate": 0.001},
                    {"quantile_max": 1.0, "slippage_rate": 0.0005},
                ]
            ),
            "升序",
            id="duplicate-cut-not-strict",
        ),
        pytest.param(
            lambda p: p.update(
                slippage_tiers=[
                    {"quantile_max": 0.2, "slippage_rate": 0.002},
                    {"quantile_max": 0.9, "slippage_rate": 0.0005},
                ]
            ),
            "1.0",
            id="last-cut-not-one",
        ),
        pytest.param(lambda p: p.update(slippage_tiers=[]), "slippage_tiers", id="empty-tiers"),
        pytest.param(
            lambda p: p.update(slippage_tiers="nope"), "slippage_tiers", id="tiers-not-list"
        ),
        pytest.param(
            lambda p: p.update(commission_rate="0.00025"), "commission_rate", id="string-rate"
        ),
        pytest.param(
            lambda p: p.pop("min_commission"), "min_commission", id="missing-min-commission"
        ),
    ],
)
def test_bad_content_raises_value_error(tmp_path, mutate, marker):
    """内容坏（负费率/乱序档/末档非 1.0/类型错/缺键）→ ValueError，消息含问题键。"""
    payload = _replaced_payload()
    mutate(payload)
    path = _write_config(tmp_path, payload)
    with pytest.raises(ValueError) as excinfo:
        load_matching_config(path)
    assert marker in str(excinfo.value)


# ── 滑点分档边界（M4）────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("amount", "expected_rate"),
    [
        (50.0, 0.002),  # 分位 0.1 → 首档
        (200.0, 0.002),  # 分位 0.2 恰在切点 → 归更保守（更大滑点）的一档
        (300.0, 0.001),  # 分位 0.3 → 中档
        (500.0, 0.001),  # 分位 0.5 → 中档
        (600.0, 0.001),  # 分位 0.6 恰在切点 → 归中档（更保守的一侧）
        (900.0, 0.0005),  # 分位 0.9 → 末档
        (1000.0, 0.0005),  # 分位 1.0 → 末档
        (5000.0, 0.0005),  # 超出样本上界 → 末档
    ],
)
def test_quantile_tier_boundaries(amount, expected_rate):
    """分位落档：切点归更保守一侧；超出上界归末档。"""
    config = default_matching_config()
    assert slippage_rate_for_amount(amount, _MARKET_AMOUNTS, config) == expected_rate


@pytest.mark.parametrize(
    ("amount", "market_amounts"),
    [
        (None, None),
        (None, _MARKET_AMOUNTS),
        (500.0, None),
        (500.0, ()),
    ],
)
def test_missing_amount_takes_most_conservative_tier(amount, market_amounts):
    """amount 或市场样本缺失 → 最保守档 = 全部档位中 slippage_rate 最大者（0.002）。"""
    config = default_matching_config()
    assert slippage_rate_for_amount(amount, market_amounts, config) == 0.002


def test_fallback_is_max_slippage_by_value_not_position():
    """兜底档按 slippage 值取最大（不按列表位置末档）：末档滑点更小时仍取最大档。"""
    config = MatchingConfig(
        commission_rate=0.00025,
        min_commission=5.0,
        stamp_tax_rate=0.0005,
        slippage_tiers=(
            SlippageTier(quantile_max=0.5, slippage_rate=0.003),
            SlippageTier(1.0, 0.001),
        ),
    )
    assert slippage_rate_for_amount(None, None, config) == 0.003
