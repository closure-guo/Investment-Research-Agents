"""外资行二手快讯采集与事件抽取（add-foreign-broker-sentiment）。

合规边界：只保存标题、链接、短摘要（≤500 字）与抽取字段，MUST NOT 抓取或存储
原文全文；原文仍只经人工投喂进入。v1 只读观测，不进入任何分析师上下文或决策路径。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

from finance_agent.data import research_reports_store as rr_store

logger = logging.getLogger("finance_agent.data.foreign_broker_news")

JOB_NAME = "foreign_broker_news_daily"
DEFAULT_BROKERS = (
    "UBS",
    "瑞银",
    "摩根士丹利",
    "大摩",
    "摩根大通",
    "小摩",
    "高盛",
    "花旗",
    "野村",
    "汇丰",
    "巴克莱",
    "德意志银行",
    "德银",
    "美银",
)
DEFAULT_CHINA_KEYWORDS = (
    "中国",
    "A股",
    "港股",
    "中国股市",
    "中国股票",
    "China",
    "Chinese",
    "A-share",
    "Hong Kong",
)
_ACTIONS = {
    "target_up",
    "target_down",
    "rating_up",
    "rating_down",
    "positioning",
    "sentiment",
    "other",
}
_TARGET_TYPES = {"market", "sector", "stock", "unknown"}
_DIRECTIONS = {"positive", "negative", "neutral", "unknown"}
_CONFIDENCE = {"high", "medium", "low"}


def _feeds() -> list[str]:
    raw = os.getenv("FOREIGN_NEWS_FEEDS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _broker_keywords() -> tuple[str, ...]:
    raw = os.getenv("FOREIGN_NEWS_KEYWORDS", "")
    if raw.strip():
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    return DEFAULT_BROKERS


def _http_get(url: str) -> str:
    import requests

    resp = requests.get(
        url,
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (compatible; finance-agent/1.0)"},
    )
    resp.raise_for_status()
    return resp.text


def _strip_html(text: str) -> str:
    import html

    return html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def _truncate(text: str, limit: int = 500) -> str:
    return str(text or "")[:limit]


def _normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return None
    return None


def _child_text(item: ElementTree.Element, names: set[str]) -> str:
    for child in item.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _child_link(item: ElementTree.Element) -> str:
    for child in item.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
            if child.text and child.text.strip():
                return child.text.strip()
    return ""


def parse_feed(text: str, source: str = "") -> list[dict]:
    """RSS/Atom/JSON → 统一条目；解析失败返回空列表（由调用方记降级）。"""
    stripped = str(text or "").lstrip()
    if not stripped:
        return []
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return [
            _normalize_item(
                {
                    "title": item.get("title"),
                    "url": item.get("url") or item.get("link"),
                    "published_at": item.get("published_at")
                    or item.get("pubDate")
                    or item.get("published"),
                    "summary": item.get("summary") or item.get("description"),
                },
                source,
            )
            for item in items
            if isinstance(item, dict)
        ]
    try:
        root = ElementTree.fromstring(stripped)  # noqa: S314 - 源为用户配置的 feed
    except ElementTree.ParseError:
        return []
    items = list(root.iter("item")) + [
        element for element in root.iter() if element.tag.rsplit("}", 1)[-1].lower() == "entry"
    ]
    entries: list[dict] = []
    for item in items:
        entries.append(
            _normalize_item(
                {
                    "title": _child_text(item, {"title"}),
                    "url": _child_link(item),
                    "published_at": _child_text(item, {"pubdate", "published", "updated"}),
                    "summary": _child_text(item, {"description", "summary", "content"}),
                },
                source,
            )
        )
    return entries


def _normalize_item(raw: dict, source: str) -> dict:
    return {
        "title": _strip_html(raw.get("title") or ""),
        "url": str(raw.get("url") or "").strip() or None,
        "source": source or (urlparse(str(raw.get("url") or "")).netloc or None),
        "published_at": _normalize_date(raw.get("published_at")),
        "summary": _truncate(_strip_html(raw.get("summary") or ""), 500),
    }


def matches_keywords(item: dict) -> bool:
    haystack = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
    has_broker = any(keyword.lower() in haystack for keyword in _broker_keywords())
    has_china = any(keyword.lower() in haystack for keyword in DEFAULT_CHINA_KEYWORDS)
    return has_broker and has_china


def event_key(item: dict) -> str:
    raw = "|".join(
        (
            str(item.get("title") or ""),
            str(item.get("url") or ""),
            str(item.get("published_at") or ""),
        )
    )
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def _coerce_extraction(payload: dict, item: dict) -> dict:
    def pick(value: Any, allowed: set[str], default: str) -> str:
        text = str(value or "").strip().lower()
        return text if text in allowed else default

    published = _normalize_date(payload.get("published_at")) or item.get("published_at")
    return {
        "broker": str(payload.get("broker") or "").strip() or None,
        "published_at": published,
        "action": pick(payload.get("action"), _ACTIONS, "other"),
        "target_type": pick(payload.get("target_type"), _TARGET_TYPES, "unknown"),
        "target": str(payload.get("target") or "").strip() or None,
        "direction": pick(payload.get("direction"), _DIRECTIONS, "unknown"),
        "summary": _truncate(str(payload.get("summary") or "").strip(), 500) or None,
        "confidence": pick(payload.get("confidence"), _CONFIDENCE, "low"),
    }


def _default_extractor(item: dict) -> tuple[dict, dict]:
    from finance_agent.llm.contracts import extract_json
    from finance_agent.llm.gateway import complete_text
    from finance_agent.prompts.loader import load_prompt_with_meta

    info = load_prompt_with_meta("foreign_broker_event")
    prompt = (
        info.template.replace("{{title}}", item.get("title") or "")
        .replace("{{summary}}", item.get("summary") or "")
        .replace("{{source}}", item.get("source") or "")
    )
    raw_text, metadata = complete_text(
        [{"role": "user", "content": prompt}],
        purpose="foreign_broker_event",
        max_tokens=1200,
        trace={"name": "foreign_broker_event", "metadata": {"url": item.get("url")}},
    )
    payload = extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("抽取响应不是 JSON 对象")
    return _coerce_extraction(payload, item), metadata


def upsert_event(
    row: dict,
    *,
    db_path: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> None:
    rr_store.init_db(db_path)
    conn = rr_store._connect(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    try:
        conn.execute(
            """
            INSERT INTO foreign_broker_events (
              event_key, title, url, source, published_at, fetched_at, broker, action,
              target_type, target, direction, summary, confidence, extraction_status,
              model, prompt_version, usage_policy, region, event_score, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_key) DO UPDATE SET
              broker=excluded.broker,
              action=excluded.action,
              target_type=excluded.target_type,
              target=excluded.target,
              direction=excluded.direction,
              summary=excluded.summary,
              confidence=excluded.confidence,
              extraction_status=excluded.extraction_status,
              model=excluded.model,
              prompt_version=excluded.prompt_version,
              event_score=excluded.event_score,
              updated_at=excluded.updated_at
            """,
            (
                row["event_key"],
                row["title"],
                row.get("url"),
                row.get("source"),
                row.get("published_at"),
                now,
                row.get("broker"),
                row.get("action"),
                row.get("target_type"),
                row.get("target"),
                row.get("direction"),
                row.get("summary"),
                row.get("confidence"),
                row.get("extraction_status") or "ok",
                model,
                prompt_version,
                "personal_research",
                "foreign",
                row.get("event_score"),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_event(event_key_value: str, *, db_path: str | None = None) -> dict | None:
    rr_store.init_db(db_path)
    conn = rr_store._connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM foreign_broker_events WHERE event_key=?", (event_key_value,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_foreign_sentiment(
    *,
    broker: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    direction: str | None = None,
    start: str | None = None,
    end: str | None = None,
    db_path: str | None = None,
) -> dict:
    rr_store.init_db(db_path)
    sql = "SELECT * FROM foreign_broker_events WHERE 1=1"
    params: list[Any] = []
    for column, value in (
        ("broker", broker),
        ("action", action),
        ("target_type", target_type),
        ("direction", direction),
    ):
        if value:
            sql += f" AND {column}=?"  # noqa: S608
            params.append(value)
    if start:
        sql += " AND published_at >= ?"
        params.append(start)
    if end:
        sql += " AND published_at <= ?"
        params.append(end)
    sql += " ORDER BY published_at DESC, updated_at DESC LIMIT 500"
    conn = rr_store._connect(db_path)
    try:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    counts = {"total": len(rows), "by_action": {}, "by_direction": {}, "by_broker": {}}
    for row in rows:
        for key, bucket in (
            ("action", counts["by_action"]),
            ("direction", counts["by_direction"]),
            ("broker", counts["by_broker"]),
        ):
            value = str(row.get(key) or "unknown")
            bucket[value] = bucket.get(value, 0) + 1
    return {"status": "ok" if rows else "empty", "rows": rows, "counts": counts}


def run_foreign_broker_news_daily(
    *,
    feeds: list[str] | None = None,
    fetcher: Callable[[str], str] | None = None,
    extractor: Callable[[dict], tuple[dict, dict]] | None = None,
    db_path: str | None = None,
    as_of: str | None = None,
) -> dict:
    """抓取→过滤→抽取→落库→结算到期市场事件→外资记分卡。

    未配置源为空态，但仍结算存量事件；单源失败降级。
    """
    from finance_agent.outcome.foreign_broker_scoring import (
        build_foreign_scorecard,
        event_score,
        persist_foreign_scorecard,
        settle_foreign_events,
    )

    rr_store.init_db(db_path)
    sources = feeds if feeds is not None else _feeds()
    if not sources:
        settlement = settle_foreign_events(as_of=as_of, db_path=db_path)
        snapshot_date = as_of or date.today().isoformat()
        score_rows = build_foreign_scorecard(snapshot_date, db_path=db_path)
        persist_foreign_scorecard(snapshot_date, score_rows, db_path=db_path)
        detail = json.dumps(
            {
                "feeds": 0,
                "settled": settlement["settled"],
                "unresolved": settlement["unresolved"],
                "pending": settlement["pending"],
                "scorecard_rows": len(score_rows),
            },
            ensure_ascii=False,
        )
        rr_store.record_run(JOB_NAME, None, None, "empty", 0, detail=detail, db_path=db_path)
        return {
            "status": "empty",
            "fetched": 0,
            "matched": 0,
            "ingested": 0,
            "errors": [],
            **settlement,
            "scorecard_rows": len(score_rows),
        }

    fetch = fetcher or _http_get
    extract = extractor or _default_extractor
    fetched = matched = ingested = skipped_existing = extraction_failed = 0
    errors: list[str] = []
    for index, url in enumerate(sources):
        if index and fetcher is None:
            time.sleep(1.0)  # 串行限流：生产抓取间隔 ≥1s；测试注入 fetcher 时不等待
        try:
            text = fetch(url)
            items = parse_feed(text, source=urlparse(url).netloc)
        except Exception as exc:  # noqa: BLE001 - 单源失败不影响其他源
            errors.append(f"{url}: {exc}")
            logger.warning("外资快讯源失败 %s: %s", url, exc)
            continue
        for item in items:
            fetched += 1
            if not item.get("title") or not matches_keywords(item):
                continue
            matched += 1
            key = event_key(item)
            if get_event(key, db_path=db_path):
                skipped_existing += 1
                continue
            row = {
                "event_key": key,
                "title": item["title"],
                "url": item.get("url"),
                "source": item.get("source"),
                "published_at": item.get("published_at"),
                "extraction_status": "ok",
            }
            model = prompt_version = None
            try:
                extracted, metadata = extract(item)
                row.update(extracted)
                model = str(metadata.get("model") or "") or None
                prompt_version = str(metadata.get("prompt_version") or "") or None
            except Exception as exc:  # noqa: BLE001 - 抽取失败保留原文
                row["extraction_status"] = "failed"
                row["confidence"] = "low"
                extraction_failed += 1
                errors.append(f"{item.get('title')}: 抽取失败 {exc}")
            row["event_score"] = event_score(
                row.get("action"), row.get("direction"), row.get("confidence")
            )
            upsert_event(row, db_path=db_path, model=model, prompt_version=prompt_version)
            ingested += 1

    settlement = settle_foreign_events(as_of=as_of, db_path=db_path)
    snapshot_date = as_of or date.today().isoformat()
    score_rows = build_foreign_scorecard(snapshot_date, db_path=db_path)
    persist_foreign_scorecard(snapshot_date, score_rows, db_path=db_path)
    if errors and ingested == 0 and fetched == 0:
        status = "failed"
    elif errors or extraction_failed:
        status = "degraded"
    else:
        status = "ok"
    detail = {
        "feeds": len(sources),
        "fetched": fetched,
        "matched": matched,
        "skipped_existing": skipped_existing,
        "extraction_failed": extraction_failed,
        "settled": settlement["settled"],
        "unresolved": settlement["unresolved"],
        "pending": settlement["pending"],
        "scorecard_rows": len(score_rows),
        "errors": errors,
    }
    rr_store.record_run(
        JOB_NAME,
        None,
        None,
        status,
        ingested,
        detail=json.dumps(detail, ensure_ascii=False),
        db_path=db_path,
    )
    return {
        "status": status,
        "fetched": fetched,
        "matched": matched,
        "ingested": ingested,
        "skipped_existing": skipped_existing,
        "extraction_failed": extraction_failed,
        **settlement,
        "scorecard_rows": len(score_rows),
        "errors": errors,
    }
