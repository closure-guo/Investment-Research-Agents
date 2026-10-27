"""选股规则运行时配置（delta add-screening-rules-config-ui）。

可配置项恰为三项：``min_trading_days`` / ``min_avg_amount_20d`` / ``l2_top_n``；
来源优先级逐项独立：设置文件 > 环境变量 > 默认值。文件缺失/损坏/字段非法时按项
回退并如实披露 ``file_status``，不以默认值冒充已保存设置。本模块只管「值从哪来、
怎么写」；运行期生效由 ``pipeline.run_screening_batch`` 批次开始时 ``resolve()``。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.screening.constraints import MIN_AVG_AMOUNT_20D, MIN_TRADING_DAYS

_logger = logging.getLogger("finance_agent.screening.rules_config")

DEFAULT_MIN_TRADING_DAYS = MIN_TRADING_DAYS  # 60
DEFAULT_MIN_AVG_AMOUNT_20D = MIN_AVG_AMOUNT_20D  # 5e7
# 与 pipeline.L2_TOP_N 保持一致；不能 import pipeline（会成环），由测试钉住相等
DEFAULT_L2_TOP_N = 100

ENV_VAR_PATH = "SCREENING_RULES_CONFIG_PATH"
ENV_VARS: dict[str, str] = {
    "min_trading_days": "SCREENING_MIN_TRADING_DAYS",
    "min_avg_amount_20d": "SCREENING_MIN_AVG_AMOUNT_20D",
    "l2_top_n": "SCREENING_L2_TOP_N",
}
# 键 → (下限, 上限)；成交额项接受 int/float，整数项仅 int（bool 是 int 子类，先排除）
_BOUNDS: dict[str, tuple[float, float]] = {
    "min_trading_days": (1, 250),
    "min_avg_amount_20d": (1_000_000.0, 1_000_000_000.0),
    "l2_top_n": (1, 500),
}
_DEFAULTS: dict[str, int | float] = {
    "min_trading_days": DEFAULT_MIN_TRADING_DAYS,
    "min_avg_amount_20d": DEFAULT_MIN_AVG_AMOUNT_20D,
    "l2_top_n": DEFAULT_L2_TOP_N,
}


def settings_path() -> Path:
    return Path(os.environ.get(ENV_VAR_PATH, "data/settings/screening_rules.json"))


def _valid(key: str, value: Any) -> int | float | None:
    """合法值 → int/float；bool / 类型不符 / 越界 → None。"""
    low, high = _BOUNDS[key]
    if isinstance(value, bool):
        return None
    if key == "min_avg_amount_20d":
        if not isinstance(value, (int, float)):
            return None
        checked: int | float = value
    else:
        if not isinstance(value, int):
            return None
        checked = value
    if not low <= checked <= high:
        return None
    return checked


def validate(key: str, value: Any) -> int | float:
    parsed = _valid(key, value)
    if parsed is None:
        low, high = _BOUNDS[key]
        raise ValueError(f"{key} 必须是 {low:g}–{high:g} 范围内的合法数值: {value!r}")
    return parsed


def _env_value(key: str) -> int | float | None:
    raw = os.getenv(ENV_VARS[key])
    if not raw:
        return None
    try:
        parsed = float(raw)
    except ValueError:
        _logger.warning("非法 %s=%r，忽略", ENV_VARS[key], raw)
        return None
    if key != "min_avg_amount_20d" and parsed == int(parsed):
        parsed = int(parsed)  # 整数项去浮点尾巴
    if _valid(key, parsed) is None:
        _logger.warning("%s=%r 越界或类型不符，忽略", ENV_VARS[key], raw)
        return None
    return parsed


def _read_settings_file() -> tuple[dict[str, Any], str]:
    """返回 (文件内合法值 dict, 文件状态)；状态 ∈ ok / missing / corrupt / invalid。"""
    path = settings_path()
    if not path.is_file():
        return {}, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}, "corrupt"
    if not isinstance(data, dict):
        return {}, "corrupt"
    values: dict[str, Any] = {}
    for key in _BOUNDS:
        parsed = _valid(key, data.get(key))
        if parsed is not None:
            values[key] = parsed
    if not values:
        return {}, "invalid"
    status = "ok" if len(values) == len(_BOUNDS) else "invalid"
    return values, status


def _file_updated_at() -> str | None:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    updated = data.get("updated_at") if isinstance(data, dict) else None
    return updated if isinstance(updated, str) else None


def resolve() -> dict[str, Any]:
    """读取生效值：逐项 file > env > default；附区间、默认值、env 披露与文件状态。"""
    saved, file_status = _read_settings_file()
    updated_at = _file_updated_at() if file_status in ("ok", "invalid") else None
    result: dict[str, Any] = {}
    for key, (low, high) in _BOUNDS.items():
        env_value = _env_value(key)
        if key in saved:
            value, source = saved[key], "file"
        elif env_value is not None:
            value, source = env_value, "env"
        else:
            value, source = _DEFAULTS[key], "default"
        result[key] = {
            "value": value,
            "source": source,
            "default": _DEFAULTS[key],
            "min": low,
            "max": high,
            "env_value": env_value,
        }
    result["file_status"] = file_status
    result["updated_at"] = updated_at
    return result


def save(payload: dict[str, Any]) -> dict[str, Any]:
    """三项整体校验后原子写设置文件，返回与 ``resolve()`` 同形的生效态。"""
    if not isinstance(payload, dict):
        raise ValueError(f"请求体必须是对象: {payload!r}")
    missing = sorted(set(_BOUNDS) - set(payload))
    if missing:
        raise ValueError(f"缺少字段: {missing}")
    parsed = {key: validate(key, payload[key]) for key in _BOUNDS}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        **parsed,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return resolve()
