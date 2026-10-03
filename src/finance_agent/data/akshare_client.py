"""AKShare API 封装 — 数据拉取 + 年报过滤 + 缺失值处理。

接口：
- fetch_balance_sheet(stock_code, years=5) → DataFrame
- fetch_income_statement(stock_code, years=5) → DataFrame
- fetch_cash_flow(stock_code, years=5) → DataFrame
- fetch_indicators(stock_code, start_year) → DataFrame
- fetch_industry(stock_code) → dict
- fetch_stock_quote(stock_code) → dict
- fetch_peer_data(stock_codes) → DataFrame

数据降级：三大报表缺失 → 抛异常；其他缺失 → 标记 N/A。
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd
import requests

logger = logging.getLogger(__name__)

_SINA_MAX_RETRIES = 2
_SINA_RETRY_DELAY = 2
_AK_TIMEOUT = 15  # 单次 AKShare 调用超时秒数
_AK_MAX_RETRIES = 3  # 通用 AKShare 调用重试次数（应对 RemoteDisconnected 限频）
_AK_RETRY_DELAY = 1.5  # 重试退避秒数

# 结算/回测取数的复权口径（delta add-backtest-leakage-controls）。
# 前复权（qfq，fetch_kline 默认）随最新除权事件整体重算历史价，回填历史时点会引入
# 未来信息（as-of 不保真）；后复权（hfq）历史价 = 当时真实成交价，故结算与回测
# 统一显式传 hfq。管线分析输入（nodes/fetch.py）保持默认 qfq 不动。
SETTLEMENT_ADJUST = "hfq"


def _call_ak(func, *args, **kwargs):
    """带超时 + 重试的 AKShare 调用包装。

    超时或连接异常（含 RemoteDisconnected 限频）时重试，全部失败返回 None。

    注意：不能用 `with ThreadPoolExecutor(...) as pool`（退出时 shutdown(wait=True)
    会无限等待卡死的 AKShare 线程，超时形同虚设，泄漏线程打满 executor 池）。
    超时后 shutdown(wait=False, cancel_futures=True) 立即释放，卡死线程自然消亡。
    """
    for attempt in range(1, _AK_MAX_RETRIES + 1):
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(func, *args, **kwargs)
        try:
            result = future.result(timeout=_AK_TIMEOUT)
            pool.shutdown(wait=False)
            return result
        except FuturesTimeoutError:
            # 超时：不等待卡死线程（wait=False），取消其未来任务，释放 executor
            pool.shutdown(wait=False, cancel_futures=True)
            logger.warning(
                "AKShare 调用超时 (%ds): %s (attempt %d/%d)",
                _AK_TIMEOUT,
                getattr(func, "__name__", str(func)),
                attempt,
                _AK_MAX_RETRIES,
            )
        except Exception as e:
            # 捕获 RemoteDisconnected / ConnectionError 等网络异常，重试而非直接抛出
            pool.shutdown(wait=False, cancel_futures=True)
            logger.warning(
                "AKShare 调用异常: %s: %s (attempt %d/%d)",
                getattr(func, "__name__", str(func)),
                type(e).__name__,
                attempt,
                _AK_MAX_RETRIES,
            )
        if attempt < _AK_MAX_RETRIES:
            time.sleep(_AK_RETRY_DELAY * attempt)  # 线性退避
    logger.error(
        "AKShare 调用全部失败 (%d 次): %s",
        _AK_MAX_RETRIES,
        getattr(func, "__name__", str(func)),
    )
    return None


def _sina_report(stock: str, symbol: str) -> pd.DataFrame:
    """Call stock_financial_report_sina with retry - the Sina API is flaky."""
    for attempt in range(1, _SINA_MAX_RETRIES + 1):
        df = _call_ak(ak.stock_financial_report_sina, stock=stock, symbol=symbol)
        if df is not None and not df.empty:
            return df
        logger.warning("Sina API returned empty for %s/%s (attempt %d)", stock, symbol, attempt)
        if attempt < _SINA_MAX_RETRIES:
            time.sleep(_SINA_RETRY_DELAY)
    raise RuntimeError(f"新浪财经接口连续 {_SINA_MAX_RETRIES} 次无响应，请稍后再试")


# AKShare 返回中文列名，下游使用英文 key，在此做映射。
_QUOTE_KEY_MAP = {
    "名称": "name",
    "代码": "code",
    "最新价": "price",
    "涨跌幅": "pct_change",
    "总市值": "market_cap",
    "市盈率-动态": "PE",
    "市盈率-静态": "PE_static",
    "市盈率-TTM": "PE_ttm",
    "市净率": "PB",
}

# 行业 PE 接口列名映射
_INDUSTRY_PE_KEY_MAP = {
    "行业名称": "industry_name",
    "静态市盈率-算术平均": "avg_pe",
    "静态市盈率-中位数": "median_pe",
    "静态市盈率-加权平均": "weighted_pe",
    "公司家数": "company_count",
}

_INDUSTRY_KEY_MAP = {
    "公司名称": "name",
    "股票简称": "name",
    "股票名称": "name",
    "公司简称": "name",
    "行业": "industry",
}

# 指数成分接口列名（2026-09-23 实测，akshare 1.18.94）：
#   中证官网 ak.index_stock_cons_csindex → '成分券代码' / '成分券名称'
#   东财回退 ak.index_stock_cons        → '品种代码' / '品种名称'
_CONSTITUENT_CODE_COLS = ("成分券代码", "品种代码", "代码")
_CONSTITUENT_NAME_COLS = ("成分券名称", "品种名称", "名称", "股票名称", "股票简称")

# 腾讯 qt.gtimg.cn 单标的行情串字段位序（2026-10-01 金样本 sh688072 实抓钉死，
# update-quote-primary-source）。field 39 市盈率为 TTM 口径——腾讯无静态 PE，
# 按 delta spec MUST NOT 进入 quote 输出（PE 推导留给 compute derived_ttm）。
_TENCENT_FIELD_IDX: dict[str, int] = {
    "name": 1,
    "code": 2,
    "price": 3,
    "change": 31,
    "pct_change": 32,
    "high": 33,
    "low": 34,
    "turnover_rate": 38,
    "float_market_cap": 44,  # 亿
    "market_cap": 45,  # 亿
    "PB": 46,
}
# 亿 → 元归一字段（quote 层统一元契约，前端 Charts /1e8 显示「亿」）
_TENCENT_YI_FIELDS = frozenset({"float_market_cap", "market_cap"})
# 关键字段缺失即视为主源失败（触发回退）
_TENCENT_REQUIRED = ("price", "market_cap")


def _add_prefix(code: str) -> str:
    """给股票代码加 sh/sz 前缀。"""
    if code.startswith(("sh", "sz")):
        return code
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


class AKShareClient:
    def __init__(self) -> None:
        # 实际生效的数据源留痕（供 cohort universe 登记文件落 `sources` 审计）：
        # 每维度只记标签集合（values 为常量集合，无增长风险）。
        self.sources_seen: dict[str, set[str]] = {
            "constituents": set(),
            "industry": set(),
            "market_cap": set(),
        }

    def _filter_annual(self, df: pd.DataFrame) -> pd.DataFrame:
        """只保留年报（报告日以 1231 结尾），并按报告日降序（最新在前）对齐契约。"""
        if df.empty:
            return df
        mask = df["报告日"].astype(str).str.endswith("1231")
        return df[mask].sort_values("报告日", ascending=False).reset_index(drop=True)

    def _normalize_nan(self, df: pd.DataFrame) -> pd.DataFrame:
        # D3：float64 列上 where(other=None) 会把 None 重新强转回 NaN（归一不生效），
        # 先 astype(object) 再归一，保证出口全列 NaN → None、消费端 `is None` 判空可靠
        # （「不产出伪值」契约）
        return df.astype(object).where(df.notna(), None)  # pyrefly: ignore[no-matching-overload]

    def _check_min_years(self, df: pd.DataFrame, stock_code: str) -> None:
        if len(df) < 2:
            raise ValueError(
                f"股票 {stock_code} 年报数据不足 2 年（当前 {len(df)} 年），至少需要 2 年"
            )

    def _trim_years(self, df: pd.DataFrame, years: int = 5) -> pd.DataFrame:
        return df.head(years)

    def _rename_parent_cols(self, df: pd.DataFrame) -> pd.DataFrame:
        """重命名归母口径列，解决终端编码不一致问题。

         AKShare 返回的中文列名在部分环境中出现编码差异，导致
         row.get('归属于母公司股东的净利润') 返回 None。
        通过列位置定位并统一重命名为标准列名。
        """
        if df.empty:
            return df
        cols = list(df.columns)

        # 利润表：索引 50 = 归属于母公司股东的净利润, 索引 137 = 归属于母公司股东权益合计
        # 资产负债表：索引 137 = 归属于母公司股东权益合计
        # 这些索引基于 AKShare stock_financial_report_sina 的稳定列序
        _rename_map = {
            50: "归母净利润",
            137: "归母所有者权益",
        }
        rename_dict: dict[str, str] = {}
        for idx, new_name in _rename_map.items():
            if idx < len(cols):
                old_name = cols[idx]
                # 仅当该列尚未被重命名时才处理
                if old_name != new_name and new_name not in cols:
                    rename_dict[old_name] = new_name
        if rename_dict:
            df = df.rename(columns=rename_dict)
        return df

    def fetch_balance_sheet(self, stock_code: str, years: int = 5) -> pd.DataFrame:
        stock = _add_prefix(stock_code)
        df = _sina_report(stock, "资产负债表")
        df = self._filter_annual(df)
        df = self._rename_parent_cols(df)
        self._check_min_years(df, stock_code)
        return self._trim_years(df, years)

    def fetch_income_statement(self, stock_code: str, years: int = 5) -> pd.DataFrame:
        stock = _add_prefix(stock_code)
        df = _sina_report(stock, "利润表")
        df = self._filter_annual(df)
        df = self._rename_parent_cols(df)
        self._check_min_years(df, stock_code)
        return self._trim_years(df, years)

    def fetch_cash_flow(self, stock_code: str, years: int = 5) -> pd.DataFrame:
        stock = _add_prefix(stock_code)
        df = _sina_report(stock, "现金流量表")
        df = self._filter_annual(df)
        self._check_min_years(df, stock_code)
        return self._trim_years(df, years)

    def fetch_indicators(self, stock_code: str, start_year: str = "2020") -> pd.DataFrame:
        df = _call_ak(
            ak.stock_financial_analysis_indicator, symbol=stock_code, start_year=start_year
        )
        if df is None or df.empty:
            return pd.DataFrame()
        # 只保留年报（日期以 12-31 结尾）
        if "日期" in df.columns:
            mask = df["日期"].astype(str).str.endswith("12-31")
            df = df[mask].reset_index(drop=True)
            # 该接口内部按日期升序排序（sort_values 默认 ascending=True），
            # 显式降序对齐「index 0 = 最新」契约，head(N) = 最新 N 年。
            df = df.sort_values("日期", ascending=False).reset_index(drop=True)
        return df

    def _fetch_name_fallback(self, stock_code: str) -> dict:
        """当东方财富接口不可用时，用 stock_info_a_code_name 获取名称。"""
        try:
            df = ak.stock_info_a_code_name()
            row = df[df["code"] == stock_code]
            if not row.empty:
                return {"name": row.iloc[0]["name"]}
        except Exception:
            logger.warning("stock_info_a_code_name fallback failed for %s", stock_code)
        return {}

    # 同日多分类标准 tie-break：申万（券商研报事实标准）> 中证 > 巨潮 > 证监会
    _INDUSTRY_STANDARD_PRIORITY = (
        ("申银万国", 0),
        ("中证", 1),
        ("巨潮", 2),
        ("证监会", 3),
    )

    def _fetch_industry_cninfo(self, stock_code: str) -> str | None:
        """当东方财富接口不可用时，用 cninfo 获取行业名称（行业中类）。

        变更史含多套分类标准：按变更日期降序 + 标准优先级 tie-break（稳定排序）
        取最新非空条目。此前 iloc[0] 拿最旧分类（688072 →「其它专用机械」）；
        仅按日期排序则同日随机（688072 2022-04-20 巨潮「集成电路」vs 中证
        「半导体设备」并存）——两种形态都会让行业阈值覆盖永不命中。
        """
        try:
            df = ak.stock_industry_change_cninfo(symbol=stock_code)
            if not df.empty and "行业中类" in df.columns:
                df = df.copy()
                if "分类标准" in df.columns:
                    df["_std_pri"] = df["分类标准"].apply(
                        lambda s: next(
                            (pri for key, pri in self._INDUSTRY_STANDARD_PRIORITY if key in str(s)),
                            4,
                        )
                    )
                else:
                    df["_std_pri"] = 4
                if "变更日期" in df.columns:
                    df = df.sort_values(
                        ["变更日期", "_std_pri"], ascending=[False, True], kind="mergesort"
                    )
                else:
                    df = df.sort_values("_std_pri", kind="mergesort")
                series = df["行业中类"].dropna()
                if not series.empty:
                    return str(series.iloc[0])
        except Exception:
            logger.warning("stock_industry_change_cninfo failed for %s", stock_code)
        return None

    def fetch_industry(self, stock_code: str) -> dict:
        # 主源：东方财富（含行业+名称）
        df = _call_ak(ak.stock_individual_info_em, symbol=stock_code)
        if df is not None and not df.empty:
            result = {}
            for _, row in df.iterrows():
                key = _INDUSTRY_KEY_MAP.get(row["item"], row["item"])
                result[key] = row["value"]
            if result.get("name") or result.get("industry"):
                self.sources_seen["industry"].add("eastmoney")
                return result
        # 降级：cninfo 行业 + 名称 fallback
        logger.warning("东财个股信息不可用，降级 cninfo 行业+名称: %s", stock_code)
        result = self._fetch_name_fallback(stock_code)
        industry = self._fetch_industry_cninfo(stock_code)
        if industry:
            result["industry"] = industry
            self.sources_seen["industry"].add("cninfo")
        else:
            logger.error("行业数据降级后仍缺失（名称/cninfo 均无返回）: %s", stock_code)
            self.sources_seen["industry"].update({"cninfo", "missing"})
        return result

    @staticmethod
    def _quote_from_spot_df(df, stock_code: str) -> dict | None:
        """东财全市场 spot 表 → 单标的 quote 映射。

        fetch_stock_quote 与 fetch_peer_data 共享的主源映射逻辑（同业批抓取
        共享单次 spot 表，N 标的从 N× 全市场拉取降到 1×）。
        """
        if df is None or df.empty:
            return None
        # 尝试多种格式匹配（纯数字 / 带前缀）
        row = pd.DataFrame()
        for code_key in (stock_code, stock_code.lstrip("sh").lstrip("sz")):
            row = df[df["代码"] == code_key]
            if not row.empty:
                break
        if row.empty:
            return None
        raw = row.iloc[0].to_dict()
        return {
            _QUOTE_KEY_MAP.get(str(k), k): v  # spot 表列名 Any → str 后再查映射
            for k, v in raw.items()
        }

    def _fetch_tencent_quote(self, stock_code: str) -> dict | None:
        """腾讯 qt.gtimg.cn 单标的行情直查（update-quote-primary-source 新主源）。

        1 请求/标的，替代东财全市场 spot 翻页主源（~50 请求/次拿单只股票——
        本机 IP 被东财行情域封禁的直接成因，实测为 IP 级封禁、浏览器指纹伪装
        无效）。GBK 解码 + `~` 分割，字段位序见 _TENCENT_FIELD_IDX（金样本单测
        钉死）。总市值/流通市值单位为亿，×1e8 归一到元。解析失败/关键字段缺失
        返回 None 触发既有回退链，MUST NOT 抛异常。
        """
        symbol = self._to_sina_symbol(stock_code)
        try:
            resp = requests.get(f"https://qt.gtimg.cn/q={symbol}", timeout=10)
            text = resp.content.decode("gbk")
        except Exception as e:
            logger.warning("腾讯行情直查失败: %s %s", stock_code, e)
            return None
        fields = text.split("~")
        if len(fields) <= max(_TENCENT_FIELD_IDX.values()):
            logger.warning("腾讯行情串字段不足（%d 段）: %s", len(fields), stock_code)
            return None
        result: dict = {"name": fields[_TENCENT_FIELD_IDX["name"]], "code": stock_code}
        for key, idx in _TENCENT_FIELD_IDX.items():
            if key in ("name", "code"):  # code 保持入参字符串，不走 float 转换
                continue
            raw = fields[idx].strip()
            if not raw:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            result[key] = value * 1e8 if key in _TENCENT_YI_FIELDS else value
        missing = [k for k in _TENCENT_REQUIRED if result.get(k) is None]
        if missing:
            logger.warning("腾讯行情关键字段缺失 %s: %s", missing, stock_code)
            return None
        return result

    def _quote_fallback_baidu_tx(self, stock_code: str) -> dict:
        """第二回退：百度估值补 market_cap/PB + 腾讯日线补 price（原二级回退语义）。

        各源独立 try/except：任一失败不影响其余；全部失败仅名称时留 ERROR
        （维度缺失可观测）。百度总市值单位亿元 ×1e8 归一到元（终审 C1）。
        """
        result = self._fetch_name_fallback(stock_code)
        if result:
            result["code"] = stock_code
        for indicator, key, scale in (("总市值", "market_cap", 1e8), ("市净率", "PB", 1.0)):
            try:
                df_val = _call_ak(
                    ak.stock_zh_valuation_baidu, symbol=stock_code, indicator=indicator
                )
                if df_val is not None and not df_val.empty and "value" in df_val.columns:
                    result[key] = float(df_val.iloc[-1]["value"]) * scale
            except Exception:
                logger.warning("百度估值 %s 拉取失败: %s", indicator, stock_code)
        try:
            df_tx = _call_ak(
                ak.stock_zh_a_hist_tx, symbol=self._to_sina_symbol(stock_code), adjust="qfq"
            )
            if df_tx is not None and not df_tx.empty and "close" in df_tx.columns:
                result["price"] = float(df_tx.iloc[-1]["close"])
        except Exception:
            logger.warning("腾讯日线价格拉取失败: %s", stock_code)
        has_valuation = any(result.get(k) is not None for k in ("market_cap", "PB", "price"))
        self.sources_seen["market_cap"].add(
            "baidu" if result.get("market_cap") is not None else "missing"
        )
        if not has_valuation:
            logger.error("行情回退后仍缺估值/价格（PE/PB/市值/价格缺失）: %s", stock_code)
        return result

    def _quote_via_chain(
        self, stock_code: str, spot_df: pd.DataFrame | None = None
    ) -> tuple[dict, pd.DataFrame | None]:
        """单标的完整行情链：腾讯主源 → 东财 spot（惰性共享）→ 百度+腾讯日线。

        spot_df 语义：None=未尝试（首次需要时拉取一次并传出供批调用复用）；
        空 DataFrame=已尝试且失败（哨兵，批调用不重复拉）。
        """
        q = self._fetch_tencent_quote(stock_code)
        if q is not None:
            self.sources_seen["market_cap"].add("tencent")
            return q, spot_df
        if spot_df is None:
            try:
                pulled = _call_ak(ak.stock_zh_a_spot_em)
                spot_df = pulled if pulled is not None else pd.DataFrame()
            except Exception as e:
                logger.warning("东财行情表拉取失败: %s", e)
                spot_df = pd.DataFrame()
        if not spot_df.empty:
            mapped = self._quote_from_spot_df(spot_df, stock_code)
            if mapped is not None:
                self.sources_seen["market_cap"].add(
                    "eastmoney" if mapped.get("market_cap") is not None else "missing"
                )
                return mapped, spot_df
        logger.warning("腾讯/东财行情均不可用，尝试百度估值+腾讯日线回退: %s", stock_code)
        return self._quote_fallback_baidu_tx(stock_code), spot_df

    def fetch_stock_quote(self, stock_code: str) -> dict:
        """个股行情（update-quote-primary-source 三级链）。

        主源：腾讯 qt.gtimg.cn 单标的直查（1 请求/标的）。东财全市场 spot
        翻页降为第一回退（仅主源失败时触发；该抓取模式是本机 IP 被东财
        行情域封禁的直接成因——实测 IP 级封禁，非代码注释早先猜测的 TLS
        指纹风控，浏览器指纹伪装无效）。第二回退：百度估值+腾讯日线。
        """
        q, _ = self._quote_via_chain(stock_code)
        return q

    def fetch_quarterly_income(self, stock_code: str, quarters: int = 4) -> pd.DataFrame:
        """拉取单季度利润表，计算同比/环比变化率。

        使用 stock_profit_sheet_by_quarterly_em（东方财富），返回数据中的
        PARENT_NETPROFIT 为单季度归母净利润。

        返回列：报告日, 季度, 归母净利润(单季), 营业收入(单季), 营业成本(单季), 环比, 同比, 营收同比
        营收同比在宽窗口（截断前）计算后随行携带——compute 层只见最近 quarters
        季，结构性找不到去年同期，必须在此处（head(quarters) 之前）算好。
        """
        # symbol 需要大写 SH/SZ 前缀
        prefix = "SH" if stock_code.startswith(("6", "9")) else "SZ"
        symbol = f"{prefix}{stock_code.lstrip('sh').lstrip('sz')}"

        df = _call_ak(ak.stock_profit_sheet_by_quarterly_em, symbol=symbol)
        if df is None or df.empty or "PARENT_NETPROFIT" not in df.columns:
            raise ValueError(f"股票 {stock_code} 季度利润表数据不可用")

        # 按报告日排序（最新的在前），copy 避免 fragmentation warning
        df = df.sort_values("REPORT_DATE", ascending=False).reset_index(drop=True).copy()

        # 过滤掉未来季度（报告日晚于当前日期）
        from datetime import datetime

        today = datetime.now()
        df["_dt"] = pd.to_datetime(df["REPORT_DATE"])
        df = df[df["_dt"] <= today].reset_index(drop=True)

        # 提取季度标签 (YYYY-Qx)
        def _quarter_label(dt) -> str:
            month = dt.month
            year = dt.year
            q = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}.get(month, "")
            return f"{year}{q}"

        df["季度"] = df["_dt"].apply(_quarter_label)
        df = df[df["季度"] != ""]  # 过滤非季度报告

        # 取最近 N 个季度
        df = df.head(quarters * 2 + 2)  # 多取一些用于计算同比

        # 构建结果
        records = []
        for _i, row in df.iterrows():
            curr_np = row["PARENT_NETPROFIT"]
            if pd.isna(curr_np):
                continue
            qoq = row.get("PARENT_NETPROFIT_QOQ")

            # 计算同比：找去年同期（同季度标签，上一年）
            curr_q = row["季度"]
            curr_year = int(curr_q[:4])
            prev_year_q = f"{curr_year - 1}{curr_q[4:]}"
            prev_rows = df[df["季度"] == prev_year_q]
            yoy = None
            rev_yoy = None
            if not prev_rows.empty:
                prev_np = prev_rows.iloc[0]["PARENT_NETPROFIT"]
                if not pd.isna(prev_np) and prev_np != 0:
                    yoy = (float(curr_np) - float(prev_np)) / abs(float(prev_np)) * 100
                # 营收同比：复用同一去年同期行（列可能缺失或值为 NaN/None，均需防）
                prev_rev = prev_rows.iloc[0].get("OPERATE_INCOME")
                curr_rev = row.get("OPERATE_INCOME")
                if (
                    prev_rev is not None
                    and curr_rev is not None
                    and not pd.isna(prev_rev)
                    and not pd.isna(curr_rev)
                    and prev_rev != 0
                ):
                    rev_yoy = (float(curr_rev) - float(prev_rev)) / abs(float(prev_rev)) * 100

            rev = row.get("OPERATE_INCOME")
            cost = row.get("OPERATE_COST")
            records.append(
                {
                    "报告日": str(row["REPORT_DATE"])[:10],
                    "季度": curr_q,
                    "归母净利润(单季)": float(curr_np),
                    "营业收入(单季)": float(rev) if not pd.isna(rev) else None,
                    "营业成本(单季)": float(cost) if not pd.isna(cost) else None,
                    "环比": float(qoq) if not pd.isna(qoq) else None,
                    "同比": yoy,
                    "营收同比": round(rev_yoy, 2) if rev_yoy is not None else None,
                }
            )

        result = pd.DataFrame(records)
        # 只保留最近 N 个季度
        result = result.head(quarters)
        return self._normalize_nan(result)

    # ── 最新报告期快照（update-financial-freshness-and-valuation Task 3）──

    # 报告日尾码 → 期类型
    _PERIOD_TYPE = {"1231": "年报", "0630": "中报", "0331": "一季报", "0930": "三季报"}

    @staticmethod
    def _compact_date(v) -> str:
        """报告日归一化为紧凑 YYYYMMDD 字符串。

        新浪接口返回 "20260630"，部分源/夹具用 "2026-06-30" 或 date 对象——
        统一归一后再做排序与同期匹配，避免两种格式混用时匹配失效。
        """
        digits = re.sub(r"\D", "", str(v))
        return digits[:8]

    @staticmethod
    def _yi(v) -> float | None:
        """元 → 亿元，round 2；缺失返回 None。"""
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return round(float(v) / 1e8, 2)

    def fetch_latest_period_snapshot(self, stock_code: str) -> dict:
        """最新报告期快照（不限年报）：中报/季报关键科目 + 同比，供基本面分析消费。

        金额单位亿元、比率%；同期数据缺失时同比为 None 并在 missing 标注。
        利润表整体不可用时 raise（由 fetch 层降级）；资产负债表缺失降级为部分快照。
        """
        stock = _add_prefix(stock_code)
        inc = _sina_report(stock, "利润表")
        if inc.empty:
            raise ValueError(f"股票 {stock_code} 利润表数据不可用")
        inc = self._rename_parent_cols(inc).copy()
        inc["_ymd"] = inc["报告日"].map(self._compact_date)
        inc = inc.sort_values("_ymd", ascending=False).reset_index(drop=True)
        latest = inc.iloc[0]
        ymd = str(latest["_ymd"])
        report_date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        period_type = self._PERIOD_TYPE.get(ymd[4:], "定期报告")

        missing: list[str] = []

        raw_rev = latest.get("营业总收入")
        raw_cost = latest.get("营业成本")
        raw_np = latest.get("归母净利润") or latest.get("归属于母公司股东的净利润")
        revenue = self._yi(raw_rev)
        cost = self._yi(raw_cost)
        np_attr = self._yi(raw_np)

        # 展示精度 4 位（update-sell-action-typing 同期 #190 修正）：同比与下游 LLM
        # 复算须可对账——2 位舍入基期 0.94 会把 688072 归母同比算成 1328.72%
        # （精确 1324.10%），精度链断裂即多口径并存
        def _yi4(v: object) -> float | None:
            # round 6 = 元级精度：下游 LLM 用展示值复算同比须能与 raw 同比值对上
            # （round 4 的 0.9429 会再引入 2e-5 舍入，复算得 1324.07 ≠ 1324.1）
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            return round(float(v) / 1e8, 6)

        # 毛利率按原始值计算（先 round 成亿元再算比值会引入截断误差）；
        # revenue/cost 守卫排除缺失与零营收，raw 守卫收窄 pandas-stubs 的 Any|None
        gross_margin: float | None = None
        if (
            revenue not in (None, 0)
            and cost is not None
            and raw_rev is not None
            and raw_cost is not None
        ):
            gross_margin = round((1 - raw_cost / raw_rev) * 100, 2)
        if gross_margin is None:
            missing.append("毛利率")

        # 同期数据（上一年同月日，如 20260630 → 20250630）
        prior_mask = (inc["_ymd"].str.slice(4) == ymd[4:]) & (
            inc["_ymd"].str.slice(0, 4) == str(int(ymd[:4]) - 1)
        )
        prior_rows = inc[prior_mask]
        prior_rev: float | None = None
        prior_np: float | None = None
        if not prior_rows.empty:
            prior = prior_rows.iloc[0]
            prior_rev = _yi4(prior.get("营业总收入"))
            prior_np = _yi4(prior.get("归母净利润") or prior.get("归属于母公司股东的净利润"))
        if prior_rev is None and prior_np is None:
            # 无同期行，或同期行存在但值全缺（审查 M2）——同比无从计算，均须标注
            missing.append("上年同期数据缺失")
        # 同比按原始元值计算（与毛利率 raw 同款，update-sell-action-typing 期 #190
        # 修正）：舍入亿元值相除引入基期截断误差（688072 实证 1328.72% vs 精确
        # 1324.10%），先 round 再算比值是错误顺序
        rev_yoy: float | None = None
        if raw_rev is not None and prior_rev is not None and prior_rev != 0:
            rev_yoy = round((float(raw_rev) / 1e8 - prior_rev) / abs(prior_rev) * 100, 2)
        np_yoy: float | None = None
        if raw_np is not None and prior_np is not None and prior_np != 0:
            np_yoy = round((float(raw_np) / 1e8 - prior_np) / abs(prior_np) * 100, 2)

        # 资产负债表（取同一报告日；该期缺失取最新一期并标注）
        snap: dict = {
            "报告日": report_date,
            "期类型": period_type,
            "营业总收入(累计)": revenue,
            "归母净利润(累计)": np_attr,
            "营业成本(累计)": cost,
            "毛利率(%)": gross_margin,
            "资产负债率(%)": None,
            "存货": None,
            "合同负债": None,
            "上年同期营业总收入": prior_rev,
            "上年同期归母净利润": prior_np,
            "营收同比(%)": rev_yoy,
            "归母净利同比(%)": np_yoy,
            "missing": missing,
        }

        bs = _sina_report(stock, "资产负债表")
        if not bs.empty:
            bs = bs.copy()
            bs["_ymd"] = bs["报告日"].map(self._compact_date)
            bs = bs.sort_values("_ymd", ascending=False).reset_index(drop=True)
            bs_rows = bs[bs["_ymd"] == ymd]
            if bs_rows.empty:
                # 无同报告日行：回退取最新一期，但必须标注（审查 M1）——邻期值不得伪装同期值
                row_bs = bs.iloc[0]
                missing.append("资产负债表非同期（取最新一期）")
            else:
                row_bs = bs_rows.iloc[0]
            assets = self._yi(row_bs.get("资产总计"))
            liab = self._yi(row_bs.get("负债合计"))
            snap["存货"] = self._yi(row_bs.get("存货"))
            snap["合同负债"] = self._yi(row_bs.get("合同负债"))
            if assets is not None and assets != 0 and liab is not None:
                snap["资产负债率(%)"] = round(liab / assets * 100, 2)
            else:
                missing.append("资产负债率")
            if snap["存货"] is None:
                missing.append("存货")
            if snap["合同负债"] is None:
                missing.append("合同负债")
        else:
            missing.extend(["资产负债率", "存货", "合同负债"])
        return snap

    def fetch_industry_pe(self, stock_code: str) -> dict | None:
        """获取个股所属行业的平均静态PE。
        通过 stock_individual_info_em 获取行业名称，再匹配
        stock_industry_pe_ratio_cninfo 的行业PE数据。
        如果任一环节失败返回 None，避免阻塞主流程。
        """
        try:
            # 1. 获取行业名称
            info_df = _call_ak(ak.stock_individual_info_em, symbol=stock_code)
            if info_df is None or info_df.empty:
                return None
            industry_name = None
            for _, row in info_df.iterrows():
                if row["item"] == "行业":
                    industry_name = row["value"]
                    break
            if not industry_name:
                return None

            # 2. 获取全部行业PE（证监会行业分类）
            from datetime import datetime

            date_str = datetime.now().strftime("%Y%m%d")
            pe_df = _call_ak(
                ak.stock_industry_pe_ratio_cninfo, symbol="证监会行业分类", date=date_str
            )
            if pe_df is None or pe_df.empty:
                return None
            # 列名映射
            pe_df = pe_df.rename(columns=_INDUSTRY_PE_KEY_MAP)

            # 3. 模糊匹配行业名称
            matched = pe_df[pe_df["industry_name"].str.contains(industry_name, na=False)]
            if matched.empty:
                return None

            row = matched.iloc[0]
            return {
                "industry_name": row.get("industry_name"),
                "avg_pe": row.get("avg_pe"),
                "median_pe": row.get("median_pe"),
                "weighted_pe": row.get("weighted_pe"),
                "company_count": row.get("company_count"),
            }
        except Exception:
            # 网络异常或 AKShare 接口变更时不阻塞主流程
            return None

    def fetch_kline(self, stock_code: str, days: int = 250, *, adjust: str = "qfq") -> pd.DataFrame:
        """拉取个股日 K 线，返回最近 N 个交易日。

        `adjust`：复权口径，透传三源（东财/新浪/腾讯）。
        - 默认 ``"qfq"``（前复权）= 管线分析输入口径，向后兼容不变。
        - 结算/回测路径显式传 ``SETTLEMENT_ADJUST``（后复权 hfq）——as-of 保真：
          后复权历史价 = 当时真实成交价，前复权会随最新除权事件重算历史价而引入未来信息。

        优先使用 stock_zh_a_hist（东方财富），失败时回退 stock_zh_a_daily（新浪），
        再回退 stock_zh_a_hist_tx（腾讯）。
        返回统一中文列名：日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 换手率
        """
        from datetime import datetime, timedelta

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        # ── 方案1: 东方财富 stock_zh_a_hist（仅 1 次重试） ──
        df = _call_ak(
            ak.stock_zh_a_hist,
            symbol=stock_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if df is not None and not df.empty:
            df = df.sort_values("日期").reset_index(drop=True)
            return df.tail(days).reset_index(drop=True)

        # ── 方案2: 新浪 stock_zh_a_daily（回退，仅 1 次重试） ──
        logger.info("东财K线拉取失败，尝试新浪源: %s", stock_code)
        sina_symbol = self._to_sina_symbol(stock_code)
        df = _call_ak(ak.stock_zh_a_daily, symbol=sina_symbol, adjust=adjust)
        if df is not None and not df.empty:
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "close": "收盘",
                "high": "最高",
                "low": "最低",
                "volume": "成交量",
                "amount": "成交额",
                "turnover": "换手率",
            }
            df = df.rename(columns=rename_map)
            df = df.sort_values("日期").reset_index(drop=True)
            return df.tail(days).reset_index(drop=True)

        # ── 方案3: 腾讯 stock_zh_a_hist_tx（二级回退，独立链路） ──
        logger.info("新浪K线拉取失败，尝试腾讯源: %s", stock_code)
        df = _call_ak(ak.stock_zh_a_hist_tx, symbol=sina_symbol, adjust=adjust)
        if df is not None and not df.empty:
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "close": "收盘",
                "high": "最高",
                "low": "最低",
                "volume": "成交量",
                "amount": "成交额",
                "turnover": "换手率",
            }
            df = df.rename(columns=rename_map)
            df = df.sort_values("日期").reset_index(drop=True)
            return df.tail(days).reset_index(drop=True)

        logger.error("K线拉取均失败: %s", stock_code)
        return pd.DataFrame()

    @staticmethod
    def _to_sina_symbol(stock_code: str) -> str:
        """A 股代码转新浪格式：600519 → sh600519, 000001 → sz000001。"""
        if stock_code.startswith(("6", "9")):
            return f"sh{stock_code}"
        elif stock_code.startswith(("0", "2", "3")):
            return f"sz{stock_code}"
        elif stock_code.startswith(("4", "8")):
            return f"bj{stock_code}"
        return f"sh{stock_code}"

    @staticmethod
    def _to_sina_index_symbol(index_code: str) -> str:
        """指数代码转新浪格式：000300 → sh000300, 399001 → sz399001。"""
        if index_code.startswith("399"):
            return f"sz{index_code}"
        return f"sh{index_code}"

    def fetch_index_kline(self, index_code: str, days: int = 250) -> pd.DataFrame:
        """拉取指数日 K 线，返回最近 N 个交易日。

        优先使用 index_zh_a_hist（东方财富），失败时回退 stock_zh_index_daily（新浪）。
        返回统一中文列名：日期, 开盘, 收盘, 最高, 最低, 成交量。
        """
        from datetime import datetime, timedelta

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        # ── 方案1: 东方财富 index_zh_a_hist ──
        df = _call_ak(
            ak.index_zh_a_hist,
            symbol=index_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
        )
        if df is not None and not df.empty:
            df = df.sort_values("日期").reset_index(drop=True)
            return df.tail(days).reset_index(drop=True)

        # ── 方案2: 新浪 stock_zh_index_daily（回退） ──
        logger.info("东财指数K线拉取失败，尝试新浪源: %s", index_code)
        sina_symbol = self._to_sina_index_symbol(index_code)
        df = _call_ak(ak.stock_zh_index_daily, symbol=sina_symbol)
        if df is not None and not df.empty:
            rename_map = {
                "date": "日期",
                "open": "开盘",
                "close": "收盘",
                "high": "最高",
                "low": "最低",
                "volume": "成交量",
            }
            df = df.rename(columns=rename_map)
            df = df.sort_values("日期").reset_index(drop=True)
            return df.tail(days).reset_index(drop=True)

        logger.error("指数 K线拉取均失败: %s", index_code)
        return pd.DataFrame()

    def fetch_benchmark_kline(self, days: int = 250) -> pd.DataFrame:
        """沪深 300 日 K(fetch_index_kline 的 000300 特化,行为与原来一致)。"""
        return self.fetch_index_kline("000300", days=days)

    def fetch_bond_yield_curve(self, start_date: str, end_date: str) -> pd.DataFrame | None:
        """中债国债收益率曲线日频读数（update-risk-free-rate-source）。

        源：中债估值中心（yield.chinabond.com.cn，经 ak.bond_china_yield），
        非东财域。返回列 日期(str[:10])/1年(float, 百分数——年化小数转换由
        track_record.risk_free 层负责，与本文件其余 fetch_* 返回原单位的约定一致）。
        仅取「中债国债收益率曲线」；空结果/调用失败返回 None。
        """
        df = _call_ak(ak.bond_china_yield, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return None
        df = df[df["曲线名称"] == "中债国债收益率曲线"]
        if df.empty:
            return None
        out = pd.DataFrame(
            {
                "日期": df["日期"].astype(str).str[:10],
                "1年": df["1年"].astype(float),
            }
        )
        return out.dropna(subset=["1年"]).sort_values("日期").reset_index(drop=True)

    @staticmethod
    def _parse_constituents(df: pd.DataFrame | None) -> list[dict[str, str]]:
        """从指数成分 DataFrame 提取 [{"ticker","name"}]。

        ticker 统一为 6 位数字（去交易所后缀 / sh|sz 前缀）。列名不可识别时返回 []。
        """
        if df is None or getattr(df, "empty", True):
            return []
        cols = list(df.columns)
        code_col = next((c for c in _CONSTITUENT_CODE_COLS if c in cols), None)
        name_col = next((c for c in _CONSTITUENT_NAME_COLS if c in cols), None)
        if code_col is None or name_col is None:
            return []
        rows: list[dict[str, str]] = []
        for _, row in df.iterrows():
            match = re.search(r"\d{6}", str(row[code_col]))
            if not match:
                continue
            rows.append({"ticker": match.group(0), "name": str(row[name_col]).strip()})
        return rows

    def fetch_index_constituents(self, index_code: str = "000300") -> list[dict[str, str]]:
        """指数成分券清单（中证官网优先，东财回退）。返回 [{"ticker","name"}]。

        ticker 为 6 位数字（去交易所后缀）。两源均失败/列名漂移时返回 [] 并留 ERROR。

        实测列名（2026-09-23，akshare 1.18.94，symbol="000300"，各 300 行）：
        - ak.index_stock_cons_csindex(symbol=...)：'成分券代码' / '成分券名称'
          （另有 日期/指数代码/指数名称/成分券英文名称/交易所 等）
        - ak.index_stock_cons(symbol=...)：'品种代码' / '品种名称'（另有 '纳入日期'）
        """
        for label, func in (
            ("index_stock_cons_csindex", ak.index_stock_cons_csindex),
            ("index_stock_cons", ak.index_stock_cons),
        ):
            df = _call_ak(func, symbol=index_code)
            rows = self._parse_constituents(df)
            if rows:
                source = "csindex" if label == "index_stock_cons_csindex" else "eastmoney"
                self.sources_seen["constituents"].add(source)
                return rows
            logger.warning("指数成分接口 %s 无可用数据（空返回/列名漂移）: %s", label, index_code)
        logger.error("指数成分双源均失败（成分信源缺失）: %s", index_code)
        self.sources_seen["constituents"].add("none")
        return []

    # ── 宏观指标 ──

    @staticmethod
    def _with_freshness(key: str, records: list[dict]) -> dict:
        """为指标 records 附加 as_of_date + freshness。

        最新日期取自 records[0] 首列（已降序）；解析失败按 stale 处理（fail-safe）。
        """
        raw = records[0].get(next(iter(records[0])))  # 首列的键值
        as_of = None
        if isinstance(raw, str):
            import re

            m = re.search(r"(\d{4})[-年/.]?(\d{1,2})", raw)
            if m:
                try:
                    as_of = date(int(m.group(1)), int(m.group(2)), 1)
                except ValueError:
                    as_of = None
            else:
                as_of = None
        elif isinstance(raw, (date, datetime)):
            as_of = raw.date() if isinstance(raw, datetime) else raw
        if as_of is None:
            logger.warning("macro %s as_of_date 解析失败，按 stale 处理", key)
        days = (datetime.now().date() - as_of).days if as_of else 10**9
        return {
            "as_of_date": as_of.isoformat() if as_of else None,
            "freshness": "fresh" if days <= 90 else "stale",
            "records": records,
        }

    def fetch_macro_indicators(self) -> dict[str, list[dict] | dict]:
        """拉取宏观经济指标：CPI、PMI、M2、LPR。

        返回 dict，每个指标取最近 6 个月数据，并附加 as_of_date + freshness
        时效守卫（90 天界，各指标独立计算）。
        失败的指标返回空列表，不阻塞流程。
        """
        result: dict[str, list[dict] | dict] = {}

        def _safe_macro(key: str, func) -> None:
            df = _call_ak(func)
            if df is not None and not df.empty:
                # akshare 宏观接口为降序（最新在前）；显式按首列（月份/日期）降序排序，
                # 保证 index 0 = 最新，对齐财务数据「降序、head=最新」约定
                # （不依赖 akshare 隐藏顺序）。
                df = df.sort_values(df.columns[0], ascending=False).reset_index(drop=True)
                records = df.head(6).to_dict(orient="records")
                # 序列化 date/datetime 对象，避免 JSON 编码失败
                for r in records:
                    for k, v in r.items():
                        if isinstance(v, (date, datetime)):
                            r[k] = v.isoformat()
                # ── 时效守卫：as_of_date + freshness（90 天界，各指标独立）──
                result[key] = self._with_freshness(key, records)
            else:
                # 可观测性（2026-09-08 审计）：宏观指标失败/空返回时此前静默置 []
                # ——宏观分析师看不到该维度却无日志可查。空返回 MUST 留 ERROR。
                logger.error("宏观指标 %s 拉取失败/返回空", key)
                result[key] = []

        _safe_macro("cpi", ak.macro_china_cpi)
        _safe_macro("pmi", ak.macro_china_pmi)
        _safe_macro("m2", ak.macro_china_money_supply)
        _safe_macro("lpr", ak.macro_china_lpr)

        return result

    # ── 新闻资讯 ──

    def fetch_news(self, stock_code: str, limit: int = 20) -> list[dict]:
        """拉取个股新闻资讯（东方财富）。

        返回 list[dict]，每条含 title、content、datetime、source。
        失败返回空列表。
        """
        df = _call_ak(ak.stock_news_em, symbol=stock_code)
        if df is None or df.empty:
            # 可观测性（2026-09-08 审计）：新闻是舆情分析师唯一数据源，失败静默
            # 返回 [] 会让舆情维度无数据却无日志可查。空返回 MUST 留 ERROR。
            logger.error("个股新闻拉取失败/返回空（舆情数据缺失）: %s", stock_code)
            return []
        df = df.head(limit)
        # 标准化列名
        col_map = {
            "新闻标题": "title",
            "新闻内容": "content",
            "发布时间": "datetime",
            "文章来源": "source",
            "新闻链接": "url",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        records = df.to_dict(orient="records")
        # 序列化 date/datetime 对象
        for r in records:
            for k, v in r.items():
                if isinstance(v, (date, datetime)):
                    r[k] = v.isoformat()
            r.setdefault("title", "")
            r.setdefault("content", "")
            r.setdefault("datetime", "")
            r.setdefault("source", "")
        return records

    def fetch_announcements(self, stock_code: str, days: int = 180) -> list[dict]:
        """拉取个股公告列表（巨潮 cninfo，近 days 天）。

        返回 list[dict]：title/date/category/url。失败或空返回 []（降级语义同 fetch_news）。
        """
        end = date.today()
        start = end - timedelta(days=days)
        df = _call_ak(
            ak.stock_zh_a_disclosure_report_cninfo,
            symbol=stock_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            logger.error("公告列表拉取失败/返回空（公告信源缺失）: %s", stock_code)
            return []
        rows: list[dict] = []
        for _, row in df.head(10).iterrows():
            rows.append(
                {
                    "title": str(row.get("公告标题") or ""),
                    "date": str(row.get("公告日期") or ""),
                    "category": str(row.get("公告类型") or ""),
                    "url": str(row.get("网址") or ""),
                }
            )
        return rows

    def fetch_research_reports(self, stock_code: str) -> list[dict]:
        """拉取个股券商研报列表（东财）。

        返回 list[dict]：title/org/rating/target_price/date。目标价缺失为 None。
        失败或空返回 []。
        """
        df = _call_ak(ak.stock_research_report_em, symbol=stock_code)
        if df is None or df.empty:
            logger.error("券商研报拉取失败/返回空（研报信源缺失）: %s", stock_code)
            return []
        rows: list[dict] = []
        for _, row in df.head(8).iterrows():
            target = row.get("目标价")
            rows.append(
                {
                    "title": str(row.get("报告名称") or ""),
                    "org": str(row.get(" research机构") or row.get("research机构") or ""),
                    "rating": str(row.get("东财评级") or ""),
                    "target_price": float(target) if pd.notna(target) else None,
                    "date": str(row.get("报告日期") or ""),
                }
            )
        return rows

    def fetch_share_unlock(self, stock_code: str) -> list[dict]:
        """拉取个股限售解禁排队（东财）。

        返回 list[dict]：date/shares/market_value/pct_float。失败或空返回 []。
        """
        df = _call_ak(ak.stock_restricted_release_queue_em, symbol=stock_code)
        if df is None or df.empty:
            logger.error("限售解禁拉取失败/返回空（解禁信源缺失）: %s", stock_code)
            return []
        rows: list[dict] = []
        for _, row in df.head(8).iterrows():
            rows.append(
                {
                    "date": str(row.get("解禁时间") or ""),
                    "shares": row.get("解禁数量"),
                    "market_value": row.get("实际解禁市值"),
                    "pct_float": row.get("占解禁前流通市值比例"),
                }
            )
        return rows

    def fetch_block_trades(self, stock_code: str, days: int = 30) -> list[dict]:
        """拉取个股大宗交易明细（东财按日期区间拉全市场后过滤，近 days 天）。

        返回 list[dict]：date/price/volume/amount/premium/buyer/seller。失败或空返回 []。
        """
        end = date.today()
        start = end - timedelta(days=days)
        df = _call_ak(
            ak.stock_dzjy_mrmx,
            symbol="A股",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            logger.error("大宗交易拉取失败/返回空（大宗信源缺失）: %s", stock_code)
            return []
        if "股票代码" not in df.columns:
            # 空数据日/维护窗口信源会返回非空但缺列的变体——按契约降级 []，与真空结果区分记录
            logger.warning("大宗交易信源返回缺「股票代码」列（空数据日变体）: %s", stock_code)
            return []
        df = df[df["股票代码"].astype(str) == stock_code]
        rows: list[dict] = []
        for _, row in df.head(10).iterrows():
            rows.append(
                {
                    "date": str(row.get("交易日期") or ""),
                    "price": row.get("成交价格"),
                    "volume": row.get("成交量"),
                    "amount": row.get("成交金额"),
                    "premium": row.get("溢价率"),
                    "buyer": str(row.get("买方营业部") or ""),
                    "seller": str(row.get("卖方营业部") or ""),
                }
            )
        return rows

    def fetch_peer_data(self, stock_codes: list[str]) -> pd.DataFrame | None:
        """逐标的抓取同业名称/PE/PB（复用 _quote_via_chain 三级链）。

        单标的失败或无 PE/PB 跳过不拖垮整批；全部失败或输入空返回 None。
        spot 表惰性共享：腾讯主源健康时逐标的 1 请求、零 spot 调用；腾讯
        失败后首个标的触发一次全市场 spot 拉取，批内复用（N 标的 1×）。
        """
        codes = [str(c).strip() for c in (stock_codes or []) if str(c).strip()]
        if not codes:
            return None
        spot_df: pd.DataFrame | None = None
        rows: list[dict] = []
        for code in codes:
            try:
                q, spot_df = self._quote_via_chain(code, spot_df)
            except Exception as e:
                logger.warning("同业 %s 行情抓取失败，跳过: %s", code, e)
                continue
            pe = q.get("PE") or q.get("pe")
            pb = q.get("PB") or q.get("pb")
            # 停牌 peer 的 NaN PE/PB（东财 spot 实测行为）按缺数归一——NaN 真值
            # 直通会毒化同业均值（终审 C1 同源问题）
            if pe is not None and pd.isna(pe):
                pe = None
            if pb is not None and pd.isna(pb):
                pb = None
            if pe is None and pb is None:
                logger.warning("同业 %s 无 PE/PB（全回退失败），跳过", code)
                continue
            rows.append({"name": q.get("name") or code, "code": code, "PE": pe, "PB": pb})
        if not rows:
            return None
        # 出口根因归一（终审 C1）：混合行 DataFrame 构造把 None 强转回 float64
        # NaN 毒化同业均值——出口必须 _normalize_nan
        return self._normalize_nan(pd.DataFrame(rows, columns=["name", "code", "PE", "PB"]))
