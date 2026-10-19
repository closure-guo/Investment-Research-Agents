"""全市场日频行情快照日批编排（add-market-snapshot T6）。

编排顺序钉死（顺序本身是防错设计，不得自行调整）：

1. 解析 ``trade_date``（缺省 = 北京时区今天）与 ``client``（缺省 ``AKShareClient``）；
2. 日历守卫（D4）：拉全历史交易日历；``trade_date`` 不在日历内 → 返回
   ``{"skipped": "non_trading_day", ...}`` 且**不触碰 meta**；日历不可得时，
   显式传入日期 → 继续跑（摘要 ``calendar="unavailable"``），未显式传入 → 抛错；
3. spot 拉取（``fetch_market_spot``，一次全市场，禁止逐票循环）：None → 抛错；
4. 读上一交易日行（``latest_snapshot_date(before=trade_date)``）与证券主表。
   同日重跑必须取**严格早于**当日的行：拿当日已写行当 rebase 基准会把
   ``k = 昨收 / 当日收盘`` 误判成复权事件，二次复权整段历史 qfq；
5. D1 rebase：``compute_rebase_factors`` → 逐票 ``apply_rebase``，并同步更新
   内存 prev 行的 ``close_qfq``（本轮合成停牌行要承载复权后的基准）；
6. ``build_snapshot_rows``（D2 合成 + D3 状态位）→ ``write_snapshot_rows``（幂等）；
7. master 刷新：名单为空或每周一 → ``fetch_listing_info`` + upsert，并在**同一分支**
   同频抓取退市清单（``fetch_delist_lists`` + ``upsert_delisted_masters``，
   add-microcap-lane-guardrails）；任一拉取失败只 WARN，不失败整批；
8. 成功 meta：``last_success_date`` / ``last_attempt_at`` / ``trade_calendar``
   （本次拉到才写）与清 ``last_error``/``last_error_date`` 合并为**一次**
   ``set_meta_many(..., delete_keys=...)``（单事务「先删后写」，无中间态）；
9. 失败路径：2–8 任一步异常 → 写 ``last_error``/``last_error_date``/
   ``last_attempt_at`` 后**重抛**（交 ops ``run_job`` 记 failed、scheduler 重试；
   重试成功则 meta 被成功路径覆盖）。

``MARKET_SNAPSHOT_SCHEDULE`` 是 ops 注册表（``outcome/ops/jobs.py`` 的
``SCHEDULES``）与 scheduler 共用的 cron 规格。旁路铁律：本模块只抛错、不吞错。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from finance_agent.data.akshare_client import AKShareClient
from finance_agent.data.snapshot import (
    SnapshotError,
    _parse_date_param,
    apply_rebase,
    build_snapshot_rows,
    compute_rebase_factors,
    get_security_master,
    latest_snapshot_date,
    read_universe,
    set_meta_many,
    upsert_delisted_masters,
    upsert_security_master,
    write_snapshot_rows,
)

logger = logging.getLogger(__name__)

_BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# 成功路径一次删清的失败标记键（D5 靠 last_error_date 判定当日告警）
_ERROR_META_KEYS: tuple[str, ...] = ("last_error", "last_error_date")

# ops 注册表 / scheduler 共用的 cron 规格：工作日 15:30（A 股收盘后），逐字钉死
MARKET_SNAPSHOT_SCHEDULE: dict[str, Any] = {
    "day_of_week": "mon-fri",
    "hour": 15,
    "minute": 30,
    "timezone": "Asia/Shanghai",
}


def run_market_snapshot_batch(
    trade_date: str | None = None,
    *,
    client: AKShareClient | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """跑一轮全市场日批，返回摘要 dict（编排顺序见模块 docstring）。

    摘要键：``trade_date`` / ``written`` / ``rebased``（发生复权的票数）/
    ``suspended``（合成停牌行数）/ ``master``（``refreshed``|``skipped``）/
    ``calendar``（``ok``|``unavailable``）/ ``spot_source``（实际生效 spot 源
    ``eastmoney``|``sina``；客户端未上报 → None）；非交易日返回
    ``{"trade_date", "skipped": "non_trading_day", "written": 0, "rebased": 0,
    "suspended": 0}``。
    """
    explicit_date = trade_date is not None
    if trade_date is None:
        trade_date = datetime.now(_BEIJING_TZ).date().isoformat()
    else:
        # 与读取路径同一严格解析器：非规范日期会让日历比对/落库静默错位
        trade_date = _parse_date_param(trade_date, param="trade_date").isoformat()
    client = client or AKShareClient()
    try:
        calendar = client.fetch_trade_calendar()
        if calendar is None:
            if not explicit_date:
                raise SnapshotError("交易日历不可得")
            calendar_state = "unavailable"
        else:
            calendar_state = "ok"
            if trade_date not in calendar:
                # D4：非交易日不写行、不碰 meta（status 仍 ok）
                return {
                    "trade_date": trade_date,
                    "skipped": "non_trading_day",
                    "written": 0,
                    "rebased": 0,
                    "suspended": 0,
                }

        spot = client.fetch_market_spot()
        if spot is None:
            raise SnapshotError("全市场行情拉取失败")
        # add-market-data-fallback：实际生效源如实入摘要（eastmoney|sina；客户端未上报 → None）
        spot_source = getattr(client, "last_spot_source", None)

        last_date = latest_snapshot_date(db_path, before=trade_date)
        prev_rows = (
            {row["code"]: row for row in read_universe(last_date, db_path=db_path)}
            if last_date
            else {}
        )
        master = get_security_master(db_path)

        factors = compute_rebase_factors(spot, prev_rows)
        for code, factor in factors.items():
            apply_rebase(code, factor, trade_date, db_path=db_path)
            prev = prev_rows[code]
            # 同步内存基准：本轮合成停牌行承载复权后的 close_qfq
            prev["close_qfq"] = round(float(prev["close_qfq"]) * factor, 4)

        rows = build_snapshot_rows(
            trade_date=trade_date, spot=spot, prev_rows=prev_rows, master=master
        )
        written = write_snapshot_rows(trade_date, rows, db_path=db_path)

        master_state = "skipped"
        delist_state = "skipped"
        if not master or date.fromisoformat(trade_date).weekday() == 0:
            listing = client.fetch_listing_info()
            if listing:
                upsert_security_master(listing, db_path=db_path)
                master_state = "refreshed"
            else:
                logger.warning("主表刷新失败(trade_date=%s)：沿用既有名单", trade_date)
            # add-microcap-lane-guardrails：退市清单与上市清单**同频**刷新（同一分支）。
            # 失败只 WARN 不失败整批——缺退市数据时小票通道门禁保持 observation_only，
            # 快照/主档日批照常可用（口径见 metrics.md §1.16）。
            try:
                delisted = client.fetch_delist_lists()
            except Exception as e:  # noqa: BLE001 - 明细类数据缺失不拖垮日批
                logger.warning("退市清单拉取异常(trade_date=%s)：本次跳过: %s", trade_date, e)
                delisted = []
            if delisted:
                upsert_delisted_masters(delisted, db_path=db_path)
                delist_state = "refreshed"
            else:
                logger.warning("退市清单拉取失败/空(trade_date=%s)：沿用既有退市数据", trade_date)

        meta = {
            "last_success_date": trade_date,
            "last_attempt_at": datetime.now(_BEIJING_TZ).isoformat(),
        }
        if calendar is not None:
            meta["trade_calendar"] = json.dumps(calendar)
        # 单次调用内「先删失败标记、后写成功键」（同一事务）：不得分两步提交，
        # 否则中间态会让 D5 在成功已写入后继续按 last_error_date 告警
        set_meta_many(meta, db_path=db_path, delete_keys=_ERROR_META_KEYS)

        return {
            "trade_date": trade_date,
            "written": written,
            "rebased": len(factors),
            "suspended": sum(1 for row in rows if row.get("is_suspended") == 1),
            "master": master_state,
            "delist_master": delist_state,
            "calendar": calendar_state,
            "spot_source": spot_source,
        }
    except Exception as exc:  # noqa: BLE001 - 失败标记后重抛，交 ops/scheduler 处置
        try:
            set_meta_many(
                {
                    "last_error": f"{type(exc).__name__}: {exc}",
                    "last_error_date": datetime.now(_BEIJING_TZ).date().isoformat(),
                    "last_attempt_at": datetime.now(_BEIJING_TZ).isoformat(),
                },
                db_path=db_path,
            )
        except Exception:  # noqa: BLE001 - 告警落库失败只记日志，不得盖住真错误
            logger.exception("快照失败标记落库失败")
        raise
