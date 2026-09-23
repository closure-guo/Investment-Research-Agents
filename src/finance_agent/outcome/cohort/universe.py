"""cohort 标的池登记与可复现抽样(delta add-forward-paper-trading-cohort)。

设计：**登记文件是唯一真相**——抽样与富化(行业/市值)在构建期一次性完成并落
`data/cohort/universe-v1.json`，运行时(runner)只 `load_universe` 读文件，
不再联网抽样，保证同 `universe_version` 内标的池逐字不变(纵向可比)。

**分层口径(Δ3T2 审查 I1 修订)**：`market_cap_bucket` 配额优先 + 桶内行业去重。
沪深300 的 cninfo 行业中类近乎逐股唯一，若按 `(industry, bucket)` 联合分层会退化为
「行业优先」、市值档零约束；故改为：

1. 按总市值三分位切 `large` / `mid` / `small`；
2. 名额按池内三档占比分配（最大余数法，平局按 `BUCKETS` 顺序）；
3. 桶内「行业去重优先 + rng 抖动」抽满名额（去重耗尽则回填，名额不得缩水）。

`industry` 字段完整保留作审计维度。

**可审计性(审查 I2/I3)**：登记文件额外落 `pool_size` / `cut_points` / 逐成分
`market_cap` / `sources`（实际生效的数据源）/ `excluded`（缺值剔除计数）/
`unknown_industry`，使抽样结果可离线复核。缺 `market_cap` 的标的**剔除出池**
（绝不置 0、绝不判 small）；行业缺失记 `未知` 并计数披露。

**版本期内换池被拒**：`write_universe` 校验「文件名版本 = 内容 version = 既有文件
version」，不一致直接拒绝（`force` 亦然）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("version", "effective_date", "seed", "strata", "constituents")
CONSTITUENT_KEYS = ("ticker", "name", "industry", "market_cap_bucket")
META_KEYS = ("pool_size", "cut_points", "excluded", "unknown_industry", "sources")

STRATA_BY = "market_cap(quota)+industry(dedup)"
BUCKETS = ("large", "mid", "small")
SOURCE_DIMENSIONS = ("constituents", "industry", "market_cap")
UNKNOWN_SOURCE = "unknown"
UNKNOWN_INDUSTRY = "未知"
# 市值分位切点：< 1/3 → small；< 2/3 → mid；其余 large
_QUANTILE_LOW = 1 / 3
_QUANTILE_HIGH = 2 / 3
_FILE_PREFIX = "universe-"


@dataclass(frozen=True)
class Constituent:
    ticker: str
    name: str
    industry: str
    market_cap_bucket: str  # "large" / "mid" / "small"（按总市值分位）
    market_cap: float | None = None  # 审计用：入池时的总市值（亿元）


@dataclass(frozen=True)
class Universe:
    version: str
    effective_date: str
    seed: int
    strata: dict[str, Any]  # {"by": ..., "n": 10, "quota": {...}}
    constituents: tuple[Constituent, ...]
    meta: dict[str, Any] = field(default_factory=dict)  # pool_size/cut_points/sources/...


def _require_keys(payload: dict[str, Any], keys: tuple[str, ...], what: str) -> None:
    for key in keys:
        if key not in payload:
            raise ValueError(f"{what}缺少必需字段: {key}")


def load_universe(path: str | Path) -> Universe:
    """读取并校验 universe 登记文件；缺任一必需键抛 ValueError（消息含字段名）。

    审计字段（`META_KEYS`）为可选，存在时收进 `Universe.meta`。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("universe 登记文件根节点必须是 JSON 对象")
    _require_keys(raw, REQUIRED_KEYS, "universe 登记文件")

    constituents: list[Constituent] = []
    for item in raw["constituents"]:
        if not isinstance(item, dict):
            raise ValueError("universe 成分必须是 JSON 对象")
        _require_keys(item, CONSTITUENT_KEYS, "universe 成分")
        cap = item.get("market_cap")
        constituents.append(
            Constituent(
                ticker=str(item["ticker"]),
                name=str(item["name"]),
                industry=str(item["industry"]),
                market_cap_bucket=str(item["market_cap_bucket"]),
                market_cap=float(cap) if isinstance(cap, (int, float)) else None,
            )
        )
    return Universe(
        version=str(raw["version"]),
        effective_date=str(raw["effective_date"]),
        seed=int(raw["seed"]),
        strata=dict(raw["strata"]),
        constituents=tuple(constituents),
        meta={k: raw[k] for k in META_KEYS if k in raw},
    )


def _version_from_filename(path: Path) -> str | None:
    """`universe-v1.json` → `v1`；非约定文件名返回 None（不做文件名校验）。"""
    stem = path.stem
    if stem.startswith(_FILE_PREFIX):
        return stem[len(_FILE_PREFIX) :]
    return None


def write_universe(data: dict[str, Any], path: str | Path, *, force: bool = False) -> Path:
    """落登记文件。

    校验（`force` 亦然）：文件名版本 = 内容 version = 既有文件 version。
    默认拒绝覆盖既有文件（版本生效期内标的池不得变更）；`force=True` 仅允许
    **同版本重生成**，换池必须另起 version。
    """
    out = Path(path)
    _require_keys(data, REQUIRED_KEYS, "universe 登记数据")
    version = str(data["version"])

    expected = _version_from_filename(out)
    if expected is not None and expected != version:
        raise ValueError(
            f"文件名版本 {expected} 与内容 version {version} 不一致（登记文件不得混用版本号）"
        )
    if out.exists():
        existing = str(json.loads(out.read_text(encoding="utf-8")).get("version"))
        if existing != version:
            raise ValueError(
                f"既有登记文件 version={existing} 与写入 version={version} 不一致"
                "（换池须另起版本号）"
            )
        if not force:
            raise FileExistsError(
                f"universe 登记文件已存在，版本生效期内不得改写: {out}"
                "（换池请用新 version，或显式 force=True 重生成同版本）"
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _market_cap_bucket(cap: float, low: float, high: float) -> str:
    if cap <= low:
        return "small"
    if cap <= high:
        return "mid"
    return "large"


def allocate_quota(bucket_counts: dict[str, int], n: int) -> dict[str, int]:
    """按 `market_cap_bucket` 池内占比分配 n 个名额（最大余数法）。

    平局与余数补位按 `BUCKETS` 顺序（large → mid → small），保证与 seed 无关的确定性。
    """
    total = sum(bucket_counts.values())
    if n > total:
        raise ValueError(f"可选池仅 {total} 只，不足以无放回抽取 n={n} 只")
    quota = dict.fromkeys(BUCKETS, 0)
    remainders: list[tuple[float, int, str]] = []
    assigned = 0
    for idx, bucket in enumerate(BUCKETS):
        exact = n * bucket_counts.get(bucket, 0) / total
        base = int(exact)
        quota[bucket] = base
        assigned += base
        remainders.append((exact - base, idx, bucket))
    for _, _, bucket in sorted(remainders, key=lambda t: (-t[0], t[1])):
        if assigned >= n:
            break
        if bucket_counts.get(bucket, 0) <= quota[bucket]:
            continue  # 空桶/容量已满不领余数
        quota[bucket] += 1
        assigned += 1
    for bucket in BUCKETS:  # 兜底：余数被容量卡住时按序补齐
        while assigned < n and quota[bucket] < bucket_counts.get(bucket, 0):
            quota[bucket] += 1
            assigned += 1
    return quota


def _sample_bucket(rng: np.random.Generator, members: list[dict[str, Any]], quota: int):
    """桶内抽样：行业去重优先 + rng 抖动；去重耗尽则回填（名额不缩水）。"""
    order = [int(i) for i in rng.permutation(len(members))]
    picked: list[int] = []
    used_industries: set[str] = set()
    for i in order:
        if len(picked) >= quota:
            break
        if members[i]["industry"] in used_industries:
            continue
        picked.append(i)
        used_industries.add(members[i]["industry"])
    chosen = set(picked)
    for i in order:
        if len(picked) >= quota:
            break
        if i in chosen:
            continue
        picked.append(i)
        chosen.add(i)
    return [members[i] for i in picked]


def _stratified_sample(
    rows: list[dict[str, Any]], n: int, seed: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """市值配额优先 + 桶内行业去重，无放回抽 n 只（返回抽样结果与各桶名额）。

    rng 消费顺序固定为「按 BUCKETS 顺序、仅对有配额的桶做一次 permutation」，
    故同 seed 同池 ⇒ 逐字相同的成分清单。
    """
    if n > len(rows):
        raise ValueError(f"可选池仅 {len(rows)} 只，不足以无放回抽取 n={n} 只")
    rng = np.random.default_rng(seed)
    buckets: dict[str, list[dict[str, Any]]] = {b: [] for b in BUCKETS}
    # 先按 ticker 归一化池内顺序 → 抽样只是「池内容集合 + seed」的函数，
    # 上游返回行序波动（成分接口排序变化）不会悄悄换掉成分清单。
    for row in sorted(rows, key=lambda r: r["ticker"]):
        buckets[row["market_cap_bucket"]].append(row)
    quota = allocate_quota({b: len(members) for b, members in buckets.items()}, n)

    picked: list[dict[str, Any]] = []
    for bucket in BUCKETS:
        if quota[bucket] <= 0:
            continue
        picked.extend(_sample_bucket(rng, buckets[bucket], quota[bucket]))
    return sorted(picked, key=lambda r: r["ticker"]), quota


def _collect_sources(client: Any) -> dict[str, list[str]]:
    """取客户端记录的实际生效数据源（缺记录时记 `unknown`），供登记文件审计。"""
    seen = getattr(client, "sources_seen", None)
    if not isinstance(seen, dict):
        return {dim: [UNKNOWN_SOURCE] for dim in SOURCE_DIMENSIONS}
    return {dim: sorted(seen.get(dim) or {UNKNOWN_SOURCE}) for dim in SOURCE_DIMENSIONS}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_universe(
    version: str,
    *,
    n: int = 10,
    seed: int = 42,
    client: Any | None = None,
) -> dict[str, Any]:
    """沪深300 成分 → 逐只富化(行业/市值,带缓存) → 市值配额分层抽样 n 只 → dict。

    富化对**整池**执行一次（分位切点需全池），结果连同池来源/切点写入登记文件；
    运行时不再抓取。缺 `market_cap` 的标的剔除出池并计数（不置 0、不判 small）。
    """
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()

    raw = client.fetch_index_constituents()
    if not raw:
        raise ValueError("指数成分列表为空，无法构建 universe（成分信源不可用）")

    industry_cache: dict[str, str] = {}
    cap_cache: dict[str, float | None] = {}
    rows: list[dict[str, Any]] = []
    excluded_missing_cap = 0
    unknown_industry = 0
    for item in raw:
        ticker = str(item["ticker"])
        if ticker not in industry_cache:
            info = client.fetch_industry(ticker) or {}
            industry_cache[ticker] = str(info.get("industry") or UNKNOWN_INDUSTRY)
        if ticker not in cap_cache:
            quote = client.fetch_stock_quote(ticker) or {}
            cap_cache[ticker] = _as_float(quote.get("market_cap"))

        industry = industry_cache[ticker]
        if industry == UNKNOWN_INDUSTRY:
            unknown_industry += 1
        cap = cap_cache[ticker]
        if cap is None:
            # I3：缺市值 → 剔除出池（绝不置 0 参与分位、绝不判 small）
            excluded_missing_cap += 1
            logger.warning("市值缺失，剔除出池（不参与分位与抽样）: %s", ticker)
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": str(item.get("name") or ""),
                "industry": industry,
                "market_cap": cap,
            }
        )

    if not rows:
        raise ValueError("剔除缺市值标的后池为空，无法抽样")

    caps = np.array([r["market_cap"] for r in rows], dtype=float)
    low = float(np.quantile(caps, _QUANTILE_LOW))
    high = float(np.quantile(caps, _QUANTILE_HIGH))
    for row in rows:
        row["market_cap_bucket"] = _market_cap_bucket(row["market_cap"], low, high)

    picked, quota = _stratified_sample(rows, n, seed)
    return {
        "version": version,
        "effective_date": date.today().isoformat(),
        "seed": seed,
        "strata": {"by": STRATA_BY, "n": n, "quota": quota},
        "pool_size": len(rows),
        "cut_points": {"low": low, "high": high},
        "excluded": {"missing_market_cap": excluded_missing_cap},
        "unknown_industry": unknown_industry,
        "sources": _collect_sources(client),
        "constituents": [
            {
                "ticker": r["ticker"],
                "name": r["name"],
                "industry": r["industry"],
                "market_cap_bucket": r["market_cap_bucket"],
                "market_cap": r["market_cap"],
            }
            for r in picked
        ],
    }
