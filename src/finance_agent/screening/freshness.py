"""深度分析前的信号时效性校验（从 api 迁出，供人工/自动批量分析共用）。

以**最新可得快照**（``latest_snapshot_date``）为基准日，对池内符号重校验停牌/
一字涨跌停/新事件（``check_signal_freshness``）；不通过者返回 findings
（``[{symbol, reason, detail}]``）并落 ``screening_removals`` 审计行（基准日 =
快照日；审计写失败记 warning 不阻断返回）。

数据不可得 → 返回 ``[]``（记日志，不臆造移除）：① 快照库无行（无「最新可得
状态」可核对）；② 快照最新日期早于池批次日（无更新状态）。读快照失败同样
降级——本校验是重检（不是主门禁），可用性故障不得把批量分析打成 500、
也不得以空 findings 冒充「已校验通过」。三条降级路径（含跳过）**一律记
WARNING 及以上**：spec 要求的重校验被跳过着对运维必须可见，不得静默通过。
"""

from __future__ import annotations

import logging
from typing import Any

from finance_agent.data.snapshot import latest_snapshot_date, read_universe
from finance_agent.events.pipeline import fetch_key_events
from finance_agent.screening.pipeline import (
    check_signal_freshness,
    read_snapshot_calendar,
)
from finance_agent.screening.store import record_screening_removals

logger = logging.getLogger("finance_agent.screening.freshness")


def screening_freshness_removals(
    pool_rows: list[dict[str, Any]], batch_date: str | None
) -> list[dict[str, Any]]:
    """深度分析前的信号时效性校验；返回被移除标的的 findings。"""
    snapshot_date = latest_snapshot_date()
    if snapshot_date is None:
        logger.warning("选股时效性校验跳过：快照库无数据（无最新可得状态可核对）")
        return []
    if batch_date is not None and snapshot_date < batch_date:
        logger.warning(
            "选股时效性校验跳过：快照最新日期 %s 早于池批次 %s（无更新状态可核对）",
            snapshot_date,
            batch_date,
        )
        return []
    try:
        rows = read_universe(snapshot_date)
        calendar = read_snapshot_calendar()
    except Exception:  # noqa: BLE001 -- 校验输入不可得：降级跳过，不让调用方 500
        logger.exception("选股时效性校验读取快照失败（%s），本次跳过校验", snapshot_date)
        return []
    symbols = [str(row.get("symbol") or "").strip() for row in pool_rows]
    symbols = [symbol for symbol in symbols if symbol]
    names_by_symbol = {
        str(row.get("code") or "").strip(): str(row.get("name") or "").strip() for row in rows
    }
    findings = check_signal_freshness(
        symbols,
        snapshot_rows={str(row.get("code") or "").strip(): row for row in rows},
        events_by_symbol={
            symbol: fetch_key_events(symbol, names_by_symbol.get(symbol, "")) for symbol in symbols
        },
        as_of=snapshot_date,
        calendar=calendar,
    )
    if findings:
        try:
            record_screening_removals(snapshot_date, findings)
        except Exception:  # noqa: BLE001 -- 审计写失败不回滚已判定的移除（记日志）
            logger.exception("选股移除审计写库失败（%s）", snapshot_date)
    return findings
