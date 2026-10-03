"""update-risk-free-rate-source：无风险利率序列（中债国债收益率曲线 1 年期）。

sync_risk_free_rates：拉取→年化小数→幂等落库（日批与回填脚本共用）；
risk_free_series：目标日期 → 年化 rf，回退链 库内 carry-forward（序列起点前
backward-fill 到最早记录）→ 空表回退常数 RISK_FREE_RATE（TRACK_RISK_FREE_RATE）。
口径细则见 openspec/changes/update-risk-free-rate-source。
"""

from __future__ import annotations

import bisect
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from finance_agent.outcome.track_record.model import (
    earliest_mark_date,
    list_risk_free_rates,
    upsert_risk_free_rates,
)

logger = logging.getLogger(__name__)

RF_SOURCE = "chinabond-cgb-1y"
_BACKFILL_BUFFER_DAYS = 7
_DEFAULT_LOOKBACK_DAYS = 400


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def sync_risk_free_rates(
    *,
    client: Any = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """拉取中债 1Y 国债收益率并落库；取数失败抛异常（调用方负责隔离）。

    区间起点 = 最早盯市日 − 7 天缓冲（覆盖首日 rf 对齐），无 marks 时回退
    今日 − 400 天（首部署默认回填窗）。
    """
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    if (first := earliest_mark_date(db_path=db_path)) is not None:
        start = (date.fromisoformat(first) - timedelta(days=_BACKFILL_BUFFER_DAYS)).isoformat()
    else:
        start = (date.fromisoformat(_today()) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)).isoformat()
    end = _today()
    df = client.fetch_bond_yield_curve(start, end)
    if df is None or df.empty:
        raise RuntimeError(f"国债收益率曲线取数为空 [{start}..{end}]")
    rows = [
        (str(d)[:10], float(r) / 100.0, RF_SOURCE)
        for d, r in zip(df["日期"], df["1年"], strict=False)
    ]
    stored = upsert_risk_free_rates(rows, db_path=db_path)
    return {"stored": stored, "start": start, "end": end}


def risk_free_series(
    dates: list[str],
    db_path: str | Path | None = None,
) -> dict[str, float]:
    """目标日期序列 → 年化 rf（小数）。as-of join：最近历史日优先（含当日命中，
    缺日 carry-forward），序列起点前 backward-fill 最早记录，空表全常数
    RISK_FREE_RATE。"""
    from finance_agent.outcome.track_record.metrics import RISK_FREE_RATE

    records = list_risk_free_rates(db_path=db_path)
    if not records:
        return dict.fromkeys(dates, RISK_FREE_RATE)
    rec_dates = [r["rate_date"] for r in records]
    rec_rates = [r["rate"] for r in records]
    out: dict[str, float] = {}
    for d in dates:
        i = bisect.bisect_right(rec_dates, d) - 1
        out[d] = rec_rates[i] if i >= 0 else rec_rates[0]
    return out
