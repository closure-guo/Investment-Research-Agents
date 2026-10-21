"""validate_financials: 勾稽校验节点 — 三大报表数据质量验证。

在 compute_metrics 之前执行，硬等式失败时短路终止。
"""

from __future__ import annotations

import re
from typing import cast

from finance_agent.metrics.validate import validate_financials


def validate_node(state: dict) -> dict:
    """执行勾稽校验，结果写入 state。

    Returns
    -------
    dict
        {"validation_result": "PASS" | "FAIL", "validation_warnings": list[str]}
    """
    bs = state.get("balance_sheet")
    inc = state.get("income_statement")
    cf = state.get("cash_flow_statement")

    if bs is None or inc is None or cf is None:
        return {
            "validation_result": "FAIL",
            "validation_warnings": ["勾稽校验跳过：三大报表数据缺失"],
        }

    result = validate_financials(bs, inc, cf)
    return {
        "validation_result": result["result"],
        "validation_warnings": result["warnings"],
    }


# ── toolize-price-levels：交易价位 sanity 校验（确定性，LLM 不参与）──

_ENTRY_DEVIATION_LIMIT = 0.15  # entry 距最新收盘偏差上限（防 LLM 报无关价位）


def _plan_direction(action: str) -> str | None:
    """buy→long / sell→short；hold/watch 无价位要求。"""
    if action == "buy":
        return "long"
    if action == "sell":
        return "short"
    return None


def _is_missing_price(v: object) -> bool:
    """价位缺失判定（require-trade-price-declaration）：None / ≤0 / 非数值均为缺失。

    0 是 LLM 实际输出的「未提供」形态（比亚迪 sell 0/0 已在报告按未提供渲染），
    与 None 同等对待。
    """
    if v is None:
        return True
    try:
        return float(v) <= 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return True


_RATIO_KEYWORDS = ("赔率", "盈亏比")
_RATIO_WINDOW = 40  # 关键词后扫描窗口（字符）
_RATIO_TOL = 0.10  # 相对容差：四舍五入级差异不修正
# 赔率表述形态（extend-payout-self-check-coverage）：`N:1` 与 `N倍`——600030 实证
# 「赔率约1.78倍纸面占优」形态旧正则未命中（risk_judge 改止损后旧赔率残留）
_RATIO_FORM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[:：]\s*1|倍)")
# 转述护栏（601888 实证）：匹配数字后近距离（12 字符）出现批评语境词 = 转述辩论
# 对方的赔率判断——替换会反转批评指向，跳过仅计数。主语词（激进方/保守方等）不进
# 词表：出现在数字前不进窗口（窗口自「赔率」关键词起算），出现在数字后是新句主语
# （600030「赔率约1.78倍纸面占优。激进方建议…」为自报+后续叙述，须替换）
_TRANSCRIPT_GUARD_WORDS = ("批评", "质疑", "反驳", "驳回")
_GUARD_SPAN = 12  # 数字后护栏检测半径（字符）


def check_and_fix_stated_ratio(
    reasoning: str, derived_ratio: float | None
) -> tuple[str, bool, int]:
    """赔率自检：reasoning 自报赔率与代码计算冲突时原位替换为代码值（确定性，无 LLM）；
    转述窗口（辩论指涉词）跳过替换、计数返回；容差内 / 派生缺失 / 无赔率表述 → 原样。

    返回 (修正后文本, 是否修正, 转述跳过计数)。证据：601899「1.7:1」vs 1.24、
    000333「2.6:1」vs 2.00、600030「1.78倍」vs 1.57（形态盲区）、601888 转述「1.55:1」。"""
    if derived_ratio is None or derived_ratio <= 0 or not reasoning:
        return reasoning, False, 0
    fixed = reasoning
    corrected = False
    skipped = 0
    for kw in _RATIO_KEYWORDS:
        start = 0
        while True:
            i = fixed.find(kw, start)
            if i < 0:
                break
            window = fixed[i : i + len(kw) + _RATIO_WINDOW]
            m = _RATIO_FORM.search(window)
            if not m:
                start = i + len(kw)
                continue
            if any(w in window[m.end(1) : m.end(1) + _GUARD_SPAN] for w in _TRANSCRIPT_GUARD_WORDS):
                # 转述语境（数字后紧跟批评语境词）：不替换（防反转批评语义），计数可见
                skipped += 1
                start = i + m.end(1)
                continue
            stated = float(m.group(1))
            if stated > 0 and abs(stated - derived_ratio) / derived_ratio > _RATIO_TOL:
                new_window = window[: m.start(1)] + f"{derived_ratio:.2f}" + window[m.end(1) :]
                fixed = fixed[:i] + new_window + fixed[i + len(window) :]
                corrected = True
                start = i + len(new_window)
            else:
                start = i + m.end(1)
    return fixed, corrected, skipped


def apply_payout_self_check(
    reasoning: str, action: object, entry: object, stop: object, target: object
) -> tuple[str, bool, int]:
    """自算派生赔率并对 reasoning 做原位修正（trader / risk_judge 产出路径共用）。

    返回 (修正后文本, 是否修正, 转述跳过计数)——跳过计数随节点 state 上报
    （`payout_ratio_conflict_skipped`），供批次归因消费。"""
    derived = _compute_derived_metrics(str(action or ""), entry, stop, target)
    return check_and_fix_stated_ratio(reasoning, derived.get("risk_reward_ratio"))


def final_price_missing(decision: object) -> list[str]:
    """终稿（final_trade_decision）buy/sell 的缺失价位清单（None/≤0；watch/hold 无要求）。

    extend-payout-self-check-coverage（601888 实证：buy 价位全 None 直通）——
    完整性校验语义与 Trader 价位必填一致，但只查缺失，不重跑关系/参考带。"""
    action = str(getattr(decision, "action", "") or "")
    if action not in ("buy", "sell"):
        return []
    missing: list[str] = []
    for label, value in (
        ("entry_price", getattr(decision, "entry_price", None)),
        ("stop_loss", getattr(decision, "stop_loss", None)),
        ("target_price", getattr(decision, "target_price", None)),
    ):
        if _is_missing_price(value):
            missing.append(label)
    return missing


def inaction_rationale_missing(decision: object) -> list[str]:
    """非执行动作（watch/hold）缺失理由清单（require-watch-hold-rationale）。

    只查缺失、不做形态清洗（模型层负责）：inaction_reason 纯空白视为缺失；
    reeval_triggers 无有效条目视为缺失（str 形态视为已申报，兼容 dict 历史对象）。
    buy/sell 无要求（返回 []）。接受 pydantic 对象与 dict 两种形态。
    """
    if isinstance(decision, dict):
        action = str(decision.get("action") or "")
        reason = decision.get("inaction_reason")
        triggers = decision.get("reeval_triggers")
    else:
        action = str(getattr(decision, "action", "") or "")
        reason = getattr(decision, "inaction_reason", None)
        triggers = getattr(decision, "reeval_triggers", None)
    if action not in ("watch", "hold"):
        return []
    missing: list[str] = []
    if not (isinstance(reason, str) and reason.strip()):
        missing.append("inaction_reason")
    if isinstance(triggers, str):
        triggers = [triggers] if triggers.strip() else []
    if not (isinstance(triggers, list) and any(isinstance(t, str) and t.strip() for t in triggers)):
        missing.append("reeval_triggers")
    return missing


def exec_reeval_missing(decision: object) -> list[str]:
    """终稿 buy/sell 的 reeval_triggers 必填判定（agent-node-contracts 2026-09 扩展，
    601818 实证：sell 终稿无任何再评估触发条件，FM 仍批准）。

    watch/hold 的 reeval 由 inaction_rationale_missing 管，本检查不重复约束。
    只查清洗后的有效条目数（模型层已归一噪声形态），不做内容质量判断。
    接受 pydantic 对象与 dict 两种形态（历史落库 JSON 的 str 形态按单元素归一，
    同 inaction_rationale_missing）。
    """
    if isinstance(decision, dict):
        action = str(decision.get("action") or "")
        triggers = decision.get("reeval_triggers")
    else:
        action = str(getattr(decision, "action", "") or "")
        triggers = getattr(decision, "reeval_triggers", None)
    if action not in ("buy", "sell"):
        return []
    if isinstance(triggers, str):
        triggers = [triggers] if triggers.strip() else []
    valid = [t for t in (triggers or []) if isinstance(t, str) and t.strip()]
    if not valid:
        return ["reeval_triggers"]
    return []


def _inaction_rationale_check(plan: object, state: dict) -> dict:
    """非执行动作理由完整性检查（trader 侧）：缺一次打回，仍缺放行+如实标注。

    与价位检查同款一次重试语义（require-trade-price-declaration 先例），
    独立 attempts 计数，避免与价位回路互相消耗。
    """
    missing = inaction_rationale_missing(plan)
    if not missing:
        return {"inaction_rationale_check": {"result": "pass"}}
    attempts = int(state.get("inaction_rationale_attempts") or 0)
    if attempts < 1:
        reason = (
            f"watch/hold 决策必须结构化申报不行动理由与再评估触发条件，缺失：{'、'.join(missing)}"
        )
        return {
            "inaction_rationale_check": {"result": "fail", "reason": reason},
            "inaction_rationale_feedback": (
                f"非执行动作理由检查未通过：{reason}。"
                "请补全 inaction_reason（一句话，具体到当前不满足执行条件的点）与 "
                "reeval_triggers（1-3 条可观察、可判定的再评估触发条件）。"
            ),
            "inaction_rationale_attempts": attempts + 1,
        }
    return {
        "inaction_rationale_check": {
            "result": "pass",
            "note": (
                "已打回仍未申报非执行动作理由（watch/hold 必填），放行——"
                "报告按「未申报」渲染，不虚构内容"
            ),
        },
        "inaction_rationale_attempts": attempts,
    }


def validate_trade_prices(state: dict) -> dict:
    """校验 trader 方案：价位 sanity（原语义）+ 非执行动作理由完整性。

    require-watch-hold-rationale：watch/hold 的结构化理由缺失同样打回一次。
    两类检查动作互斥（buy/sell vs watch/hold），但理由键在所有路径统一补齐——
    防止上一轮 fail 状态滞留导致路由回跳。
    """
    updates = _validate_price_levels(state)
    plan = state.get("trader_plan") or {}
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    updates.update(_inaction_rationale_check(plan, state))
    return updates


def _validate_price_levels(state: dict) -> dict:
    """校验 trader 价位：价格关系 / entry 距现价偏差 / 工具参考带。

    三路结果写入 price_check.result：pass（含跳过）/ fail（打回）/ corrected
    （按工具参考带修正，price_level_corrected 可观测）。校验纯规则，无 LLM。
    """
    plan = state.get("trader_plan") or {}
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()

    action = str(plan.get("action") or "")
    direction = _plan_direction(action)
    if direction is None:
        return {"price_check": {"result": "pass", "note": "hold/watch 无价位要求"}}

    entry = plan.get("entry_price")
    stop = plan.get("stop_loss")
    target = plan.get("target_price")
    # buy/sell 价位必填（require-trade-price-declaration）：任一缺失（None/≤0）不再
    # 静默跳过——连续 4 轮真实运行 0 申报的根因即旧「schema 可选，跳过校验」分支。
    # 首次 fail 打回要求申报；已打回仍缺失 → 放行+如实标注（缺失无数值可修，不进
    # 参考带修正路径），报告端按「未提供」渲染，不虚构数值。
    labels = (("entry_price", entry), ("stop_loss", stop), ("target_price", target))
    missing = [label for label, v in labels if _is_missing_price(v)]
    if missing:
        attempts = int(state.get("price_check_attempts") or 0)
        if attempts < 1:
            reason = f"buy/sell 决策必须申报数值价位，缺失/无效：{'、'.join(missing)}"
            return {
                "price_check": {"result": "fail", "reason": reason},
                "price_check_feedback": (
                    f"价位 sanity 校验未通过：{reason}。"
                    "请按最新收盘与风险逻辑申报全部三价数值"
                    "（entry_price/stop_loss/target_price），watch/hold 才可豁免。"
                ),
                "price_check_attempts": attempts + 1,
            }
        return {
            "price_check": {
                "result": "pass",
                "note": "已打回仍未申报价位（buy/sell 必填），放行——报告按「未提供」渲染，不虚构数值",
            },
            "price_check_attempts": attempts,
        }

    # missing 检查后 entry 必然齐备（mypy 无法从列表推导窄化，显式 cast）
    entry = cast(float, entry)
    levels = state.get("price_levels") or {}
    # 派生指标只依赖申报价格，与参考带/行情无关——跳过 band 校验也 MUST 计算，
    # 否则风险辩论拿不到代码值、退回 LLM 心算（delta 核心承诺）
    derived = _compute_derived_metrics(action, entry, stop, target)
    if not levels.get("available"):
        return {
            "price_check": {
                "result": "pass",
                "note": f"price_levels 不可用（{levels.get('reason', 'unknown')}），跳过校验",
            },
            "derived_metrics": derived,
        }

    kline = state.get("kline")
    if kline is None or len(kline) == 0:
        return {
            "price_check": {"result": "pass", "note": "无行情数据，跳过校验"},
            "derived_metrics": derived,
        }
    close = float(kline["收盘"].iloc[-1])

    reasons: list[str] = []

    # 1. 价格关系（三者齐备才校验关系）
    if stop is not None and target is not None:
        if direction == "long" and not (stop < entry < target):
            reasons.append(
                f"价格关系违规（long 要求 stop<entry<target，实际 {stop}/{entry}/{target}）"
            )
        if direction == "short" and not (stop > entry > target):
            reasons.append(
                f"价格关系违规（short 要求 stop>entry>target，实际 {stop}/{entry}/{target}）"
            )

    # 2. entry 距最新收盘偏差
    if abs(entry - close) / close > _ENTRY_DEVIATION_LIMIT:
        reasons.append(f"entry {entry} 距最新收盘 {close} 偏差超 {_ENTRY_DEVIATION_LIMIT:.0%}")

    # 3. 放宽带（stop/target 落在近期交易区间 ±2ATR 内）
    full_band = levels.get("full_band") or []
    if len(full_band) == 2 and stop is not None and target is not None:
        lo, hi = float(full_band[0]), float(full_band[1])
        for label, v in (("stop", stop), ("target", target)):
            if not (lo <= v <= hi):
                reasons.append(f"{label} {v} 落在参考带 [{lo}, {hi}] 之外")

    if not reasons:
        return {
            "price_check": {"result": "pass"},
            "derived_metrics": _compute_derived_metrics(action, entry, stop, target),
        }

    reason = "；".join(reasons)
    attempts = int(state.get("price_check_attempts") or 0)
    if attempts < 1:
        feedback = (
            f"价位 sanity 校验未通过：{reason}。"
            f"请参考价位参考带重出（entry_ref={levels.get('entry_ref')}，"
            f"stop_band_long={levels.get('stop_band_long')}，"
            f"target_band_long={levels.get('target_band_long')}，"
            f"{'long: stop<entry<target' if direction == 'long' else 'short: stop>entry>target'}）"
        )
        return {
            "price_check": {"result": "fail", "reason": reason},
            "price_check_feedback": feedback,
            "price_check_attempts": attempts + 1,
        }

    # 二次失败：按工具参考带修正（可观测，不静默）；派生指标按修正后价位计算
    corrected = correct_prices(plan, levels, action)
    return {
        "trader_plan": corrected,
        "price_check": {"result": "corrected", "reason": reason},
        "price_level_corrected": True,
        "price_level_correction_reason": reason,
        "derived_metrics": _compute_derived_metrics(
            action,
            corrected.get("entry_price"),
            corrected.get("stop_loss"),
            corrected.get("target_price"),
        ),
    }


def _compute_derived_metrics(action: str, entry: object, stop: object, target: object) -> dict:
    """派生风险指标确定性计算（deterministic-derived-metrics）。

    止损距离 = |entry-stop|/entry；赔率 = |reward|/|risk|（buy：t-e / e-s；
    sell：e-t / s-e）。参与数缺失/为 0 或除零 → 对应值 None 并注明原因，
    MUST NOT 产出 0/无穷/NaN 占位。纯规则，无 LLM。
    """
    missing: list[str] = []
    try:
        e = float(entry) if entry is not None else None  # type: ignore[arg-type]
        s = float(stop) if stop is not None else None  # type: ignore[arg-type]
        t = float(target) if target is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {
            "stop_distance_pct": None,
            "risk_reward_ratio": None,
            "missing_reason": "价位参数非数值",
        }
    if not e or e <= 0:
        missing.append("entry 缺失")
    if not s or s <= 0:
        missing.append("stop 缺失")
    if not t or t <= 0:
        missing.append("target 缺失")
    if e and s and e == s:
        missing.append("stop 与 entry 相等，止损距离为零除")
    if missing:
        return {
            "stop_distance_pct": None,
            "risk_reward_ratio": None,
            "missing_reason": "；".join(missing),
        }
    e, s, t = cast(float, e), cast(float, s), cast(float, t)  # missing 守卫已排除 None
    stop_distance = abs(e - s) / e
    if action == "sell":
        reward, risk = e - t, s - e
    else:
        reward, risk = t - e, e - s
    ratio = abs(reward) / risk if risk > 0 else None
    if ratio is None:
        missing.append("risk 非正（价位关系非法），赔率不可计算")
    return {
        "stop_distance_pct": stop_distance,
        "risk_reward_ratio": ratio,
        "missing_reason": "；".join(missing) or None,
    }


def correct_prices(plan: dict, levels: dict, action: str) -> dict:
    """按工具参考带修正价位（确定性）：entry=entry_ref，stop/target=参考带中值。

    short 镜像：stop=close±ATR 带中值对称外推，target 反向。返回新 dict，
    不修改原 plan（trader_plan 整体替换进 state）。
    """
    direction = _plan_direction(action) or "long"
    close = float(levels.get("entry_ref") or 0)
    atr = float(levels.get("atr") or 0)
    stop_band = levels.get("stop_band_long") or {}
    target_band = levels.get("target_band_long") or {}

    corrected = dict(plan)
    corrected["entry_price"] = close
    if direction == "long":
        corrected["stop_loss"] = (
            (float(stop_band["low"]) + float(stop_band["high"])) / 2
            if stop_band
            else close - 1.5 * atr
        )
        corrected["target_price"] = (
            (float(target_band["low"]) + float(target_band["high"])) / 2
            if target_band
            else close + 3 * atr
        )
    else:
        # short 镜像：stop 在上方 1-2ATR 带中值，target 在下方 2-4ATR 带中值
        corrected["stop_loss"] = close + 1.5 * atr
        corrected["target_price"] = close - 3 * atr
    corrected["price_level_corrected"] = True
    corrected["price_level_correction_reason"] = "sanity 校验二次未通过，按工具参考带修正"
    return corrected
