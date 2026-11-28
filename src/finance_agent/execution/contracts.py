"""决策契约字段解析（enforce-decision-contract-at-execution）。

决策层（trader/risk_judge）申报的执行约束——仓位档位与 ``exit_schedule`` 首批
比例——的自由文本 → 结构化口径，供信号契约与订单规划消费（issue #593）：

- 档位锚与 ``src/finance_agent/prompts/trader.md`` 档位表对齐取下沿（保守方向，
  宁小勿大；600780 2026-11-12 方案 FM 批准文本「light 档下沿约 10%」即此口径）；
- 首批比例取**首个可读的首批表述**（issue #626A）：除「首批 …N%」外还认
  第一批/第一步/先减/先处置/先卖出/先了结/行首减仓，以及减半、N/M、
  「N 分之 M」、三成等写法——11-24 批 10/11 份生产减仓节奏只认「N%」不可机读，
  调用方会退化为全额退出（#593 语义）。同一文本出现两个**不同**的首批比例
  （如「首批 70%，另一方案首批 50%」）→ ``None`` 歧义；同一批次内只取第一个
  数值（「首批…70%（较原方案 50% 上调）」= 0.7，维持既有口径）。
  无匹配/越界 → ``None``（调用方退化为既有全额退出并留痕 ``schedule_unparsed``）。
"""

from __future__ import annotations

import re

POSITION_TIER_WEIGHTS: dict[str, float] = {
    "light": 0.10,
    "moderate": 0.30,
    "heavy": 0.50,
}

# 首批语境锚点：显式「首批」族，或动作词（先减/先处置…/行首「(分批)减仓」）。
# 两类分开收集：「首批 70%，另一方案首批 50%」是同句两个方案（歧义）；
# 「首批减 30%，若反弹先减 20%」的第二处是后续条件动作，不应把首批读数判成歧义。
_FIRST_BATCH_CUE_RE = re.compile(
    r"(?P<explicit>首批|第一批|第一步)"
    r"|(?P<verb>先减|先减持|先处置|先卖出|先了结|减持|减出|^\s*(?:分批)?减仓)",
    re.MULTILINE,
)
# 首批表述的作用域：到句子/分号/换行为止（逗号不断句：「首批…派发，比例过半
# （原方案 50%）」的读数在逗号后；「首批减 1/3，剩余 2/3」靠「每段取第一个读数」
# 保证只取首批）。
_FIRST_BATCH_SCOPE_END_RE = re.compile(r"[。\n；;]")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
_FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_CN_FRACTION_RE = re.compile(
    r"([一二两三四五六七八九十百]+)\s*分之\s*([一二两三四五六七八九十百]+)"
)
_HALF_RE = re.compile(r"减半|一半|半仓|^半")
# 「减至/降至/减到 N」的 N 是**剩余**比例，卖出比例 = 1 − N。生产实证：
# 「首批减至 2/3（约 67%）；余 1/3…」「一次性减至原敞口 1/4 以内（减仓不少于 3/4）」。
_REDUCE_TO_SUFFIX_RE = re.compile(r"(?:减|降|调)(?:仓|持)?(?:至|到)[^0-9%％]{0,12}$")
_REDUCE_TO_AFTER_VERB_RE = re.compile(r"^\s*至[^0-9%％]{0,12}$")
_ARABIC_CHENG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*成")
_CN_CHENG_RE = re.compile(r"([一二两三四五六七八九十])\s*成")
_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100}
# 两个首批比例相差 ≤ 此值视为同一比例（三分之二 vs 67%、减半 vs 50%）
_RATIO_SAME_TOL = 0.02


def parse_position_tier(value: object) -> str | None:
    """档位名 → 归一小写键；非字符串/未知档位/百分比串 → ``None``（未申报档位）。"""
    if not isinstance(value, str):
        return None
    tier = value.strip().lower()
    return tier if tier in POSITION_TIER_WEIGHTS else None


def _cn_number(text: str) -> int | None:
    """中文数字（一~九十九、一百）→ int；含未知字符/空串 → ``None``。"""
    if not text:
        return None
    total = 0
    current = 0
    for char in text:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            total += (current or 1) * _CN_UNITS[char]
            current = 0
        else:
            return None
    return total + current


def _first_ratio_in_scope(scope: str) -> float | None:
    """首批作用域内**距锚点最近**的比例读数（位置优先，不按写法定优先级）。

    位置优先是生产实证要求：``先减 2/3 作为主执行（…VaR95 3.57%…）`` 里
    百分比 3.57% 是风险指标而非减仓比例，按「百分比优先」会误读成 0.036；
    取最近读数即 2/3。同一位置多写法（``减半（约 50%）``）取先列者。
    """
    candidates: list[tuple[int, float]] = []
    for match in _PERCENT_RE.finditer(scope):
        candidates.append((match.start(), float(match.group(1)) / 100.0))
    for match in _FRACTION_RE.finditer(scope):
        denominator = int(match.group(2))
        if denominator:
            candidates.append((match.start(), int(match.group(1)) / denominator))
    for match in _CN_FRACTION_RE.finditer(scope):
        cn_denominator = _cn_number(match.group(1))
        cn_numerator = _cn_number(match.group(2))
        if cn_denominator and cn_numerator is not None:
            candidates.append((match.start(), cn_numerator / cn_denominator))
    for match in _HALF_RE.finditer(scope):
        candidates.append((match.start(), 0.5))
    for match in _ARABIC_CHENG_RE.finditer(scope):
        candidates.append((match.start(), float(match.group(1)) / 10.0))
    for match in _CN_CHENG_RE.finditer(scope):
        cn_numerator = _cn_number(match.group(1))
        if cn_numerator:
            candidates.append((match.start(), cn_numerator / 10.0))
    valid = [(pos, ratio) for pos, ratio in candidates if 0.0 < ratio <= 1.0]
    if not valid:
        return None
    pos, ratio = min(valid, key=lambda item: (item[0], -item[1]))
    # 「减至 N」= 减到剩 N → 卖出比例 1−N；只看该读数紧邻的前缀，避免把
    # 句子后半的「股价降至 20 元」误当比例语境（价格串含数字，不匹配）。
    prefix = scope[:pos]
    if _REDUCE_TO_SUFFIX_RE.search(prefix) or _REDUCE_TO_AFTER_VERB_RE.match(prefix):
        ratio = 1.0 - ratio
    return ratio if 0.0 < ratio <= 1.0 else None


def parse_first_batch_ratio(schedule: object) -> float | None:
    """``exit_schedule`` 自由文本 → 首批比例（(0,1]）；无/歧义/越界 → ``None``。

    多个**相同**的首批读数不算歧义（重复表述同一比例）；多个不同读数 → ``None``
    （宁可不分批，也不按错误节奏减仓——调用方退化为全额退出并留痕）。
    """
    if not isinstance(schedule, str) or not schedule.strip():
        return None
    # 「第一批」统一成「首批」，与既有的显式锚点同路
    text = schedule.replace("第一批", "首批")
    explicit: list[float] = []
    verbal: list[float] = []
    for cue in _FIRST_BATCH_CUE_RE.finditer(text):
        start = cue.end()
        scope_end = _FIRST_BATCH_SCOPE_END_RE.search(text, start)
        scope = text[start : scope_end.start() if scope_end else len(text)]
        ratio = _first_ratio_in_scope(scope)
        if ratio is None or not (0.0 < ratio <= 1.0):
            continue
        (explicit if cue.group("explicit") else verbal).append(ratio)
    # 显式「首批/第一步」优先：同日多个方案（首批 70% / 另一方案首批 50%）判歧义；
    # 动作词只在一个显式方案都没有时才消费，且其内部不同读数同样判歧义。
    ratios = explicit or verbal
    if not ratios:
        return None
    first = ratios[0]
    if any(abs(ratio - first) > _RATIO_SAME_TOL for ratio in ratios[1:]):
        return None
    return first
