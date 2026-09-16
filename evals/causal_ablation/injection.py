"""注入法污染矩阵：8 类污染的确定性构造 + 机制开关注册（spec causal-ablation）。

**注入载荷（payload）schema v3 —— 只打真实 state 结构 + 真靶点（P1 重校轮）**

v1 的载荷写通用占位键（revenue / price / close_series / macro_as_of / stop_loss ...），
而管线读的是 `income_statement`（DataFrame）/ `kline`（DataFrame）/ `news_list`（list[dict]）/
`macro_indicators`（dict，per 指标 as_of_date/freshness/records）/ 决策 dict —— 注入落不到
模型输入上。真跑腿据此判 void（`pilot_runner._real_status`），故载荷必须接线到真实结构。

v2 接线到真实结构后，round-1 真跑实证「结构对但靶点不对」：`value_error` 打利润表单元格、
`mirror_narrative` 打原始 kline —— 分析师**从不逐字引用**这两处（技术面 context 读的是
预计算 `technical_indicators`/`derived_series`），8 个 context 级真跑单元 7 个判 void。
v3 据此换靶点（结构不变）：`value_error` 偶数实例打 `derived_series`（分析师被提示直接引用
的派生值表，且在 `citation._COMPUTATIONAL_RECALC` 重算注册表里 → A3 值级校验可重算抓偏差），
`mirror_narrative` 打 `technical_indicators.MA.5` 序列（分析师直读的技术指标）。

`apply_injection` 只认 `payload["op"]`（未登记 op = 不改快照 + 记 `applied=False`），且
`payload` 其余键为该 op 的参数：

- `dataframe_cell`     ：报表 DataFrame「最新报告期行 × 指定列」× factor
                         （value_error 回落形态）；
- `dict_path_value`    ：任意嵌套 dict 数值叶子 × factor（value_error 真靶点，round-3
                         二阶段：claim 实际引用且可重算的字段，如 `profitability_metrics.ROE.2025`）；
- `derived_series_value`：派生值 dict 指定键 × factor（`fallback` 给旧单元格形态）；
- `reverse_rows`       ：序列 DataFrame 行倒序（mirror_narrative 回落形态）；
- `reverse_series`     ：嵌套 dict 指定路径的 list 序列倒序（mirror_narrative；`fallback`
                         给 kline 行序形态）；
- fabricated_event 的载荷为**分析师输出侧**（`claim_text`，由 `pilot_runner` 的离线注入器
  消费）：round-3 重校实证「假新闻与假引用同源注入」等于给污染自证出处（A6 回声匹配必然
  PASS），故不再提供 `append_news` 快照 op——真实形态是「claim 断言的事件在输入里不存在」；
- `macro_stale`        ：macro_indicators 各项 as_of_date/freshness 置滞后（stale_macro）；
- `decision_price`     ：决策 dict 破价位关系（illegal_price，trader_plan 优先）。

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
FABRICATED_EVENT_TITLE = "某公司宣布重大战略合作（注入）"
DECISION_KEYS: tuple[str, ...] = ("trader_plan", "final_trade_decision")
# P1 重校轮真靶点：技术面 context 直读的两处（analysts._build_technical_context）
DERIVED_SERIES_TARGET = "derived_series"
DERIVED_SERIES_KEY = "chg_5d"
TECHNICAL_TARGET = "technical_indicators"
TECHNICAL_MA_PATH: tuple[str, ...] = ("MA", "5")


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

    `applied=True` 时 `reason` 为补充说明（如「主靶点缺失，已回落」——回落形态必须可审计，
    否则「打了哪个靶点」只能从 target 反推）。

    `polluted_values` = 终态产物里要检出的污染标记（值/字符串），`field_names` =
    污染所在 state 路径（field_ref 命中判定用，如 `derived_series.chg_5d`）。
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


def _pick_target(targets: Sequence[dict[str, Any]] | None, index: int) -> dict[str, Any] | None:
    """按实例轮换挑选 claim 级靶点（同 seed 同结果：顺序由构造方定序）。"""
    if not targets:
        return None
    return targets[index % len(targets)]


def build_injection_cases(
    pollution_type: str,
    *,
    ticker: str,
    base_values: dict[str, Any],
    n: int = 4,
    seed: int = 0,
    targets: Sequence[dict[str, Any]] | None = None,
) -> list[InjectionCase]:
    """确定性构造 n 个污染单元（同 seed 同结果；实例序号 index 亦参与构造，决定形态分派）。"""
    if pollution_type not in POLLUTION_TYPES:
        raise ValueError(f"未知污染类型: {pollution_type!r}（须为 {POLLUTION_TYPES}）")
    point, mechanism = _POLLUTION_ROUTING[pollution_type]
    rng = random.Random(f"{seed}:{ticker}:{pollution_type}")  # noqa: S311 — 可复现 fixture，非加密用途
    cases: list[InjectionCase] = []
    for i in range(n):
        payload = _payload(pollution_type, base_values, rng, index=i, targets=targets)
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
    pollution_type: str,
    base_values: dict[str, Any],
    rng: random.Random,
    *,
    index: int = 0,
    targets: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if pollution_type == "value_error":
        factor = 1.0 + rng.choice([-1.0, 1.0]) * rng.uniform(0.05, 0.50)
        # 旧靶点（回落形态）：利润表「最新报告期行 × 营业总收入」× factor。
        cell = {
            "op": "dataframe_cell",
            "target": "income_statement",
            "column": REVENUE_COLUMN,
            "row": "latest",
            "factor": factor,
            # 申报基值只作对照：真实污染值由 apply_injection 从 DataFrame 单元格现取
            "declared_original": _declared(base_values, "revenue"),
            "field": f"income_statement.{REVENUE_COLUMN}",
        }
        picked = _pick_target(targets, index)
        if picked is not None:
            # round-3 二阶段真靶点：claim 实际引用 + 可重算字段（按实例轮换）
            return {
                "op": "dict_path_value",
                "target": picked["root"],
                "path": list(picked["path"]),
                "factor": factor,
                "field": picked["field_ref"],
                "fallback": cell,
            }
        if index % 2 == 1:
            # 无 claim 靶点时保留旧单元格形态（真跑实证它到不了产物）
            return cell
        # 偶数实例：真靶点 = 技术面 context 的「常用派生值」表（`_build_technical_context`
        # 明示 analyst 直接引用、field_ref 前缀 derived_series.），且 derived_series 在
        # `citation._COMPUTATIONAL_RECALC` 重算注册表里 → A3 值级校验能从 kline 重算并抓
        # 出偏差。round-1 打利润表格单元格：analyst 从不逐字引用 → 7/8 真跑单元判 void。
        return {
            "op": "derived_series_value",
            "target": DERIVED_SERIES_TARGET,
            "key": DERIVED_SERIES_KEY,
            "factor": factor,
            "fallback": cell,
            "field": f"{DERIVED_SERIES_TARGET}.{DERIVED_SERIES_KEY}",
        }
    if pollution_type == "direction_error":
        return {"set": {"growth": -float(base_values["growth"])}, "field": "growth"}
    if pollution_type == "unit_error":
        return {"set": {"revenue": float(base_values["revenue"]) * 1.0e8}, "field": "revenue"}
    if pollution_type == "period_shift":
        return {"set": {"period_label": "2026Q3", "period_value": 0.0}, "field": "period_label"}
    if pollution_type == "mirror_narrative":
        # 真靶点：技术面 context 直读 `technical_indicators`（序列与 kline 等长升序，
        # 但 analyst 只看到这份预计算序列，原始 kline 从不直接读）——round-1 打 kline
        # 全判 void；kline 行序保留为回落形态。
        return {
            "op": "reverse_series",
            "target": TECHNICAL_TARGET,
            "path": list(TECHNICAL_MA_PATH),
            "fallback": {"op": "reverse_rows", "target": "kline", "field": "kline"},
            "field": f"{TECHNICAL_TARGET}.{'.'.join(TECHNICAL_MA_PATH)}",
        }
    if pollution_type == "fabricated_event":
        # round-3 重校：只注入 claim（编造事件），**不**写 news_list——同源注入会让
        # A6 回声匹配必然 PASS（出处自造），单元不携带机制信息（round-2 10/10 案例如此）。
        return {
            "op": "fabricated_claim",
            "target": "analyst_reports",
            "claim_text": FABRICATED_EVENT_TITLE,
            "field": "analyst_reports",
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
    """序列行倒序（mirror_narrative 回落形态）：按正序读序列者会把最旧一期当最新。"""
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


def _apply_reverse_series(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """嵌套 dict 里的 list 序列倒序（mirror_narrative 真靶点）。

    倒序后「末位 = 最旧一期」：按正序读的 LLM 会把最旧值当最新一期（镜像叙事）。
    污染标记 = 倒序后末位起第一个数值（均线窗口前导 None 跳过）——即正序读法会
    当成「最新」引用的那个值；解析不出数值时标记为空（不伪造）。
    """
    target = str(payload.get("target") or TECHNICAL_TARGET)
    path = [str(part) for part in (payload.get("path") or list(TECHNICAL_MA_PATH))]
    path_label = f"{target}.{'.'.join(path)}"
    field_names = (str(payload.get("field") or path_label), target)
    node: Any = out.get(target)
    for part in path[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
    values = node.get(path[-1]) if isinstance(node, dict) else None
    if not isinstance(values, list) or len(values) < 2:
        return _fallback_effect(
            out, payload, field_names, f"缺目标序列 {path_label}（list 形态且长度 ≥ 2）"
        )
    reversed_values = list(reversed(values))
    node[path[-1]] = reversed_values
    marker = _last_number(reversed_values)
    return InjectionEffect(
        True,
        target=path_label,
        polluted_values=(marker,) if marker is not None else (),
        field_names=field_names,
    )


def _apply_derived_series_value(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """派生值 dict 指定键 × factor（value_error 真靶点）。

    `derived_series` 是技术面 context 明示 analyst 直接引用的字段，且在
    `citation._COMPUTATIONAL_RECALC` 重算注册表里（A3 可从 kline 重算抓偏差）。
    缺该结构 / 目标键非数值（数据不足的真实形态）→ 回落 `payload["fallback"]`。
    """
    target = str(payload.get("target") or DERIVED_SERIES_TARGET)
    key = str(payload.get("key") or DERIVED_SERIES_KEY)
    field_names = (f"{target}.{key}", target)
    series = out.get(target)
    if not isinstance(series, dict) or not _is_number(series.get(key)):
        return _fallback_effect(
            out, payload, field_names, f"缺可用派生值 {target}.{key}（dict 形态且该键为数值）"
        )
    original = float(series[key])
    polluted = original * float(payload.get("factor") or 1.0)
    series[key] = polluted
    return InjectionEffect(
        True,
        target=f"{target}.{key}",
        polluted_values=(polluted,),
        field_names=field_names,
    )


def _fallback_effect(
    out: dict[str, Any], payload: dict[str, Any], field_names: Sequence[str], reason: str
) -> InjectionEffect:
    """主靶点不可用时的回落：按 `payload["fallback"]` 的 op 施加，并如实记回落说明。

    回落形态必须可审计（`reason` 里点名「回落」）：否则报告只能从 target 反推打到哪个靶点。
    回落也失败 → `applied=False`，reason 同时保留主靶点与回落失败原因（两处都要看得见）。
    """
    fallback = payload.get("fallback") or {}
    handler = _OPS.get(str(fallback.get("op") or ""))
    primary = "/".join(field_names)
    if handler is None:
        return _noop(primary, f"{reason}；且载荷缺可用的回落 op", field_names)
    effect = handler(out, fallback)
    if not effect.applied:
        return _noop(effect.target or primary, f"{reason}；回落失败：{effect.reason}", field_names)
    return InjectionEffect(
        True,
        reason=f"{reason}；已回落 {fallback.get('op')}→{effect.target}",
        target=effect.target,
        polluted_values=effect.polluted_values,
        field_names=effect.field_names,
    )


def _apply_dict_path_value(out: dict[str, Any], payload: dict[str, Any]) -> InjectionEffect:
    """嵌套 dict 数值叶子 × factor（value_error 真靶点，round-3 二阶段）。

    靶点 = **产物 claim 实际引用且校验器可重算**的字段（如 `profitability_metrics.ROE.2025`）：
    round-2 打利润表格 / 派生值都到不了产物（分析师不逐字引用），而重算注册表里的字段
    既是分析师会引用的，又让 A3 能从原始数据重算抓偏差。叶子缺失/非数值 → 回落并如实记原因。
    """
    root = str(payload.get("target") or "")
    path = [str(part) for part in (payload.get("path") or [])]
    label = ".".join([root, *path])
    field_names = (str(payload.get("field") or label), root)
    node: Any = out.get(root)
    for part in path[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
    if not path or not isinstance(node, dict) or not _is_number(node.get(path[-1])):
        return _fallback_effect(out, payload, field_names, f"缺可用数值叶子 {label}")
    original = float(node[path[-1]])
    polluted = original * float(payload.get("factor") or 1.0)
    node[path[-1]] = polluted
    return InjectionEffect(True, target=label, polluted_values=(polluted,), field_names=field_names)


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
    "derived_series_value": _apply_derived_series_value,
    "reverse_rows": _apply_reverse_rows,
    "reverse_series": _apply_reverse_series,
    "dict_path_value": _apply_dict_path_value,
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


def _last_number(values: Sequence[Any]) -> float | None:
    """从序列末位往前找第一个数值（None = 数据不足期，跳过）；取不到返回 None。"""
    for value in reversed(list(values)):
        if _is_number(value):
            return float(value)
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
