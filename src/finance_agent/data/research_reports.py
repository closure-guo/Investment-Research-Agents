"""研报数据面采集器（add-research-report-dataplane）。

免费主干（付费源仅备选，不在此模块）：
- 东财 reportapi 五类元数据：``/report/list``（个股 qType=0、行业 qType=1）与
  ``/report/jg``（策略 2、宏观 3、晨报 4），均支持全市场按发布日窗口拉取；
- 巨潮评级变动（akshare ``stock_rank_forecast_cninfo``，按日全市场）；
- 东财全市场盈利预测快照（akshare ``stock_profit_forecast_em``，一次约 2887 只）；
- 同花顺单股一致预期明细（akshare ``stock_profit_forecast_ths``，按需 + 1 天内存缓存）；
- 新浪研报列表（第二源，仅取各分类计数做交叉/降级记录）。

合规边界：只落元数据与链接字符串，MUST NOT 下载/解析/存储 PDF 或任何全文内容。

限流纪律：对东财/巨潮的请求串行、间隔 ≥1s + 随机抖动、复用会话、携带 UA/Referer
（公开工程的防封铁律 + 本机东财行情域曾 IP 级封禁的历史）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import time
import unicodedata
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
import requests

from finance_agent.data import research_reports_store as store
from finance_agent.data.akshare_client import _call_ak

logger = logging.getLogger(__name__)

EM_BASE = "https://reportapi.eastmoney.com/report/list"
EM_JG = "https://reportapi.eastmoney.com/report/jg"
SINA_LIST = "https://vip.stock.finance.sina.com.cn/q/go.php/vReport_List/kind/{cat}/index.phtml"

REPORT_TYPES = ("stock", "industry", "strategy", "macro", "morning")
Q_TYPES = {"stock": "0", "industry": "1", "strategy": "2", "macro": "3", "morning": "4"}
DETAIL_TEMPLATES = {
    "stock": "https://data.eastmoney.com/report/zw_stock.jshtml?encodeUrl={enc}",
    "industry": "https://data.eastmoney.com/report/zw_industry.jshtml?encodeUrl={enc}",
    "strategy": "https://data.eastmoney.com/report/zw_strategy.jshtml?encodeUrl={enc}",
    "macro": "https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl={enc}",
    "morning": "https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl={enc}",
}
PDF_TEMPLATE = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
SINA_CATEGORIES = {
    # 新浪研报频道实测仅四类（公司/行业/策略/宏观）；券商晨报无独立分类，
    # 晨报的交叉校验只依赖东财单源（2026-10-20 实测导航页）。
    "company": "company",
    "industry": "industry",
    "strategy": "strategy",
    "macro": "macro",
}
MAX_PAGES = 200
# 手工投喂目录（个人研究自用；env 可覆盖）
INBOX_DIR_DEFAULT = "data/research_report_inbox"
_YEAR_EPS = re.compile(r"^(\d{4})预测每股收益$")
_THS_CACHE: dict[str, tuple[float, dict]] = {}
_THS_TTL_S = 86_400
_SESSION: requests.Session | None = None


class EMRateLimiter:
    """东财/巨潮串行限流：上次请求后至少 ``min_interval`` 秒（含抖动）。"""

    def __init__(
        self,
        min_interval: float = 1.0,
        jitter: float = 0.2,
        *,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
    ) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            elapsed = self._clock() - self._last
            delay = self.min_interval + random.uniform(0, self.jitter) - elapsed  # noqa: S311
            if delay > 0:
                self._sleep(delay)
        self._last = self._clock()


_LIMITER = EMRateLimiter()


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
                ),
                "Referer": "https://data.eastmoney.com/report/",
            }
        )
    return _SESSION


def _em_get(url: str, params: dict, *, session: requests.Session | None = None) -> dict:
    _LIMITER.wait()
    resp = (session or _session()).get(url, params=params, timeout=25)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError(f"unexpected payload type: {type(payload).__name__}")
    return payload


def _num(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        number = float(value)
        return None if pd.isna(number) else number
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _date10(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def normalize_em_row(raw: dict, report_type: str, current_year: int) -> dict:
    """东财原始行 → 数据面行（五类共用；个股额外带 code/name/评级/预测）。"""
    encode_url = str(raw.get("encodeUrl") or "")
    info_code = str(raw.get("infoCode") or "")
    eps: dict[str, float] = {}
    pe: dict[str, float] = {}
    if report_type == "stock":
        for key, year, target in (
            ("predictThisYearEps", current_year, eps),
            ("predictNextYearEps", current_year + 1, eps),
            ("predictNextTwoYearEps", current_year + 2, eps),
        ):
            value = _num(raw.get(key))
            if value is not None:
                target[str(year)] = value
        for key, year in (
            ("predictThisYearPe", current_year),
            ("predictNextYearPe", current_year + 1),
            ("predictNextTwoYearPe", current_year + 2),
        ):
            value = _num(raw.get(key))
            if value is not None:
                pe[str(year)] = value
    return {
        "report_type": report_type,
        "title": str(raw.get("title") or "").strip(),
        "org": str(raw.get("orgSName") or "").strip(),
        "analyst": str(raw.get("researcher") or raw.get("author") or "").strip(),
        "publish_date": _date10(raw.get("publishDate")),
        "code": (str(raw.get("stockCode") or "").strip() or None)
        if report_type == "stock"
        else None,
        "name": (str(raw.get("stockName") or "").strip() or None)
        if report_type == "stock"
        else None,
        "industry": str(raw.get("industryName") or "").strip() or None,
        "rating": str(raw.get("emRatingName") or "").strip() or None
        if report_type == "stock"
        else None,
        "eps_forecasts": eps,
        "pe_forecasts": pe,
        "pdf_url": PDF_TEMPLATE.format(info_code=info_code) if info_code else None,
        "detail_url": DETAIL_TEMPLATES[report_type].format(
            enc=urllib.parse.quote(encode_url, safe="")
        )
        if encode_url
        else None,
        "depth_class": classify_depth(str(raw.get("title") or "")),
        "source": "eastmoney",
    }


def fetch_market_reports(
    report_type: str,
    begin: date,
    end: date,
    *,
    session: requests.Session | None = None,
    limiter: EMRateLimiter | None = None,
) -> list[dict]:
    """按发布日窗口拉取某类研报的全市场元数据（分页拉全）。"""
    if report_type not in Q_TYPES:
        raise ValueError(f"unknown report_type: {report_type}")
    url = EM_BASE if report_type in ("stock", "industry") else EM_JG
    qtype = Q_TYPES[report_type]
    base_params: dict[str, Any] = {
        "qType": qtype,
        "beginTime": begin.isoformat(),
        "endTime": end.isoformat(),
        "pageSize": "100",
        "pageNo": "1",
    }
    if report_type in ("stock", "industry"):
        base_params.update(
            {"industryCode": "*", "industry": "*", "rating": "*", "ratingChange": "*"}
        )
    first = _em_get(url, base_params, session=session)
    total_pages = int(first.get("TotalPage") or 0)
    current_year = int(first.get("currentYear") or datetime.now().year)
    rows = [normalize_em_row(item, report_type, current_year) for item in (first.get("data") or [])]
    for page in range(2, min(total_pages, MAX_PAGES) + 1):
        params = dict(base_params)
        params["pageNo"] = str(page)
        payload = _em_get(url, params, session=session)
        rows.extend(
            normalize_em_row(item, report_type, current_year)
            for item in (payload.get("data") or [])
        )
    return rows


def fetch_rating_changes(as_of: date) -> list[dict]:
    """巨潮全市场投资评级变动（按日）。"""
    df = _call_ak(ak.stock_rank_forecast_cninfo, date=as_of.strftime("%Y%m%d"))
    if df is None or df.empty:
        return []
    rows = []
    for _, item in df.iterrows():
        first_raw = str(item.get("是否首次评级") or "").strip()
        if first_raw == "是":
            is_first: bool | None = True
        elif first_raw in ("不是首次评级", "否"):
            is_first = False
        else:
            is_first = None
        rows.append(
            {
                "code": str(item.get("证券代码") or "").strip(),
                "name": str(item.get("证券简称") or "").strip(),
                "publish_date": _date10(item.get("发布日期")),
                "org": str(item.get("研究机构简称") or "").strip(),
                "analyst": str(item.get("研究员名称") or "").strip(),
                "rating": str(item.get("投资评级") or "").strip(),
                "is_first_rating": is_first,
                "rating_change": str(item.get("评级变化") or "").strip(),
                "prev_rating": str(item.get("前一次投资评级") or "").strip(),
                "target_low": _num(item.get("目标价格-下限")),
                "target_high": _num(item.get("目标价格-上限")),
                "source": "cninfo",
            }
        )
    return rows


def fetch_forecast_snapshot() -> list[dict]:
    """东财全市场盈利预测快照（研报数 + 五档评级分布 + 三年 EPS）。"""
    df = _call_ak(ak.stock_profit_forecast_em, symbol="")
    if df is None or df.empty:
        return []
    year_cols: list[tuple[int, str]] = []
    for column in df.columns:
        match = _YEAR_EPS.match(str(column))
        if match:
            year_cols.append((int(match.group(1)), str(column)))
    year_cols.sort()
    # 「当年起三年」：财务预测表仍保留上一年列（如 2026 年的 2025E），按当前年份起算；
    # 若表内尚无当年列（年初极端场景），退回全部列以免整表为空。
    current_year = date.today().year
    years = [item for item in year_cols if item[0] >= current_year] or year_cols
    years = years[:3]
    rows = []
    for _, item in df.iterrows():
        code = str(item.get("代码") or "").strip()
        if not code:
            continue
        row: dict[str, Any] = {
            "code": code,
            "name": str(item.get("名称") or "").strip() or None,
            "report_count": _int_or_none(item.get("研报数")),
            "rating_buy": _int_or_none(item.get("机构投资评级(近六个月)-买入")),
            "rating_add": _int_or_none(item.get("机构投资评级(近六个月)-增持")),
            "rating_neutral": _int_or_none(item.get("机构投资评级(近六个月)-中性")),
            "rating_reduce": _int_or_none(item.get("机构投资评级(近六个月)-减持")),
            "rating_sell": _int_or_none(item.get("机构投资评级(近六个月)-卖出")),
            "source": "eastmoney",
        }
        for index, (year, column) in enumerate(years, start=1):
            row[f"eps_y{index}"] = _num(item.get(column))
            row[f"eps_y{index}_year"] = year
        rows.append(row)
    return rows


def fetch_ths_detail(stock_code: str) -> dict:
    """同花顺单股一致预期明细（1 天内存缓存；失败返回空 dict）。"""
    cached = _THS_CACHE.get(stock_code)
    now = time.time()
    if cached and now - cached[0] < _THS_TTL_S:
        return cached[1]
    result: dict[str, Any] = {}
    try:
        summary = _call_ak(
            ak.stock_profit_forecast_ths, symbol=stock_code, indicator="预测年报每股收益"
        )
        if summary is not None and not summary.empty:
            result["summary"] = summary.to_dict(orient="records")
        detail = _call_ak(
            ak.stock_profit_forecast_ths, symbol=stock_code, indicator="业绩预测详表-机构"
        )
        if detail is not None and not detail.empty:
            result["institutions"] = detail.head(10).to_dict(orient="records")
    except Exception as exc:  # 明细是增强项，失败只降级
        logger.warning("同花顺一致预期明细失败 %s: %s", stock_code, exc)
        return {}
    _THS_CACHE[stock_code] = (now, result)
    return result


def fetch_sina_counts() -> dict[str, int]:
    """新浪研报列表各分类当日条目计数（第二源交叉/降级记录用，失败返回已得部分）。"""
    counts: dict[str, int] = {}
    session = _session()
    for name, cat in SINA_CATEGORIES.items():
        try:
            _LIMITER.wait()
            resp = session.get(SINA_LIST.format(cat=cat), timeout=20)
            resp.raise_for_status()
            counts[name] = resp.text.count("vReport_Show")
        except Exception as exc:
            logger.warning("新浪研报分类计数失败 %s: %s", name, exc)
    return counts


def infer_metadata_from_filename(stem: str) -> tuple[str | None, str | None]:
    """从投喂文件名推断机构与发布日期（慧博式命名：国信证券-标题-261019）。

    推断是尽力而为：解析不出就返回 (None, None)，MUST NOT 编造日期。
    """
    org = stem.split("-")[0].strip() if "-" in stem else None
    match = re.search(r"[-_](\d{6})$", stem)
    if match:
        digits = match.group(1)
        try:
            parsed = date(2000 + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
            return org, parsed.isoformat()
        except ValueError:
            return org, None
    match = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", stem)
    if match:
        try:
            parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return org, parsed.isoformat()
        except ValueError:
            return org, None
    return org, None


_DEPTH_DEEP_KEYWORDS = ("深度", "专题", "策略", "展望")
_DEPTH_COMMENT_KEYWORDS = ("点评", "跟踪", "快评", "更新")


def page_offsets(pages: list[str]) -> list[dict]:
    """每页在 ``"\\n".join(pages)`` 中的字符边界（左闭右开）。"""
    offsets: list[dict] = []
    cursor = 0
    for index, page in enumerate(pages, start=1):
        length = len(page)
        offsets.append({"page": index, "start": cursor, "end": cursor + length})
        cursor += length + 1
    return offsets


def classify_depth(title: str | None) -> str:
    """确定性深度分级：deep / comment / unknown。只落库，不用于丢弃或加权。"""
    text = str(title or "")
    if any(keyword in text for keyword in _DEPTH_DEEP_KEYWORDS):
        return "deep"
    if any(keyword in text for keyword in _DEPTH_COMMENT_KEYWORDS):
        return "comment"
    return "unknown"


def normalize_title(title: str | None) -> str:
    """跨源标题归一：NFKC + 去空白/标点 + casefold（保留中文实词）。"""
    text = unicodedata.normalize("NFKC", str(title or ""))
    return re.sub(r"[\W_]+", "", text).casefold()


def clean_doc_title(stem: str | None, org: str | None) -> str:
    """文件名 stem → 可匹配标题：去机构前缀与尾部日期/序号。"""
    text = str(stem or "")
    prefix = str(org or "").strip()
    if prefix and text.startswith(f"{prefix}-"):
        text = text[len(prefix) + 1 :]
    text = re.sub(r"[-_](?:\d{6}|\d{8}|20\d{6})(?:\s*\(\d+\))?$", "", text)
    text = re.sub(r"\s*\(\d+\)$", "", text)
    return text.strip() or str(stem or "")


def match_report_key(
    broker: str | None,
    publish_date: str | None,
    title: str | None,
    *,
    window_days: int = 1,
    db_path: str | Path | None = None,
) -> str | None:
    """按 (机构, 日期 ±1 天, 归一标题) 关联元数据；恰好 1 条命中才返回。"""
    if not broker or not publish_date or not title:
        return None
    rows = store.list_reports_for_match(
        str(broker), str(publish_date)[:10], window_days=window_days, db_path=db_path
    )
    target = normalize_title(title)
    hits = [row for row in rows if normalize_title(row.get("title")) == target]
    return hits[0]["report_key"] if len(hits) == 1 else None


def extract_pdf_text(path: str | Path) -> tuple[str, int, list[dict]]:
    """抽取 PDF 全文、页数与页偏移（pypdf；延迟导入避免非投喂路径加载解析器）。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    texts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(texts), len(reader.pages), page_offsets(texts)


def _document_key(data: bytes) -> str:
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


_INGEST_SUFFIXES = {".pdf", ".txt", ".md"}


def ingest_research_document(
    filename: str,
    data: bytes,
    *,
    db_path: str | None = None,
    extract: bool = True,
    overrides: dict | None = None,
    inbox_dir: str | Path | None = None,
    file_path: str | None = None,
) -> dict:
    """单文档直投入库（add-research-report-ingest）：与目录扫描共享同一套语义。

    按内容 sha1 幂等（重复返回 ``skipped``，不动首次入库时间）；``.pdf`` 走
    ``extract_pdf_text``（解析失败仍入库、正文空、``parse_error`` 披露，与
    ``ingest_inbox`` 降级语义一致），``.txt``/``.md`` 按 UTF-8 优先、GBK 兜底解码。
    ``file_path`` 显式给出时使用原路径（目录扫描路径），否则落盘到投喂目录
    （命名冲突追加短哈希后缀）。``overrides`` 的 org/publish_date/title 显式值
    优先于文件名推断。``extract=True`` 且正文非空时同步做 LLM 结构化抽取并
    回链 ``report_key``，抽取失败只写 ``extraction_error`` 不阻断。

    Returns:
        结构化回执：status(ingested|skipped)/doc_key/filename/char_count/
        page_count/org/publish_date/report_key/depth_class/parse_error/extraction_error。
    """
    name = Path(str(filename or "")).name or "untitled"
    suffix = Path(name).suffix.lower()
    if suffix not in _INGEST_SUFFIXES:
        raise ValueError(f"不支持的文件类型 {suffix!r}：仅支持 pdf/txt/md")
    key = _document_key(data)
    if store.document_exists(key, db_path=db_path):
        return {
            "status": "skipped",
            "doc_key": key,
            "filename": name,
            "char_count": 0,
            "page_count": 0,
            "org": None,
            "publish_date": None,
            "report_key": None,
            "depth_class": None,
            "parse_error": None,
            "extraction_error": None,
        }

    if file_path is None:
        target_dir = Path(
            inbox_dir
            if inbox_dir is not None
            else os.getenv("RESEARCH_REPORT_INBOX_DIR", INBOX_DIR_DEFAULT)
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if target.exists():
            target = target_dir / f"{target.stem}_{key[:8]}{target.suffix}"
        target.write_bytes(data)
        file_path = str(target.resolve())

    text, pages, offsets = "", 0, None
    parse_error: str | None = None
    if suffix == ".pdf":
        try:
            parsed = extract_pdf_text(file_path)
            if len(parsed) == 2:  # 兼容旧自定义解析器/monkeypatch
                text, pages = parsed
            else:
                text, pages, offsets = parsed
        except Exception as exc:  # 无文本层/加密/损坏：文档仍入库，偏移为 null
            parse_error = f"parse_failed: {exc}"
            logger.warning("直投解析降级 %s: %s", name, exc)
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            try:
                text = data.decode("gbk")
            except UnicodeDecodeError:
                raise ValueError(f"文本解码失败（utf-8/gbk 均失败）: {exc}") from exc
        pages = 1

    overrides = overrides or {}
    stem = Path(name).stem
    org, publish_date = infer_metadata_from_filename(stem)
    org = str(overrides.get("org") or "").strip() or org
    publish_date = str(overrides.get("publish_date") or "").strip() or publish_date
    title = str(overrides.get("title") or "").strip() or clean_doc_title(stem, org)
    depth = classify_depth(title)
    report_key = match_report_key(org, publish_date, title, db_path=db_path)
    created = store.upsert_document(
        {
            "doc_key": key,
            "filename": name,
            "file_path": file_path,
            "title": title,
            "org": org,
            "publish_date": publish_date,
            "page_count": pages,
            "char_count": len(text),
            "text": text,
            "page_offsets": offsets,
            "report_key": report_key,
            "depth_class": depth,
            "usage_policy": "personal_research",
            "source_type": "manual_feed",
        },
        db_path=db_path,
    )
    if not created:  # 并发竞态兜底：另一路已入库，等价幂等 skipped
        return {
            "status": "skipped",
            "doc_key": key,
            "filename": name,
            "char_count": 0,
            "page_count": 0,
            "org": None,
            "publish_date": None,
            "report_key": None,
            "depth_class": None,
            "parse_error": None,
            "extraction_error": None,
        }

    extraction_error: str | None = None
    if extract and text:
        try:
            extraction = extract_document(key, db_path=db_path)
            broker = extraction.get("broker") or org
            date_value = extraction.get("report_date") or publish_date
            linked = match_report_key(broker, date_value, title, db_path=db_path)
            if linked:
                report_key = linked
                store.update_document_metadata(
                    key,
                    page_offsets=offsets,
                    report_key=linked,
                    depth_class=depth,
                    db_path=db_path,
                )
        except Exception as exc:  # 抽取失败不影响入库
            extraction_error = f"{type(exc).__name__}: {exc}"
            logger.error("直投抽取失败 %s: %s", name, exc)

    return {
        "status": "ingested",
        "doc_key": key,
        "filename": name,
        "char_count": len(text),
        "page_count": pages,
        "org": org,
        "publish_date": publish_date,
        "report_key": report_key,
        "depth_class": depth,
        "parse_error": parse_error,
        "extraction_error": extraction_error,
    }


def ingest_inbox(
    inbox_dir: str | Path | None = None,
    *,
    db_path: str | None = None,
    extract: bool = True,
    force_reparse: bool = False,
) -> dict:
    """扫描投喂目录，把新 PDF 解析入库（文件内容哈希幂等，重复投喂 no-op）。

    个人研究自用：全文落 ``report_documents``（usage_policy=personal_research）；
    ``extract=True`` 时对新入库文档同步做 LLM 结构化抽取（失败只记错误，
    不阻断入库与其他文件）。``force_reparse=True`` 时只重算既有文档的页偏移/
    跨源关联/深度分级，不新增行、不调 LLM；原文件缺失保留原行并记 ``file_missing``。
    """
    resolved = (
        inbox_dir
        if inbox_dir is not None
        else os.getenv("RESEARCH_REPORT_INBOX_DIR", INBOX_DIR_DEFAULT)
    )
    directory = Path(resolved)
    store.init_db(db_path)
    scanned = ingested = skipped = reparsed = 0
    errors: list[str] = []

    def _parse_pdf(path: Path) -> tuple[str, int, list[dict] | None]:
        parsed = extract_pdf_text(path)
        if len(parsed) == 2:  # 兼容旧自定义解析器/monkeypatch
            text, pages = parsed
            return text, pages, None
        return parsed

    if force_reparse:
        for doc in store.list_documents(limit=1000, db_path=db_path):
            scanned += 1
            path = Path(str(doc.get("file_path") or ""))
            if not path.exists():
                errors.append(f"{doc.get('filename')}: file_missing")
                continue
            try:
                _, _, offsets = _parse_pdf(path)
                org = doc.get("org")
                publish_date = doc.get("publish_date")
                title = clean_doc_title(Path(str(doc.get("filename") or "")).stem, org)
                store.update_document_metadata(
                    str(doc["doc_key"]),
                    page_offsets=offsets,
                    report_key=match_report_key(org, publish_date, title, db_path=db_path),
                    depth_class=classify_depth(title),
                    db_path=db_path,
                )
                reparsed += 1
            except Exception as exc:
                logger.error("投喂重解析失败 %s: %s", doc.get("filename"), exc)
                errors.append(f"{doc.get('filename')}: {exc}")
    elif directory.exists():
        for path in sorted(directory.rglob("*.pdf")):
            scanned += 1
            try:
                data = path.read_bytes()
                receipt = ingest_research_document(
                    path.name,
                    data,
                    db_path=db_path,
                    extract=extract,
                    file_path=str(path.resolve()),
                )
                if receipt["status"] == "ingested":
                    ingested += 1
                    if receipt["parse_error"]:
                        errors.append(f"{path.name}: {receipt['parse_error']}")
                    if receipt["extraction_error"]:
                        errors.append(f"{path.name} 抽取: {receipt['extraction_error']}")
                else:
                    skipped += 1
            except Exception as exc:
                logger.error("投喂解析失败 %s: %s", path.name, exc)
                errors.append(f"{path.name}: {exc}")
    if errors and ingested == 0 and reparsed == 0 and scanned:
        status = "failed"
    elif errors:
        status = "degraded"
    elif scanned:
        status = "ok"
    else:
        status = "empty"
    store.record_run(
        "research_report_inbox_daily",
        None,
        None,
        status,
        ingested + reparsed,
        detail=json.dumps(
            {
                "scanned": scanned,
                "skipped": skipped,
                "reparsed": reparsed,
                "errors": errors,
            },
            ensure_ascii=False,
        )
        if (errors or skipped or reparsed)
        else None,
        db_path=db_path,
    )
    return {
        "inbox_dir": str(directory),
        "scanned": scanned,
        "ingested": ingested,
        "skipped": skipped,
        "reparsed": reparsed,
        # 深度层覆盖度：抽取只覆盖人工投喂 PDF，此值为 0 时下游触发分析无输入。
        # 落进 job_runs.summary，运维面可直接看见「空转」而不是只看到 ok。
        "extractions_total": store.count_extractions(db_path=db_path),
        "errors": errors,
    }


EXTRACTION_PROMPT_NAME = "report_extraction"
EXTRACTION_MAX_CHARS = 30_000
RATING_BUCKETS = ("买入", "增持", "中性", "减持", "卖出", "未知")
_RATING_MAP = {
    "买入": "买入",
    "强烈推荐": "买入",
    "推荐": "买入",
    "优于大市": "买入",
    "增持": "增持",
    "谨慎增持": "增持",
    "跑赢行业": "增持",
    "中性": "中性",
    "持有": "中性",
    "同步大市": "中性",
    "减持": "减持",
    "弱于大市": "减持",
    "卖出": "卖出",
    # 观点型表述（本系统自产报告与部分卖方用词）
    "看多": "买入",
    "偏多": "增持",
    "看空": "卖出",
    "偏空": "减持",
    "观望": "中性",
    # 英文机构口径（外资行/中资英文报告）
    "buy": "买入",
    "strong buy": "买入",
    "overweight": "增持",
    "accumulate": "增持",
    "neutral": "中性",
    "hold": "中性",
    "underweight": "减持",
    "sell": "卖出",
}


def _normalize_rating(value: Any) -> str:
    text = str(value or "").strip()
    return _RATING_MAP.get(text) or _RATING_MAP.get(text.lower()) or "未知"


def _str_list(value: Any, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _page_or_none(value: Any, page_count: int | None) -> int | None:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    if page < 1:
        return None
    if page_count is not None and page > int(page_count):
        return None
    return page


def _coerce_items(value: Any, kind: str, page_count: int | None) -> tuple[list[str], list[dict]]:
    """兼容字符串与 ``{text, source_page}`` 两种抽取形态，返回文本与页码来源。"""
    texts: list[str] = []
    sources: list[dict] = []
    if not isinstance(value, list):
        return texts, sources
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            page = _page_or_none(item.get("source_page"), page_count)
        else:
            text = str(item or "").strip()
            page = None
        if not text or text in texts or len(texts) >= 5:
            continue
        index = len(texts)
        texts.append(text)
        if page is not None:
            sources.append({"kind": kind, "index": index, "text": text, "source_page": page})
    return texts, sources


def _coerce_sector_views(value: Any, page_count: int | None) -> list[dict]:
    """策略/宏观行业观点收敛；direction 白名单外归「未知」，不丢弃行业。"""
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        industry = str(item.get("industry") or "").strip()
        if not industry:
            continue
        direction = str(item.get("direction") or "").strip()
        if direction not in ("看多", "中性", "看空", "未知"):
            direction = "未知"
        out.append(
            {
                "industry": industry,
                "direction": direction,
                "rationale": str(item.get("rationale") or "").strip() or None,
                "source_page": _page_or_none(item.get("source_page"), page_count),
            }
        )
        if len(out) >= 10:
            break
    return out


def _coerce_market_view(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("sentiment", "positioning"):
        text = str(value.get(key) or "").strip()
        if text:
            out[key] = text
    catalysts = value.get("key_catalysts")
    if isinstance(catalysts, list):
        items = [str(item).strip() for item in catalysts if str(item).strip()]
        if items:
            out["key_catalysts"] = items[:5]
    return out


def _coerce_extraction(payload: dict, *, page_count: int | None = None) -> dict:
    """LLM 抽取载荷 → 入库形状（只做类型收敛与白名单，不补事实）。"""
    stocks: list[dict] = []
    raw_stocks = payload.get("stocks")
    if isinstance(raw_stocks, list):
        for item in raw_stocks:
            if not isinstance(item, dict):
                continue
            code_text = str(item.get("code") or "").strip()
            code = code_text if re.fullmatch(r"\d{6}", code_text) else None
            eps: dict[str, float] = {}
            raw_eps = item.get("eps_forecasts")
            if isinstance(raw_eps, dict):
                for year, value in raw_eps.items():
                    year_text = str(year).strip()
                    number = _num(value)
                    if re.fullmatch(r"\d{4}", year_text) and number is not None:
                        eps[year_text] = number
            normalized = _normalize_rating(item.get("rating_normalized"))
            if normalized == "未知":
                # 模型给了「未知」时退回原始评级再做确定性映射（看空→卖出 等）
                normalized = _normalize_rating(item.get("rating"))
            stocks.append(
                {
                    "code": code,
                    "name": str(item.get("name") or "").strip() or None,
                    "rating": str(item.get("rating") or "").strip() or None,
                    "rating_normalized": normalized,
                    "target_price": _num(item.get("target_price")),
                    "eps_forecasts": eps,
                    "industry": str(item.get("industry") or "").strip() or None,
                    "source_page": _page_or_none(item.get("source_page"), page_count),
                }
            )
    report_date: str | None = str(payload.get("report_date") or "").strip()[:10]
    try:
        report_date = date.fromisoformat(report_date).isoformat()
    except ValueError:
        report_date = None
    summary = str(payload.get("summary") or "").strip()
    core_logic, logic_sources = _coerce_items(payload.get("core_logic"), "core_logic", page_count)
    catalysts, catalyst_sources = _coerce_items(payload.get("catalysts"), "catalysts", page_count)
    risks, risk_sources = _coerce_items(payload.get("risks"), "risks", page_count)
    return {
        "broker": str(payload.get("broker") or "").strip() or None,
        "analyst": str(payload.get("analyst") or "").strip() or None,
        "report_date": report_date,
        "report_type": str(payload.get("report_type") or "").strip() or None,
        "stocks": stocks,
        "core_logic": core_logic,
        "catalysts": catalysts,
        "risks": risks,
        "summary": summary[:500] or None,
        "sector_views": _coerce_sector_views(payload.get("sector_views"), page_count),
        "market_view": _coerce_market_view(payload.get("market_view")),
        "sources": logic_sources + catalyst_sources + risk_sources,
    }


def extract_document(doc_key: str, *, force: bool = False, db_path: str | None = None) -> dict:
    """对一份投喂文档做 LLM 结构化抽取（已有抽取且非 force 时直接返回）。"""
    doc = store.get_document(doc_key, db_path=db_path)
    if not doc:
        raise ValueError(f"文档不存在: {doc_key}")
    existing = store.get_extraction(doc_key, db_path=db_path)
    if existing and not force:
        return existing
    text = (doc.get("text") or "").strip()
    if not text:
        raise ValueError("文档全文为空，无法抽取")
    from finance_agent.llm.contracts import extract_json
    from finance_agent.llm.gateway import complete_text
    from finance_agent.prompts.loader import load_prompt_with_meta

    prompt_info = load_prompt_with_meta(EXTRACTION_PROMPT_NAME)
    prompt = prompt_info.template.replace("{{document_text}}", text[:EXTRACTION_MAX_CHARS])
    raw_text, metadata = complete_text(
        [{"role": "user", "content": prompt}],
        purpose="report_extraction",
        # reasoning 与正文共享输出配额（deepseek 类端点）：预算过小会正文为空，
        # 实测 2500 不足、6000 可完整产出 1.3k 字符 JSON
        max_tokens=6000,
        trace={
            "name": "report_extraction",
            "metadata": {"doc_key": doc_key, "filename": doc.get("filename")},
        },
    )
    try:
        payload = extract_json(raw_text)
    except json.JSONDecodeError:
        # 空正文/纯散文响应（生产 2026-10-21 实测一例）：抽取是投喂链唯一的 LLM
        # 步骤，单次失败会让整份研报停在「已入库未抽取」→ 一次强化重试，仍失败才抛。
        raw_text, metadata = complete_text(
            [
                {
                    "role": "user",
                    "content": prompt
                    + "\n\n只输出一个 JSON 对象本身，不要任何解释、前后缀或 Markdown 围栏。",
                }
            ],
            purpose="report_extraction",
            max_tokens=6000,
            trace={
                "name": "report_extraction_retry",
                "metadata": {"doc_key": doc_key, "filename": doc.get("filename")},
            },
        )
        payload = extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("抽取响应不是 JSON 对象")
    coerced = _coerce_extraction(payload, page_count=doc.get("page_count"))
    store.upsert_extraction(
        doc_key,
        coerced,
        model=str(metadata.get("model") or "") or None,
        prompt_version=prompt_info.prompt_version,
        db_path=db_path,
    )
    return store.get_extraction(doc_key, db_path=db_path) or {}


def extract_all(*, force: bool = False, db_path: str | None = None) -> dict:
    """对已投喂但未抽取的文档批量抽取（失败逐条记录，不中断）。"""
    extracted = skipped = 0
    errors: list[str] = []
    for doc in store.list_documents(limit=1000, db_path=db_path):
        key = doc["doc_key"]
        if not force and store.get_extraction(key, db_path=db_path):
            skipped += 1
            continue
        try:
            extract_document(key, force=force, db_path=db_path)
            extracted += 1
        except Exception as exc:
            logger.error("抽取失败 %s: %s", doc.get("filename"), exc)
            errors.append(f"{doc.get('filename')}: {exc}")
    return {"extracted": extracted, "skipped": skipped, "errors": errors}


def _add_unique(target: list[str], values: Any, limit: int = 10) -> None:
    for item in values or []:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text and text not in target and len(target) < limit:
            target.append(text)


def merge_reports_for_stock(stock_code: str, *, db_path: str | None = None) -> dict | None:
    """按标的合并多篇投喂研报的结构化抽取（确定性聚合，不再调 LLM）。

    产出：报告数/覆盖机构/评级分布/目标价区间/分年 EPS 区间/观点去重清单。
    无该标的的抽取记录时返回 None。
    """
    from statistics import median

    matched: list[tuple[dict, dict]] = []
    for row in store.list_extractions(limit=1000, db_path=db_path):
        try:
            stocks = json.loads(row.get("stocks_json") or "[]")
        except json.JSONDecodeError:
            continue
        for stock in stocks:
            if str((stock or {}).get("code") or "") == stock_code:
                matched.append((row, stock))
                break
    if not matched:
        return None
    ratings = dict.fromkeys(RATING_BUCKETS, 0)
    targets: list[float] = []
    eps_values: dict[str, list[float]] = {}
    core_logic: list[str] = []
    catalysts: list[str] = []
    risks: list[str] = []
    brokers: set[str] = set()
    dates: list[str] = []
    documents: list[dict] = []
    for row, stock in matched:
        ratings[_normalize_rating(stock.get("rating_normalized") or stock.get("rating"))] += 1
        target = _num(stock.get("target_price"))
        if target is not None:
            targets.append(target)
        for year, value in (stock.get("eps_forecasts") or {}).items():
            number = _num(value)
            if number is not None:
                eps_values.setdefault(str(year), []).append(number)
        try:
            _add_unique(core_logic, json.loads(row.get("core_logic_json") or "[]"))
            _add_unique(catalysts, json.loads(row.get("catalysts_json") or "[]"))
            _add_unique(risks, json.loads(row.get("risks_json") or "[]"))
        except json.JSONDecodeError:
            continue
        if row.get("broker"):
            brokers.add(str(row["broker"]))
        if row.get("report_date"):
            dates.append(str(row["report_date"]))
        documents.append(
            {
                "doc_key": row.get("doc_key"),
                "broker": row.get("broker"),
                "report_date": row.get("report_date"),
                "report_type": row.get("report_type"),
            }
        )
    eps_summary = {
        year: {
            "min": min(values),
            "median": median(values),
            "max": max(values),
            "count": len(values),
        }
        for year, values in sorted(eps_values.items())
    }
    return {
        "stock_code": stock_code,
        "report_count": len(matched),
        "brokers": sorted(brokers),
        "latest_report_date": max(dates) if dates else None,
        "ratings": ratings,
        "target_price": (
            {
                "min": min(targets),
                "median": median(targets),
                "max": max(targets),
                "count": len(targets),
            }
            if targets
            else None
        ),
        "eps_forecasts": eps_summary,
        "core_logic": core_logic,
        "catalysts": catalysts,
        "risks": risks,
        "documents": documents,
    }


def format_merge_digest(merged: dict | None, *, max_items: int = 5) -> str:
    """合并结果 → 分析师上下文可用的文本段（无数据返回空串）。"""
    if not merged:
        return ""
    parts = [
        f"手工投喂研报合并（{merged['report_count']} 篇；机构：{'、'.join(merged['brokers']) or '未知'}；"
        f"最新报告日：{merged.get('latest_report_date') or '未知'}）"
    ]
    ratings = merged.get("ratings") or {}
    distribution = "/".join(
        f"{bucket}{ratings.get(bucket, 0)}" for bucket in RATING_BUCKETS if bucket != "未知"
    )
    if any(ratings.get(bucket) for bucket in RATING_BUCKETS):
        parts.append(f"评级分布：{distribution}")
    target = merged.get("target_price")
    if target:
        parts.append(
            f"目标价：{target['min']:.2f}~{target['max']:.2f}（中位 {target['median']:.2f}，{target['count']} 家）"
        )
    eps = merged.get("eps_forecasts") or {}
    if eps:
        parts.append(
            "一致预期 EPS："
            + "；".join(
                f"{year}E {stats['min']:.2f}~{stats['max']:.2f}（中位 {stats['median']:.2f}）"
                for year, stats in eps.items()
            )
        )
    for label, key in (("核心逻辑", "core_logic"), ("催化剂", "catalysts"), ("风险", "risks")):
        values = merged.get(key) or []
        if values:
            parts.append(f"{label}：" + "；".join(values[:max_items]))
    parts.append(
        "注意：投喂研报为个人研究材料，观点存在立场偏差与覆盖偏差，"
        "不得直接作为你的结论依据，仅作对照参考。"
    )
    return "\n".join(parts)


def get_manual_report_digest(stock_code: str, *, db_path: str | None = None) -> dict | None:
    """分析期读取：投喂研报合并摘要（无投喂记录返回 None，纯读库不调 LLM）。"""
    return merge_reports_for_stock(stock_code, db_path=db_path)


STRATEGY_REPORT_TYPES = ("策略报告", "宏观研究")
STRATEGY_DIGEST_MAX_REPORTS = 3
STRATEGY_DIGEST_MAX_AGE_DAYS = 30


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def get_strategy_digest(
    *,
    as_of: str | None = None,
    db_path: str | None = None,
    max_reports: int | None = None,
    max_age_days: int | None = None,
) -> dict | None:
    """策略/宏观研报观点聚合（确定性、零 LLM）。

    默认取 30 天内、按报告日期降序的前 3 篇；无记录或全部陈旧返回 None。
    """
    as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    limit_reports = max_reports or _env_int(
        "STRATEGY_DIGEST_MAX_REPORTS", STRATEGY_DIGEST_MAX_REPORTS
    )
    age_limit = max_age_days or _env_int(
        "STRATEGY_DIGEST_MAX_AGE_DAYS", STRATEGY_DIGEST_MAX_AGE_DAYS
    )
    selected: list[tuple[dict, date]] = []
    for row in store.list_extractions(limit=500, db_path=db_path):
        if str(row.get("report_type") or "") not in STRATEGY_REPORT_TYPES:
            continue
        try:
            report_date = date.fromisoformat(str(row.get("report_date") or "")[:10])
        except ValueError:
            continue
        if (as_of_date - report_date).days > age_limit:
            continue
        selected.append((row, report_date))
    if not selected:
        return None
    selected.sort(key=lambda item: item[1], reverse=True)
    docs = selected[:limit_reports]

    sector: dict[str, dict] = {}
    core_logic: list[str] = []
    catalysts: list[str] = []
    risks: list[str] = []
    summaries: list[dict] = []
    market_views: list[dict] = []
    brokers: set[str] = set()
    documents: list[dict] = []

    for row, _ in docs:
        if row.get("broker"):
            brokers.add(str(row["broker"]))
        for view in _json_list(row.get("sector_views_json")):
            if not isinstance(view, dict):
                continue
            industry = str(view.get("industry") or "").strip()
            if not industry:
                continue
            direction = str(view.get("direction") or "未知")
            item = sector.setdefault(industry, {"directions": {}, "rationales": []})
            item["directions"][direction] = item["directions"].get(direction, 0) + 1
            rationale = str(view.get("rationale") or "").strip()
            if rationale and rationale not in item["rationales"] and len(item["rationales"]) < 3:
                item["rationales"].append(rationale)
        for target, key in (
            (core_logic, "core_logic_json"),
            (catalysts, "catalysts_json"),
            (risks, "risks_json"),
        ):
            for text in _json_list(row.get(key)):
                text = str(text or "").strip()
                if text and text not in target and len(target) < 3:
                    target.append(text)
        summary = str(row.get("summary") or "").strip()
        if summary:
            summaries.append(
                {
                    "broker": row.get("broker"),
                    "report_date": row.get("report_date"),
                    "summary": summary,
                }
            )
        market_view = row.get("market_view_json")
        try:
            parsed_market = json.loads(market_view) if market_view else {}
        except (TypeError, json.JSONDecodeError):
            parsed_market = {}
        if isinstance(parsed_market, dict) and parsed_market:
            market_views.append(
                {
                    "broker": row.get("broker"),
                    "report_date": row.get("report_date"),
                    "market_view": parsed_market,
                }
            )
        documents.append(
            {
                "doc_key": row.get("doc_key"),
                "broker": row.get("broker"),
                "report_date": row.get("report_date"),
                "report_type": row.get("report_type"),
            }
        )

    disagreements = [
        industry
        for industry, item in sector.items()
        if item["directions"].get("看多", 0) and item["directions"].get("看空", 0)
    ]
    return {
        "report_count": len(docs),
        "brokers": sorted(brokers),
        "single_broker": len(brokers) == 1,
        "latest_report_date": max(item[1] for item in docs).isoformat(),
        "sector_views": sector,
        "disagreements": disagreements,
        "core_logic": core_logic,
        "catalysts": catalysts,
        "risks": risks,
        "summaries": summaries[:3],
        "market_views": market_views[:3],
        "documents": documents,
    }


def format_strategy_digest(digest: dict | None) -> str:
    """策略观点聚合 → 宏观分析师上下文文本段；无数据返回空串。"""
    if not digest:
        return ""
    brokers = "、".join(digest.get("brokers") or []) or "未知"
    suffix = "；单机构观点" if digest.get("single_broker") else ""
    lines = [
        f"卖方策略观点（{digest.get('report_count', 0)} 篇；机构：{brokers}；"
        f"最新报告日：{digest.get('latest_report_date') or '未知'}{suffix}）"
    ]
    for industry, item in (digest.get("sector_views") or {}).items():
        counts = "/".join(
            f"{direction}{count}"
            for direction, count in (item.get("directions") or {}).items()
            if count
        )
        line = f"- 行业：{industry}（{counts or '未知'}）"
        rationales = item.get("rationales") or []
        if rationales:
            line += "；论据：" + "；".join(rationales[:3])
        lines.append(line)
    if digest.get("disagreements"):
        lines.append("- 机构分歧行业：" + "、".join(digest["disagreements"]))
    for label, key in (("核心逻辑", "core_logic"), ("催化剂", "catalysts"), ("风险", "risks")):
        values = digest.get(key) or []
        if values:
            lines.append(f"- {label}：" + "；".join(values[:3]))
    if digest.get("market_views"):
        first = digest["market_views"][0]
        view = first.get("market_view") or {}
        bits = []
        if view.get("sentiment"):
            bits.append(f"情绪：{view['sentiment']}")
        if view.get("positioning"):
            bits.append(f"仓位：{view['positioning']}")
        if bits:
            lines.append(
                f"- 市场观点（{first.get('broker') or '未知'} {first.get('report_date') or ''}）："
                + "；".join(bits)
            )
    lines.append(
        "注意：卖方观点存在立场与覆盖偏差，不得替代宏观数据推理，"
        "不得直接作为结论依据，仅作对照参考。"
    )
    return "\n".join(lines)


def get_consensus_for_analysis(stock_code: str, *, db_path: str | None = None) -> dict:
    """分析期一致预期：数据面快照 → 按需直拉全市场 → 缺失标注。

    返回 dict 的 ``status`` 三态：covered / no_coverage / missing。
    """
    got = store.get_consensus(stock_code, db_path=db_path)
    if got["status"] != "missing":
        if got["status"] == "covered" and got.get("eps_y1") is None:
            detail = fetch_ths_detail(stock_code)
            if detail:
                got["ths_detail"] = detail
        return got
    try:
        rows = fetch_forecast_snapshot()
    except Exception as exc:
        logger.warning("一致预期直拉失败 %s: %s", stock_code, exc)
        return {"status": "missing", "stock_code": stock_code}
    snapshot_date = date.today().isoformat()
    store.upsert_earnings_forecasts(snapshot_date, rows, db_path=db_path)
    match = next((row for row in rows if row.get("code") == stock_code), None)
    if not match:
        return {"status": "no_coverage", "stock_code": stock_code, "snapshot_date": snapshot_date}
    result = dict(match)
    result["status"] = "covered"
    result["stock_code"] = stock_code
    result["snapshot_date"] = snapshot_date
    return result


def _run_status(rows: int, errors: list[str]) -> str:
    if not errors:
        return "ok" if rows > 0 else "empty"
    return "degraded" if rows > 0 else "failed"


def run_reports_daily(
    as_of: date | None = None, *, window_days: int = 3, db_path: str | None = None
) -> dict:
    """研报元数据日批：五类采集 + 新浪计数交叉 + 分析师档案聚合。"""
    as_of = as_of or date.today()
    begin = as_of - timedelta(days=window_days)
    store.init_db(db_path)
    total_rows = 0
    errors: list[str] = []
    per_type: dict[str, int] = {}
    for report_type in REPORT_TYPES:
        try:
            rows = fetch_market_reports(report_type, begin, as_of)
            per_type[report_type] = store.upsert_reports(rows, db_path=db_path)
            total_rows += per_type[report_type]
        except Exception as exc:
            logger.error("研报元数据采集失败 %s: %s", report_type, exc)
            errors.append(f"{report_type}: {exc}")
            per_type[report_type] = 0
    sina_counts = fetch_sina_counts()
    store.record_run(
        "research_reports_daily",
        begin.isoformat(),
        as_of.isoformat(),
        _run_status(total_rows, errors),
        total_rows,
        detail=json.dumps(
            {"per_type": per_type, "errors": errors, "sina_counts": sina_counts},
            ensure_ascii=False,
        )
        if (errors or sina_counts)
        else None,
        db_path=db_path,
    )
    try:
        store.refresh_analysts(as_of=as_of, db_path=db_path)
    except Exception as exc:
        logger.error("分析师档案聚合失败: %s", exc)
    return {
        "as_of": as_of.isoformat(),
        "reports_rows": total_rows,
        "per_type": per_type,
        "errors": errors,
        "sina_counts": sina_counts,
    }


def run_ratings_daily(as_of: date | None = None, *, db_path: str | None = None) -> dict:
    """巨潮评级变动日批（按日全市场；空窗记 empty，失败记 failed 并上抛由 job 记录）。"""
    as_of = as_of or date.today()
    store.init_db(db_path)
    rows = 0
    error: str | None = None
    try:
        rows = store.upsert_rating_changes(fetch_rating_changes(as_of), db_path=db_path)
    except Exception as exc:
        logger.error("评级变动采集失败: %s", exc)
        error = str(exc)
    store.record_run(
        "rating_changes_daily",
        as_of.isoformat(),
        as_of.isoformat(),
        "failed" if error else ("ok" if rows else "empty"),
        rows,
        detail=error,
        db_path=db_path,
    )
    if error:
        raise RuntimeError(error)
    return {"as_of": as_of.isoformat(), "rating_rows": rows}


def run_forecasts_daily(as_of: date | None = None, *, db_path: str | None = None) -> dict:
    """东财全市场一致预期快照日批（一次约 2887 只）。"""
    as_of = as_of or date.today()
    store.init_db(db_path)
    rows = 0
    error: str | None = None
    try:
        rows = store.upsert_earnings_forecasts(
            as_of.isoformat(), fetch_forecast_snapshot(), db_path=db_path
        )
    except Exception as exc:
        logger.error("预期快照采集失败: %s", exc)
        error = str(exc)
    store.record_run(
        "earnings_forecasts_daily",
        as_of.isoformat(),
        as_of.isoformat(),
        "failed" if error else ("ok" if rows else "empty"),
        rows,
        detail=error,
        db_path=db_path,
    )
    if error:
        raise RuntimeError(error)
    return {"as_of": as_of.isoformat(), "forecast_rows": rows}


def run_daily(
    as_of: date | None = None, *, window_days: int = 3, db_path: str | None = None
) -> dict:
    """手动/CLI 组合日批：三 job 依次执行（scheduler 侧各自独立注册互不阻塞）。

    兄弟 job 失败只记录在本 job 运行行，不阻断后续 job（rating/forecast 函数内部
    已落 failed 运行记录并把异常上抛，此处吞掉继续）。
    """
    as_of = as_of or date.today()
    reports = run_reports_daily(as_of, window_days=window_days, db_path=db_path)
    rating_rows = 0
    try:
        rating_rows = run_ratings_daily(as_of, db_path=db_path)["rating_rows"]
    except Exception:
        logger.error("评级变动 job 失败（已记录运行行），继续预期快照")
    forecast_rows = 0
    try:
        forecast_rows = run_forecasts_daily(as_of, db_path=db_path)["forecast_rows"]
    except Exception:
        logger.error("预期快照 job 失败（已记录运行行）")
    return {
        "as_of": as_of.isoformat(),
        "reports_rows": reports["reports_rows"],
        "per_type": reports["per_type"],
        "rating_rows": rating_rows,
        "forecast_rows": forecast_rows,
        "errors": reports["errors"],
        "sina_counts": reports["sina_counts"],
    }


def run_backfill(start: date, *, end: date | None = None, db_path: str | None = None) -> dict:
    """历史回填：按月分窗复用五类采集（不做评级/预期快照——那两者是时点数据）。"""
    end = end or date.today()
    store.init_db(db_path)
    total = 0
    per_type = dict.fromkeys(REPORT_TYPES, 0)
    cursor = start.replace(day=1)
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        window_end = min(next_month - timedelta(days=1), end)
        for report_type in REPORT_TYPES:
            try:
                rows = fetch_market_reports(report_type, cursor, window_end)
                per_type[report_type] += store.upsert_reports(rows, db_path=db_path)
            except Exception as exc:
                logger.error("回填失败 %s %s: %s", report_type, cursor, exc)
        total = sum(per_type.values())
        cursor = next_month
    store.record_run(
        "research_reports_backfill",
        start.isoformat(),
        end.isoformat(),
        "ok" if total else "empty",
        total,
        detail=json.dumps(per_type, ensure_ascii=False),
        db_path=db_path,
    )
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": total, "per_type": per_type}


# ── 行业研报聚合（add-industry-report-context）────────────────────────────────
# 研报侧行业名是申万 2021 分类（带 Ⅱ/Ⅲ 层级后缀，如「白酒Ⅱ」「银行Ⅱ」），个股侧来自
# 东财 F10（如「白酒」「多元化保险」）。实测纯等值仅 2/5 命中（见
# tests/validation/2026-10-22-industry-report-industry-vocab-match.md），故：
# 归一剥离层级后缀 + 显式别名表（只收录实测确认项）+ 未命中显式 unmatched 状态。

INDUSTRY_DIGEST_MAX_REPORTS = 8
INDUSTRY_DIGEST_MAX_AGE_DAYS = 90
INDUSTRY_TITLE_MAX_CHARS = 80

# 键 = 个股行业名（东财 F10，归一后）；值 = 研报词表名（归一后）。
# 只收录实测确认的词表差异项；值必须存在于 tests/fixtures/industry_report_vocab.tsv。
INDUSTRY_ALIASES: dict[str, str] = {
    "多元化保险": "保险",
}

# NFKC 已把 Ⅱ/Ⅲ 折成 ASCII II/III，按长度降序剥离，避免 III 被切成 I
_LEVEL_SUFFIXES = ("III", "II")


def normalize_industry(name: str | None) -> str:
    """行业名归一：NFKC + 去全部空白 + 剥离末尾申万层级标识（不做任何模糊匹配）。"""
    if not name:
        return ""
    text = "".join(unicodedata.normalize("NFKC", str(name)).split())
    for suffix in _LEVEL_SUFFIXES:
        if len(text) > len(suffix) and text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _empty_digest(industry: str, status: str) -> dict:
    return {
        "industry": industry,
        "status": status,
        "report_count": 0,
        "brokers": [],
        "latest_report_date": None,
        "age_days": None,
        "documents": [],
    }


def get_industry_digest(
    industry: str | None,
    *,
    as_of: str | None = None,
    db_path: str | Path | None = None,
    max_reports: int | None = None,
    max_age_days: int | None = None,
) -> dict | None:
    """同行业研报聚合（确定性、零 LLM）。

    三态：ok（窗口内有数据）/ stale（窗口内无、库内有历史）/ unmatched（行业名未覆盖）。
    只有「库内没有任何行业研报」或「该行业在 as_of 前无任何记录」才返回 None。
    读库异常 fail-open 返回 None（同 get_strategy_digest 纪律）。
    """
    target = normalize_industry(industry)
    if not target:
        return None
    as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    limit = max_reports or _env_int("INDUSTRY_DIGEST_MAX_REPORTS", INDUSTRY_DIGEST_MAX_REPORTS)
    age_limit = max_age_days or _env_int(
        "INDUSTRY_DIGEST_MAX_AGE_DAYS", INDUSTRY_DIGEST_MAX_AGE_DAYS
    )
    try:
        rows = store.list_industry_reports(db_path=db_path)
    except Exception:  # noqa: BLE001 -- fail-open：读不到库 ≠ 没有行业研报的结论
        logger.warning("行业研报读取失败（fail-open 空段）: %s", industry, exc_info=True)
        return None
    if not rows:
        return None

    wanted = {target}
    alias = INDUSTRY_ALIASES.get(target)
    if alias:
        wanted.add(normalize_industry(alias))

    matched: list[tuple[date, dict]] = []
    for row in rows:
        if normalize_industry(row.get("industry")) not in wanted:
            continue
        try:
            publish = date.fromisoformat(str(row.get("publish_date") or "")[:10])
        except ValueError:
            continue
        matched.append((publish, row))
    if not matched:
        return _empty_digest(str(industry), "unmatched")

    history = [(publish, row) for publish, row in matched if publish <= as_of_date]
    if not history:
        return None  # 该行业在 as_of 之前没有任何研报：时点正确性优先
    history.sort(key=lambda item: item[0], reverse=True)
    latest_date = history[0][0]
    in_window = [
        (publish, row) for publish, row in history if (as_of_date - publish).days <= age_limit
    ][:limit]
    documents = [
        {
            "report_key": row.get("report_key"),
            "title": str(row.get("title") or "").strip(),
            "org": str(row.get("org") or "").strip(),
            "analyst": str(row.get("analyst") or "").strip(),
            "publish_date": publish.isoformat(),
            "depth_class": row.get("depth_class"),
        }
        for publish, row in in_window
    ]
    return {
        "industry": str(industry),
        "status": "ok" if documents else "stale",
        "report_count": len(documents),
        "brokers": sorted({doc["org"] for doc in documents if doc["org"]}),
        "latest_report_date": latest_date.isoformat(),
        "age_days": (as_of_date - latest_date).days,
        "documents": documents,
    }


def format_industry_digest(digest: dict | None) -> str:
    """行业研报聚合 → 基本面分析师上下文文本段；无数据返回空串。"""
    if not digest:
        return ""
    industry = str(digest.get("industry") or "").strip() or "未知行业"
    header = "同行业研报（state 键 industry_report_digest；仅元数据，未读取正文）"
    status = str(digest.get("status") or "")
    if status == "unmatched":
        return (
            f"{header}: 个股行业「{industry}」，研报库无对应行业口径"
            "（申万分类未覆盖），本段不提供行业研报线索。"
        )
    if status == "stale":
        latest = digest.get("latest_report_date") or "未知"
        age = digest.get("age_days")
        age_text = f"距今 {age} 天" if isinstance(age, int) else "距今天数未知"
        return (
            f"{header}: 「{industry}」行业断更——最新一篇 {latest}（{age_text}），"
            "窗口内无新报告，不列陈旧标题。"
        )
    lines = [f"{header}:"]
    for doc in digest.get("documents") or []:
        title = str(doc.get("title") or "").strip()
        if not title:
            continue
        if len(title) > INDUSTRY_TITLE_MAX_CHARS:
            title = title[:INDUSTRY_TITLE_MAX_CHARS] + "…"
        date_text = str(doc.get("publish_date") or "").strip()
        org = str(doc.get("org") or "").strip()
        lines.append(f"- {date_text} {org} | {title}".replace("  ", " "))
    lines.append(
        "注意：同行业研报仅提供标题层面线索，正文未读取；标题与机构立场不构成结论依据，"
        "不得直接作为结论依据，也不得据标题推断报告观点。"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="研报数据面采集（免费主干）")
    sub = parser.add_subparsers(dest="command", required=True)
    daily = sub.add_parser("daily", help="跑一轮日批")
    daily.add_argument("--date", default=None, help="基准日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--window-days", type=int, default=3)
    daily.add_argument("--db", default=None, help="库路径（默认 RESEARCH_REPORTS_DB_PATH）")
    backfill = sub.add_parser("backfill", help="按日期区间分窗回填")
    backfill.add_argument("--start", required=True, help="起点 YYYY-MM-DD")
    backfill.add_argument("--end", default=None)
    backfill.add_argument("--db", default=None)
    inbox = sub.add_parser("inbox", help="扫描投喂目录并解析入库（个人研究自用）")
    inbox.add_argument("--dir", default=None, help="投喂目录（默认 RESEARCH_REPORT_INBOX_DIR）")
    inbox.add_argument("--no-extract", action="store_true", help="只入库不抽取")
    inbox.add_argument(
        "--force-reparse",
        action="store_true",
        help="重算既有文档的页偏移/跨源关联/深度分级（不新增行、不调 LLM）",
    )
    inbox.add_argument("--db", default=None)
    extract = sub.add_parser("extract", help="对投喂文档做 LLM 结构化抽取")
    extract.add_argument("--doc-key", default=None, help="只抽一份；省略则批量")
    extract.add_argument("--force", action="store_true", help="已抽取也重抽")
    extract.add_argument("--db", default=None)
    merge = sub.add_parser("merge", help="按标的合并多篇投喂研报")
    merge.add_argument("--code", required=True)
    merge.add_argument("--text", action="store_true", help="输出给分析师看的分段文本")
    merge.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    if args.command == "daily":
        result = run_daily(
            date.fromisoformat(args.date) if args.date else None,
            window_days=args.window_days,
            db_path=args.db,
        )
    elif args.command == "inbox":
        result = ingest_inbox(
            args.dir,
            db_path=args.db,
            extract=not args.no_extract,
            force_reparse=args.force_reparse,
        )
    elif args.command == "extract":
        if args.doc_key:
            result = extract_document(args.doc_key, force=args.force, db_path=args.db)
        else:
            result = extract_all(force=args.force, db_path=args.db)
    elif args.command == "merge":
        merged = merge_reports_for_stock(args.code, db_path=args.db)
        result = ({"text": format_merge_digest(merged)} if args.text else merged) or {
            "stock_code": args.code,
            "report_count": 0,
        }
    else:
        result = run_backfill(
            date.fromisoformat(args.start),
            end=date.fromisoformat(args.end) if args.end else None,
            db_path=args.db,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
