"""机构/评级分桶记分卡与只读权重（add-analyst-ex-post-verification）。

所有数字由确定性代码计算，不调用 LLM；样本 n<30 标记 unreliable，
v1 不输出显著性结论、不把权重接入任何决策路径。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import mean, median

from finance_agent.data import analyst_claims_store as store

logger = logging.getLogger("finance_agent.outcome.analyst_claims.scorecard")

RELIABLE_MIN_SAMPLES = 30
DEFAULT_TIERS_PATH = Path(__file__).resolve().parents[4] / "config" / "analyst_tiers.json"
DEFAULT_SCORING_PATH = Path(__file__).resolve().parents[4] / "config" / "analyst_scoring.json"
_DEFAULT_DEPTH_WEIGHTS = {"deep": 1.0, "comment": 0.6, "unknown": 0.4}


def load_scoring_config(path: str | Path | None = None) -> dict:
    """深度/外资先验权重；文件缺失或损坏回退内置默认，不阻断 job。"""
    target = Path(path) if path else DEFAULT_SCORING_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"depth_weights": dict(_DEFAULT_DEPTH_WEIGHTS)}
    except Exception as exc:  # noqa: BLE001 - 配置损坏不得使日批失败
        logger.warning("打分权重配置读取失败（用内置默认）: %s", exc)
        return {"depth_weights": dict(_DEFAULT_DEPTH_WEIGHTS)}
    if not isinstance(payload, dict):
        return {"depth_weights": dict(_DEFAULT_DEPTH_WEIGHTS)}
    weights = payload.get("depth_weights")
    if not isinstance(weights, dict):
        payload["depth_weights"] = dict(_DEFAULT_DEPTH_WEIGHTS)
    else:
        merged = dict(_DEFAULT_DEPTH_WEIGHTS)
        for key, value in weights.items():
            try:
                merged[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        payload["depth_weights"] = merged
    return payload


def load_tiers(path: str | Path | None = None) -> dict[str, str]:
    """读取机构梯队先验；文件缺失/损坏回退空表（全部 unrated），不阻断 job。"""
    target = Path(path) if path else DEFAULT_TIERS_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - 配置损坏不得使日批失败
        logger.warning("机构梯队配置读取失败（按 unrated 处理）: %s", exc)
        return {}
    tiers = payload.get("tiers") if isinstance(payload, dict) else None
    if not isinstance(tiers, dict):
        logger.warning("机构梯队配置缺少 tiers 对象（按 unrated 处理）")
        return {}
    mapping: dict[str, str] = {}
    for tier, orgs in tiers.items():
        if not isinstance(orgs, list):
            continue
        for org in orgs:
            text = str(org or "").strip()
            if text:
                mapping[text] = str(tier)
    return mapping


def tier_for(org: str | None, tiers: dict[str, object]) -> str:
    """支持两种形态：扁平 ``{org: tier}`` 与原始配置 ``{tier: [org, ...]}``。"""
    name = str(org or "").strip()
    direct = tiers.get(name)
    if isinstance(direct, str):
        return direct
    for tier, orgs in tiers.items():
        if isinstance(orgs, list) and name in {str(item).strip() for item in orgs}:
            return str(tier)
    return "unrated"


def weight_for(mean_excess: float | None) -> float | None:
    """先验占位公式：clip(0.5 + mean_excess / 0.10, 0.5, 1.5)。仅只读输出。"""
    if mean_excess is None:
        return None
    value = 0.5 + float(mean_excess) / 0.10
    return max(0.5, min(1.5, value))


def _bucket_rows(rows: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        buckets[("org", str(row.get("org") or "unknown"))].append(row)
        buckets[("rating", str(row.get("rating") or "未知"))].append(row)
        buckets[("claim_type", str(row.get("claim_type") or "unknown"))].append(row)
        buckets[("industry", str(row.get("industry") or "unknown"))].append(row)
        buckets[("depth", str(row.get("depth_class") or "unknown"))].append(row)
    return buckets


def _stats(
    bucket_type: str,
    bucket_key: str,
    rows: list[dict],
    tiers: dict[str, str],
    depth_weights: dict[str, float],
) -> dict:
    excess_values = [float(r["excess_return"]) for r in rows if r.get("excess_return") is not None]
    n = len(excess_values)
    wins = sum(1 for value in excess_values if value > 0)
    mean_excess = mean(excess_values) if excess_values else None
    median_excess = median(excess_values) if excess_values else None
    weighted_mean_excess = None
    deep_n = None
    if bucket_type in ("org", "rating"):
        weighted_pairs = [
            (
                depth_weights.get(str(r.get("depth_class") or "unknown"), depth_weights["unknown"]),
                float(r["excess_return"]),
            )
            for r in rows
            if r.get("excess_return") is not None
        ]
        total_weight = sum(weight for weight, _ in weighted_pairs)
        if total_weight > 0:
            weighted_mean_excess = (
                sum(weight * value for weight, value in weighted_pairs) / total_weight
            )
        deep_n = sum(1 for r in rows if str(r.get("depth_class") or "") == "deep")
    return {
        "bucket_type": bucket_type,
        "bucket_key": bucket_key,
        "n": n,
        "wins": wins,
        "win_rate": (wins / n) if n else None,
        "mean_excess": mean_excess,
        "median_excess": median_excess,
        "reliable": n >= RELIABLE_MIN_SAMPLES,
        "weight": weight_for(mean_excess) if bucket_type == "org" else None,
        "tier": tier_for(bucket_key, tiers) if bucket_type == "org" else None,
        "region": "domestic",
        "score_family": "event_study",
        "weighted_mean_excess": weighted_mean_excess,
        "deep_n": deep_n,
    }


def build_scorecard(
    snapshot_date: str,
    *,
    db_path: str | Path | None = None,
    tiers: dict[str, str] | None = None,
    scoring_config: dict | None = None,
) -> list[dict]:
    resolved = store.list_resolved(db_path=db_path)
    tier_map = tiers if tiers is not None else load_tiers()
    config = scoring_config if scoring_config is not None else load_scoring_config()
    depth_weights = config.get("depth_weights") or _DEFAULT_DEPTH_WEIGHTS
    buckets = _bucket_rows(resolved)
    rows = [
        _stats(bucket_type, bucket_key, members, tier_map, depth_weights)
        for (bucket_type, bucket_key), members in sorted(buckets.items())
    ]
    return rows


def persist_scorecard(
    snapshot_date: str, rows: list[dict], *, db_path: str | Path | None = None
) -> int:
    return store.replace_scorecard(snapshot_date, rows, db_path=db_path)
