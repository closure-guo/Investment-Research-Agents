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
        return df.where(df.notna(), other=None)  # pyrefly: ignore[no-matching-overload]

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

    def _fetch_industry_cninfo(self, stock_code: str) -> str | None:
        """当东方财富接口不可用时，用 cninfo 获取行业名称（行业中类）。"""
        try:
            df = ak.stock_industry_change_cninfo(symbol=stock_code)
            if not df.empty and "行业中类" in df.columns:
                return str(df.iloc[0]["行业中类"])
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

    def fetch_stock_quote(self, stock_code: str) -> dict:
        # 主源：东方财富实时行情（含 PE/PB/市值/价格）
        df = _call_ak(ak.stock_zh_a_spot_em)
        if df is not None and not df.empty:
            # 尝试多种格式匹配（纯数字 / 带前缀）
            for code_key in (stock_code, stock_code.lstrip("sh").lstrip("sz")):
                row = df[df["代码"] == code_key]
                if not row.empty:
                    break
            if not row.empty:
                raw = row.iloc[0].to_dict()
                mapped = {_QUOTE_KEY_MAP.get(k, k): v for k, v in raw.items()}
                self.sources_seen["market_cap"].add(
                    "eastmoney" if mapped.get("market_cap") is not None else "missing"
                )
                return mapped

        # ── 二级回退（add-quote-baidu-fallback）：东财被 TLS 风控封锁时，
        # 用百度估值补 market_cap/PB、腾讯日线补 price——恢复估值与价格维度
        # （此前静默降级为仅名称，GARP/相对估值/图表全部丢失且无日志）。
        # PE 不在 quote 内推导（口径依赖财务数据，留给下游 compute/charts），
        # 各源独立 try/except：任一失败不影响其余，缺失字段由下游守卫跳过。
        logger.warning("东财行情不可用，尝试百度估值+腾讯日线回退: %s", stock_code)
        result = self._fetch_name_fallback(stock_code)
        if result:
            result["code"] = stock_code

        # 百度估值：总市值 → market_cap；市净率 → PB（各指标末行最新）
        for indicator, key in (("总市值", "market_cap"), ("市净率", "PB")):
            try:
                df_val = _call_ak(
                    ak.stock_zh_valuation_baidu, symbol=stock_code, indicator=indicator
                )
                if df_val is not None and not df_val.empty and "value" in df_val.columns:
                    result[key] = float(df_val.iloc[-1]["value"])
            except Exception:
                logger.warning("百度估值 %s 拉取失败: %s", indicator, stock_code)

        # 腾讯日线最新收盘 → price
        try:
            df_tx = _call_ak(
                ak.stock_zh_a_hist_tx, symbol=self._to_sina_symbol(stock_code), adjust="qfq"
            )
            if df_tx is not None and not df_tx.empty and "close" in df_tx.columns:
                result["price"] = float(df_tx.iloc[-1]["close"])
        except Exception:
            logger.warning("腾讯日线价格拉取失败: %s", stock_code)

        # 可观测性：估值/价格维度全部缺失时留 ERROR（数据维度缺失，非预期降级）
        has_valuation = any(result.get(k) is not None for k in ("market_cap", "PB", "price"))
        self.sources_seen["market_cap"].add(
            "baidu" if result.get("market_cap") is not None else "missing"
        )
        if not has_valuation:
            logger.error("行情回退后仍缺估值/价格（PE/PB/市值/价格缺失）: %s", stock_code)
        return result

    def fetch_quarterly_income(self, stock_code: str, quarters: int = 4) -> pd.DataFrame:
        """拉取单季度利润表，计算同比/环比变化率。

        使用 stock_profit_sheet_by_quarterly_em（东方财富），返回数据中的
        PARENT_NETPROFIT 为单季度归母净利润。

        返回列：报告日, 归母净利润(单季), 环比(%), 同比(%)
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
            if not prev_rows.empty:
                prev_np = prev_rows.iloc[0]["PARENT_NETPROFIT"]
                if not pd.isna(prev_np) and prev_np != 0:
                    yoy = (float(curr_np) - float(prev_np)) / abs(float(prev_np)) * 100

            records.append(
                {
                    "报告日": str(row["REPORT_DATE"])[:10],
                    "季度": curr_q,
                    "归母净利润(单季)": float(curr_np),
                    "环比": float(qoq) if not pd.isna(qoq) else None,
                    "同比": yoy,
                }
            )

        result = pd.DataFrame(records)
        # 只保留最近 N 个季度
        result = result.head(quarters)
        return self._normalize_nan(result)

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
