"""add-market-data-fallback 真实网络验证脚本（自包含；真实 akshare，非 mock）。

用途：复现验证报告 tests/validation/2026-10-15-add-market-data-fallback-validation.md
的全部 live 读数。默认**不写主库**：快照日批落到 tempfile 隔离库（`--db` 指定则用之）。

    uv run python tests/validation/2026-10-15-add-market-data-fallback-live-check.py

覆盖：
1. 新浪全市场 spot（ak.stock_zh_a_spot）可达性：形状/列名/耗时/停牌 0 值计数；
2. `_map_sina_spot_to_em` 真实帧映射读数：列形/去前缀/股→手/0 值→NaN；
3. D2 停牌合成语义（真实停牌行 + 承价行 → is_suspended=1）；
4. 真实数据快照日批（隔离库）：摘要（含 spot_source）+ 抽样 3 行 + 库内卫生检查；
5. 回填取数新浪回退（ak.stock_zh_a_daily 双口径）真实读数 + 与 spot 交叉核对。

退出码 0（跑完）/1（关键断言失败：真实读数与契约不符）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from finance_agent.data.akshare_client import (
    _EM_SPOT_COLUMNS,
    AKShareClient,
    _map_sina_spot_to_em,
)
from finance_agent.data.snapshot import build_snapshot_rows
from finance_agent.data.snapshot_job import run_market_snapshot_batch

_FAILURES: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        _FAILURES.append(label)


def _section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="add-market-data-fallback live 验证")
    parser.add_argument(
        "--trade-date",
        default=None,
        help="快照日批交易日（缺省=北京时今天；北京时 15:00 前的 spot 为上一收盘，慎用缺省）",
    )
    parser.add_argument("--db", default=None, help="隔离库路径（缺省 tempfile 新建）")
    args = parser.parse_args(argv)

    beijing = datetime.now(ZoneInfo("Asia/Shanghai"))
    print(f"北京时: {beijing.isoformat()}（工作日 {beijing.weekday()}）")

    # ── 1. 新浪全市场 spot 可达性 ────────────────────────────────────────────
    _section("1. 新浪全市场 spot（ak.stock_zh_a_spot）")
    t0 = time.time()
    sina = ak.stock_zh_a_spot()
    sina_elapsed = time.time() - t0
    print(f"shape={sina.shape} 耗时={sina_elapsed:.1f}s")
    print(f"列: {list(sina.columns)}")
    zero_price = int((pd.to_numeric(sina["最新价"], errors="coerce") == 0).sum())
    print(
        f"停牌 0 值行（最新价 == 0）: {zero_price}；代码前缀分布: "
        f"{sina['代码'].str[:2].value_counts().to_dict()}"
    )
    _check(
        "新浪 spot 非空且列齐（代码/名称/最新价/涨跌幅/成交量/成交额/昨收/今开/最高/最低）",
        not sina.empty
        and {"代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "昨收", "今开", "最高", "最低"}
        <= set(sina.columns),
        f"{sina.shape}",
    )

    # ── 2. 真实帧映射读数 ────────────────────────────────────────────────────
    _section("2. _map_sina_spot_to_em 真实帧映射")
    mapped = _map_sina_spot_to_em(sina)
    print(f"输出列: {list(mapped.columns)}")
    print(f"NaN 最新价（含 0 值归一）: {int(mapped['最新价'].isna().sum())}")
    sample = mapped[mapped["代码"].isin(["600519", "000001", "920000"])]
    print(sample.to_string(index=False))
    _check("输出列恰为日批消费的 10 列", list(mapped.columns) == list(_EM_SPOT_COLUMNS))
    _check(
        "代码无交易所前缀（全 6 位数字）",
        bool(
            mapped["代码"].map(lambda c: isinstance(c, str) and c.isdigit() and len(c) == 6).all()
        ),
    )
    _check(
        "停牌 0 值行归一为 NaN",
        int(mapped["最新价"].isna().sum()) >= zero_price,
        f"0 值 {zero_price} 行 → NaN {int(mapped['最新价'].isna().sum())} 行",
    )
    spot_vol = float(sina.loc[sina["代码"] == "sh600519", "成交量"].iloc[0])
    mapped_vol = float(mapped.loc[mapped["代码"] == "600519", "成交量"].iloc[0])
    _check(
        "成交量股→手（÷100）", abs(mapped_vol - spot_vol / 100) < 1e-6, f"{spot_vol} → {mapped_vol}"
    )

    # ── 3. D2 停牌合成语义（真实停牌行 + 承价行） ─────────────────────────────
    _section("3. D2 停牌合成语义（真实停牌行）")
    suspended_codes = mapped.loc[mapped["最新价"].isna(), "代码"].tolist()
    prev_rows = {}
    for code in suspended_codes:
        row = mapped.loc[mapped["代码"] == code].iloc[0]
        # 承价：真实「昨收」当昨行收盘（真实停牌股上一交易日有行 → close 可承）
        prev_rows[code] = {
            "code": code,
            "name": row["名称"],
            "close_raw": float(row["昨收"]),
            "close_qfq": float(row["昨收"]),
            "list_date": None,
        }
    synth = {
        r["code"]: r
        for r in build_snapshot_rows(
            trade_date=args.trade_date or beijing.date().isoformat(),
            spot=mapped,
            prev_rows=prev_rows,
            master={},
        )
        if r["code"] in suspended_codes
    }
    print(f"真实停牌码 {len(suspended_codes)} 个: {suspended_codes}")
    print(
        pd.DataFrame(
            [
                {
                    k: v
                    for k, v in synth[c].items()
                    if k
                    in (
                        "code",
                        "close_raw",
                        "close_qfq",
                        "volume",
                        "amount",
                        "pct_chg",
                        "is_suspended",
                    )
                }
                for c in suspended_codes
                if c in synth
            ]
        ).to_string(index=False)
        if synth
        else "（无合成行）"
    )
    _check(
        "全部真实停牌行合成 is_suspended=1 且承昨收价",
        len(synth) == len(suspended_codes)
        and all(
            r["is_suspended"] == 1 and r["close_raw"] == prev_rows[c]["close_raw"]
            for c, r in synth.items()
        ),
        f"合成 {len(synth)}/{len(suspended_codes)}",
    )

    # ── 4. 真实数据快照日批（隔离库） ────────────────────────────────────────
    _section("4. 真实数据快照日批（隔离库）")
    db_path = (
        Path(args.db)
        if args.db
        else Path(tempfile.mkdtemp(prefix="val-snapshot-")) / "market_snapshot.db"
    )
    trade_date = args.trade_date or beijing.date().isoformat()
    print(f"隔离库: {db_path}\ntrade_date: {trade_date}")
    client = AKShareClient()
    t0 = time.time()
    summary = run_market_snapshot_batch(trade_date, client=client, db_path=db_path)
    batch_elapsed = time.time() - t0
    print(f"\n日批耗时: {batch_elapsed:.1f}s")
    print(f"摘要: {json.dumps(summary, ensure_ascii=False)}")
    print(
        f"client.sources_seen['market_spot']={client.sources_seen['market_spot']} "
        f"last_spot_source={client.last_spot_source!r}"
    )
    _check(
        "摘要 spot_source 为实际生效源（eastmoney/sina）",
        summary.get("spot_source") in ("eastmoney", "sina"),
        repr(summary.get("spot_source")),
    )
    _check(
        "摘要与客户端留痕一致",
        (client.last_spot_source in client.sources_seen["market_spot"])
        and summary.get("spot_source") == client.last_spot_source,
    )

    conn = sqlite3.connect(db_path)
    try:
        total, n_codes, n_zero, n_null, n_vol0, amount_sum = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT code), "
            "SUM(CASE WHEN close_raw IS NOT NULL AND close_raw <= 0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN close_raw IS NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN volume = 0 THEN 1 ELSE 0 END), SUM(amount) "
            "FROM market_snapshot WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        n_prefixed = conn.execute(
            "SELECT COUNT(*) FROM market_snapshot WHERE trade_date = ? "
            "AND (code LIKE 'sh%' OR code LIKE 'sz%' OR code LIKE 'bj%')",
            (trade_date,),
        ).fetchone()[0]
        mismatched = conn.execute(
            "SELECT COUNT(*) FROM market_snapshot WHERE trade_date = ? AND close_qfq != close_raw",
            (trade_date,),
        ).fetchone()[0]
        sampled = conn.execute(
            "SELECT code, name, close_raw, close_qfq, pct_chg, volume, amount, prev_close, "
            "is_st, is_suspended, listed_days FROM market_snapshot WHERE trade_date = ? "
            "ORDER BY code LIMIT 3",
            (trade_date,),
        ).fetchall()
        top = conn.execute(
            "SELECT code, name, amount FROM market_snapshot WHERE trade_date = ? "
            "ORDER BY amount DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
        zero_volume = conn.execute(
            "SELECT code, name, close_raw, volume, amount FROM market_snapshot "
            "WHERE trade_date = ? AND volume = 0 ORDER BY code LIMIT 6",
            (trade_date,),
        ).fetchall()
    finally:
        conn.close()
    print(
        f"\n库内行数: {total}（distinct code {n_codes}）；close_raw<=0: {n_zero}；"
        f"close_raw NULL: {n_null}；volume=0: {n_vol0}；带前缀代码: {n_prefixed}；"
        f"close_qfq != close_raw: {mismatched}"
    )
    print(f"amount 合计: {amount_sum:.4g}；最大成交额: {top}")
    print(f"volume=0 行（有效价格但当日零成交，新浪原样透传）: {zero_volume}")
    print(
        "抽样 3 行（code/name/close_raw/close_qfq/pct_chg/volume/amount/prev_close/is_st/"
        "is_suspended/listed_days）:"
    )
    for row in sampled:
        print("  " + " | ".join(str(v) for v in row))
    _check("无 0/负收盘价（停牌 0 值未落库）", n_zero == 0, f"{n_zero} 行")
    _check("无带交易所前缀的代码", n_prefixed == 0, f"{n_prefixed} 行")
    _check("当日 close_qfq == close_raw（D1 当日基准）", mismatched == 0, f"{mismatched} 行")

    # ── 5. 回填取数新浪回退（真实双口径）+ 跨端点交叉核对 ────────────────────
    _section("5. 回填取数新浪回退（ak.stock_zh_a_daily 双口径）")
    start = (pd.Timestamp(trade_date) - pd.Timedelta(days=30)).strftime("%Y%m%d")
    end = pd.Timestamp(trade_date).strftime("%Y%m%d")
    trade_dt = date.fromisoformat(trade_date)

    # 生效源判别：扫描本次调用期间客户端日志（回退成功必留「回退新浪源」行）
    records: list[str] = []

    class _RecordHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    ak_logger = logging.getLogger("finance_agent.data.akshare_client")
    handler = _RecordHandler()
    prev_level = ak_logger.level
    ak_logger.addHandler(handler)
    ak_logger.setLevel(logging.INFO)  # 回退判别依赖 INFO 级「回退新浪源」行
    try:
        t0 = time.time()
        em_leg = client._fetch_symbol_history_em("600519", start, end)
        em_elapsed = time.time() - t0
        records.clear()
        t0 = time.time()
        hist = client.fetch_symbol_history_raw_qfq("600519", start, end)
        hist_elapsed = time.time() - t0
        fell_back = any("回退新浪源" in m for m in records)
    finally:
        ak_logger.removeHandler(handler)
        ak_logger.setLevel(prev_level)
    print(
        f"symbol=600519 区间 {start}~{end}：东财腿{'失败(None)' if em_leg is None else '成功'} "
        f"耗时 {em_elapsed:.1f}s；完整调用耗时 {hist_elapsed:.1f}s；"
        f"本调用生效源={'sina（回退）' if fell_back else 'eastmoney'}"
    )
    if hist is None:
        _check("回填取数（东财失败→新浪回退）返回双口径帧", False, "返回 None")
    else:
        print(f"shape={hist.shape} 列={list(hist.columns)}")
        print(hist.tail(3).to_string(index=False))
        spot_row = mapped.loc[mapped["代码"] == "600519"].iloc[0]
        _check(
            "列集合为回填输出契约",
            list(hist.columns) == ["日期", "开盘", "收盘_raw", "收盘_qfq", "成交量"],
        )
        # 跨端点交叉核对（价格）：spot 昨收 = 上一交易日收盘，两侧端点独立给值
        prior = hist[hist["日期"] < trade_dt]
        _check(
            "hist 上一交易日 收盘_raw == spot 昨收（跨端点）",
            not prior.empty
            and abs(float(prior.iloc[-1]["收盘_raw"]) - float(spot_row["昨收"])) < 0.005,
            f"{prior.iloc[-1]['收盘_raw'] if not prior.empty else 'n/a'} vs {spot_row['昨收']}",
        )
        if hist.iloc[-1]["日期"] == trade_dt:
            _check(
                "hist 含当日 bar 时 收盘_raw == spot 最新价",
                abs(float(hist.iloc[-1]["收盘_raw"]) - float(spot_row["最新价"])) < 0.005,
            )
        else:
            print(
                "注：新浪历史端点当日 bar 尚未发布（实测 15:05 曾含 10-15 bar、15:15 不含）"
                "——该场景不作为断言，避免把上游发布滞后误判为回退缺陷"
            )
        # 成交量单位自证：源 volume 为股（amount/(volume×close) ≈ 1）→ 出口 ÷100 得手
        raw_leg = ak.stock_zh_a_daily(symbol="sh600519", start_date=start, end_date=end, adjust="")
        raw_vol = dict(zip(raw_leg["date"], raw_leg["volume"], strict=True))
        raw_amt = dict(zip(raw_leg["date"], raw_leg["amount"], strict=True))
        ratios = [
            raw_amt[d] / (v * c)
            for d, v, c in zip(raw_leg["date"], raw_leg["volume"], raw_leg["close"], strict=True)
            if v and v > 0 and c and c > 0
        ]
        _check(
            "源成交量确为股（amount/(volume×close) ≈ 1，±2%）",
            bool(ratios) and all(0.98 <= r <= 1.02 for r in ratios),
            f"区间 [{min(ratios):.4f}, {max(ratios):.4f}]" if ratios else "无有效行",
        )
        _check(
            "出口成交量 == 源股数 ÷100（逐日核对）",
            all(
                abs(float(r["成交量"]) - float(raw_vol[r["日期"]]) / 100) < 1e-6
                for _, r in hist.iterrows()
            ),
        )

    _section("结论")
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} 项 → {_FAILURES}")
        return 1
    print("全部 live 读数与契约一致（PASS）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
