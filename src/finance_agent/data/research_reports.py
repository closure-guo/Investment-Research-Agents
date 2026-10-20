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


def extract_pdf_text(path: str | Path) -> tuple[str, int]:
    """抽取 PDF 全文与页数（pypdf；延迟导入避免非投喂路径加载解析器）。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    texts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(texts).strip(), len(reader.pages)


def _document_key(data: bytes) -> str:
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def ingest_inbox(
    inbox_dir: str | Path | None = None,
    *,
    db_path: str | None = None,
    extract: bool = True,
) -> dict:
    """扫描投喂目录，把新 PDF 解析入库（文件内容哈希幂等，重复投喂 no-op）。

    个人研究自用：全文落 ``report_documents``（usage_policy=personal_research）；
    ``extract=True`` 时对新入库文档同步做 LLM 结构化抽取（失败只记错误，
    不阻断入库与其他文件）。
    """
    resolved = (
        inbox_dir
        if inbox_dir is not None
        else os.getenv("RESEARCH_REPORT_INBOX_DIR", INBOX_DIR_DEFAULT)
    )
    directory = Path(resolved)
    store.init_db(db_path)
    scanned = ingested = skipped = 0
    errors: list[str] = []
    if directory.exists():
        for path in sorted(directory.rglob("*.pdf")):
            scanned += 1
            try:
                data = path.read_bytes()
                key = _document_key(data)
                if store.document_exists(key, db_path=db_path):
                    skipped += 1
                    continue
                text, pages = extract_pdf_text(path)
                org, publish_date = infer_metadata_from_filename(path.stem)
                created = store.upsert_document(
                    {
                        "doc_key": key,
                        "filename": path.name,
                        "file_path": str(path.resolve()),
                        "title": path.stem,
                        "org": org,
                        "publish_date": publish_date,
                        "page_count": pages,
                        "char_count": len(text),
                        "text": text,
                        "usage_policy": "personal_research",
                        "source_type": "manual_feed",
                    },
                    db_path=db_path,
                )
                if created:
                    ingested += 1
                    if extract:
                        try:
                            extract_document(key, db_path=db_path)
                        except Exception as exc:  # 抽取失败不影响入库
                            logger.error("投喂抽取失败 %s: %s", path.name, exc)
                            errors.append(f"{path.name} 抽取: {exc}")
                else:
                    skipped += 1
            except Exception as exc:
                logger.error("投喂解析失败 %s: %s", path.name, exc)
                errors.append(f"{path.name}: {exc}")
    if errors and ingested == 0 and scanned:
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
        ingested,
        detail=json.dumps(
            {"scanned": scanned, "skipped": skipped, "errors": errors},
            ensure_ascii=False,
        )
        if (errors or skipped)
        else None,
        db_path=db_path,
    )
    return {
        "inbox_dir": str(directory),
        "scanned": scanned,
        "ingested": ingested,
        "skipped": skipped,
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
}


def _normalize_rating(value: Any) -> str:
    text = str(value or "").strip()
    return _RATING_MAP.get(text, "未知")


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


def _coerce_extraction(payload: dict) -> dict:
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
                }
            )
    report_date: str | None = str(payload.get("report_date") or "").strip()[:10]
    try:
        report_date = date.fromisoformat(report_date).isoformat()
    except ValueError:
        report_date = None
    summary = str(payload.get("summary") or "").strip()
    return {
        "broker": str(payload.get("broker") or "").strip() or None,
        "analyst": str(payload.get("analyst") or "").strip() or None,
        "report_date": report_date,
        "report_type": str(payload.get("report_type") or "").strip() or None,
        "stocks": stocks,
        "core_logic": _str_list(payload.get("core_logic")),
        "catalysts": _str_list(payload.get("catalysts")),
        "risks": _str_list(payload.get("risks")),
        "summary": summary[:500] or None,
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
    payload = extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("抽取响应不是 JSON 对象")
    coerced = _coerce_extraction(payload)
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
        result = ingest_inbox(args.dir, db_path=args.db, extract=not args.no_extract)
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
