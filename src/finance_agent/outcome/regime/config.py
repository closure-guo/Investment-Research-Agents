"""regime 判定配置加载（delta add-crowding-regime-positioning T1）。

范式逐字照抄 ``execution/config.py``（撮合配置）：

- 默认路径 ``config/regime.json``（相对本模块文件定位，不依赖调用方 CWD）；
  env ``REGIME_CONFIG_PATH`` 可覆盖，显式 ``path`` 入参优先于 env；
- 缺文件（路径不存在）→ 回落 ``RegimeConfig()`` 内置默认 + WARN——缺文件是
  部署常态，不是错误；
- 文件存在但内容坏（JSON 不可解析/根节点非对象/缺键/类型不符/取值非法——
  阈值或系数越界 ``[0, 1]``、``elevated > danger``、``min_history >
  history_window`` 等）→ ``ValueError`` fail loud：静默回落会把操作员的配置
  错误藏成「按默认口径运行」，口径漂移不可观测。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 仓库根相对路径（src/finance_agent/outcome/regime/config.py → parents[4]）
_CONFIG_RELATIVE_PATH = Path("config") / "regime.json"


@dataclass(frozen=True)
class RegimeConfig:
    """regime 全部可配置口径（默认值与 design.md / config/regime.json 逐字一致）。"""

    elevated_threshold: float = 0.8
    danger_threshold: float = 0.9
    multiplier_normal: float = 1.0
    multiplier_elevated: float = 0.5
    multiplier_danger: float = 0.0
    small_cap_quantile: float = 0.3
    history_window: int = 250
    min_history: int = 60
    stale_days: int = 7


def _default_config_path() -> Path:
    """仓库默认配置路径（相对模块文件定位，避免依赖 CWD）。"""
    return Path(__file__).resolve().parents[4] / _CONFIG_RELATIVE_PATH


def load_regime_config(path: str | Path | None = None) -> RegimeConfig:
    """按「显式 path → env REGIME_CONFIG_PATH → 仓库默认路径」加载 regime 配置。

    缺文件 → 内置默认 + WARN；内容坏 → ``ValueError``（模块 docstring 的裁决）。
    """
    if path is not None:
        resolved = Path(path)
    else:
        env_path = os.getenv("REGIME_CONFIG_PATH")
        resolved = Path(env_path) if env_path else _default_config_path()
    try:
        raw_text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("regime 配置文件不存在，使用默认参数: %s", resolved)
        return RegimeConfig()
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"regime 配置 JSON 不可解析: {resolved}: {exc}") from exc
    return _config_from_payload(payload, source=resolved)


def _require_section(mapping: dict[str, Any], key: str, *, source: Path) -> dict[str, Any]:
    if key not in mapping:
        raise ValueError(f"regime 配置缺 {key} 段: {source}")
    value = mapping[key]
    if not isinstance(value, dict):
        raise ValueError(f"regime 配置 {key} 必须为对象: {value!r}: {source}")
    return value


def _require_number(mapping: dict[str, Any], key: str, *, source: Path, context: str = "") -> float:
    """取数值键（bool 不算数值，防 True→1.0 的静默歧义）；缺失/类型不符 → ValueError。"""
    label = f"{context}.{key}" if context else key
    if key not in mapping:
        raise ValueError(f"regime 配置缺 {label} 字段: {source}")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"regime 配置 {label} 必须为数值: {value!r}: {source}")
    return float(value)


def _require_int(mapping: dict[str, Any], key: str, *, source: Path) -> int:
    """取整型键（bool/float 均类型不符）；缺失/类型不符 → ValueError。"""
    if key not in mapping:
        raise ValueError(f"regime 配置缺 {key} 字段: {source}")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"regime 配置 {key} 必须为整数: {value!r}: {source}")
    return int(value)


def _require_unit_interval(value: float, *, label: str, source: Path) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"regime 配置 {label} 越界（须在 [0, 1]）: {value!r}: {source}")
    return value


def _config_from_payload(payload: Any, *, source: Path) -> RegimeConfig:
    """校验 JSON 载荷并构造 ``RegimeConfig``（任何结构/取值问题 → ValueError）。"""
    if not isinstance(payload, dict):
        raise ValueError(f"regime 配置根节点必须是 JSON 对象: {source}")
    thresholds = _require_section(payload, "thresholds", source=source)
    elevated = _require_unit_interval(
        _require_number(thresholds, "elevated", source=source, context="thresholds"),
        label="thresholds.elevated",
        source=source,
    )
    danger = _require_unit_interval(
        _require_number(thresholds, "danger", source=source, context="thresholds"),
        label="thresholds.danger",
        source=source,
    )
    if elevated > danger:
        raise ValueError(
            f"regime 配置 thresholds.elevated 不得大于 danger: {elevated!r} > {danger!r}: {source}"
        )

    multipliers = _require_section(payload, "multipliers", source=source)
    multiplier_normal = _require_unit_interval(
        _require_number(multipliers, "normal", source=source, context="multipliers"),
        label="multipliers.normal",
        source=source,
    )
    multiplier_elevated = _require_unit_interval(
        _require_number(multipliers, "elevated", source=source, context="multipliers"),
        label="multipliers.elevated",
        source=source,
    )
    multiplier_danger = _require_unit_interval(
        _require_number(multipliers, "danger", source=source, context="multipliers"),
        label="multipliers.danger",
        source=source,
    )

    quantile = _require_number(payload, "small_cap_quantile", source=source)
    if not 0.0 < quantile <= 1.0:
        raise ValueError(
            f"regime 配置 small_cap_quantile 越界（须在 (0, 1]）: {quantile!r}: {source}"
        )
    history_window = _require_int(payload, "history_window", source=source)
    if history_window < 1:
        raise ValueError(f"regime 配置 history_window 必须为正整数: {history_window!r}: {source}")
    min_history = _require_int(payload, "min_history", source=source)
    if min_history < 1:
        raise ValueError(f"regime 配置 min_history 必须为正整数: {min_history!r}: {source}")
    if min_history > history_window:
        raise ValueError(
            f"regime 配置 min_history 不得大于 history_window: "
            f"{min_history!r} > {history_window!r}: {source}"
        )
    stale_days = _require_int(payload, "stale_days", source=source)
    if stale_days < 0:
        raise ValueError(f"regime 配置 stale_days 不得为负: {stale_days!r}: {source}")

    return RegimeConfig(
        elevated_threshold=elevated,
        danger_threshold=danger,
        multiplier_normal=multiplier_normal,
        multiplier_elevated=multiplier_elevated,
        multiplier_danger=multiplier_danger,
        small_cap_quantile=quantile,
        history_window=history_window,
        min_history=min_history,
        stale_days=stale_days,
    )
