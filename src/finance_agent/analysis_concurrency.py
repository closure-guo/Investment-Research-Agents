"""批量分析最大并发数设置（delta add-analysis-concurrency-setting）。

来源优先级：设置文件 > 环境变量 ``SCREENING_ANALYZE_CONCURRENCY`` > 默认值。
文件缺失/损坏/字段非法时回退并如实披露 ``file_status``，不以默认值冒充已保存设置。
本模块只管「值从哪来、怎么写」；运行期生效由 api 层应用到 AnalyzeQueue。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_logger = logging.getLogger("finance_agent.analysis_concurrency")

DEFAULT_MAX_CONCURRENCY = 10
MIN_MAX_CONCURRENCY = 1
MAX_MAX_CONCURRENCY = 20
ENV_VAR = "SCREENING_ANALYZE_CONCURRENCY"


def settings_path() -> Path:
    return Path(
        os.environ.get("ANALYSIS_CONCURRENCY_PATH", "data/settings/analysis_concurrency.json")
    )


def _valid(value: Any) -> int | None:
    """合法值 → int；布尔/非整数/越界 → None（bool 是 int 子类，先排除）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not MIN_MAX_CONCURRENCY <= value <= MAX_MAX_CONCURRENCY:
        return None
    return value


def validate(value: Any) -> int:
    """保存入参校验：非法 → ValueError（端点转 400）。"""
    parsed = _valid(value)
    if parsed is None:
        raise ValueError(
            f"最大并发分析数必须是 {MIN_MAX_CONCURRENCY}–{MAX_MAX_CONCURRENCY} 的整数: {value!r}"
        )
    return parsed


def _env_value() -> int | None:
    raw = os.getenv(ENV_VAR)
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        _logger.warning("非法 %s=%r，忽略", ENV_VAR, raw)
        return None
    if _valid(parsed) is None:
        _logger.warning("%s=%r 越界，忽略", ENV_VAR, parsed)
        return None
    return parsed


def _read_settings_file() -> tuple[int | None, str]:
    """返回 (保存值, 文件状态)；状态 ∈ ok / missing / corrupt / invalid。"""
    path = settings_path()
    if not path.is_file():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, "corrupt"
    if not isinstance(data, dict):
        return None, "corrupt"
    value = _valid(data.get("max_concurrency"))
    if value is None:
        return None, "invalid"
    return value, "ok"


def resolve() -> dict[str, Any]:
    """读取生效值：文件 > env > 默认；附来源与环境变量披露（只读）。"""
    saved, file_status = _read_settings_file()
    env_value = _env_value()
    if saved is not None:
        value, source = saved, "file"
    elif env_value is not None:
        value, source = env_value, "env"
    else:
        value, source = DEFAULT_MAX_CONCURRENCY, "default"
    return {
        "value": value,
        "source": source,
        "default": DEFAULT_MAX_CONCURRENCY,
        "min": MIN_MAX_CONCURRENCY,
        "max": MAX_MAX_CONCURRENCY,
        "env_value": env_value,
        "file_status": file_status,
    }


def save(value: Any) -> dict[str, Any]:
    """校验并原子写设置文件，返回与 ``resolve()`` 同形的生效态。"""
    parsed = validate(value)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "max_concurrency": parsed,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return resolve()
