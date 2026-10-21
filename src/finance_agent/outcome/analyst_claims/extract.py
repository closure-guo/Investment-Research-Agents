"""事件式卖方主张生成（add-analyst-ex-post-verification）。

口径（docs/evals/metrics.md 登记）：只记录**变动**，不记录「当前评级是什么」。
评级方向由序数比较确定性推导，不依赖巨潮 ``rating_change`` 文本
（实测该字段只有「维持/未知」）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from finance_agent.data import analyst_claims_store as claims_store
from finance_agent.data import research_reports_store as rr_store

_RATING_ORDINAL = {
    "买入": 5,
    "强烈推荐": 5,
    "推荐": 5,
    "优于大市": 5,
    "增持": 4,
    "谨慎增持": 4,
    "跑赢行业": 4,
    "中性": 3,
    "持有": 3,
    "同步大市": 3,
    "减持": 2,
    "弱于大市": 2,
    "卖出": 1,
}

RATING_HORIZON_DAYS = 20
EPS_HORIZON_DAYS = 60
TARGET_HORIZON_DAYS = 60


def _sha1(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def rating_ordinal(value: str | None) -> int | None:
    return _RATING_ORDINAL.get(str(value or "").strip())


def classify_rating(current: str | None, previous: str | None, is_first: bool | None) -> str | None:
    """评级事件类型；无法判断（评级词未识别）返回 None，不生成主张。"""
    cur_ord = rating_ordinal(current)
    if cur_ord is None:
        return None
    prev_text = str(previous or "").strip()
    if is_first is True or not prev_text:
        return "rating_initiation"
    prev_ord = rating_ordinal(prev_text)
    if prev_ord is None:
        return None
    if cur_ord > prev_ord:
        return "rating_upgrade"
    if cur_ord < prev_ord:
        return "rating_downgrade"
    return "rating_maintain"


def _source_first(value: str | None) -> str:
    text = str(value or "").strip()
    return text.split(",")[0] if text else "unknown"


def _industry_map(conn) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT code, industry FROM research_reports
        WHERE industry IS NOT NULL AND industry != ''
        ORDER BY publish_date ASC
        """
    ).fetchall()
    return {str(r["code"]): str(r["industry"]) for r in rows}


def extract_rating_claims(conn, industry: dict[str, str]) -> list[dict]:
    rows = conn.execute(
        """
        SELECT event_key, code, org, analyst, publish_date, rating, prev_rating,
               is_first_rating, source
        FROM rating_changes
        ORDER BY publish_date ASC, code ASC, org ASC
        """
    ).fetchall()
    claims: list[dict] = []
    for row in rows:
        claim_type = classify_rating(
            row["rating"], row["prev_rating"], bool(row["is_first_rating"])
        )
        if claim_type is None:
            continue
        event_key = row["event_key"] or _sha1(
            row["code"], row["org"], row["publish_date"], row["rating"], row["prev_rating"]
        )
        claims.append(
            {
                "claim_key": f"{event_key}:rating",
                "claim_type": claim_type,
                "code": str(row["code"]),
                "org": str(row["org"]),
                "analyst": row["analyst"],
                "published_at": str(row["publish_date"]),
                "rating": row["rating"],
                "prev_rating": row["prev_rating"],
                "target_price": None,
                "prev_target_price": None,
                "eps_year": None,
                "eps_value": None,
                "prev_eps_value": None,
                "horizon_days": RATING_HORIZON_DAYS,
                "industry": industry.get(str(row["code"])),
                "region": "domestic",
                "depth_class": None,
                "source": _source_first(row["source"]),
            }
        )
    return claims


def _parse_eps(value: Any) -> dict[int, float]:
    if isinstance(value, dict):
        data = value
    else:
        try:
            data = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
    out: dict[int, float] = {}
    for year, number in (data or {}).items():
        try:
            year_int = int(str(year))
            out[year_int] = float(number)
        except (TypeError, ValueError):
            continue
    return out


def extract_eps_claims(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT report_key, code, org, analyst, publish_date, rating, industry,
               depth_class, eps_forecasts, source
        FROM research_reports
        WHERE report_type='stock'
          AND eps_forecasts IS NOT NULL
          AND code IS NOT NULL
        ORDER BY code ASC, org ASC, publish_date ASC, report_key ASC
        """
    ).fetchall()
    # (code, org, year) → 上一行（日期必须不同才比较）
    last: dict[tuple[str, str, int], dict] = {}
    claims: list[dict] = []
    for row in rows:
        eps = _parse_eps(row["eps_forecasts"])
        for year, value in eps.items():
            key = (str(row["code"]), str(row["org"]), year)
            prev = last.get(key)
            if prev is not None and prev["publish_date"] != str(row["publish_date"]):
                prev_value = prev["eps_value"]
                if value > prev_value:
                    direction = "up"
                elif value < prev_value:
                    direction = "down"
                else:
                    direction = None
                if direction:
                    claim_type = f"eps_revision_{direction}"
                    claims.append(
                        {
                            "claim_key": _sha1(
                                row["code"], row["org"], row["publish_date"], year, direction
                            ),
                            "claim_type": claim_type,
                            "code": str(row["code"]),
                            "org": str(row["org"]),
                            "analyst": row["analyst"],
                            "published_at": str(row["publish_date"]),
                            "rating": row["rating"],
                            "prev_rating": None,
                            "target_price": None,
                            "prev_target_price": None,
                            "eps_year": year,
                            "eps_value": value,
                            "prev_eps_value": prev_value,
                            "horizon_days": EPS_HORIZON_DAYS,
                            "industry": row["industry"],
                            "region": "domestic",
                            "depth_class": row["depth_class"],
                            "source": _source_first(row["source"]),
                        }
                    )
            last[key] = {
                "publish_date": str(row["publish_date"]),
                "eps_value": value,
            }
    return claims


def _target_value(low: Any, high: Any) -> float | None:
    try:
        low_v = float(low) if low is not None and str(low).strip() != "" else None
        high_v = float(high) if high is not None and str(high).strip() != "" else None
    except (TypeError, ValueError):
        return None
    if low_v is None and high_v is None:
        return None
    if low_v is None:
        return high_v
    if high_v is None:
        return low_v
    return (low_v + high_v) / 2.0


def extract_target_claims(conn, industry: dict[str, str]) -> list[dict]:
    rows = conn.execute(
        """
        SELECT event_key, code, org, analyst, publish_date, rating,
               target_low, target_high, source
        FROM rating_changes
        ORDER BY code ASC, org ASC, publish_date ASC, event_key ASC
        """
    ).fetchall()
    last_target: dict[tuple[str, str], float] = {}
    claims: list[dict] = []
    for row in rows:
        group = (str(row["code"]), str(row["org"]))
        target = _target_value(row["target_low"], row["target_high"])
        if target is None:
            continue
        prev_target = last_target.get(group)
        if prev_target is not None and abs(target - prev_target) > 1e-9:
            direction = "up" if target > prev_target else "down"
            claims.append(
                {
                    "claim_key": _sha1(
                        row["code"], row["org"], row["publish_date"], target, prev_target
                    ),
                    "claim_type": f"target_price_{direction}",
                    "code": group[0],
                    "org": group[1],
                    "analyst": row["analyst"],
                    "published_at": str(row["publish_date"]),
                    "rating": row["rating"],
                    "prev_rating": None,
                    "target_price": target,
                    "prev_target_price": prev_target,
                    "eps_year": None,
                    "eps_value": None,
                    "prev_eps_value": None,
                    "horizon_days": TARGET_HORIZON_DAYS,
                    "industry": industry.get(group[0]),
                    "region": "domestic",
                    "depth_class": None,
                    "source": _source_first(row["source"]),
                }
            )
        last_target[group] = target
    return claims


def generate_claims(db_path: str | None = None) -> dict[str, int]:
    """生成三类事件主张并幂等入库；返回各类生成条数。"""
    rr_store.init_db(db_path)
    claims_store.init_db(db_path)
    conn = claims_store._connect(db_path)
    try:
        industry = _industry_map(conn)
        rating_claims = extract_rating_claims(conn, industry)
        eps_claims = extract_eps_claims(conn)
        target_claims = extract_target_claims(conn, industry)
    finally:
        conn.close()
    rows = rating_claims + eps_claims + target_claims
    claims_store.upsert_claims(rows, db_path=db_path)
    return {
        "rating": len(rating_claims),
        "eps": len(eps_claims),
        "target": len(target_claims),
        "total": len(rows),
    }
