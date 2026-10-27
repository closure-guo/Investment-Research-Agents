"""因子诊断读数日批（add-factor-diagnostics-batch）。

背景：详情接口曾在请求内现算 500 日准入面板（风格暴露 + 分层单调性），IC 历史
回填把护栏（窗口 IC < 30 短路）绕过后，active 因子详情全部挂死（生产实测单请求
71+ 分钟）。本模块把读数生产移到日批（作业 ``factor_diagnostics_daily``）：

- **待算集合**：窗口 IC 点数 ≥ ``MIN_STYLE_SAMPLES`` 且（无落库读数 或 ``as_of``
  落后最新快照日）——周末快照无新交易日 → 全部已新鲜 → 零面板构建近零成本；
- **面板按需**：仅在有 ≥ 1 个待算因子时经 ``monthly._panel_from_snapshot`` 构建
  （准入深度口径不变），单次运行至多一次并逐因子复用（与收割「无候选不建面板」
  语义一致）；
- **逐因子失败隔离**：单因子异常（表达式不可求值/数据异常）只跳过该因子（保留
  旧读数），MUST NOT 中断其余因子或上抛（作业记 ok）；
- 落库读数为**只读观测项**，SHALL NOT 参与任何准入裁决或处置（处置仍只经衰减
  监控路径）。

诊断口径与原详情路径逐字同源（``check_monotonicity`` / ``compute_style_exposure``
不改语义）；读数反映日批运行时点的面板（``as_of`` = 面板最新交易日）。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from finance_agent.data.snapshot import latest_snapshot_date
from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import (
    get_factor_diagnostics,
    list_factors,
    list_ic_series,
    set_factor_diagnostics,
)
from finance_agent.factors.monthly import (
    _panel_from_snapshot,  # noqa: PLC2701 -- 与 builtin/harvest 同先例
)
from finance_agent.factors.style_exposure import (  # noqa: PLC2701
    MIN_STYLE_SAMPLES,
    compute_style_exposure,
    style_exposure_payload,
)
from finance_agent.factors.tools import IC_WINDOW
from finance_agent.factors.validate import check_monotonicity

logger = logging.getLogger(__name__)


def _finite(value: Any) -> Any:
    """非有限值 → ``None``（dict/list 递归清洗；JSON 不写 NaN/Inf，monitor 同纪律）。"""
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def run_factor_diagnostics(
    *,
    panel: MarketPanel | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """诊断读数日批入口（作业零参数调用）。

    ``panel`` 仅供测试注入（生产路径按需经 ``_panel_from_snapshot`` 构建）。返回
    摘要 ``{"candidates", "computed", "skipped_fresh", "skipped_samples", "failed",
    "as_of"}``——``candidates`` = 过 IC 门槛的因子数，``computed`` = 本次落库 id，
    ``skipped_fresh`` = 读数已新鲜跳过数，``skipped_samples`` = IC 点数不足数，
    ``failed`` = ``{factor_id: 错误摘要}``（失败隔离，不抛错）。
    """
    rows = list_factors(db_path=db_path)
    candidates: list[dict[str, Any]] = []
    skipped_samples = 0
    for row in rows:
        series = list_ic_series(int(row["id"]), limit=IC_WINDOW, db_path=db_path)
        if len(series) < MIN_STYLE_SAMPLES:
            skipped_samples += 1
            continue
        candidates.append(row)

    if panel is not None:
        as_of = str(panel.fields["close"].index[-1])
    else:
        as_of = latest_snapshot_date(snapshot_db_path)
        if as_of is None:
            if candidates:
                raise ValueError(
                    "快照库为空：存在待算因子但无法构建诊断面板（先跑 market_snapshot_daily）"
                )
            return {
                "candidates": 0,
                "computed": [],
                "skipped_fresh": 0,
                "skipped_samples": skipped_samples,
                "failed": {},
                "as_of": None,
            }

    # 增量过滤：读数缺失或 as_of 落后 → 待算；其余已新鲜跳过（ISO 日期字典序=时间序）
    stale: list[dict[str, Any]] = []
    skipped_fresh = 0
    for row in candidates:
        stored = get_factor_diagnostics(int(row["id"]), db_path=db_path)
        if stored is not None and str(stored["as_of"]) >= str(as_of):
            skipped_fresh += 1
            continue
        stale.append(row)

    summary: dict[str, Any] = {
        "candidates": len(candidates),
        "computed": [],
        "skipped_fresh": skipped_fresh,
        "skipped_samples": skipped_samples,
        "failed": {},
        "as_of": as_of,
    }
    if not stale:
        return summary  # 零待算 → 零面板构建（MUST NOT 为空集建面板）

    if panel is None:
        panel = _panel_from_snapshot(snapshot_db_path)
    fwd = forward_returns(panel.fields["close"])

    for row in stale:
        factor_id = int(row["id"])
        try:
            values = evaluate(parse(str(row["expression"])), panel)
            factor_ic = rank_ic_series(values, fwd)
            monotonicity = check_monotonicity(values, fwd, factor_ic)
            exposure = style_exposure_payload(compute_style_exposure(factor_ic, panel, fwd=fwd))
            set_factor_diagnostics(
                factor_id,
                str(as_of),
                style_exposure=_finite(exposure),
                monotonicity=_finite({"passed": monotonicity.passed, **monotonicity.detail}),
                db_path=db_path,
            )
            summary["computed"].append(factor_id)
        except (OSError, ValueError) as exc:
            # 失败隔离：保留旧读数（无则维持无读数），不中断批、不上抛
            summary["failed"][factor_id] = f"{type(exc).__name__}: {exc}"
            logger.warning("因子 %s 诊断读数计算失败（保留旧读数）: %s", factor_id, exc)
    return summary
