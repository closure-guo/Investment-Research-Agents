"""决策文本价位交叉校验器（update-decision-integrity-gates Task 1）。

spec price-level-tooling：决策层产出的自由文本价位（``reeval_triggers`` 条目与
``inaction_reason``/``reasoning`` 中出现的数值价位）由确定性代码与 state 中已验证的
技术指标值（最新收盘、MA5/10/20/60、布林轨道、近期高低点、价位参考带）交叉核对，
MUST NOT 仅因数值出现在决策文本即视为可信。两种 anomaly 形态（update-decision-price-gate 门禁化：调用方据 anomaly 打回重试/阻断）：

- **偏差形态**（deviation）：文本价位与归属指标已验证值偏差 > 2%
  （``_DEVIATION_THRESHOLD``），且不落在 ``price_levels`` 参考带内；
- **空洞形态**（empty_trigger）：上破类触发（站上/突破/收复/站稳 X）中 X 不高于
  最新收盘价，或下破类触发（跌破/回落至/失守 X）中 X 不低于最新收盘价——触发
  条件在当前时点已满足，不构成有效再评估门槛。空洞形态同样覆盖结构化触发位
  （add-watch-trigger-tracking：``trigger_high``/``trigger_low`` 字段，与文本
  来源同构、同门禁）。

归属规则：文本点名指标名（MA60/布林上轨/止损参考带上沿等）→ 直接取对应已验证值；
未点名 → 在全部已验证指标中取相对偏差最小者。非价格量纲（带 % 的降幅阈值、
赔率比值、0-1 区间小数、日期与指标名成分数字、周期/结算记号（T+20）、
RSI/赔率等关键词语境）不进入价位校验（600015 的 18.35% 阈值、「赔率修复至
1:1」不误报）。

「已站上/已跌破」为事实陈述而非再评估门槛，不构成空洞形态。处置语义由调用方（risk_judge 门禁回路）保证：anomaly 非空 → 打回重试一次 → 仍异常阻断；校验器自身异常由调用方 fail-open。本模块保持纯函数、不改写决策。
"""

from __future__ import annotations

import math
import re

from finance_agent.models import TradeDecision

# spec：偏差阈值默认 2%
_DEVIATION_THRESHOLD = 0.02

# 空洞形态方向词（spec：上破类「站上/突破/收复 X」/ 下破类「跌破/回落至 X」；
# 「放量突破」被「突破」覆盖；「站稳」为同族上破语义扩展；
# 「回撤至/回调至」为回踩语境同族下破词（fix-decision-price-check-false-positives，
# 688072 实证：缺失导致复合回踩触发的前半句无方向、后半句被误判空洞）
_BREAKOUT_WORDS: tuple[str, ...] = ("站上", "突破", "收复", "放量突破", "站稳")
_BREAKDOWN_WORDS: tuple[str, ...] = ("跌破", "回落至", "失守", "回撤至", "回调至")

# 方向词与数值的最大字符距离（词尾 → 数值起点；触发条目为 1-3 个短句）
_BREAK_WORD_WINDOW = 12

# 点名指标与数值的最大字符距离（任一方向）
_NAMED_WINDOW = 15

# 价格量纲窗口：数值须与基准价同数量级（±10 倍），排除日期年份/指数点位/宏观量级
_SCALE_RATIO = 10.0

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 日期整体排除（2026-09-30 / 2026年9月30日 / 9月30日）
_DATE_PATTERNS = (
    re.compile(r"\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?"),
    re.compile(r"\d{1,2}月\d{1,2}日?"),
    # 无年份的连字日期（fix-decision-price-enumerators，600026 实证「（09-30
    # neutral）」的 09/30 被误读为股价）；连字数值区间（「19-20」）同形——
    # 区间是多值语境而非单一价位，一并豁免（误杀由观测通道承担，好过阻断）
    re.compile(r"\d{1,2}-\d{1,2}"),
    # 破折号/波浪号数值区间（「2—3.5%」「19.99～20.10」）：与连字区间同策——
    # 多值语境而非单一价位，一并豁免（2026-10-13 宝信软件实证「止损距离仅约
    # 2—3.5%（估算值）」的 2 与止损参考带下沿 16.3771 偏差 87.79% → 打回重试）
    re.compile(r"\d+(?:\.\d+)?\s*[—–~～]\s*\d+(?:\.\d+)?"),
)
# 枚举序号整体排除（fix-decision-price-enumerators，600026 实证）：模型以
# 「（1）（2）（3）」给论点编号是固定行文格式，打回重试也换不掉——括号内
# 1-2 位裸整数是序号而非股价（600026 生产阻断消息「文本价位 3 与 近期低点
# 14.06 偏差 78.66%」即出自「（3）赔率1.56:1」）。仅匹配括号内无其他文字的
# 裸短整数，带小数点/上下文的括号价位（如「（13.0）」）不受豁免。
# 后括号形式「1) 2)」（issue #254，600515 实证「；1) 相对近窗…；2) 止损
# 距离…」）：负向后顾拦「2.6171）」的尾数（前邻数字/小数点不是序号起点），
# 真实括号价位（「（止损 9.99）」）不受豁免（反掩蔽锚见测试）。
_ENUM_SPAN_PATTERNS = (
    re.compile(r"[（(]\s*\d{1,2}\s*[)）]"),
    re.compile(r"(?<![\d.])\d{1,2}[)）]"),
)
# 指数名整体排除（add-horizon-semantics E2E 实证，600519 10-11 实跑）：
# 决策层周期契约注入后 reasoning 恒含「相对沪深300 超额收益」——300 被提为
# 价位与近期低点 1170.28 偏差 74.37% → 打回重试也无法去掉（prompt 要求写
# 考核基准）→ 报告阻断。量纲守卫 ±10 倍抓不住（300 vs 1170 = 3.9x），
# 关键词窗抓不住（「沪深」不在 _NON_PRICE_KEYWORDS）。指数名前缀直接跟随
# 的 2-4 位数字是指数点位而非个股股价；名称后隔开的真实价位不受豁免
# （护栏测试 TestIndexNameFalsePositives.test_real_price_after_index_mention_still_checked 钉死）。
# 名称与数字间空白不限个数（2026-10-13 同花顺实证：「沪深  300」多空格
# 排版变体从 \s? 单空格豁免区漏出，300 被提为价位偏差 22.21% 阻断）。
_INDEX_NAME_PATTERN = re.compile(
    r"(?:沪深|中证|上证|深证|北证|国证|恒生|纳斯达克|标普|道琼斯)\s*\d{2,4}"
)
# 周期/结算记号整体排除（2026-10-13/14 cohort 实证）：决策层周期契约要求
# reasoning 写「T+20 个交易日」考核语义（agent-prompt-contracts），规范形态
# 被量词后缀（个/日）豁免，但模型自由文本缩写形态（「T+20内」「T+20考核」
# 「T+20低概率尾部」「T+2（结算惯例）」）没有 → 20/2 被提为价位（同花顺
# 10-14 三处 89.19% 阻断；华夏银行/南方航空/海南机场 10-13 同族）→ 打回
# 重试仍然写 T+N（契约要求，换不掉）→ 恶化判定 → 报告阻断。记号后紧邻的
# 真实价位不受豁免（T+N 恒以汉字连接，数字不与记号粘连）。
_T_PLUS_PATTERN = re.compile(r"[Tt]\+\d{1,3}")
# 数值后紧跟（可隔空格）即非价格量纲：百分比/倍率/比值/成交量/日期时间单位/均线
# + 计数量词（fix-decision-price-enumerators：「3个月」「3成仓」的 3 不是股价；
# 计量字后接真实价位的表述不存在，不伤及正常价位提取；「家」为 2026-10-13
# 赛轮轮胎实证补充——「持股减少超 10 家」的 10 与止损参考带下沿 11.9029
# 偏差 15.99% → 报告阻断）+ 斜杠分数分子
# （600515 10-07 补跑实证「（1/3仓位…）」的 1 被提为价位偏差 61.79% 阻断）
_NON_PRICE_SUFFIX = "%％倍折万亿手天日均月年号时点分秒周季:：个家成档条项次笔轮层类种枚名步位线/"
# 数值前紧跟（可隔空格）即非价格上下文：比值冒号、序数、斜杠分数分母
# （「1/3」的 3；斜杠并列价位「19.99/20.10」一并豁免——与连字区间同策，
# 误杀由观测通道承担，好过阻断）
_NON_PRICE_PREFIX = ":：第/"
# 数值前窗（8 字符）内出现即非价格量纲：技术指标（RSI/KDJ…）、财务与比率语义词
_NON_PRICE_KEYWORDS: tuple[str, ...] = (
    "rsi",
    "kdj",
    "macd",
    "dif",
    "dea",
    "pe",
    "pb",
    "roe",
    "roa",
    "eps",
    "pmi",
    "cpi",
    "ppi",
    "gdp",
    "m2",
    "lpr",
    "beta",
    "var",  # VaR95/VaR(95% 风险度量语境（fix-decision-price-check-false-positives：
    # 688072 实证 95 曾被误读为股价）
    "在险价值",
    "换手",
    "量比",
    "赔率",
    "胜率",
    "概率",
    "置信",
    "分位",
    "涨幅",
    "跌幅",
    "振幅",
    "回撤",
    "增速",
    "增长",
    "收益率",
    "利率",
    "利润率",
    "毛利",
    "净利",
    "营收",
    "负债率",
    "估值",
    "市盈",
    "市净",
    "股息",
    "折价",
    "溢价",
    "评分",
    "得分",
    "占比",
    "份额",
    "回报",
    "波动率",
    "同比",
    "环比",
    "累计",
)

# 文本点名指标别名（小写匹配）→ (规范显示名, 已验证值表键)；长别名优先无实质影响
# （全部 occurrence 都收集），边界守卫防 MA5 命中 MA50、MA10 命中 MA100
_ALIAS_TABLE: tuple[tuple[str, str, str], ...] = (
    ("ma60", "MA60", "MA60"),
    ("ma 60", "MA60", "MA60"),  # 空格变体（fix-decision-price-enumerators：600845
    # 实证「MA 60」的 60 曾被误读为股价——整体入别名排除区并就近归属 MA60）
    ("ma20", "MA20", "MA20"),
    ("ma 20", "MA20", "MA20"),
    ("ma10", "MA10", "MA10"),
    ("ma 10", "MA10", "MA10"),
    ("ma5", "MA5", "MA5"),
    ("ma 5", "MA5", "MA5"),
    ("60日均线", "MA60", "MA60"),
    ("20日均线", "MA20", "MA20"),
    ("10日均线", "MA10", "MA10"),
    ("5日均线", "MA5", "MA5"),
    ("布林上轨", "布林上轨", "布林上轨"),
    ("上轨", "布林上轨", "布林上轨"),
    ("布林中轨", "布林中轨", "布林中轨"),
    ("中轨", "布林中轨", "布林中轨"),
    ("布林下轨", "布林下轨", "布林下轨"),
    ("下轨", "布林下轨", "布林下轨"),
    ("近期高点", "近期高点", "近期高点"),
    ("近期最高", "近期高点", "近期高点"),
    ("前高", "近期高点", "近期高点"),
    ("近期低点", "近期低点", "近期低点"),
    ("近期最低", "近期低点", "近期低点"),
    ("前低", "近期低点", "近期低点"),
    ("止损参考带上沿", "止损参考带上沿", "止损参考带上沿"),
    ("止损带上沿", "止损参考带上沿", "止损参考带上沿"),
    ("止损参考带下沿", "止损参考带下沿", "止损参考带下沿"),
    ("止损带下沿", "止损参考带下沿", "止损参考带下沿"),
    ("目标参考带上沿", "目标参考带上沿", "目标参考带上沿"),
    ("目标带上沿", "目标参考带上沿", "目标参考带上沿"),
    ("目标参考带下沿", "目标参考带下沿", "目标参考带下沿"),
    ("目标带下沿", "目标参考带下沿", "目标参考带下沿"),
    ("最新收盘", "最新收盘价", "最新收盘价"),
    ("收盘价", "最新收盘价", "最新收盘价"),
    ("现价", "最新收盘价", "最新收盘价"),
)


def _as_positive_finite(value: object) -> float | None:
    """正有限数值则窄化为 float；None/非数/NaN/≤0 一律不可用（不伪造）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) and converted > 0 else None


def _seq_last(seq: object) -> float | None:
    """calc_technical 序列末值（最新值）；非列表/空/预热段 None 不可用。"""
    if isinstance(seq, (list, tuple)) and seq:
        return _as_positive_finite(seq[-1])
    return None


def _verified_table(
    technical_indicators: dict,
    price_levels: dict,
    latest_close: float | None,
) -> dict[str, float]:
    """已验证指标值表（canonical 名 → 数值）：仅价格量纲，RSI/MACD/KDJ 不入表。"""
    table: dict[str, float] = {}

    def _put(name: str, value: object) -> None:
        converted = _as_positive_finite(value)
        if converted is not None:
            table[name] = converted

    ti = technical_indicators or {}
    ma = ti.get("MA") or {}
    _put("MA5", _seq_last(ma.get("5")))
    _put("MA10", _seq_last(ma.get("10")))
    _put("MA20", _seq_last(ma.get("20")))
    _put("MA60", _seq_last(ma.get("60")))
    boll = ti.get("BOLL") or {}
    _put("布林上轨", _seq_last(boll.get("upper")))
    _put("布林中轨", _seq_last(boll.get("middle")))
    _put("布林下轨", _seq_last(boll.get("lower")))

    levels = price_levels or {}
    if levels.get("available"):
        _put("近期高点", levels.get("recent_high"))
        _put("近期低点", levels.get("recent_low"))
        stop_band = levels.get("stop_band_long")
        if isinstance(stop_band, dict):
            _put("止损参考带上沿", stop_band.get("high"))
            _put("止损参考带下沿", stop_band.get("low"))
        target_band = levels.get("target_band_long")
        if isinstance(target_band, dict):
            _put("目标参考带上沿", target_band.get("high"))
            _put("目标参考带下沿", target_band.get("low"))
    # 最新收盘：调用方 kline 尾行优先；缺位时 entry_ref（工具参考入场基准=最新收盘）兜底
    close = _as_positive_finite(latest_close)
    if close is not None:
        table["最新收盘价"] = close
    else:
        entry_ref = _as_positive_finite(levels.get("entry_ref"))
        if entry_ref is not None:
            table["最新收盘价"] = entry_ref
    return table


def _reference_bands(price_levels: dict) -> list[tuple[float, float]]:
    """price_levels 参考带（支撑/压力带 + sanity 放宽带），不可用时无豁免区间。"""
    levels = price_levels or {}
    if not levels.get("available"):
        return []
    bands: list[tuple[float, float]] = []
    for key in ("stop_band_long", "target_band_long"):
        band = levels.get(key)
        if isinstance(band, dict):
            low, high = _as_positive_finite(band.get("low")), _as_positive_finite(band.get("high"))
            if low is not None and high is not None:
                bands.append((low, high))
    full_band = levels.get("full_band")
    if isinstance(full_band, (list, tuple)) and len(full_band) == 2:
        low, high = _as_positive_finite(full_band[0]), _as_positive_finite(full_band[1])
        if low is not None and high is not None:
            bands.append((low, high))
    return bands


def _in_reference_bands(value: float, bands: list[tuple[float, float]]) -> bool:
    return any(lo <= value <= hi for lo, hi in bands)


def _alias_boundary_ok(lower_text: str, start: int, end: int, alias: str) -> bool:
    """MA 别名边界守卫：MA5 不命中 MA50（后随数字）、不命中 XMA5（前邻 ASCII 字母数字）。

    前缀只拦 ASCII：CJK 汉字的 str.isalnum() 亦为 True，不限定 isascii 时
    中文行文「上穿MA60」的 ma60 别名会被误拒，60 被提为价位（issue #254，
    600845/600026 生产阻断实证；打回重试不可能修复，行文必含汉字）。
    """
    if alias.startswith("ma"):
        if start > 0 and lower_text[start - 1].isascii() and lower_text[start - 1].isalnum():
            return False
        if end < len(lower_text) and lower_text[end].isdigit():
            return False
    return True


def _alias_occurrences(text: str) -> list[tuple[int, int, str, str]]:
    """文本中全部点名指标 occurrence：(start, end, 规范名, 值表键)。"""
    lower_text = text.lower()
    out: list[tuple[int, int, str, str]] = []
    for alias, canonical, key in _ALIAS_TABLE:
        pos = 0
        while (found := lower_text.find(alias, pos)) != -1:
            end = found + len(alias)
            if _alias_boundary_ok(lower_text, found, end, alias):
                out.append((found, end, canonical, key))
            pos = found + 1
    return out


def _exclusion_spans(text: str, aliases: list[tuple[int, int, str, str]]) -> list[tuple[int, int]]:
    """数值提取排除区：点名指标 occurrence（MA60/60日均线的数字成分）+ 日期/区间 + 枚举序号 + 指数名 + 周期/结算记号（T+N）。"""
    spans = [(start, end) for start, end, _canonical, _key in aliases]
    for pattern in _DATE_PATTERNS:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    for pattern in _ENUM_SPAN_PATTERNS:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    spans.extend((m.start(), m.end()) for m in _INDEX_NAME_PATTERN.finditer(text))
    spans.extend((m.start(), m.end()) for m in _T_PLUS_PATTERN.finditer(text))
    return spans


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < e and end > s for s, e in spans)


def _non_price_context(text: str, span: tuple[int, int]) -> bool:
    """邻接/关键词量纲归类：百分比、倍率、比值、日期、非价格指标语境 → True。"""
    start, end = span
    i = start - 1
    while i >= 0 and text[i] == " ":
        i -= 1
    if i >= 0 and text[i] in _NON_PRICE_PREFIX:
        return True
    if i >= 0 and text[i].isascii() and text[i].isalpha():
        # 数字紧贴（隔空格亦可）ASCII 字母属标识符形态：M2/M1 宏观指标、
        # RSI14 参数、H1 周期——不是独立股价（601818 10-08 run 41 实证
        # 「M2支撑扩表」的 2 被提为价位偏差 31.51% 阻断）。本语料中股价
        # 恒跟随 CJK 方向词/量词，ASCII 字母前缀的裸数字无股价形态。
        return True
    j = end
    while j < len(text) and text[j] == " ":
        j += 1
    if j < len(text) and text[j] in _NON_PRICE_SUFFIX:
        return True
    window = text[max(0, start - 8) : start].lower()
    return any(keyword in window for keyword in _NON_PRICE_KEYWORDS)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _named_indicator(
    text: str,
    span: tuple[int, int],
    aliases: list[tuple[int, int, str, str]],
    table: dict[str, float],
) -> tuple[str, float] | None:
    """数值就近归属的点名指标（值缺失时返回 None → 回退最接近匹配）。"""
    num_start, num_end = span
    best: tuple[int, str, str] | None = None  # (gap, canonical, key)
    for start, end, canonical, key in aliases:
        if end <= num_start:
            gap = num_start - end
        elif num_end <= start:
            gap = start - num_end
        else:
            gap = 0
        if gap > _NAMED_WINDOW:
            continue
        if best is None or gap < best[0]:
            best = (gap, canonical, key)
    if best is not None and best[2] in table:
        return best[1], table[best[2]]
    return None


def _closest_indicator(table: dict[str, float], value: float) -> tuple[str, float] | None:
    """全部已验证指标中与该数值相对偏差最小者。"""
    best: tuple[str, float] | None = None
    best_dev = math.inf
    for name, verified in table.items():
        dev = abs(value - verified) / verified
        if dev < best_dev:
            best_dev = dev
            best = (name, verified)
    return best


def _directions_in_window(text: str, num_start: int) -> set[str]:
    """数值前方窗口内全部方向词的方向集合；「已 X」为事实陈述不计。

    复合回踩豁免（fix-decision-price-check-false-positives）预扫专用：同值既有
    down 又有 up 语境 → 空洞判定跳过。与 _break_direction 分离——最近词胜出后
    单值只归一个方向，豁免判定需要窗口内的完整方向集（688072 案回归锚）。
    """
    out: set[str] = set()
    for words, direction in ((_BREAKOUT_WORDS, "up"), (_BREAKDOWN_WORDS, "down")):
        for word in words:
            pos = 0
            while (found := text.find(word, pos)) != -1:
                pos = found + 1
                if found > 0 and text[found - 1] == "已":
                    continue
                if 0 <= num_start - (found + len(word)) <= _BREAK_WORD_WINDOW:
                    out.add(direction)
    return out


def _break_direction(text: str, num_start: int) -> str | None:
    """数值前方**最近**方向词的方向 → "up"/"down"；「已 X」为事实陈述不计。

    最近词胜出（600845 10-08 run 41 实证）：旧实现先扫完上破类再扫下破类，
    「突破20.58确认/跌破16.73证伪」的 16.73 距「跌破」2 字符、距「突破」
    11 字符（窗口 12 内）→ up 抢占 → 跌破触发（16.73 < 收盘，有效前瞻门槛）
    被误判「上破触发价不高于收盘」空洞形态阻断。
    """
    best: tuple[int, str] | None = None
    for words, direction in ((_BREAKOUT_WORDS, "up"), (_BREAKDOWN_WORDS, "down")):
        for word in words:
            pos = 0
            while (found := text.find(word, pos)) != -1:
                pos = found + 1
                if found > 0 and text[found - 1] == "已":
                    continue
                dist = num_start - (found + len(word))
                if 0 <= dist <= _BREAK_WORD_WINDOW and (best is None or dist < best[0]):
                    best = (dist, direction)
    return best[1] if best else None


def _excerpt(text: str, span: tuple[int, int], full: bool) -> str:
    """anomaly 原文片段：触发条目整条保留（Task 3 同源文本匹配）；长字段局部摘录。"""
    if full:
        return text
    start = max(0, span[0] - 24)
    end = min(len(text), span[1] + 24)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _fmt(value: float) -> str:
    return f"{value:g}"


def _check_snippet(
    text: str,
    source_full: bool,
    table: dict[str, float],
    bands: list[tuple[float, float]],
    scale: float,
    latest_close: float | None,
) -> list[dict]:
    """单段文本的价位交叉校验；每处数值至多登记 deviation + empty_trigger 各一条。

    复合回踩豁免（fix-decision-price-check-false-positives）：同一文本内同一数值
    同时出现下破语境与上破语境（「先跌破/回撤至 X，再站稳/站上 X」）时，该数值的
    空洞判定跳过——两个子条件串联，整体是前瞻有效门槛，非当前时点已满足。
    """
    anomalies: list[dict] = []
    aliases = _alias_occurrences(text)
    exclusions = _exclusion_spans(text, aliases)
    # 复合回踩豁免（fix-decision-price-check-false-positives）：同一文本内同一数值
    # 同时出现下破语境与上破语境（「先跌破/回撤至 X，再站稳 X」）时，该数值的
    # 空洞判定跳过——两个子条件串联，整体是前瞻有效门槛，非当前时点已满足。
    # 预扫不做量纲过滤（_non_price_context）：方向检测与量纲分类正交，且
    # 「回撤」关键词会误杀「回撤至610」的下破方向（688072 gen1 实证），使
    # down+up 共现判定缺一半；预扫结果只用于豁免，不产生 anomaly
    _value_dirs: dict[float, set[str]] = {}
    for m in _NUM_RE.finditer(text):
        s = (m.start(), m.end())
        if _overlaps(s, exclusions):
            continue
        v = float(m.group())
        if 0 < v < 1:
            continue
        # 预扫收集窗口内全部方向（_directions_in_window）：最近词胜出后
        # _break_direction 单值只归一个方向，复合豁免需要完整方向集
        _value_dirs.setdefault(v, set()).update(_directions_in_window(text, s[0]))
    for match in _NUM_RE.finditer(text):
        span = (match.start(), match.end())
        if _overlaps(span, exclusions):
            continue  # 指标名成分数字 / 日期
        value = float(match.group())
        if 0 < value < 1:
            continue  # 0-1 区间小数：比值/置信度形态
        if _non_price_context(text, span):
            continue  # 百分比/倍率/比值/日期/宏观与技术指标语境
        if not scale / _SCALE_RATIO <= value <= scale * _SCALE_RATIO:
            continue  # 与基准价不同数量级：指数点位/宏观量级等非股价量纲

        named = _named_indicator(text, span, aliases, table)
        indicator: str | None = None
        verified: float | None = None
        if named is not None:
            indicator, verified = named
        else:
            closest = _closest_indicator(table, value)
            if closest is not None:
                indicator, verified = closest

        source_text = _excerpt(text, span, source_full)
        if indicator is not None and verified is not None:
            deviation = abs(value - verified) / verified
            if deviation > _DEVIATION_THRESHOLD and not _in_reference_bands(value, bands):
                pct = round(deviation * 100, 2)
                anomalies.append(
                    {
                        "kind": "deviation",
                        "source_text": source_text,
                        "indicator": indicator,
                        "verified_value": verified,
                        "deviation_pct": pct,
                        "message": (
                            f"文本价位 {_fmt(value)} 与 {indicator} 已验证值 "
                            f"{_fmt(verified)} 偏差 {_fmt(pct)}%，超过 2% 阈值"
                            "且不在 price_levels 参考带内，价位待核实"
                        ),
                    }
                )

        if latest_close is not None:
            direction = _break_direction(text, span[0])
            is_empty = (direction == "up" and value <= latest_close) or (
                direction == "down" and value >= latest_close
            )
            dirs_of_value = _value_dirs.get(value, set())
            if is_empty and "up" in dirs_of_value and "down" in dirs_of_value:
                is_empty = False  # 复合回踩结构：down+up 共现，见函数头注释
            if is_empty:
                word = "上破" if direction == "up" else "下破"
                relation = "不高于" if direction == "up" else "不低于"
                anomalies.append(
                    {
                        "kind": "empty_trigger",
                        "source_text": source_text,
                        "indicator": "最新收盘价",
                        "verified_value": latest_close,
                        "deviation_pct": None,
                        "message": (
                            f"{word}触发价位 {_fmt(value)} {relation}最新收盘价 "
                            f"{_fmt(latest_close)}，触发条件在当前时点已满足，"
                            "不构成有效再评估门槛"
                        ),
                    }
                )
    return anomalies


def _check_structured_trigger_levels(
    decision: TradeDecision, latest_close: float | None
) -> list[dict]:
    """结构化触发位方向校验（add-watch-trigger-tracking）。

    ``trigger_high``/``trigger_low`` 与文本触发条目同语义：上破门槛须严格高于
    最新收盘、下破门槛须严格低于最新收盘；违反即空洞形态 anomaly——dict 键与
    ``_check_snippet`` 的 empty_trigger 构造完全同构（含 indicator/verified_value/
    deviation_pct），risk.py 门禁零改动共用打回预算与 source_text 集合运算。
    不依赖文本解析；无可用收盘价直通。
    """
    close = _as_positive_finite(latest_close)
    if close is None:
        return []
    anomalies: list[dict] = []
    for field, direction in (("trigger_high", "上破"), ("trigger_low", "下破")):
        value = _as_positive_finite(getattr(decision, field, None))
        if value is None:
            continue  # 未申报（Task 1 宽松清洗已归 None）不构成空洞
        violated = value <= close if field == "trigger_high" else value >= close
        if violated:
            relation = "高于" if field == "trigger_high" else "低于"
            anomalies.append(
                {
                    "kind": "empty_trigger",
                    "source_text": f"结构化触发位 {field}={_fmt(value)}",
                    "indicator": "最新收盘价",
                    "verified_value": close,
                    "deviation_pct": None,
                    "message": (
                        f"结构化{direction}触发位（{field}）{_fmt(value)} 未{relation}"
                        f"最新收盘价 {_fmt(close)}，触发条件在当前时点已满足，"
                        "不构成有效再评估门槛"
                    ),
                }
            )
    return anomalies


def check_decision_prices(
    decision: TradeDecision,
    technical_indicators: dict,
    price_levels: dict,
    latest_close: float | None,
) -> list[dict]:
    """决策文本价位 vs 已验证技术指标交叉校验（纯函数，不改写决策）。

    Parameters
    ----------
    decision : TradeDecision
        待校验决策（读取 reeval_triggers / inaction_reason / reasoning 文本，及
        trigger_high / trigger_low 结构化触发位）。
    technical_indicators : dict
        calc_technical() 输出形态：{"MA": {"5": [...], ...}, "BOLL": {...}}，
        序列与 K 线等长、末值为最新值（预热段 None 视为不可用）。
    price_levels : dict
        calc_price_levels() 输出形态；不可用（available=False）时无参考带豁免。
    latest_close : float | None
        最新收盘价；None 时跳过空洞检测、仅做偏差校验。

    Returns
    -------
    list[dict]
        每条 anomaly：``{"kind": "deviation"|"empty_trigger", "source_text": str,
        "indicator": str|None, "verified_value": float|None,
        "deviation_pct": float|None, "message": str}``。门禁语义：调用方据非空 anomaly 打回重试，
        仍异常阻断交付（见 update-decision-price-gate spec）。
    """
    table = _verified_table(technical_indicators, price_levels, latest_close)
    if not table:
        return []  # 无任何已验证指标可核对：不凭空报 anomaly
    close = _as_positive_finite(latest_close)
    scale = close if close is not None else _median(list(table.values()))
    if scale is None:
        return []

    snippets: list[tuple[bool, str]] = [(True, entry) for entry in decision.reeval_triggers]
    if decision.inaction_reason:
        snippets.append((False, decision.inaction_reason))
    if decision.reasoning:
        snippets.append((False, decision.reasoning))

    bands = _reference_bands(price_levels)
    anomalies: list[dict] = []
    for source_full, text in snippets:
        if text:
            anomalies.extend(
                _check_snippet(text, source_full, table, bands, float(scale), latest_close)
            )
    # 结构化触发位并入同一 anomaly 列表（add-watch-trigger-tracking）：latest_close
    # 可用时本函数不可能在上面提前返回（最新收盘价必在已验证值表中），合并点安全
    anomalies.extend(_check_structured_trigger_levels(decision, latest_close))
    return anomalies
