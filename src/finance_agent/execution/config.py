"""撮合引擎参数加载（成本/滑点外置，可改配置不改代码；add-matching-engine Task 2）。

- 默认路径 ``config/matching_engine.json``（仓库根；相对本模块文件定位，不依赖
  调用方 CWD）；env ``MATCHING_CONFIG_PATH`` 可覆盖，显式 ``path`` 入参优先于 env。
- 缺文件（路径不存在）→ 回落 ``default_matching_config()`` 并留 WARN——缺文件是
  部署常态，不是错误。
- 文件存在但内容坏（JSON 不可解析/根节点非对象/缺键或类型不符/负费率/
  ``slippage_tiers`` 非严格升序或末档 ``quantile_max != 1.0``）→ ``ValueError``
  fail loud：静默回落会把操作员的配置错误藏成「按默认口径运行」，口径漂移不可
  观测（计划 Task 2 与派单澄清的裁决）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from finance_agent.execution.matching import MatchingConfig, SlippageTier

logger = logging.getLogger(__name__)

# 仓库根相对路径（src/finance_agent/execution/config.py → parents[3]）
_CONFIG_RELATIVE_PATH = Path("config") / "matching_engine.json"


def _default_config_path() -> Path:
    """仓库默认配置路径（相对模块文件定位，避免依赖 CWD）。"""
    return Path(__file__).resolve().parents[3] / _CONFIG_RELATIVE_PATH


def default_matching_config() -> MatchingConfig:
    """内置默认参数（与 config/matching_engine.json 逐字一致，供缺文件回退与测试对照）。"""
    return MatchingConfig(
        commission_rate=0.00025,
        min_commission=5.0,
        stamp_tax_rate=0.0005,
        slippage_tiers=(
            SlippageTier(quantile_max=0.2, slippage_rate=0.002),
            SlippageTier(quantile_max=0.6, slippage_rate=0.001),
            SlippageTier(quantile_max=1.0, slippage_rate=0.0005),
        ),
    )


def load_matching_config(path: str | Path | None = None) -> MatchingConfig:
    """按「显式 path → env MATCHING_CONFIG_PATH → 仓库默认路径」加载撮合配置。

    缺文件 → 默认配置 + WARN；内容坏 → ``ValueError``（见模块 docstring 的裁决）。
    """
    if path is not None:
        resolved = Path(path)
    else:
        env_path = os.getenv("MATCHING_CONFIG_PATH")
        resolved = Path(env_path) if env_path else _default_config_path()
    try:
        raw_text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("撮合配置文件不存在，使用默认参数: %s", resolved)
        return default_matching_config()
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"撮合配置 JSON 不可解析: {resolved}: {exc}") from exc
    return _config_from_payload(payload, source=resolved)


def _require_number(mapping: dict[str, Any], key: str, *, source: Path, context: str = "") -> float:
    """取数值键（bool 不算数值，防 True→1.0 的静默歧义）；缺失/类型不符 → ValueError。"""
    label = f"{context}.{key}" if context else key
    if key not in mapping:
        raise ValueError(f"撮合配置缺 {label} 字段: {source}")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"撮合配置 {label} 必须为数值: {value!r}: {source}")
    return float(value)


def _config_from_payload(payload: Any, *, source: Path) -> MatchingConfig:
    """校验 JSON 载荷并构造 ``MatchingConfig``（任何结构/取值问题 → ValueError）。"""
    if not isinstance(payload, dict):
        raise ValueError(f"撮合配置根节点必须是 JSON 对象: {source}")
    commission_rate = _require_number(payload, "commission_rate", source=source)
    min_commission = _require_number(payload, "min_commission", source=source)
    stamp_tax_rate = _require_number(payload, "stamp_tax_rate", source=source)
    for name, value in (
        ("commission_rate", commission_rate),
        ("min_commission", min_commission),
        ("stamp_tax_rate", stamp_tax_rate),
    ):
        if value < 0:
            raise ValueError(f"撮合配置 {name} 不得为负: {value!r}: {source}")

    tiers_raw = payload.get("slippage_tiers")
    if not isinstance(tiers_raw, list) or not tiers_raw:
        raise ValueError(f"撮合配置 slippage_tiers 必须为非空数组: {source}")
    tiers: list[SlippageTier] = []
    for idx, item in enumerate(tiers_raw):
        if not isinstance(item, dict):
            raise ValueError(f"撮合配置 slippage_tiers[{idx}] 必须为对象: {source}")
        quantile_max = _require_number(
            item, "quantile_max", source=source, context=f"slippage_tiers[{idx}]"
        )
        slippage_rate = _require_number(
            item, "slippage_rate", source=source, context=f"slippage_tiers[{idx}]"
        )
        if quantile_max <= 0:
            raise ValueError(f"撮合配置 slippage_tiers[{idx}].quantile_max 必须为正数: {source}")
        if slippage_rate < 0:
            raise ValueError(f"撮合配置 slippage_tiers[{idx}].slippage_rate 不得为负: {source}")
        if tiers and quantile_max <= tiers[-1].quantile_max:
            raise ValueError(f"撮合配置 slippage_tiers 必须按 quantile_max 严格升序: {source}")
        tiers.append(SlippageTier(quantile_max=quantile_max, slippage_rate=slippage_rate))
    if tiers[-1].quantile_max != 1.0:
        raise ValueError(
            f"撮合配置末档 quantile_max 必须为 1.0（当前 {tiers[-1].quantile_max!r}）: {source}"
        )
    return MatchingConfig(
        commission_rate=commission_rate,
        min_commission=min_commission,
        stamp_tax_rate=stamp_tax_rate,
        slippage_tiers=tuple(tiers),
    )
