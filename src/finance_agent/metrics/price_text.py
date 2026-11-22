"""决策文本数值量纲归类器 + 价格承诺账本（refactor-price-check-dimension-classifier，C1）。

「什么数字是什么量纲」的知识唯一实现（此前以 10 种机制散布在 decision_price_check
头部：排除区正则/后缀字符表/前缀字符表/关键词窗/别名表/阻断字符集/元话语标记/
复合回踩预扫/日期正则/枚举正则——7 周 12 次误报修复、每条豁免 3 处同步的根因）。

接口：
- ``classify``：单个数值 occurrence 的量纲归类（价位候选 / 结构记号 / 非价格语境 /
  自引用讨论 / 量纲外 / 子单元比值）
- ``extract_commitments``：文本 → 价格承诺账本（PriceLedger）——量纲归类后的价位
  候选列表，附方向词绑定与复合回踩方向集；校验消费账本而非直接扫文本
- ``break_direction`` / ``directions_in_window``：方向词绑定（窗口+分句阻断）
- ``alias_occurrences`` / ``exclusion_spans``：点名指标与结构排除区

行为契约：与原 decision_price_check 内联实现逐行等价（spec price-level-tooling
「量纲归类豁免」段——归类护栏 Scenario 钉死豁免不得泄漏到真实价位）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── 方向词绑定 ──────────────────────────────────────────────────────────────

# 空洞形态方向词（spec：上破类「站上/突破/收复 X」/ 下破类「跌破/回落至 X」；
# 「放量突破」被「突破」覆盖；「站稳」为同族上破语义扩展；
# 「回撤至/回调至」为回踩语境同族下破词（fix-decision-price-check-false-positives，
# 688072 实证：缺失导致复合回踩触发的前半句无方向、后半句被误判空洞）
BREAKOUT_WORDS: tuple[str, ...] = ("站上", "突破", "收复", "放量突破", "站稳")
BREAKDOWN_WORDS: tuple[str, ...] = ("跌破", "回落至", "失守", "回撤至", "回调至")

# 方向词与数值的最大字符距离（词尾 → 数值起点；触发条目为 1-3 个短句）
BREAK_WORD_WINDOW = 12

# 分句/并列边界字符：方向词与数值间出现任一字符即不得绑定（002040 11-11
# 双池阻断实证）。两族误绑形态：①主池「（连续两日收盘站上才暂停减仓并重评）；
# 7.6014 为唯一操作清仓加速线」——站上属于 8.22895 的触发条件，隔「）；」
# 10 字符（窗口 12 内）抢绑 7.6014 → 合法下破止损被判「上破触发不高于收盘」；
# ②微盘「等待 8.05 放量站稳或 7.73 收盘跌破」——站稳属于 8.05（后置修饰），
# 隔「或」抢绑 7.73。两族的 retry 改写文本必然产生新 source_text → 恶化判据
# 硬阻断，模型无法自愈；绑定保守化（宁可少检不误拦）符合本门禁 precision-first
# 的既有取向（#254/#337/600845/600198 同族）。
BINDING_BLOCKERS = '。；;）)】」"、，,或'


def binding_blocked(text: str, word_end: int, num_start: int) -> bool:
    """方向词（词尾 word_end）与数值（起点 num_start）之间是否存在分句边界。"""
    return any(ch in BINDING_BLOCKERS for ch in text[word_end:num_start])


def directions_in_window(text: str, num_start: int) -> set[str]:
    """数值前方窗口内全部方向词的方向集合；「已 X」为事实陈述不计。

    复合回踩豁免（fix-decision-price-check-false-positives）预扫专用：同值既有
    down 又有 up 语境 → 空洞判定跳过。与 break_direction 分离——最近词胜出后
    单值只归一个方向，豁免判定需要窗口内的完整方向集（688072 案回归锚）。
    """
    out: set[str] = set()
    for words, direction in ((BREAKOUT_WORDS, "up"), (BREAKDOWN_WORDS, "down")):
        for word in words:
            pos = 0
            while (found := text.find(word, pos)) != -1:
                pos = found + 1
                if found > 0 and text[found - 1] == "已":
                    continue
                word_end = found + len(word)
                if 0 <= num_start - word_end <= BREAK_WORD_WINDOW and not binding_blocked(
                    text, word_end, num_start
                ):
                    out.add(direction)
    return out


def break_direction(text: str, num_start: int) -> str | None:
    """数值前方**最近**方向词的方向 → "up"/"down"；「已 X」为事实陈述不计。

    最近词胜出（600845 10-08 run 41 实证）：旧实现先扫完上破类再扫下破类，
    「突破20.58确认/跌破16.73证伪」的 16.73 距「跌破」2 字符、距「突破」
    11 字符（窗口 12 内）→ up 抢占 → 跌破触发（16.73 < 收盘，有效前瞻门槛）
    被误判「上破触发价不高于收盘」空洞形态阻断。

    分句边界阻断（002040 11-11 双池实证）：词与数值间隔「）；」「或」等分句/
    并列边界时不得绑定——越界词属于前一个价位的触发条件（见 BINDING_BLOCKERS）。
    """
    best: tuple[int, str] | None = None
    for words, direction in ((BREAKOUT_WORDS, "up"), (BREAKDOWN_WORDS, "down")):
        for word in words:
            pos = 0
            while (found := text.find(word, pos)) != -1:
                pos = found + 1
                if found > 0 and text[found - 1] == "已":
                    continue
                word_end = found + len(word)
                dist = num_start - word_end
                if (
                    0 <= dist <= BREAK_WORD_WINDOW
                    and not binding_blocked(text, word_end, num_start)
                    and (best is None or dist < best[0])
                ):
                    best = (dist, direction)
    return best[1] if best else None


# ── 量纲归类规则表 ──────────────────────────────────────────────────────────

NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 价格量纲窗口：数值须与基准价同数量级（±10 倍），排除日期年份/指数点位/宏观量级
SCALE_RATIO = 10.0

# 日期整体排除（2026-09-30 / 2026年9月30日 / 9月30日）
DATE_PATTERNS = (
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
    # 汉字「至」数值区间（fix-price-check-trading-day-context，300505 11-03 实证
    # 「（3 至 5 个交易日内未修复 MA5 即无条件清完）」的 3 与 MA5 16.154 偏差
    # 81.43% → 打回重试仍异常 → 报告阻断；「5」被「个」后缀豁免而「3」无守卫）。
    # 与连字/破折号区间同策——多值语境而非单一价位，一并豁免（误杀由观测
    # 通道承担，好过阻断）；「回落至 15.46」等方向词后接单一价位不受豁免
    # （「至」前必须紧跟数字才成区间）
    re.compile(r"\d+(?:\.\d+)?\s*至\s*\d+(?:\.\d+)?"),
    # 交易日计数（同 change，300505 第二条实证「【20 交易日窗口适配】」的 20
    # 与止损参考带上沿 17.1194 偏差 16.83%：「个」后缀只救「N 个交易日」，
    # 「N 交易日」的量词被「交」字隔开）。周期记号与 T+N 同族，「交易日」
    # 恒为计数后缀不承载价位语义，数字部分整体豁免
    re.compile(r"\d{1,3}\s*个?\s*交易日"),
    # 年份 + 报告期（「2026中报」「2025年报」「2026年三季报」）：年份是报告期
    # 记号而非价位（2026-10-06 深南电路实证「2026中报净利+65.55%」的 2026 与
    # 近期高点 459.8 偏差 340.63% → 阻断；高价股量纲守卫 ±10 倍抓不住）
    re.compile(
        r"(?:19|20)\d{2}(?=\s*年?\s*(?:中报|年报|半年报|季报|一季报|三季报|业绩预告|业绩快报))"
    ),
)
# 枚举序号整体排除（fix-decision-price-enumerators，600026 实证）：模型以
# 「（1）（2）（3）」给论点编号是固定行文格式，打回重试也换不掉——括号内
# 1-2 位裸整数是序号而非股价（600026 生产阻断消息「文本价位 3 与 近期低点
# 14.06 偏差 78.66%」即出自「（3）赔率1.56:1」）。仅匹配括号内无其他文字的
# 裸短整数，带小数点/上下文的括号价位（如「（13.0）」）不受豁免。
# 后括号形式「1) 2)」（issue #254，600515 实证「；1) 相对近窗…；2) 止损
# 距离…」）：负向后顾拦「2.6171）」的尾数（前邻数字/小数点不是序号起点），
# 真实括号价位（「（止损 9.99）」）不受豁免（反掩蔽锚见测试）。
ENUM_SPAN_PATTERNS = (
    re.compile(r"[（(]\s*\d{1,2}\s*[)）]"),
    re.compile(r"(?<![\d.])\d{1,2}[)）]"),
    # 「3. 」西文句点列表记号（301220 两轮生产实证：reasoning 结构化行文
    # 「…余仓当日清空"\n3. "2026 年报…」「4. 风险指标…」的 3/4 被提为价位、
    # 归属布林下轨 21.4241 偏差 86%/81.33% 阻断交付；编号列表是固有行文形态，
    # 打回删不掉 → 重试空转）。双守卫防误吞：lookbehind 排小数（20.67 的 20）、
    # lookahead \s 排真小数点后数字；记号后真实价位不受豁免
    re.compile(r"(?<![\d.])\d{1,2}\.(?=\s)"),
)
# 指数名整体排除（add-horizon-semantics E2E 实证，600519 10-11 实跑）：
# 决策层周期契约注入后 reasoning 恒含「相对沪深300 超额收益」——300 被提为
# 价位与近期低点 1170.28 偏差 74.37% → 打回重试也无法去掉（prompt 要求写
# 考核基准）→ 报告阻断。量纲守卫 ±10 倍抓不住（300 vs 1170 = 3.9x），
# 关键词窗抓不住（「沪深」不在关键词表）。指数名前缀直接跟随
# 的 2-4 位数字是指数点位而非个股股价；名称后隔开的真实价位不受豁免
# （护栏测试 TestIndexNameFalsePositives.test_real_price_after_index_mention_still_checked 钉死）。
# 名称与数字间空白不限个数（2026-10-13 同花顺实证：「沪深  300」多空格
# 排版变体从 \s? 单空格豁免区漏出，300 被提为价位偏差 22.21% 阻断）。
INDEX_NAME_PATTERN = re.compile(
    r"(?:沪深|中证|上证|深证|北证|国证|恒生|纳斯达克|标普|道琼斯)\s*\d{2,4}"
)
# 周期/结算记号整体排除（2026-10-13/14 cohort 实证）：决策层周期契约要求
# reasoning 写「T+20 个交易日」考核语义（agent-prompt-contracts），规范形态
# 被量词后缀（个/日）豁免，但模型自由文本缩写形态（「T+20内」「T+20考核」
# 「T+20低概率尾部」「T+2（结算惯例）」）没有 → 20/2 被提为价位（同花顺
# 10-14 三处 89.19% 阻断；华夏银行/南方航空/海南机场 10-13 同族）→ 打回
# 重试仍然写 T+N（契约要求，换不掉）→ 恶化判定 → 报告阻断。记号后紧邻的
# 真实价位不受豁免（T+N 恒以汉字连接，数字不与记号粘连）。
T_PLUS_PATTERN = re.compile(r"[Tt]\+\d{1,3}")
# 比率指标与阈值的比较链整体排除（600198 大唐电信 2026-11-09 cohort 实证）：
# risk_judge 终稿「beta 0.8628 低于 1」的 1 是 beta 中性线阈值——「beta」被
# 数值 0.8628 挤出 8 字符关键词窗、「低于」无数值算符可比照斜杠豁免 → 1 归属
# 止损参考带下沿 5.8843 偏差 83.01%。打回反馈的 source_text 已点名
# 「beta 0.8628<1」，但模型误读为「1 亿元」问题、重试改写错误对象，「低于 1」
# 原样保留 → 二次阻断 → 报告交付阻断（确定性校验 × 重试空转，incident 022
# 形状，重试不可能修复）。链条要求「比率指标名 + 数值 + 比较 + 阈值数字」
# 四要素齐备才豁免，孤立的「低于 5.88」真实价位不受豁免（护栏测试钉死）
RATIO_CHAIN_PATTERN = re.compile(
    r"(?:beta|β|夏普|胜率|赔率|换手|量比|波动率|市盈率?|市净率?|pe|pb|roe|roa)"
    r"\s*[:：]?\s*[\d.]+\s*"
    r"(?:[<>＜＞≥≤]|低于|高于|超过|不低于|不足|大于|小于)\s*\d+(?:\.\d+)?",
    re.IGNORECASE,
)
# 数值后紧跟（可隔空格）即非价格量纲：百分比/倍率/比值/成交量/日期时间单位/均线
# + 计数量词（fix-decision-price-enumerators：「3个月」「3成仓」的 3 不是股价；
# 计量字后接真实价位的表述不存在，不伤及正常价位提取；「家」为 2026-10-13
# 赛轮轮胎实证补充——「持股减少超 10 家」的 10 与止损参考带下沿 11.9029
# 偏差 15.99% → 报告阻断）+ 斜杠分数分子
# （600515 10-07 补跑实证「（1/3仓位…）」的 1 被提为价位偏差 61.79% 阻断）
# + 标准差记号 σ（2026-10-22 南方航空「20 个交易日 1σ 约 9%」的 1 与止损参考带
# 下沿 4.5314 偏差 77.93% → 阻断；σ 后恒无价位语义，排除不伤及真实价位）
NON_PRICE_SUFFIX = "%％倍折万亿手天日均月年号时点分秒周季:：个家成档条项次笔轮层类种枚名步位线/σ×"
# × 为 11-12 终裁 605033 round-1 实证补充：「约为 2×日波动以上」的 2 是波动倍数
# （trace c9f86e2c-31d），与「倍」同策——× 后的数字无前缀豁免，真实价位照常校验
# 数值前紧跟（可隔空格）即非价格上下文：比值冒号、序数、斜杠分数分母
# （「1/3」的 3；斜杠并列价位「19.99/20.10」一并豁免——与连字区间同策，
# 误杀由观测通道承担，好过阻断）+ 比较算符（600198 大唐电信 11-09 cohort
# 实证「经 beta 0.8628<1 校准后」的 1 是 beta 中性线阈值——算符紧邻的数字
# 是比较的阈值侧而非股价；本语料中股价恒跟随 CJK 方向词裸写，无「跌破 <6」
# 行文形态，排除不伤及真实价位）
NON_PRICE_PREFIX = ":：第/<＜>＞≥≤"
# 数值前窗（8 字符）内出现即非价格量纲：技术指标（RSI/KDJ…）、财务与比率语义词
NON_PRICE_KEYWORDS: tuple[str, ...] = (
    "rsi",
    "kdj",
    "j值",
    "j 值",
    "j线",
    "j 线",
    "macd",
    "dif",
    "dea",
    "pe",
    "pb",
    "roe",
    "roa",
    # peg 为 11-12 终裁 605033 实证补充（#588）：辩论双方全程在用的「PEG≈2.07」
    # 的 2.07 被归属布林下轨 15.8963、偏差 86.97% 标记——PEG 是估值倍数量纲，
    # 与 pe/pb 同策；≈ 本身不入前缀表（「现价 ≈21.4」是真实价位引用，护栏钉死）
    "peg",
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
    "覆盖",  # 利息覆盖/覆盖率比率语境（fix-price-check-trading-day-context，
    # 000767 11-03 实证「1.2 倍覆盖仍 >1 属薄而未破」的 1 与止损参考带下沿
    # 2.7543 偏差 63.69% → 报告阻断；变体行文「覆盖仍大于 1」同族。「覆盖」
    # 不承载价位语义，排除不伤及真实价位）
    "估值",
    "市盈",
    "市净",
    "股息",
    "折价",
    "溢价",
    "评分",
    "得分",
    "健康度",  # 0-100 系统健康度评分（2026-10-22 赛轮轮胎「健康度仅39.2」被提为
    # 价位，与近期高点 15.6 偏差 151.28% → 报告阻断；同族「评分/得分」已在此表）
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
ALIAS_TABLE: tuple[tuple[str, str, str], ...] = (
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

# 讨论校验本身的句子（自引用）不进入价位校验：模型收到打回后会把上一轮的异常
# 数值抄进 reasoning（「被误读为数值价位60」「打回消息称『文本价位 300…』」），
# 若照常扫描就会对同一数字二次报警，形成「越改越错」的回路（2026-10-05/06/13
# 生产实证：600845 两连阻断、300033/600015 同族）。句子级豁免只作用于含元话语
# 标记的句子；正常行情陈述（同段的「放量突破 25.0」）照常校验（护栏测试钉死）。
_META_SENTENCE_DELIMS = "。；;！!？?\n"
META_MARKERS: tuple[str, ...] = (
    "打回",
    "误读",
    "校验",
    "修正",
    "澄清",
    "价位冲突",
    "待核实",
    "恶化",
    "上一稿",
    "上一轮",
    "驳回",
    "不予采纳",
    "rejection",
    "corrective",
    "reject",
)


# ── 量纲归类 ────────────────────────────────────────────────────────────────

# 数值量纲类别（classify 返回值）：
# PRICE_CANDIDATE = 价位候选（进入交叉核对）
# STRUCTURAL = 结构记号（日期/区间/枚举/指数名/周期记号/点名指标成分）
# NON_PRICE_CONTEXT = 量纲语境（百分比/倍率/比值/关键词窗/ASCII 标识符）
# META_DISCUSSION = 自引用讨论句
# OFF_SCALE = 与基准价不同数量级
# SUB_UNIT_RATIO = 子单元比值（0<v<1）
PRICE_CANDIDATE = "price"
STRUCTURAL = "structural"
NON_PRICE_CONTEXT = "non_price"
META_DISCUSSION = "meta"
OFF_SCALE = "off_scale"
SUB_UNIT_RATIO = "sub_unit"


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


def alias_occurrences(text: str) -> list[tuple[int, int, str, str]]:
    """文本中全部点名指标 occurrence：(start, end, 规范名, 值表键)。"""
    lower_text = text.lower()
    out: list[tuple[int, int, str, str]] = []
    for alias, canonical, key in ALIAS_TABLE:
        pos = 0
        while (found := lower_text.find(alias, pos)) != -1:
            end = found + len(alias)
            if _alias_boundary_ok(lower_text, found, end, alias):
                out.append((found, end, canonical, key))
            pos = found + 1
    return out


def exclusion_spans(text: str, aliases: list[tuple[int, int, str, str]]) -> list[tuple[int, int]]:
    """数值提取排除区：点名指标 occurrence（MA60/60日均线的数字成分）+ 日期/区间 + 枚举序号 + 指数名 + 周期/结算记号（T+N）。"""
    spans = [(start, end) for start, end, _canonical, _key in aliases]
    for pattern in DATE_PATTERNS:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    for pattern in ENUM_SPAN_PATTERNS:
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    spans.extend((m.start(), m.end()) for m in INDEX_NAME_PATTERN.finditer(text))
    spans.extend((m.start(), m.end()) for m in T_PLUS_PATTERN.finditer(text))
    spans.extend((m.start(), m.end()) for m in RATIO_CHAIN_PATTERN.finditer(text))
    return spans


def overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < e and end > s for s, e in spans)


def non_price_context(text: str, span: tuple[int, int]) -> bool:
    """邻接/关键词量纲归类：百分比、倍率、比值、日期、非价格指标语境 → True。"""
    start, end = span
    i = start - 1
    while i >= 0 and text[i] == " ":
        i -= 1
    if i >= 0 and text[i] in NON_PRICE_PREFIX:
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
    if j < len(text) and text[j] in NON_PRICE_SUFFIX:
        return True
    window = text[max(0, start - 8) : start].lower()
    return any(keyword in window for keyword in NON_PRICE_KEYWORDS)


def in_meta_discussion(text: str, span: tuple[int, int]) -> bool:
    """数值所在句子是否在讨论校验本身（打回/误读/修正/澄清…）。"""
    start, end = span
    left = 0
    for ch in _META_SENTENCE_DELIMS:
        idx = text.rfind(ch, 0, start)
        if idx > left:
            left = idx + 1
    right = len(text)
    for ch in _META_SENTENCE_DELIMS:
        idx = text.find(ch, end)
        if 0 <= idx < right:
            right = idx
    sentence = text[left:right].lower()
    return any(marker in sentence for marker in META_MARKERS)


def classify(
    text: str,
    span: tuple[int, int],
    *,
    aliases: list[tuple[int, int, str, str]],
    scale: float,
) -> str:
    """单个数值 occurrence 的量纲归类（判定顺序与原内联实现一致，行为等价）。"""
    if overlaps(span, exclusion_spans(text, aliases)):
        return STRUCTURAL
    if in_meta_discussion(text, span):
        return META_DISCUSSION
    value = float(NUM_RE.match(text[span[0] : span[1]]).group())  # type: ignore[union-attr]
    if 0 < value < 1:
        return SUB_UNIT_RATIO
    if non_price_context(text, span):
        return NON_PRICE_CONTEXT
    if not scale / SCALE_RATIO <= value <= scale * SCALE_RATIO:
        return OFF_SCALE
    return PRICE_CANDIDATE


# ── 价格承诺账本（PriceLedger）──────────────────────────────────────────────


@dataclass(frozen=True)
class PriceCommitment:
    """账本条目：一个被归类为价位的数值 occurrence（附方向绑定与复合回踩方向集）。"""

    value: float
    span: tuple[int, int]
    direction: str | None  # 最近方向词胜出（up/down）；无绑定 None
    dirs: frozenset[str]  # 窗口内完整方向集（复合回踩豁免消费）


def extract_commitments(
    text: str,
    *,
    scale: float,
    aliases: list[tuple[int, int, str, str]],
) -> list[PriceCommitment]:
    """文本 → 价格承诺账本（量纲归类后的价位候选，扫描顺序 = 文本出现顺序）。

    行为与原 _check_snippet 扫描段逐行等价：预扫（方向集收集）不做量纲语境过滤
    （方向检测与量纲分类正交——「回撤」关键词会误杀「回撤至610」的下破方向，
    688072 gen1 实证）且不做量纲外过滤；主循环按 classify 全序归类。
    """
    exclusions = exclusion_spans(text, aliases)
    # 复合回踩豁免预扫：同值既有 down 又有 up 语境 → 空洞判定跳过。
    value_dirs: dict[float, set[str]] = {}
    for m in NUM_RE.finditer(text):
        s = (m.start(), m.end())
        if overlaps(s, exclusions):
            continue
        if in_meta_discussion(text, s):
            continue  # 自引用句里的数字不参与方向预扫（同主循环口径）
        v = float(m.group())
        if 0 < v < 1:
            continue
        value_dirs.setdefault(v, set()).update(directions_in_window(text, s[0]))

    commitments: list[PriceCommitment] = []
    for match in NUM_RE.finditer(text):
        span = (match.start(), match.end())
        value = float(match.group())
        dimension = classify(text, span, aliases=aliases, scale=scale)
        if dimension != PRICE_CANDIDATE:
            continue
        commitments.append(
            PriceCommitment(
                value=value,
                span=span,
                direction=break_direction(text, span[0]),
                dirs=frozenset(value_dirs.get(value, set())),
            )
        )
    return commitments
