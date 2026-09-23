"""cohort 标的池登记与可复现抽样(delta add-forward-paper-trading-cohort)。

设计：**登记文件是唯一真相**——抽样与富化(行业/市值)在构建期一次性完成并落
`data/cohort/universe-v1.json`，运行时(runner)只 `load_universe` 读文件，
不再联网抽样，保证同 `universe_version` 内标的池逐字不变(纵向可比)。

- 分层维度 = `(industry, market_cap_bucket)`；市值档按池内总市值三分位切
  `large` / `mid` / `small`。
- 抽样为**无放回**，随机源 = `np.random.default_rng(seed)`：同 seed 可复现，
  首轮每层至多抽 1 只(层覆盖)，余量按层轮转补齐。
- 版本期内换池：`write_universe` 默认拒绝覆盖已存在文件(须 `force=True`，
  即显式另起版本号)。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("version", "effective_date", "seed", "strata", "constituents")
CONSTITUENT_KEYS = ("ticker", "name", "industry", "market_cap_bucket")

STRATA_BY = "industry+market_cap"
BUCKETS = ("large", "mid", "small")
UNKNOWN_INDUSTRY = "未知"
# 市值分位切点：< 1/3 → small；< 2/3 → mid；其余 large
_QUANTILE_LOW = 1 / 3
_QUANTILE_HIGH = 2 / 3


@dataclass(frozen=True)
class Constituent:
    ticker: str
    name: str
    industry: str
    market_cap_bucket: str  # "large" / "mid" / "small"（按总市值分位）


@dataclass(frozen=True)
class Universe:
    version: str
    effective_date: str
    seed: int
    strata: dict[str, Any]  # {"by": "industry+market_cap", "n": 10}
    constituents: tuple[Constituent, ...]


def _require_keys(payload: dict[str, Any], keys: tuple[str, ...], what: str) -> None:
    for key in keys:
        if key not in payload:
            raise ValueError(f"{what}缺少必需字段: {key}")


def load_universe(path: str | Path) -> Universe:
    """读取并校验 universe 登记文件；缺任一必需键抛 ValueError（消息含字段名）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("universe 登记文件根节点必须是 JSON 对象")
    _require_keys(raw, REQUIRED_KEYS, "universe 登记文件")

    constituents: list[Constituent] = []
    for item in raw["constituents"]:
        if not isinstance(item, dict):
            raise ValueError("universe 成分必须是 JSON 对象")
        _require_keys(item, CONSTITUENT_KEYS, "universe 成分")
        constituents.append(
            Constituent(
                ticker=str(item["ticker"]),
                name=str(item["name"]),
                industry=str(item["industry"]),
                market_cap_bucket=str(item["market_cap_bucket"]),
            )
        )
    return Universe(
        version=str(raw["version"]),
        effective_date=str(raw["effective_date"]),
        seed=int(raw["seed"]),
        strata=dict(raw["strata"]),
        constituents=tuple(constituents),
    )


def write_universe(data: dict[str, Any], path: str | Path, *, force: bool = False) -> Path:
    """落登记文件。默认拒绝覆盖（版本生效期内标的池不得变更）；须 force 显式换池。"""
    out = Path(path)
    if out.exists() and not force:
        raise FileExistsError(
            f"universe 登记文件已存在，版本生效期内不得改写: {out}"
            "（换池请用新 version，或显式 force=True）"
        )
    _require_keys(data, REQUIRED_KEYS, "universe 登记数据")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _market_cap_bucket(cap: float, low: float, high: float) -> str:
    if cap <= low:
        return "small"
    if cap <= high:
        return "mid"
    return "large"


def _stratified_sample(rows: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """按 (industry, market_cap_bucket) 分层无放回抽 n 只（np.random.default_rng(seed)）。

    首轮每层至多 1 只 → 保证层覆盖；余量按层轮转补齐。无放回即不重复。
    """
    if n > len(rows):
        raise ValueError(f"可选池仅 {len(rows)} 只，不足以无放回抽取 n={n} 只")
    rng = np.random.default_rng(seed)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        strata.setdefault((row["industry"], row["market_cap_bucket"]), []).append(row)

    keys = sorted(strata)
    # 层内顺序 + 层间顺序均由 rng 决定 → 同 seed 完全复现
    pools = {k: [strata[k][i] for i in rng.permutation(len(strata[k]))] for k in keys}
    key_order = [keys[i] for i in rng.permutation(len(keys))]

    picked: list[dict[str, Any]] = []
    for key in key_order:
        if len(picked) >= n:
            break
        picked.append(pools[key].pop(0))
    while len(picked) < n:
        progressed = False
        for key in key_order:
            if len(picked) >= n:
                break
            if pools[key]:
                picked.append(pools[key].pop(0))
                progressed = True
        if not progressed:  # 防御：n <= len(rows) 时不可达
            break
    return sorted(picked, key=lambda r: r["ticker"])


def build_universe(
    version: str,
    *,
    n: int = 10,
    seed: int = 42,
    client: Any | None = None,
) -> dict[str, Any]:
    """沪深300 成分 → 逐只富化(行业/市值,带缓存) → 分层抽样 n 只 → 可 JSON 化 dict。

    富化对**整池**执行一次（市值分位切点需全池），结果写入登记文件；运行时不再抓取。
    """
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()

    raw = client.fetch_index_constituents()
    if not raw:
        raise ValueError("指数成分列表为空，无法构建 universe（成分信源不可用）")

    industry_cache: dict[str, str] = {}
    cap_cache: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    for item in raw:
        ticker = str(item["ticker"])
        if ticker not in industry_cache:
            info = client.fetch_industry(ticker) or {}
            industry_cache[ticker] = str(info.get("industry") or UNKNOWN_INDUSTRY)
        if ticker not in cap_cache:
            quote = client.fetch_stock_quote(ticker) or {}
            cap = quote.get("market_cap")
            if not isinstance(cap, (int, float)) or isinstance(cap, bool):
                logger.warning("市值缺失，按 0 计入最小区间: %s", ticker)
                cap = 0.0
            cap_cache[ticker] = float(cap)
        rows.append(
            {
                "ticker": ticker,
                "name": str(item.get("name") or ""),
                "industry": industry_cache[ticker],
                "market_cap": cap_cache[ticker],
            }
        )

    caps = np.array([r["market_cap"] for r in rows], dtype=float)
    low = float(np.quantile(caps, _QUANTILE_LOW))
    high = float(np.quantile(caps, _QUANTILE_HIGH))
    for row in rows:
        row["market_cap_bucket"] = _market_cap_bucket(row["market_cap"], low, high)

    picked = _stratified_sample(rows, n, seed)
    return {
        "version": version,
        "effective_date": date.today().isoformat(),
        "seed": seed,
        "strata": {"by": STRATA_BY, "n": n},
        "constituents": [
            {
                "ticker": r["ticker"],
                "name": r["name"],
                "industry": r["industry"],
                "market_cap_bucket": r["market_cap_bucket"],
            }
            for r in picked
        ],
    }
