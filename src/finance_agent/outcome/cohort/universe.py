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

**池快照与离线重建(审查收口)**：上游成分/行情随快照波动会使「同 seed 重跑复现同一
成分清单」不成立（首轮 `pool_size=298` vs 次轮 `296`）。故生成时把**富化后的整池**
（含缺值行）落旁车文件 `data/cohort/universe-<version>.pool.json`，登记文件记
`pool_ref`（文件名）与 `pool_hash`（池内容 sha256 前 12 位，对行序不敏感）。
`build_universe(..., pool=<快照>)` 即**不联网重建**：抽样只由池内容 + seed 决定。

离线重建的可复现保证覆盖 `constituents` / `cut_points` / `pool_size` / `excluded` /
`strata`；`sources` 与 `unknown_industry` 为**环境相关披露字段**（上游信源瞬时
失败指纹跨轮不同、不可由快照反演），不保证逐字一致。

**版本期内换池被拒**：`write_universe` 校验「文件名版本 = 内容 version = 既有文件
version」，不一致直接拒绝（`force` 亦然）。
"""

from __future__ import annotations

import hashlib
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
META_KEYS = (
    "pool_size",
    "cut_points",
    "excluded",
    "unknown_industry",
    "sources",
    "pool_ref",
    "pool_hash",
)
# build_universe 返回的瞬态键（整池行，供脚本落旁车快照）；不入登记文件
POOL_KEY = "pool"

STRATA_BY = "market_cap(quota)+industry(dedup)"
# force 覆盖时必须逐字段一致的核心字段（spec Req1「版本生效期内标的池 SHALL NOT 变更」）
FORCE_COMPARE_KEYS = ("constituents", "cut_points", "pool_size", "excluded", "strata")
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


def _assert_same_pool_content(payload: dict[str, Any], existing: dict[str, Any]) -> None:
    """force 覆盖前逐字段比对：核心字段任一不同 → 拒绝（换池须另起 version）。

    比对 ``FORCE_COMPARE_KEYS``（``constituents`` 含顺序 / ``cut_points`` /
    ``pool_size`` / ``excluded`` / ``strata``）。披露字段（``sources`` /
    ``unknown_industry``）与 ``effective_date`` 不参与——它们跨轮可变、不由池内容
    决定，放行「同内容重生成」的离线重建场景（spec Req1 Scenario 2 的合法旁路）。
    """
    for key in FORCE_COMPARE_KEYS:
        if payload.get(key) != existing.get(key):
            raise ValueError(
                f"force 覆盖被拒：核心字段 {key} 与既有登记文件不同"
                "（版本生效期内标的池不得变更；换池请另起新 version）"
            )


def write_universe(data: dict[str, Any], path: str | Path, *, force: bool = False) -> Path:
    """落登记文件。

    校验（`force` 亦然）：文件名版本 = 内容 version = 既有文件 version。
    默认拒绝覆盖既有文件（版本生效期内标的池不得变更）；`force=True` **仅允许
    同内容重生成**——逐字段比对 ``FORCE_COMPARE_KEYS``，任一不同即抛 ``ValueError``
    （换池必须另起 version）。离线重建（同 seed + 同池快照）核心字段逐字一致，
    属合法覆盖场景。

    防御性剔除瞬态键 `POOL_KEY`（整池行只落旁车快照 `pool_ref`，不写进登记文件）。
    """
    out = Path(path)
    payload = {k: v for k, v in data.items() if k != POOL_KEY}
    _require_keys(payload, REQUIRED_KEYS, "universe 登记数据")
    version = str(payload["version"])

    expected = _version_from_filename(out)
    if expected is not None and expected != version:
        raise ValueError(
            f"文件名版本 {expected} 与内容 version {version} 不一致（登记文件不得混用版本号）"
        )
    if out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))
        if str(existing.get("version")) != version:
            raise ValueError(
                f"既有登记文件 version={existing.get('version')} 与写入 version={version} 不一致"
                "（换池须另起版本号）"
            )
        if not force:
            raise FileExistsError(
                f"universe 登记文件已存在，版本生效期内不得改写: {out}"
                "（换池请用新 version，或显式 force=True 重生成同版本）"
            )
        _assert_same_pool_content(payload, existing)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    pool: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """沪深300 成分 → 逐只富化(行业/市值,带缓存) → 市值配额分层抽样 n 只 → dict。

    富化对**整池**执行一次（分位切点需全池），结果连同池来源/切点写入登记文件；
    运行时不再抓取。缺 `market_cap` 的标的剔除出池并计数（不置 0、不判 small）。

    `pool` 传入时**不联网**：直接用给定池（`pool_snapshot` 落盘的旁车文件内容）抽样，
    使「按种子重跑脚本复现同一成分清单」不依赖上游快照波动。

    返回 dict 除登记文件内容外，另含**瞬态键 `pool`**（富化后的整池行，供脚本落旁车
    快照）；`write_universe` 会剔除该键，登记文件不会写入整池。
    """
    if pool is not None:
        # 离线重建：池内容即真相，不触网
        rows_all = _canonical_pool(pool)
        sources = {dim: ["snapshot"] for dim in SOURCE_DIMENSIONS}
    else:
        if client is None:
            from finance_agent.data.akshare_client import AKShareClient

            client = AKShareClient()
        rows_all = _enrich_pool(client)
        sources = _collect_sources(client)

    excluded_missing_cap = sum(1 for r in rows_all if r["market_cap"] is None)
    unknown_industry = sum(1 for r in rows_all if r["industry"] == UNKNOWN_INDUSTRY)
    # 复制：bucket 标注不得回写到 rows_all（旁车快照行只含 ticker/name/industry/market_cap）
    rows = [dict(r) for r in rows_all if r["market_cap"] is not None]
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
        "pool_ref": pool_ref(version),
        "pool_hash": pool_hash(rows_all),
        "pool_size": len(rows),
        "cut_points": {"low": low, "high": high},
        "excluded": {"missing_market_cap": excluded_missing_cap},
        "unknown_industry": unknown_industry,
        "sources": sources,
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
        POOL_KEY: rows_all,
    }


def pool_ref(version: str) -> str:
    """池旁车快照的约定文件名（与登记文件同目录）。"""
    return f"universe-{version}.pool.json"


def pool_hash(pool: list[dict[str, Any]]) -> str:
    """整池内容 hash（sha256 前 12 位）。对行序不敏感、对任一字段变化敏感。"""
    canonical = json.dumps(
        _canonical_pool(pool), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _canonical_pool(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """归一化池行（字段齐备 + 按 ticker 排序）→ 抽样与 hash 与入参行序无关。"""
    rows = [
        {
            "ticker": str(item["ticker"]),
            "name": str(item.get("name") or ""),
            "industry": str(item.get("industry") or UNKNOWN_INDUSTRY),
            "market_cap": _as_float(item.get("market_cap")),
        }
        for item in pool
    ]
    rows.sort(key=lambda r: str(r["ticker"]))
    return rows


def _enrich_pool(client: Any) -> list[dict[str, Any]]:
    """逐只富化整池（每 ticker 的行业/市值只取一次，缓存字典）。

    返回**全部**富化行（含 `market_cap is None` 的缺值行）——缺值行不入抽样池，
    但必须留在快照里，否则离线重建无法复现 `excluded` 计数。
    """
    raw = client.fetch_index_constituents()
    if not raw:
        raise ValueError("指数成分列表为空，无法构建 universe（成分信源不可用）")

    industry_cache: dict[str, str] = {}
    cap_cache: dict[str, float | None] = {}
    rows: list[dict[str, Any]] = []
    for item in raw:
        ticker = str(item["ticker"])
        if ticker not in industry_cache:
            info = client.fetch_industry(ticker) or {}
            industry_cache[ticker] = str(info.get("industry") or UNKNOWN_INDUSTRY)
        if ticker not in cap_cache:
            quote = client.fetch_stock_quote(ticker) or {}
            cap_cache[ticker] = _as_float(quote.get("market_cap"))

        industry = industry_cache[ticker]
        cap = cap_cache[ticker]
        if cap is None:
            logger.warning("市值缺失，剔除出池（不参与分位与抽样）: %s", ticker)
        rows.append(
            {
                "ticker": ticker,
                "name": str(item.get("name") or ""),
                "industry": industry,
                "market_cap": cap,
            }
        )
    return _canonical_pool(rows)
