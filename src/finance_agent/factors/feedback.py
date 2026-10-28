"""跨会话因子记忆桥（收割侧 → 生成侧）。

RD-Agent 的 feedback 只在**同一 trace**（会话）内传递（`trace.hist`）；新会话的历史为空，
于是同一经典因子会换名字反复提案（实测 `mom20`/`mom_20d`/`MOM_20D`/`momentum_20d` 四条同式）。
本模块把本系统已探索/已裁决的因子导出成生成侧可读的清单。

产物：``data/factor_research/explored_factors.json``（消费方：distro 内
``rdagent/scenarios/qlib/proposal/explored_feedback.py``，经环境变量
``FACTOR_FEEDBACK_PATH`` 指向同一路径）。

口径：
- 表达式按 DSL 规范化（``1e-6`` 以下常数视为 0）去重——epsilon 守卫变体不应各占一条；
- 排序：admitted → untranslatable（保底配额，最近优先）→ rejected（闸②优先）→
  transcription_error，各档内按时间倒序；
- ``untranslatable_quota``：untranslatable 条目的保底配额（默认 20）。该档条目
  远超 rejected 档（实测 991 vs 190），若按原「状态优先」整体截断会被结构性
  饿死——生成侧永远看不到「某机制族不可译」的经验，同一族因子（如残差×反转）
  跨会话反复重提。配额不足时空位归还 rejected；
- untranslatable 条目紧凑化（desc 60 字符 / detail 40 字符）：渲染侧有字符预算，
  配额条目必须挤得进去；
- rejected 档 per-gate 保底分配（``PER_GATE_FLOOR``，不足全量）：单一闸口不得独占
  出场位——按闸号升序整体截断曾把闸④（REDUNDANT_WITH_POOL）结构性饿死（闸②累计
  数百条占满全部出场位），生成侧永远收不到「与池冗余」的拒收经验；余量按各闸累计
  拒收量占比分配，保底/占比未用满的空位归还使用；
- 条目数由 ``limit`` 截断（默认 80），供 prompt 预算使用；
- ``contract`` 块（复杂度上限 / 算子与字段白名单 / 表达规则）+ ``gate_rejections`` 聚合——
  生成侧表达契约回喂（feed-dsl-contract-to-factor-generator）：数值与白名单 import
  单一真相源（``dsl`` 复杂度常量、``qlib_translate`` 映射表），不复制字面量；
- 任何字段缺失如实留空，不编造。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.factors.dsl import (
    MAX_AST_DEPTH,
    MAX_FIELDS,
    MAX_FREE_PARAMS,
    Call,
    Number,
    parse,
    serialize,
)
from finance_agent.factors.qlib_translate import FIELD_MAPPINGS, OPERATOR_MAPPINGS

DEFAULT_OUTPUT_PATH = "data/factor_research/explored_factors.json"
DEFAULT_LIMIT = 80
# untranslatable 保底配额：该档经验（机制族不可译）决定生成侧是否重提同族因子，
# 按状态优先整体截断会使其被 admitted+rejected 挤成 0 条，故单列配额。
UNTRANSLATABLE_QUOTA = 20
# rejected 档每闸保底出场数：出现过拒收的闸口至少出场 N 条（累计不足则全量），
# 防「排序 + 总量截断」把低频闸口饿死。
PER_GATE_FLOOR = 5
_MAX_DESC = 160
_MAX_DESC_UNTRANSLATABLE = 60
_MAX_DETAIL = 80
_MAX_DETAIL_UNTRANSLATABLE = 40

#: 表达规则文本（生成侧 prompt 语言=英文；三条对应实测转译损失前三死因：
#: ε 符号常量无值、横截面运算缺算子、矩阵/回归/ML 流水线超出 DSL 边界）。
CONTRACT_RULES: tuple[str, ...] = (
    "Numeric constants must be concrete numbers (e.g. write 1e-12); symbolic constants "
    "such as epsilon are inexpressible and the proposal is rejected.",
    "Per-instrument time-series expressions only: cross-sectional (same-day cross-stock) "
    "operations such as cross-sectional percentile rank or market-wide averages are inexpressible.",
    "Matrix operations, rolling regressions, and machine-learning pipelines are outside "
    "the expressible DSL.",
)

_STATUS_ORDER = {"admitted": 0, "rejected": 1, "untranslatable": 2, "transcription_error": 3}


def _contract_block() -> dict[str, Any]:
    """DSL 表达契约块（数值与白名单全部 import 取值，单一真相源自动跟随）。"""
    return {
        "complexity": {
            "max_ast_depth": MAX_AST_DEPTH,
            "max_free_params": MAX_FREE_PARAMS,
            "max_fields": MAX_FIELDS,
        },
        "operators": sorted(k for k, m in OPERATOR_MAPPINGS.items() if m.dsl_name),
        "fields": sorted(k for k, f in FIELD_MAPPINGS.items() if f.dsl_field),
        "rules": list(CONTRACT_RULES),
    }


def canonical_expression(expression: str) -> str | None:
    """DSL 表达式规范化（epsilon 归零 + 可交换算子定序）；解析失败 → None。"""
    try:
        node = parse(expression)
    except Exception:
        return None
    return serialize(_canon(node))


def _canon(node: Any) -> Any:
    if isinstance(node, Number):
        if isinstance(node.value, float) and abs(node.value) < 1e-6:
            return Number(0)
        return node
    if isinstance(node, Call):
        args = tuple(_canon(arg) for arg in node.args)
        if node.op in {"add", "mul"}:
            args = tuple(sorted(args, key=serialize))
        # epsilon 守卫归一：x+0 / x-0 / x*1 / x/1 → x
        if node.op == "add":
            rest = [a for a in args if not (isinstance(a, Number) and a.value == 0)]
            if len(rest) == 1:
                return rest[0]
        if node.op == "mul":
            rest = [a for a in args if not (isinstance(a, Number) and a.value == 1)]
            if len(rest) == 1:
                return rest[0]
        if node.op in {"sub", "div"} and len(args) == 2:
            right = args[1]
            if isinstance(right, Number) and right.value == (0 if node.op == "sub" else 1):
                return args[0]
        return Call(node.op, args)
    return node


def _reason(
    verdict: Mapping[str, Any], *, detail_chars: int = _MAX_DETAIL
) -> tuple[int | None, str | None]:
    gate = verdict.get("gate")
    reason = verdict.get("reason")
    if gate is None and verdict.get("status") == "untranslatable":
        stage = verdict.get("stage")
        detail = str(verdict.get("detail") or "").strip()
        return None, f"untranslatable:{stage}" + (f"（{detail[:detail_chars]}）" if detail else "")
    return (int(gate) if isinstance(gate, int) else None), (str(reason) if reason else None)


def _entry_stage(entry: dict[str, Any]) -> str:
    """失败类别（stage）：从 reason 前缀 ``untranslatable:{stage}（detail）`` 解析。"""
    reason = str(entry.get("reason") or "")
    if reason.startswith("untranslatable:"):
        return reason.split("（", 1)[0][len("untranslatable:") :].strip() or "unknown"
    return "unknown"


def _select_untranslatable(entries: list[dict[str, Any]], quota: int) -> list[dict[str, Any]]:
    """untranslatable 配额选取：按日轮转（防单批垄断）× 批内按 stage 交织（防单类垄断）。

    收割一批常在同一分钟内产出数十条不可译候选——纯「最近优先」会让配额被最后
    一批吃满，早期批次的机制族（如残差×反转）永远看不到；同一批内单一失败类别
    （如「缺横截面算子」最大桶）同样会吃满整档。外层按 ``at`` 日期轮转、内层按
    stage 交织，每轮各取一条；结果回按时间倒序呈现。
    """
    if quota <= 0:
        return []
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for entry in entries:  # 已按时间倒序
        day = str(entry.get("at") or "")[:10]
        buckets.setdefault(day, {}).setdefault(_entry_stage(entry), []).append(entry)
    days = sorted(buckets, reverse=True)
    picked: list[dict[str, Any]] = []
    while len(picked) < quota:
        progressed = False
        for day in days:
            for stage in sorted(buckets[day]):
                queue = buckets[day][stage]
                if queue and len(picked) < quota:
                    picked.append(queue.pop(0))
                    progressed = True
            if len(picked) >= quota:
                break
        if not progressed:
            break
    picked.sort(key=lambda e: str(e["at"]), reverse=True)
    return picked


def _allocate_rejected(
    entries: list[dict[str, Any]], budget: int, floor: int = PER_GATE_FLOOR
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """rejected 档 per-gate 保底分配。

    三段：① 保底——闸号升序逐闸取满 ``floor`` 条（该闸累计不足则全量出场，
    预算不足时后闸拿剩余）；② 余量——按各闸剩余池占比分配（最大余数法取整）；
    ③ 回填——某闸池耗尽导致的缺额按闸号升序归还其他闸。预算先截顶到
    ``min(budget, len(entries))``，绝不虚占。返回 (选中条目, 按闸号分配读数)；
    选中项保持闸号升序、闸内时间倒序（``entries`` 须已按时间倒序）。
    """
    by_gate: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        gate = entry["gate"] if isinstance(entry["gate"], int) else 99
        by_gate.setdefault(gate, []).append(entry)
    gates = sorted(by_gate)
    counts = dict.fromkeys(gates, 0)
    budget = max(0, min(budget, len(entries)))
    for gate in gates:
        if sum(counts.values()) >= budget:
            break
        counts[gate] += min(floor, len(by_gate[gate]), budget - sum(counts.values()))
    remaining = budget - sum(counts.values())
    if remaining > 0:
        pools = {gate: len(by_gate[gate]) - counts[gate] for gate in gates}
        pool_total = sum(pools.values())
        if pool_total > 0:
            shares = {gate: remaining * pools[gate] // pool_total for gate in gates}
            leftover = remaining - sum(shares.values())
            order = sorted(gates, key=lambda g: (-(remaining * pools[g] % pool_total), g))
            for gate in order[:leftover]:
                shares[gate] += 1
            for gate in gates:
                counts[gate] += min(shares[gate], pools[gate])
        while sum(counts.values()) < budget:
            progressed = False
            for gate in gates:
                if counts[gate] < len(by_gate[gate]) and sum(counts.values()) < budget:
                    counts[gate] += 1
                    progressed = True
            if not progressed:
                break
    selected: list[dict[str, Any]] = []
    for gate in gates:
        selected.extend(by_gate[gate][: counts[gate]])
    return selected, {str(gate): counts[gate] for gate in gates}


def build_payload(
    processed: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = DEFAULT_LIMIT,
    untranslatable_quota: int = UNTRANSLATABLE_QUOTA,
) -> dict[str, Any]:
    """状态 + 候选表 → 生成侧清单载荷（按规范化表达式去重、分档配额、截断）。

    分档顺序：admitted（全部）→ untranslatable（最近 ``untranslatable_quota`` 条）
    → rejected（按闸号升序）→ transcription_error。untranslatable 不足配额时空位
    归还 rejected；``limit`` 仍是总量上限。
    """
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    gate_rejections: dict[str, int] = {}
    for name, verdict in processed.items():
        if not isinstance(verdict, Mapping):
            continue
        status = str(verdict.get("status") or "")
        if status not in _STATUS_ORDER:
            continue
        expression = verdict.get("expression")
        key = canonical_expression(str(expression)) if expression else None
        if key is None:
            key = f"name:{name}"
        if key in seen:
            continue
        seen.add(key)
        candidate = candidates.get(name) or {}
        description = " ".join(str(candidate.get("description") or "").split())
        compact = status == "untranslatable"
        gate, reason = _reason(
            verdict,
            detail_chars=_MAX_DETAIL_UNTRANSLATABLE if compact else _MAX_DETAIL,
        )
        if status == "rejected" and isinstance(gate, int):
            gate_rejections[str(gate)] = gate_rejections.get(str(gate), 0) + 1
        entries.append(
            {
                "name": name,
                "desc": description[: _MAX_DESC_UNTRANSLATABLE if compact else _MAX_DESC],
                "status": status,
                "gate": gate,
                "reason": reason,
                "expression": key if key.startswith("name:") is False else None,
                "at": str(verdict.get("at") or ""),
            }
        )

    entries.sort(key=lambda e: str(e["at"]), reverse=True)
    admitted = [e for e in entries if e["status"] == "admitted"]
    untranslatable = [e for e in entries if e["status"] == "untranslatable"]
    rejected = [e for e in entries if e["status"] == "rejected"]
    transcription = [e for e in entries if e["status"] == "transcription_error"]
    transcription.sort(key=lambda e: e["gate"] if isinstance(e["gate"], int) else 99)

    quota = max(0, min(untranslatable_quota, len(untranslatable)))
    picked_untranslatable = _select_untranslatable(untranslatable, quota)
    rejected_budget = max(0, limit - len(admitted) - len(picked_untranslatable))
    picked_rejected = _allocate_rejected(rejected, rejected_budget)[0]
    ordered = admitted + picked_untranslatable + picked_rejected + transcription
    trimmed = ordered[: max(limit, 0)]
    rejected_by_gate: dict[str, int] = {}
    untranslatable_by_stage: dict[str, int] = {}
    for entry in trimmed:  # 从实际出场统计，读数与清单天然一致
        if entry["status"] == "rejected" and isinstance(entry["gate"], int):
            key = str(entry["gate"])
            rejected_by_gate[key] = rejected_by_gate.get(key, 0) + 1
        elif entry["status"] == "untranslatable":
            stage = _entry_stage(entry)
            untranslatable_by_stage[stage] = untranslatable_by_stage.get(stage, 0) + 1
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "total_explored": len(entries),
        "returned": len(trimmed),
        "counts": {
            status: sum(1 for e in entries if e["status"] == status) for status in _STATUS_ORDER
        },
        "gate_rejections": dict(sorted(gate_rejections.items())),
        "quota_allocation": {
            "rejected_by_gate": dict(sorted(rejected_by_gate.items())),
            "untranslatable_by_stage": dict(sorted(untranslatable_by_stage.items())),
        },
        "contract": _contract_block(),
        "explored": trimmed,
    }


def load_state(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    processed = payload.get("processed") if isinstance(payload, dict) else None
    if not isinstance(processed, dict):
        raise ValueError(f"收割状态缺少 processed 对象：{path}")
    return processed


def load_candidates(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"候选文件缺少 candidates 数组：{path}")
    return {str(c["name"]): c for c in items if isinstance(c, dict) and c.get("name")}


def write_payload(payload: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)
    return target


def export_feedback(
    *,
    state_path: str | Path,
    candidates_path: str | Path,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    limit: int = DEFAULT_LIMIT,
    untranslatable_quota: int = UNTRANSLATABLE_QUOTA,
) -> dict[str, Any]:
    """一步导出（CLI 与测试共用）：返回写出载荷。"""
    processed = load_state(state_path)
    candidates = load_candidates(candidates_path)
    payload = build_payload(
        processed,
        candidates,
        limit=limit,
        untranslatable_quota=untranslatable_quota,
    )
    write_payload(payload, output_path)
    return payload
