"""注入法污染矩阵：8 类污染的确定性构造 + 机制开关注册（spec causal-ablation）。

**注入载荷（payload）schema v2 —— 只打真实 state 结构**

v1 的载荷写通用占位键（revenue / price / close_series / macro_as_of / stop_loss ...），
而管线读的是 `income_statement`（DataFrame）/ `kline`（DataFrame）/ `news_list`（list[dict]）/
`macro_indicators`（dict，per 指标 as_of_date/freshness/records）/ 决策 dict —— 注入落不到
模型输入上。真跑腿据此判 void（`pilot_runner._real_status`），故载荷必须接线到真实结构。

`apply_injection` 只认 `payload["op"]`（未登记 op = 不改快照 + 记 `applied=False`），且
`payload` 其余键为该 op 的参数：

- `dataframe_cell` ：报表 DataFrame「最新报告期行 × 指定列」× factor（value_error）；
- `reverse_rows`   ：序列 DataFrame 行倒序（mirror_narrative）；
- `append_news`    ：news_list 追加假新闻 dict（fabricated_event）；
- `macro_stale`    ：macro_indicators 各项 as_of_date/freshness 置滞后（stale_macro）；
- `decision_price` ：决策 dict 破价位关系（illegal_price，trader_plan 优先）。

claims 目标的型（direction_error / unit_error / period_shift）不走快照注入：payload 仍为
`{"set": {...}, "field": ...}`，由 `pilot_runner` 的离线注入器消费；`apply_injection` 对其
记 `applied=False`（不得静默写占位键——静默空转会让真跑单元冒充「注入成功」）。
"""

from __future__ import annotations

import copy
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

POLLUTION_TYPES: tuple[str, ...] = (
    "value_error",
    "direction_error",
    "unit_error",
    "period_shift",
    "mirror_narrative",
    "fabricated_event",
    "stale_macro",
    "illegal_price",
)
INJECTION_POINTS: tuple[str, ...] = ("context", "analyst_output", "decision")

# 真实 state 结构的键名/列名（口径来源：src/finance_agent/data/akshare_client.py 的 fetch 输出）
PERIOD_COLUMN = "报告日"
REVENUE_COLUMN = "营业总收入"
CLOSE_COLUMN = "收盘"
MACRO_STALE_AS_OF = "2026-05-01"
MACRO_STALE_FRESHNESS = "stale"
FABRICATED_NEWS_TITLE = "某公司宣布重大战略合作（注入）"
DECISION_KEYS: tuple[str, ...] = ("trader_plan", "final_trade_decision")


@dataclass(frozen=True)
class MechanismSwitch:
    """机制开关点：off_value/on_value 为配置项（env 或属性）的取值。"""

    id: str
    kind: str  # "env" | "callable"
    key: str
    off_value: Any
    on_value: Any


MECHANISM_SWITCHES: dict[str, MechanismSwitch] = {
    "A1": MechanismSwitch("A1", "callable", "compute_metrics_injection", False, True),
    "A2": MechanismSwitch("A2", "callable", "semantic_header", False, True),
    "A3": MechanismSwitch("A3", "callable", "verify_citations", False, True),
    "A4": MechanismSwitch("A4", "callable", "surgical_repair", False, True),
    "A5": MechanismSwitch("A5", "callable", "price_sanity_check", False, True),
    "A6": MechanismSwitch("A6", "callable", "text_echo_match", False, True),
    "A7": MechanismSwitch("A7", "callable", "macro_freshness_mark", False, True),
}

# 污染类型 → (注入点, 因果负责机制)
_POLLUTION_ROUTING: dict[str, tuple[str, str]] = {
    "value_error": ("context", "A3"),
    "direction_error": ("analyst_output", "A3"),
    "unit_error": ("analyst_output", "A3"),
    "period_shift": ("analyst_output", "A2"),
    "mirror_narrative": ("context", "A2"),
    "fabricated_event": ("analyst_output", "A6"),
    "stale_macro": ("context", "A7"),
    "illegal_price": ("decision", "A5"),
}

# 成本分型：校验侧污染可对已有产物离线重放（零 LLM）；输入侧/决策层须真跑全管线
COST_CLASS: dict[str, str] = {
    "value_error": "real_run",
    "direction_error": "offline_replay",
    "unit_error": "offline_replay",
    "period_shift": "offline_replay",
    "mirror_narrative": "real_run",
    "fabricated_event": "offline_replay",
    "stale_macro": "offline_replay",
    "illegal_price": "real_run",
}


@dataclass(frozen=True)
class InjectionCase:
    case_id: str
    pollution_type: str
    injection_point: str
    mechanism_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InjectionEffect:
    """注入效果记录：`applied=False` 时 `reason` 说明不可施加原因（缺目标结构/目标不适用）。

    `polluted_values` = 终态产物里要检出的污染标记（值/字符串），`field_names` =
    污染所在 state 路径（field_ref 命中判定用，如 `income_statement.营业总收入`）。
    """

    applied: bool
    reason: str = ""
    target: str = ""
    polluted_values: tuple[Any, ...] = ()
    field_names: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """报告用扁平记录（JSON 友好）。"""
        return {
            "applied": self.applied,
            "reason": self.reason,
            "target": self.target,
            "polluted_values": list(self.polluted_values),
            "field_names": list(self.field_names),
        }


def build_injection_cases(
    pollution_type: str,
    *,
    ticker: str,
    base_values: dict[str, Any],
    n: int = 4,
    seed: int = 0,
) -> list[InjectionCase]:
    """确定性构造 n 个污染单元（同 seed 同结果）。"""
    if pollution_type not in POLLUTION_TYPES:
        raise ValueError(f"未知污染类型: {pollution_type!r}（须为 {POLLUTION_TYPES}）")
    point, mechanism = _POLLUTION_ROUTING[pollution_type]
    rng = random.Random(f"{seed}:{ticker}:{pollution_type}")  # noqa: S311 — 可复现 fixture，非加密用途
    cases: list[InjectionCase] = []
    for i in range(n):
        payload = _payload(pollution_type, base_values, rng)
        cases.append(
            InjectionCase(
                case_id=f"{ticker}-{pollution_type}-{i}",
                pollution_type=pollution_type,
                injection_point=point,
                mechanism_id=mechanism,
                payload=payload,
            )
        )
    return cases


def _payload(
    pollution_type: str, base_values: dict[str, Any], rng: random.Random
) -> dict[str, Any]:
    if pollution_type == "value_error":
        factor = 1.0 + rng.choice([-1.0, 1.0]) * rng.uniform(0.05, 0.50)
        return {
            "op": "dataframe_cell",
            "target": "income_statement",
            "column": REVENUE_COLUMN,
            "row": "latest",
            "factor": factor,
            # 申报基值只作对照：真实污染值由 apply_injection 从 DataFrame 单元格现取
            "declared_original": _declared(base_values, "revenue"),
            "field": f"income_statement.{REVENUE_COLUMN}",
        }
    if pollution_type == "direction_error":
        return {"set": {"growth": -float(base_values["growth"])}, "field": "growth"}
    if pollution_type == "unit_error":
        return {"set": {"revenue": float(base_values["revenue"]) * 1.0e8}, "field": "revenue"}
    if pollution_type == "period_shift":
        return {"set": {"period_label": "2026Q3", "period_value": 0.0}, "field": "period_label"}
    if pollution_type == "mirror_narrative":
        return {"op": "reverse_rows", "target": "kline", "field": "kline"}
    if pollution_type == "fabricated_event":
        return {
            "op": "append_news",
            "target": "news_list",
            "news_append": [FABRICATED_NEWS_TITLE],
            "field": "news_list",
        }
    if pollution_type == "stale_macro":
        return {
            "op": "macro_stale",
            "target": "macro_indicators",
            "as_of_date": MACRO_STALE_AS_OF,
            "freshness": MACRO_STALE_FRESHNESS,
            "field": "macro_indicators.as_of_date",
        }
    if pollution_type == "illegal_price":
        return {
            "op": "decision_price",
            "targets": list(DECISION_KEYS),
            "long_factor": 1.1,  # long 要求 stop<entry<target：抬 stop 破关系
            "short_factor": 0.9,  # short 要求 stop>entry>target：压 stop 破关系
            # 申报基值只作对照：真实 entry_price 由 apply_injection 从决策 dict 现取
            "declared_entry": _declared(base_values, "entry"),
            "field": "trader_plan.stop_loss",
        }
    # 不可达：build_injection_cases 已在入口校验 pollution_type ∈ POLLUTION_TYPES，
    # 本分支只作为「新增污染类型忘了实现 _payload」的显式断路器保留
    raise ValueError(f"未实现污染类型: {pollution_type}")  # pragma: no cover


def apply_injection(
    snapshot: dict[str, Any], case: InjectionCase
) -> tuple[dict[str, Any], InjectionEffect]:
    """返回（污染后的新快照, 注入效果记录）；深拷贝入参，不修改调用方对象。

    只认 `payload["op"]` 登记的快照操作：未登记（含 claims 目标的 `{"set": ...}` 载荷）
    一律不改快照并记 `applied=False` + reason —— 静默写占位键会让「污染到不了模型输入」
    的单元冒充注入成功。
    """
    out = copy.deepcopy(snapshot)
    op = str(case.payload.get("op") or "")
    handler = _OPS.get(op)
    if handler is None:
        return out, InjectionEffect(
            False,
            reason=(
                f"载荷未登记快照操作（op={op or '缺失'}；已登记 {'/'.join(sorted(_OPS))}）："
                f"apply_injection 只打真实 state 结构，不写占位键"
            ),
        )
    return out, handler(out, case.payload)


def _noop(target: str, reason: str, field_names: Sequence[str] = ()) -> InjectionEffect:
    return InjectionEffect(False, reason=reason, target=target, field_names=tuple(field_names))


# ── op 实现（每个 op：在真实 state 结构上施加污染 + 记录可检出标记）──


def _apply_dataframe_cell(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """报表单元格 × factor（value_error）：最新报告期行 × 指定列。"""
    target = str(payload.get("target") or "")
    wanted = str(payload.get("column") or "")
    field_names = (f"{target}.{wanted}", target)
    frame = out.get(target)
    if not isinstance(frame, pd.DataFrame):
        return _noop(target, f"缺目标报表 {target!r}（真实结构为 DataFrame）", field_names)
    if frame.empty:
        return _noop(target, f"{target} 为空表（无最新报告期行可污染）", field_names)
    if PERIOD_COLUMN not in frame.columns:
        return _noop(target, f"{target} 缺 {PERIOD_COLUMN!r} 列，无法定位最新报告期", field_names)
    column = _resolve_column(wanted, frame.columns)
    if column is None:
        return _noop(
            target,
            f"{target} 无 {wanted!r} 列（真实列：{list(map(str, frame.columns))[:8]}）",
            field_names,
        )
    row_label = _period_row_label(frame, payload.get("row"))
    if row_label is None:
        return _noop(target, f"{target} 无匹配报告期行（row={payload.get('row')!r}）", field_names)
    try:
        original = float(frame.loc[row_label, column])
    except (TypeError, ValueError):
        return _noop(
            target, f"{target}[报告日={row_label}].{column} 非数值，无法按 factor 污染", field_names
        )
    polluted = original * float(payload.get("factor") or 1.0)
    if pd.api.types.is_integer_dtype(frame[column].dtype):
        # pandas 3：整型列写浮点会 TypeError，先显式升为 float64（污染值必须保留小数）
        frame[column] = frame[column].astype("float64")
    frame.loc[row_label, column] = polluted
    return InjectionEffect(
        True,
        target=target,
        polluted_values=(polluted,),
        field_names=(f"{target}.{row_label}.{column}", *field_names),
    )


def _apply_reverse_rows(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """序列行倒序（mirror_narrative）：按正序读序列者会把最旧一期当最新。"""
    target = str(payload.get("target") or "")
    field_names = (str(payload.get("field") or target),)
    frame = out.get(target)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _noop(target, f"缺目标序列 {target!r}（真实结构为非空 DataFrame）", field_names)
    reversed_frame = frame.iloc[::-1].reset_index(drop=True)
    out[target] = reversed_frame
    marker = _close_of_last_row(reversed_frame)
    return InjectionEffect(
        True,
        target=target,
        polluted_values=(marker,) if marker is not None else (),
        field_names=field_names,
    )


def _apply_append_news(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """news_list 追加假事件（fabricated_event）：list[dict]，含真实形态字段。"""
    target = str(payload.get("target") or "news_list")
    field_names = (str(payload.get("field") or target),)
    titles = [str(t) for t in (payload.get("news_append") or []) if str(t)]
    if not titles:
        return _noop(target, "载荷缺 news_append 标题（无假事件可注入）", field_names)
    news = out.get(target)
    if not isinstance(news, list):
        return _noop(
            target, f"缺目标列表 {target!r}（真实结构为 list[dict]，含 title）", field_names
        )
    title = titles[0]
    out[target] = [
        *news,
        {
            "title": title,
            "content": title,
            "datetime": payload.get("datetime"),
            "source": "注入",
            "url": None,
        },
    ]
    return InjectionEffect(True, target=target, polluted_values=(title,), field_names=field_names)


def _apply_macro_stale(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """宏观时效标记置滞后（stale_macro）：逐指标 as_of_date/freshness。"""
    target = str(payload.get("target") or "macro_indicators")
    field_names = (f"{target}.as_of_date", target)
    as_of = str(payload.get("as_of_date") or MACRO_STALE_AS_OF)
    freshness = str(payload.get("freshness") or MACRO_STALE_FRESHNESS)
    macro = out.get(target)
    if not isinstance(macro, dict) or not macro:
        return _noop(
            target,
            f"缺目标 dict {target!r}（真实结构 per 指标 as_of_date/freshness/records）",
            field_names,
        )
    marked = [key for key, value in macro.items() if isinstance(value, dict)]
    if not marked:
        return _noop(
            target, f"{target} 无 dict 形态指标项（拉取失败的 [] 无时效标记可改）", field_names
        )
    for key in marked:
        macro[key]["as_of_date"] = as_of
        macro[key]["freshness"] = freshness
    return InjectionEffect(True, target=target, polluted_values=(as_of,), field_names=field_names)


def _apply_decision_price(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """决策 dict 破价位关系（illegal_price）：trader_plan 优先，回落 final_trade_decision。"""
    keys = [str(k) for k in (payload.get("targets") or list(DECISION_KEYS))]
    field_names = ("stop_loss", *keys)
    long_factor = float(payload.get("long_factor") or 1.1)
    short_factor = float(payload.get("short_factor") or 0.9)
    blocked: list[str] = []
    for key in keys:
        plan = out.get(key)
        if plan is None:
            continue
        if not isinstance(plan, dict):
            blocked.append(f"{key} 非 dict 形态（真实结构为决策 dict）")
            continue
        direction = _plan_direction(str(plan.get("action") or ""))
        if direction is None:
            blocked.append(f"{key} 的 action={plan.get('action')!r} 无价位要求（hold/watch 豁免）")
            continue
        entry = plan.get("entry_price")
        if not _is_number(entry):
            blocked.append(f"{key} 缺数值 entry_price（无价位关系可破）")
            continue
        factor = long_factor if direction == "long" else short_factor
        polluted = round(float(entry) * factor, 4)  # type: ignore[arg-type]
        plan["stop_loss"] = polluted  # 只破 stop：其余字段原样保留
        return InjectionEffect(
            True, target=key, polluted_values=(polluted,), field_names=field_names
        )
    reason = "；".join(blocked) or f"缺决策 dict（{'/'.join(keys)} 均不在快照）"
    return _noop("/".join(keys), reason, field_names)


_OPS: dict[str, Callable[[dict[str, Any], dict[str, Any]], InjectionEffect]] = {
    "dataframe_cell": _apply_dataframe_cell,
    "reverse_rows": _apply_reverse_rows,
    "append_news": _apply_append_news,
    "macro_stale": _apply_macro_stale,
    "decision_price": _apply_decision_price,
}


# ── 工具 ──


def _resolve_column(wanted: str, columns: Any) -> str | None:
    """列名解析复用校验器单一实现（口径不得复制：营业总收入 ↔ 营业收入）。"""
    if wanted in columns:
        return wanted
    from finance_agent.citation import _resolve_column_alias

    return _resolve_column_alias(wanted, columns)


def _period_row_label(frame: pd.DataFrame, row: object) -> Any | None:
    """报告期行标签：row 为空/"latest" 取 报告日 最大者（最新），否则取等值行。"""
    labels = frame[PERIOD_COLUMN].astype(str)
    if row in (None, "", "latest"):
        if labels.empty:
            return None
        return frame.index[int(labels.to_numpy().argmax())]
    mask = (labels == str(row)).to_numpy()
    if not mask.any():
        return None
    return frame.index[int(mask.argmax())]


def _close_of_last_row(frame: pd.DataFrame) -> float | None:
    """倒序后末行收盘 = 原序列首值（LLM 按「末尾为最新」引用即镜像叙事）。"""
    if CLOSE_COLUMN not in frame.columns:
        return None
    try:
        return float(frame[CLOSE_COLUMN].iloc[-1])
    except (TypeError, ValueError):
        return None


def _plan_direction(action: str) -> str | None:
    """action→方向复用决策层单一实现（口径不得复制：finance_agent.nodes.validate）。"""
    from finance_agent.nodes.validate import _plan_direction as validate_plan_direction

    return validate_plan_direction(action)


def _declared(base_values: dict[str, Any], key: str) -> float | None:
    """申报基值（审计对照）；真实污染值一律由 apply_injection 从 state 结构现取。"""
    value = base_values.get(key)
    if value is None or not _is_number(value):
        return None
    return float(value)


def _is_number(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True
