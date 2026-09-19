"""P1 注入法反幻觉 pilot 跑批器（spec causal-ablation「注入式反幻觉消融」/「注入成本结构二分」）。

本模块是 P1 预登记（`evals/ablation/preregister/2026-09-16-p1-injection-pilot.md`）的执行体：
标的 × 8 类污染 × 4 实例 × 2 态（机制开/关），主指标 = 逃逸率（人工终裁后才出 rate）。

**产物 schema（product）**——由独立材料生成脚本产出，本模块只收发

    {
      "ticker": "600519",
      "snapshot_digest": "<evals.ablation.snapshot_digest>",
      "snapshot": { ... fetch_data + compute_metrics 的可重放 state 子集（含 DataFrame）,
                    另须含注入基值 revenue / price / growth / entry ... },
                    # 真跑腿的注入目标即此快照的真实结构（P1 重校轮换靶点，见 injection 模块
                    # docstring）：value_error → derived_series（缺则回落 income_statement）、
                    # mirror_narrative → technical_indicators.MA.5（缺则回落 kline）、
                    # illegal_price → 决策 dict（trader_plan / final_trade_decision）——
                    # 前两者缺结构时 apply_injection 记 applied=False，单元判 void（预判，零消耗）；
                    # 决策 dict 缺失时走**后置流程**（跑图产出决策 → 内存注入），不预判 void
      "base_values": {"revenue": 1.0e9, "price": 100.0, "growth": -0.1005, "entry": 100.0},
      "analyst_reports": {agent: {"claims": [claim dict], "markdown": str}},
      "citation": { ... 原始产物的 citation 通道快照（可选，透传）... },
      "paths": {"json": "<json 摘要>", "pickle": "<DataFrame 侧车>"}
    }

落盘用「JSON 摘要 + pickle 侧车」双文件：DataFrame 走 pickle，JSON 只留可读摘要（含两条路径）。
`save_product` 返回的两条路径即离线重放须记录的重放来源产物路径（spec「离线重放不消耗 LLM」）。

**两型实验单元**

- 离线重放型（`COST_CLASS[type] == "offline_replay"`，A2/A3/A4/A6/A7）：对已有产物 claims 注入后
  重放引用校验链，**零 LLM**（`run_citation_chain` 的修复步骤默认不参与：`repair_fn=None`
  → 不调用 LLM；显式传 `repair_fn` 才引入，且须计入预算）。
- 真跑型（`"real_run"`，A1/A5 输入侧联动与决策层）：`apply_injection` 污染快照 → 跑变体图
  （`value_error` / `mirror_narrative` → analysts 变体；`illegal_price` → full 变体 + 价位 sanity 步；
  产物快照无决策 dict 时 → `_run_post_graph_decision_case`：跑图产出决策 → 内存注入 → A5 两态校验），
  `graph_runner` 可注入（测试用 fake，零 LLM）。

**机制开关**：`mechanism_toggle(mechanism_id, on=..., surface=...)` 是显式补丁上下文管理器——
只替换该机制登记在本面的补丁点，退出即还原；未登记面的机制（A7 时效标记无校验侧消费者）
用 `state_transform` 表达（剥时效标记），同样随开关记录在案（spec「开关只影响目标机制」）。
"""

from __future__ import annotations

import copy
import importlib
import json
import math
import pickle
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from evals.causal_ablation.escape import (
    EscapePair,
    classify_state,
    escape_rate,
    mcnemar_table,
)
from evals.causal_ablation.injection import (
    COST_CLASS,
    DECISION_KEYS,
    FABRICATED_EVENT_TITLE,
    POLLUTION_TYPES,
    InjectionCase,
    apply_injection,
    build_injection_cases,
)
from evals.causal_ablation.pilot_calibration import calibration_verdict
from evals.causal_ablation.preregister import (
    Preregistration,
    assert_preregistered,
)
from evals.causal_ablation.units import UnitJudgment

# ── 常量 ──

LEG_OFFLINE = "offline_replay"
LEG_REAL = "real_run"
LEG_FROZEN = "frozen_replay"
STATUS_OK = "ok"
STATUS_VOID = "void_injection"
STATUS_ERROR = "error"

OFFLINE_TYPES: tuple[str, ...] = tuple(t for t in POLLUTION_TYPES if COST_CLASS[t] == LEG_OFFLINE)
REAL_TYPES: tuple[str, ...] = tuple(t for t in POLLUTION_TYPES if COST_CLASS[t] == LEG_REAL)

DEFAULT_PREREG_DIR = Path("evals/ablation/preregister")
# 门禁只认本实验的预登记文档（文件名子串）：同目录的后续实验（P2 族 B、A4 补测）
# 各有自己的预登记，不得顶替 P1 的门禁口径（2026-09-17 实测踩到）
PREREG_P1_NAME = "p1-injection-pilot"
DEFAULT_QUERY = "综合评估财务与投资价值"

# 每型校准判词下限：样本不足只报 insufficient（不得用 n 太小的数字下结论）
MIN_PAIRS_FOR_CALIBRATION = 10
# 单点修复门槛（同 citation_node：该分析师同轮 value_mismatch < 3 处才稀疏修复）
SPARSE_REPAIR_LIMIT = 3
# 终裁上限（预登记停止规则）
ADJUDICATION_LIMIT = 300
# 预登记停止规则的健康区间
DISSONANT_MIN, DISSONANT_MAX = 0.10, 0.90

# 可测面分型（spec letter：拦截 = 终态产物不含污染值 或有 FAIL 标记）。
#   "fail"         → 校验链能产 FAIL（拦截 = 被污染 claim 有 FAIL/修复）
#   "traceability" → 机制只产 PASS ↔ UNVERIFIABLE（A6 回声匹配）：拦截 = **未静默放行**
#                    （该 claim 在产物里无出处可溯 → 判定非 PASS）。round-2 把假事件与
#                    假引用同源注入 → 回声必然 PASS，10/10 案例不携带 A6 信息；round-3
#                    改为只注入 claim（见 `_inject_fabricated_event`），该面才可测。
#   "input_side"   → 机制无校验侧消费者（A7 时效标记只喂分析师 context）：改读**输入侧
#                    presence 证据**（`macro_freshness_warning`），不进不一致比读法。
#   "none"         → 无任何已定义可测面 → 报告 blind_spots 显式披露（拦截率 0 不得读作
#                    机制无价值）。
FLAG_SURFACE_KIND: dict[str, str] = {
    "value_error": "fail",
    "direction_error": "fail",
    "unit_error": "fail",
    "period_shift": "fail",
    "mirror_narrative": "none",
    "fabricated_event": "traceability",
    "stale_macro": "input_side",
    "illegal_price": "fail",
}
# 「校验侧 FAIL 面」布尔视图（历史口径，by_type 报告沿用）
VERIFICATION_FLAG_SURFACE: dict[str, bool] = {
    pollution_type: kind == "fail" for pollution_type, kind in FLAG_SURFACE_KIND.items()
}

REQUIRED_BASE_VALUES: tuple[str, ...] = ("revenue", "price", "growth", "entry")


class ProductError(ValueError):
    """产物读写/结构错误。"""


class MissingProductError(ProductError):
    """产物缺失（须先跑材料生成步骤）。"""


class InjectionError(ValueError):
    """注入不可执行（类型走错腿、目标 claim 缺失、基值缺失等）。"""


class MechanismSwitchError(ValueError):
    """机制开关未登记（不得静默空转）。"""


# ── 产物读写 ──

# 落 pickle 的部分（DataFrame 承载区）：快照 + 分析师产物
_PICKLE_KEYS: tuple[str, ...] = ("snapshot", "analyst_reports")


def product_summary(product: dict) -> dict:
    """JSON 摘要（可读、可 diff）：只留标量/结构摘要，DataFrame 明细走 pickle 侧车。"""
    reports = product.get("analyst_reports") or {}
    snapshot = product.get("snapshot") or {}
    return {
        "pilot": "p1-injection-pilot-product",
        "ticker": product.get("ticker"),
        "snapshot_digest": product.get("snapshot_digest"),
        "snapshot_keys": sorted(snapshot),
        "snapshot_summary": {
            k: (f"{type(v).__name__}{getattr(v, 'shape', '')}" if not _is_scalar(v) else v)
            for k, v in sorted(snapshot.items())
        },
        "base_values": product.get("base_values") or {},
        "citation": product.get("citation") or {},
        # 材料回填留痕（compute 输出键补齐：补了哪些键 / 摘要前后）——不得静默改产物
        "snapshot_backfill": product.get("snapshot_backfill"),
        "analyst_reports": {
            agent: {
                "claims": len(report.get("claims") or []),
                "markdown_chars": len(report.get("markdown") or ""),
            }
            for agent, report in sorted(reports.items())
        },
        "paths": dict(product.get("paths") or {}),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_product(path: Path, product: dict) -> dict[str, str]:
    """写 JSON 摘要 + pickle 侧车（DataFrame 承载区），返回两条路径（POSIX 风格）。"""
    path = Path(path)
    pickle_path = path.with_suffix(".pkl")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: product.get(k) for k in _PICKLE_KEYS}
    payload["paths"] = {"json": path.as_posix(), "pickle": pickle_path.as_posix()}
    payload["ticker"] = product.get("ticker")
    payload["snapshot_digest"] = product.get("snapshot_digest")
    with pickle_path.open("wb") as fh:
        pickle.dump(payload, fh)
    summary = product_summary(product)
    paths = {"json": path.as_posix(), "pickle": pickle_path.as_posix()}
    summary["paths"] = paths
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


def load_product(path: Path) -> dict:
    """读产物：path 可为 JSON 摘要或 pickle 侧车；两文件齐备时以 pickle 为准（DataFrame 保真）。"""
    path = Path(path)
    json_path = path if path.suffix != ".pkl" else path.with_suffix(".json")
    pickle_path = path if path.suffix == ".pkl" else path.with_suffix(".pkl")
    if not json_path.exists() and not pickle_path.exists():
        raise MissingProductError(f"产物不存在：{json_path}（先跑材料生成步骤）")
    summary: dict = {}
    if json_path.exists():
        summary = json.loads(json_path.read_text(encoding="utf-8"))
    payload: dict = {}
    if pickle_path.exists():
        with pickle_path.open("rb") as fh:
            loaded = pickle.load(fh)  # noqa: S301 - 自产自用的本地产物
        payload = loaded if isinstance(loaded, dict) else {}
    product = {
        "ticker": payload.get("ticker") or summary.get("ticker"),
        "snapshot_digest": payload.get("snapshot_digest") or summary.get("snapshot_digest"),
        "snapshot": payload.get("snapshot") or {},
        "analyst_reports": payload.get("analyst_reports") or {},
        "citation": summary.get("citation") or {},
        "base_values": summary.get("base_values") or {},
        "paths": {
            "json": (summary.get("paths") or {}).get("json") or json_path.as_posix(),
            "pickle": (summary.get("paths") or {}).get("pickle") or pickle_path.as_posix(),
        },
    }
    if not product["snapshot"] and not product["analyst_reports"]:
        raise ProductError(f"产物内容为空（{json_path}）：须同时落 JSON 摘要与 pickle 侧车")
    return product


def product_path(materials_dir: Path, ticker: str) -> Path:
    return Path(materials_dir) / f"{ticker}.json"


def load_products(materials_dir: Path, tickers: Sequence[str]) -> dict[str, dict]:
    """批量读产物；缺产物显式报错并提示材料生成步骤（spec：SHALL NOT 静默降级）。"""
    materials_dir = Path(materials_dir)
    missing = [t for t in tickers if not product_path(materials_dir, t).exists()]
    if missing:
        raise MissingProductError(
            f"缺产物 {missing}（目录 {materials_dir.as_posix()}）："
            f"先跑材料生成步骤（`uv run python tests/scripts/p1_injection_pilot.py --materials-only`），"
            f"本 CLI 不自动生成材料（真跑型成本须单独申报）"
        )
    return {t: load_product(product_path(materials_dir, t)) for t in tickers}


def base_values_from_product(product: dict) -> dict[str, float]:
    """注入基值：`base_values` 优先，缺项回退快照同名键；缺任一必填项即显式报错。"""
    snapshot = product.get("snapshot") or {}
    declared = dict(product.get("base_values") or {})
    values: dict[str, float] = {}
    for key in REQUIRED_BASE_VALUES:
        value = declared.get(key, snapshot.get(key))
        if not _is_number(value):
            raise InjectionError(
                f"产物缺注入基值 {key!r}（须在 base_values 或 snapshot 中提供）："
                f"缺基值无法构造确定性污染 payload"
            )
        values[key] = float(value)  # type: ignore[arg-type]
    return values


def claim_value_targets(product: dict) -> list[dict]:
    """产物 claim 实际引用且**校验器可重算**的字段（value_error 真跑靶点，round-3 二阶段）。

    round-2 真跑实证：打利润表单元格 / 派生值 `chg_5d` 都到不了产物（分析师不逐字引用）。
    靶点条件：① field_ref 根在 `citation._COMPUTATIONAL_RECALC`（A3 能从原始数据重算 →
    注入的偏差可被检出）；② 该路径在快照里解析为数值（注入有目标）。按被引用次数降序
    （分析师越常引用，注入越可能到达产物）。解析与重算口径复用校验器单一实现。
    """
    from finance_agent import citation as citation_mod

    snapshot = product.get("snapshot") or {}
    counts: dict[str, int] = {}
    for _, _, claim in _flat_claims(product.get("analyst_reports") or {}):
        field_ref = str(claim.get("field_ref") or "")
        parts = field_ref.split(".")
        if len(parts) < 2 or parts[0] not in citation_mod._COMPUTATIONAL_RECALC:
            continue
        try:
            value = citation_mod._resolve_field_ref(field_ref, snapshot, None)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            continue
        if not _is_number(value):
            continue
        counts[field_ref] = counts.get(field_ref, 0) + 1
    return [
        {
            "root": ref.split(".")[0],
            "path": ref.split(".")[1:],
            "field_ref": ref,
            "citations": n,
        }
        for ref, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def build_pilot_cases(
    product: dict,
    *,
    instances: int = 4,
    pollution_types: Sequence[str] | None = None,
    seed: int = 0,
) -> list[InjectionCase]:
    """按「8 类污染 × instances 实例」构造该标的的确定性用例（同 seed 同结果）。"""
    base = base_values_from_product(product)
    ticker = str(product.get("ticker") or "")
    if not ticker:
        raise ProductError("产物缺 ticker：用例 id 须携带标的")
    cases: list[InjectionCase] = []
    targets = claim_value_targets(product)
    for pollution_type in pollution_types or list(POLLUTION_TYPES):
        cases.extend(
            build_injection_cases(
                pollution_type,
                ticker=ticker,
                base_values=base,
                n=instances,
                seed=seed,
                targets=targets,
            )
        )
    return cases


# ── 离线注入 ──


def _flat_claims(reports: dict) -> list[tuple[str, int, dict]]:
    """按 agent 字典序展开 claims：(agent, agent 内下标, claim)。"""
    out: list[tuple[str, int, dict]] = []
    for agent in sorted(reports):
        for i, claim in enumerate(reports[agent].get("claims") or []):
            out.append((agent, i, claim))
    return out


def inject_offline(product: dict, case: InjectionCase) -> dict:
    """确定性离线注入（深拷贝，不动入参）：按污染类型改写 claims 或 state 时效输入。

    只支持离线重放型（校验侧污染）；真跑型走 `run_real_case` 的 `apply_injection`。
    目标 claim 缺失时显式报错（不静默跳过——静默会让「逃逸率」掺入「没注入」的单元）。
    """
    if case.pollution_type not in OFFLINE_TYPES:
        raise InjectionError(
            f"{case.pollution_type} 为真跑型（COST_CLASS={COST_CLASS[case.pollution_type]}），"
            f"不得走离线重放（spec 注入成本结构二分）；离线仅支持 {OFFLINE_TYPES}"
        )
    out: dict[str, Any] = {
        "ticker": product.get("ticker"),
        "snapshot_digest": product.get("snapshot_digest"),
        "snapshot": copy.deepcopy(product.get("snapshot") or {}),
        "analyst_reports": copy.deepcopy(product.get("analyst_reports") or {}),
        "citation": copy.deepcopy(product.get("citation") or {}),
        "base_values": dict(product.get("base_values") or {}),
        "paths": dict(product.get("paths") or {}),
    }
    injection: dict[str, Any] = {
        "case_id": case.case_id,
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "mutated_agent": None,
        "mutated_claim_index": None,
        "mutated_flat_index": None,
        "mutated_fields": [],
        "injected_values": {},
        "polluted_values": _offline_polluted_values(case),
        "notes": [],
    }
    handler = _OFFLINE_INJECTORS[case.pollution_type]
    handler(out, case, injection)
    out["injection"] = injection
    return out


def _injection_effective(original: dict, polluted: dict, snapshot: dict) -> bool:
    """污染是否**真产生**可检偏差：用校验器单一实现跑原/污染两版 claim（口径不得复制）。

    round-2 实证两类空转（`tests/validation/2026-09-16-p1-injection-pilot-validation.md` §9.1）：
    ① 方向翻转在 stated 自带符号时对齐后有效值不变（-1.21 + positive → eff 仍 -1.21）；
    ② 单位 ×1e8 在部分标的上恰是合法换算（亿元→元），污染值落在容差内。
    判据：原版**非 FAIL** 且污染版 **FAIL**——即 FAIL 由污染造成，而非 claim 本来就有的
    覆盖缺口（后者会冒充「拦下」）。
    """
    from finance_agent import citation as citation_mod

    try:
        orig = citation_mod.verify_claims([citation_mod.Claim.model_validate(original)], snapshot)
        poll = citation_mod.verify_claims([citation_mod.Claim.model_validate(polluted)], snapshot)
    except Exception:  # noqa: BLE001 - 脏 claim 不能作为注入目标
        return False
    if not orig or not poll:
        return False
    return orig[0].status != "FAIL" and poll[0].status == "FAIL"


def _effective_direction_flip(claim: dict, snapshot: dict) -> str | None:
    """可产生有效方向污染的翻转方向；None = 该 claim 不携带方向信息（跳过）。"""
    if str(claim.get("direction") or "") not in ("positive", "negative"):
        return None
    flipped = "positive" if claim["direction"] == "negative" else "negative"
    return (
        flipped if _injection_effective(claim, {**claim, "direction": flipped}, snapshot) else None
    )


# 单位误用的倍率候选（取首个**产生真偏差**者）：先试域内常见换算（亿 1e8 / 万 1e4），
# 被校验器归一化吸收则退到未注册量级（百万 1e6 / 千 1e3）。
# 依据：校验器按设计把 万/亿/percent 换算归一（避免误报），故「恰好等于某注册换算」的
# 倍率不构成错误——round-2 的 002415 即此形态（141.95 亿元 ×1e8 = 真值 元）。
_UNIT_ERROR_FACTORS: tuple[float, ...] = (1.0e8, 1.0e4, 1.0e6, 1.0e3)


def _effective_unit_error(claim: dict, snapshot: dict) -> float | None:
    """可产生有效单位污染的污染值；None = 该 claim 不携带单位信息（跳过）。"""
    stated = claim.get("stated_value")
    if not _is_number(stated):
        return None
    for factor in _UNIT_ERROR_FACTORS:
        polluted_value = float(cast(Any, stated)) * factor
        if _injection_effective(claim, {**claim, "stated_value": polluted_value}, snapshot):
            return polluted_value
    return None


def _inject_direction_error(out: dict, case: InjectionCase, injection: dict) -> None:
    """方向污染（round-3 重校：只选**翻转产生有效偏差**的 claim）。

    round-2 取首个有符号 claim 直接翻转：stated 自带符号时 `eff = stated×declared_sign`
    对齐后不变 → A3 无从拦，20/40 单元是空操作（判词见 §9.1）。重校判据走校验器单一实现。
    """
    snapshot = out["snapshot"]
    target = _select_flat_claim(
        out["analyst_reports"],
        lambda claim: _effective_direction_flip(claim, snapshot) is not None,
        case.pollution_type,
    )
    agent, idx, flat = target
    claim = out["analyst_reports"][agent]["claims"][idx]
    flipped = cast(str, _effective_direction_flip(claim, snapshot))
    claim["direction"] = flipped
    injection.update(
        mutated_agent=agent,
        mutated_claim_index=idx,
        mutated_flat_index=flat,
        mutated_fields=[f"{agent}.claims[{idx}].direction"],
        injected_values={
            "direction": flipped,
            "field_ref": claim.get("field_ref"),
            "stated_value": claim.get("stated_value"),
        },
        # 真实污染标记 = 翻转后的方向词。**不是**载荷 `set` 里的申报对照值——那个值从未
        # 被施加，若写进报告，人工终裁会按错误标记去产物里找污染（实测被问到过）。
        polluted_values=[flipped],
    )


def _inject_unit_error(out: dict, case: InjectionCase, injection: dict) -> None:
    """单位污染（round-3 重校：倍率取首个**真产生偏差**者）。

    round-2 固定 ×1e8：在部分标的上恰是合法换算（亿元→元）→ 污染值与真值同量级，
    4/40 单元不携带信息（判词见 §9.1）。候选倍率 1e8/1e4（亿/万 误用）。
    """
    snapshot = out["snapshot"]
    target = _select_flat_claim(
        out["analyst_reports"],
        lambda claim: _effective_unit_error(claim, snapshot) is not None,
        case.pollution_type,
    )
    agent, idx, flat = target
    claim = out["analyst_reports"][agent]["claims"][idx]
    polluted = cast(float, _effective_unit_error(claim, snapshot))
    claim["stated_value"] = polluted
    injection.update(
        mutated_agent=agent,
        mutated_claim_index=idx,
        mutated_flat_index=flat,
        mutated_fields=[f"{agent}.claims[{idx}].stated_value"],
        injected_values={"stated_value": polluted},
        polluted_values=[polluted],
    )


def _inject_period_shift(out: dict, case: InjectionCase, injection: dict) -> None:
    """期次污染双形态（P1 重校轮）：按实例序号交替，确定性（无 RNG）。

    round-1 校准实证：只改 `period` 标签时唯一能拦它的机制是 A2 期次检查 → 不一致比
    1.000（too_hard，矩阵单点依赖 A2）。重校形态：

    - form A（`label_only`，**偶数**实例，= 现状）：只把 `claim["period"]` 改成载荷标签；
    - form B（`label_and_ref`，奇数实例）：同时把 claim 的 `field_ref` 期次段改到一个
      **相邻真实期次**（如 2025 → 2024，该期真值与申报值不同）→ 期次与值级两路都不一致，
      A2 关态下值级校验也能拦（两态都 caught → 该实例不再携带 A2 信息）；
    - 相邻期次解析不出真值、或真值与申报值一致时回落 form A，并如实记
      `shift_form="label_only_fallback"`（不假造 form B 的拦截效果）。
    """
    from finance_agent.metric_vocab import field_ref_period_segment

    target = _select_flat_claim(
        out["analyst_reports"],
        lambda claim: (
            bool(claim.get("period"))
            and field_ref_period_segment(str(claim.get("field_ref") or "")) is not None
        ),
        case.pollution_type,
    )
    agent, idx, flat = target
    claim = out["analyst_reports"][agent]["claims"][idx]
    label = str((case.payload.get("set") or {}).get("period_label") or "2026Q3")
    variant = "label_and_ref" if _case_instance_index(case.case_id) % 2 == 1 else "label_only"
    mutated_fields = [f"{agent}.claims[{idx}].period"]
    injected_values: dict[str, Any] = {"period": label}
    shift: dict[str, Any] | None = None
    if variant == "label_and_ref":
        shift = _adjacent_period_shift(claim, out["snapshot"])
        if shift is None:
            variant = "label_only_fallback"  # 无相邻真实期次可换 → 回落 label-only
        else:
            claim["field_ref"] = shift["field_ref"]
            mutated_fields.append(f"{agent}.claims[{idx}].field_ref")
            injected_values["field_ref"] = shift["field_ref"]
            injected_values["period_segment"] = {"from": shift["from"], "to": shift["to"]}
    claim["period"] = label
    injection.update(
        mutated_agent=agent,
        mutated_claim_index=idx,
        mutated_flat_index=flat,
        mutated_fields=mutated_fields,
        injected_values=injected_values,
        polluted_values=[label],
        shift_form=variant,
        notes=(
            [
                f"form B：field_ref 期次段 {shift['from']} → {shift['to']}"
                f"（该期真值 {shift['ground_truth']!r} ≠ 申报值 → 值级校验两态都能拦）"
            ]
            if shift is not None
            else []
        ),
    )


def _case_instance_index(case_id: str) -> int:
    """用例实例序号（`{ticker}-{pollution_type}-{i}` 末段）；解析不出记 0（= form A）。

    形态分派只用实例序号（无 RNG）：同 case_id 同形态，重跑可复现。
    """
    tail = str(case_id).rsplit("-", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def _adjacent_periods(segment: str) -> list[str]:
    """期次段的相邻期次候选：先上一年，再下一年（保留季度/日期尾部形态）。"""
    match = re.match(r"^((?:19|20)\d{2})(.*)$", segment)
    if match is None:
        return []
    year, tail = int(match.group(1)), match.group(2)
    return [f"{year - 1:04d}{tail}", f"{year + 1:04d}{tail}"]


def _replace_period_segment(field_ref: str, segment: str, candidate: str) -> str | None:
    """把 field_ref 里的期次段替换为相邻期次（首个命中段，段内括号索引形态亦支持）。"""
    parts = field_ref.split(".")
    for i, part in enumerate(parts):
        if re.sub(r"\[(-?\d+)\]$", "", part) == segment:
            parts[i] = candidate
            return ".".join(parts)
    return None


def _adjacent_period_shift(claim: dict, snapshot: dict) -> dict[str, Any] | None:
    """form B 的期次段改写：只有「相邻期次真值可解析且与申报值不同」才采用。

    真值解析复用校验器的单一实现（`citation._resolve_field_ref`，与数值型值级校验同一
    路径）与同一容差（`citation.value_close`）——口径不得复制。解析不出（期次不在真实
    数据里）或真值与申报值一致（值级校验抓不到）→ None（调用方回落 label-only）。
    """
    from finance_agent import citation as citation_mod
    from finance_agent.metric_vocab import field_ref_period_segment

    field_ref = str(claim.get("field_ref") or "")
    segment = field_ref_period_segment(field_ref)
    stated = claim.get("stated_value")
    if segment is None or not _is_number(stated):
        return None
    for candidate in _adjacent_periods(segment):
        shifted_ref = _replace_period_segment(field_ref, segment, candidate)
        if shifted_ref is None:
            continue
        try:
            ground_truth = citation_mod._resolve_field_ref(
                shifted_ref, snapshot, str(claim.get("period") or "") or None
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            continue
        if not _is_number(ground_truth):
            continue
        stated_number = float(cast(Any, stated))
        ground_truth_number = float(cast(Any, ground_truth))
        if not citation_mod.value_close(stated_number, ground_truth_number):
            return {
                "field_ref": shifted_ref,
                "from": segment,
                "to": candidate,
                "ground_truth": ground_truth_number,
            }
    return None


def _inject_fabricated_event(out: dict, case: InjectionCase, injection: dict) -> None:
    """编造事件（round-3 重校：**只**注入 claim，不写 news_list）。

    round-2 把假新闻与假引用同源注入 → 回声匹配必然 PASS（出处是注入自造的），
    10/10 案例不携带 A6 信息。真实形态是「claim 断言的事件在输入里不存在」：
    A6 ON → 无出处可溯 → UNVERIFIABLE（可追溯性面「未静默放行」）；A6 OFF → 一律 PASS。
    """
    headline = str(case.payload.get("claim_text") or FABRICATED_EVENT_TITLE)
    reports = out["analyst_reports"]
    agent = sorted(reports)[-1]
    reports[agent].setdefault("claims", []).append(
        {
            "claim_type": "entity",
            "source_type": "event",
            "field_ref": headline,
            "stated_value": headline,
            "interpretation": headline,
        }
    )
    idx = len(reports[agent]["claims"]) - 1
    injection.update(
        mutated_agent=agent,
        mutated_claim_index=idx,
        mutated_flat_index=len(_flat_claims(reports)) - 1,
        mutated_fields=[f"{agent}.claims[{idx}]"],
        injected_values={"field_ref": headline},
        polluted_values=[headline],
        notes=[
            "只注入 claim（不写 news_list）：污染形态 = 断言的事件在输入里不存在；"
            "A6 ON 判 UNVERIFIABLE（可追溯性面），A6 OFF 一律 PASS"
        ],
    )


def _inject_stale_macro(out: dict, case: InjectionCase, injection: dict) -> None:
    """时效污染打在真实结构上：macro_indicators 各项的 as_of_date + freshness
    （fetch_macro_indicators 的守卫结构，见 data/akshare_client.py::_with_freshness）。"""
    snapshot = out["snapshot"]
    stale_as_of = str(case.payload.get("as_of_date") or "2026-05-01")
    freshness = str(case.payload.get("freshness") or "stale")
    fields: list[str] = []
    macro = snapshot.get("macro_indicators") or {}
    for key, value in macro.items():
        if not isinstance(value, dict):
            continue
        value["as_of_date"] = stale_as_of
        value["freshness"] = freshness
        fields.append(f"macro_indicators.{key}")
    injection.update(
        mutated_fields=fields,
        injected_values={"as_of_date": stale_as_of, "freshness": freshness},
        polluted_values=[stale_as_of],
        notes=["A7 时效标记无校验侧消费者：开关只改「喂给校验链的 state」是否带标记"],
    )


_OFFLINE_INJECTORS: dict[str, Callable[[dict, InjectionCase, dict], None]] = {
    "direction_error": _inject_direction_error,
    "unit_error": _inject_unit_error,
    "period_shift": _inject_period_shift,
    "fabricated_event": _inject_fabricated_event,
    "stale_macro": _inject_stale_macro,
}


def _select_flat_claim(
    reports: dict, predicate: Callable[[dict], bool], pollution_type: str
) -> tuple[str, int, int]:
    for flat, (agent, idx, claim) in enumerate(_flat_claims(reports)):
        if predicate(claim):
            return agent, idx, flat
    raise InjectionError(
        f"{pollution_type} 注入无可选目标 claim（谓词不满足任何 claim）："
        f"产物须含该型可污染的 claim，否则该单元不含注入信息，不得静默跳过"
    )


def _is_signed_claim(claim: dict) -> bool:
    """符号型 claim 判定复用校验器单一实现（口径不得复制：见 citation._is_signed_claim）。"""
    from finance_agent.citation import _is_signed_claim as citation_is_signed

    try:
        from finance_agent.citation import Claim

        return citation_is_signed(Claim.model_validate(claim))
    except Exception:  # noqa: BLE001 - 脏 claim 不能作为注入目标，按非符号型处理
        return False


# ── 机制开关 ──


@dataclass(frozen=True)
class _PatchSpec:
    module: str
    attr: str
    replacement: Any
    note: str

    @property
    def target(self) -> str:
        return f"{self.module}.{self.attr}"


def _a3_off_verify_claims(claims: list, state: dict) -> list:
    """A3 OFF（校验面）：跳过校验链——claims 原样通过，verdicts 空（spec letter）。"""
    return []


def _a3_off_verify_citations_node(state: dict) -> dict:
    """A3 OFF（图面）：校验节点旁路——claims 直通，citation 通道全 PASS。"""
    from finance_agent.citation import CitationReport

    return {
        "citation_report": CitationReport.from_results([]).model_dump(),
        "citation_pass": True,
        "citation_blocked": False,
        "citation_analyst_true_fail": 0,
        "citation_verifier_normalized": 0,
        "value_mismatch_repaired": 0,
        "citation_fail_buckets": {},
        "citation_fail_rates": list(state.get("citation_fail_rates") or []) + [0.0],
        "iteration_count": int(state.get("iteration_count") or 0) + 1,
    }


def _a2_off_check_period(claim: Any, state: dict) -> tuple[None, bool]:
    """A2 OFF（校验面）：期次检查跳过（返回 None = 无 FAIL、不计缺口）。"""
    return None, False


def _a2_off_semantic_header(direction: str, latest_label: str, count: int) -> str:
    """A2 OFF（context 面）：语义头不再注入（关掉「序列方向」这一路输入侧防线）。"""
    return ""


def _a4_off_repair_claims(markdown: str, failures: list[dict], llm_config: Any = None):
    """A4 OFF：单点修复回路 no-op（不改正文、不产修复记录）。"""
    return markdown, []


def _a5_off_validate_trade_prices(state: dict) -> dict:
    """A5 OFF：价位 sanity 跳过（直接放行，不产出 fail/corrected 告警）。"""
    return {"price_check": {"result": "pass", "note": "A5 关闭（消融）：价位 sanity 未运行"}}


def _a6_off_verify_textual(claim: Any, state: dict) -> Any:
    """A6 OFF：文本 claim 回声匹配被绕过——一律 PASS（编造事件不再被识别）。"""
    from finance_agent.citation import CitationResult

    return CitationResult(status="PASS", claim=claim)


_PATCHES: dict[tuple[str, str], tuple[_PatchSpec, ...]] = {
    ("A2", "verification"): (
        _PatchSpec("finance_agent.citation", "_check_period", _a2_off_check_period, "期次检查"),
    ),
    ("A2", "context"): (
        _PatchSpec(
            "finance_agent.nodes.analysts",
            "_series_semantic_header",
            _a2_off_semantic_header,
            "序列语义头",
        ),
    ),
    ("A3", "verification"): (
        _PatchSpec("finance_agent.citation", "verify_claims", _a3_off_verify_claims, "校验链"),
    ),
    ("A3", "graph"): (
        _PatchSpec(
            "evals.ablation", "verify_citations", _a3_off_verify_citations_node, "校验图节点"
        ),
    ),
    ("A4", "verification"): (
        _PatchSpec(
            "finance_agent.nodes.citation_repair",
            "repair_claims",
            _a4_off_repair_claims,
            "单点修复",
        ),
    ),
    ("A5", "decision"): (
        _PatchSpec(
            "finance_agent.nodes.validate",
            "validate_trade_prices",
            _a5_off_validate_trade_prices,
            "价位 sanity",
        ),
    ),
    ("A6", "verification"): (
        _PatchSpec("finance_agent.citation", "_verify_textual", _a6_off_verify_textual, "回声匹配"),
    ),
    ("A7", "verification"): (),
}

# 无模块消费者的机制：开关落在「喂给校验链的 state」上（A7 时效标记只有分析师 context 消费）
_STATE_TRANSFORMS: dict[str, str] = {"A7": "strip_macro_freshness_marks"}


def surface_for(mechanism_id: str) -> str:
    """机制的默认开关面（离线腿的校验面；真跑腿按注入点取 context/decision/graph）。"""
    if mechanism_id == "A5":
        return "decision"
    if mechanism_id == "A1":
        return "graph"
    return "verification"


def available_surfaces(mechanism_id: str) -> tuple[str, ...]:
    return tuple(sorted(s for (m, s) in _PATCHES if m == mechanism_id))


def patch_targets(mechanism_id: str, *, surface: str | None = None) -> tuple[str, ...]:
    """该机制在本面的补丁点（登记面；ON 态不施加补丁，只登记）。"""
    resolved = surface or surface_for(mechanism_id)
    specs = _patches_for(mechanism_id, resolved)
    return tuple(spec.target for spec in specs)


def _patches_for(mechanism_id: str, surface: str) -> tuple[_PatchSpec, ...]:
    if (mechanism_id, surface) not in _PATCHES:
        raise MechanismSwitchError(
            f"机制 {mechanism_id} 未登记开关面 {surface!r}"
            f"（可用面：{available_surfaces(mechanism_id) or '无'}；"
            f"已登记机制：{sorted({m for m, _ in _PATCHES})}）"
        )
    return _PATCHES[(mechanism_id, surface)]


@dataclass(frozen=True)
class ToggleRecord:
    """一次机制开关的完整记录（供单元载荷与审查：补丁了哪些点、剥了哪些标记）。"""

    mechanism_id: str
    on: bool
    surface: str
    patch_targets: tuple[str, ...]
    patched: tuple[str, ...]
    state_transform: str | None
    notes: tuple[str, ...] = ()

    def transform_state(self, state: dict) -> dict:
        """把开关作用到 state 上（A7 OFF = 剥时效标记）；无变换时原样返回。"""
        if self.state_transform == "strip_macro_freshness_marks":
            return strip_macro_freshness_marks(state)
        return state

    def payload(self) -> dict:
        return {
            "mechanism_id": self.mechanism_id,
            "on": self.on,
            "surface": self.surface,
            "patch_targets": list(self.patch_targets),
            "patched": list(self.patched),
            "state_transform": self.state_transform,
            "notes": list(self.notes),
        }


@contextmanager
def mechanism_toggle(
    mechanism_id: str, *, on: bool, surface: str | None = None
) -> Iterator[ToggleRecord]:
    """机制开关上下文：OFF 时只替换该机制在本面的补丁点，退出即还原（不污染全局）。"""
    resolved = surface or surface_for(mechanism_id)
    specs = _patches_for(mechanism_id, resolved)
    originals: list[tuple[Any, str, Any]] = []
    patched: list[str] = []
    notes: list[str] = []
    transform = None if on else _STATE_TRANSFORMS.get(mechanism_id)
    if not on:
        for spec in specs:
            module = importlib.import_module(spec.module)
            originals.append((module, spec.attr, getattr(module, spec.attr)))
            setattr(module, spec.attr, spec.replacement)
            patched.append(spec.target)
            notes.append(f"{spec.target} → {spec.note}关闭")
        if transform:
            notes.append(f"state 变换：{transform}（该机制无模块补丁点）")
    record = ToggleRecord(
        mechanism_id=mechanism_id,
        on=on,
        surface=resolved,
        patch_targets=tuple(spec.target for spec in specs),
        patched=tuple(patched),
        state_transform=transform,
        notes=tuple(notes),
    )
    try:
        yield record
    finally:
        for module, attr, original in reversed(originals):
            setattr(module, attr, original)


def strip_macro_freshness_marks(state: dict) -> dict:
    """剥掉喂给校验链的 state 里的宏观时效标记（macro_as_of / as_of_date / freshness）。

    数据本体（records）与其余键原样；深拷贝，不动入参。A7 的开关面即此变换——
    当前代码库无「时效标记 → FAIL」的校验侧消费者（标记只喂分析师 context），
    故该型在校验链上不产生拦截（报告 blind_spots 显式披露）。
    """
    out = dict(state)
    out.pop("macro_as_of", None)
    macro = state.get("macro_indicators")
    if isinstance(macro, dict):
        stripped: dict[str, Any] = {}
        for key, value in macro.items():
            if isinstance(value, dict):
                cleaned = {k: v for k, v in value.items() if k not in ("freshness", "as_of_date")}
                stripped[key] = cleaned
            else:
                stripped[key] = value
        out["macro_indicators"] = stripped
    return out


def freshness_marks_present(state: dict) -> bool:
    """校验链所见 state 是否携带时效标记（A7 开/关的可观测差）。"""
    if "macro_as_of" in state:
        return True
    macro = state.get("macro_indicators") or {}
    if isinstance(macro, dict):
        return any(
            isinstance(value, dict) and bool(value.get("freshness") or value.get("as_of_date"))
            for value in macro.values()
        )
    return False


# ── 离线引用校验链 ──


def resolve_module_repair() -> Callable[..., Any]:
    """引用修复入口按模块属性解析（A4 补丁才生效；不得把函数对象提前绑死）。"""
    from finance_agent.nodes import citation_repair

    return citation_repair.repair_claims


def run_citation_chain(
    claims: list[Any],
    state: dict,
    *,
    markdown: str = "",
    a4_on: bool = False,
    repair_fn: Callable[..., Any] | str | None = None,
    claim_agents: Sequence[str] | None = None,
) -> dict:
    """离线校验链：verify_claims（可被 A3 补丁旁路）→ 稀疏 value_mismatch 单点修复（可被 A4 补丁 no-op）。

    `repair_fn=None`（默认）表示修复步骤不参与——离线腿零 LLM 由构造保证；
    显式传 callable 或 `"module"`（按模块属性解析）才引入修复，且须计入预算。

    `claim_agents` 给出每条 claim 所属分析师时，**重校验仲裁按该分析师的范围**（同
    citation_node：修复后只要求被修复分析师的 claims 全 PASS）——不传则按全库判
    （默认保持原语义；全库判会把别处的无关 FAIL 算到修复头上，A4 补测实测踩到）。
    """
    from finance_agent import citation as citation_mod

    # 入参放宽：claim dict（产物原始形态）与 Claim 对象都可，链内统一走 Claim（修复回填需要）
    claim_list = [
        c if isinstance(c, citation_mod.Claim) else citation_mod.Claim.model_validate(c)
        for c in claims
    ]
    verdicts = list(citation_mod.verify_claims(claim_list, state))
    repair_participated = False
    repaired = False
    repair_records: list[dict] = []
    repaired_indices: list[int] = []
    sparse = [
        (i, v)
        for i, v in enumerate(verdicts)
        if v.status == "FAIL" and v.bucket == "value_mismatch"
    ]
    replaced: dict[int, Any] = {}
    if a4_on and repair_fn is not None and 0 < len(sparse) < SPARSE_REPAIR_LIMIT:
        fn: Callable[..., Any]
        if isinstance(repair_fn, str):
            if repair_fn != "module":
                raise InjectionError(
                    f"未知 repair_fn 标识 {repair_fn!r}（只接受 callable 或 'module'=按模块属性解析）"
                )
            fn = resolve_module_repair()
        else:
            fn = repair_fn
        failures = [
            {"agent": None, "claim": v.claim, "ground_truth": v.ground_truth} for _, v in sparse
        ]
        new_markdown, repair_records = fn(markdown, failures)
        repair_participated = True
        replaced = {
            idx: rec["updated_claim"]
            for (idx, _), rec in zip(sparse, repair_records, strict=False)
            if isinstance(rec, dict) and rec.get("updated_claim") is not None
        }
        if replaced:
            updated_claims = [replaced.get(i, c) for i, c in enumerate(claim_list)]
            scope = _reverify_scope(updated_claims, replaced, claim_agents)
            re_verdicts = list(
                citation_mod.verify_claims([updated_claims[i] for i in scope], state)
            )
            if all(v.status != "FAIL" for v in re_verdicts):
                # 重校验是仲裁（同 citation_node 语义）：修复范围内全 PASS 才算修复成功
                merged = list(verdicts)
                for slot, index in enumerate(scope):
                    merged[index] = re_verdicts[slot]
                claim_list, verdicts, repaired = updated_claims, merged, True
                repaired_indices = sorted(replaced)
                markdown = new_markdown
    fail_indices = [i for i, v in enumerate(verdicts) if v.status == "FAIL"]
    fail_buckets: dict[str, int] = {}
    for verdict in verdicts:
        if verdict.status == "FAIL" and verdict.bucket:
            fail_buckets[verdict.bucket] = fail_buckets.get(verdict.bucket, 0) + 1
    return {
        "claims": [_claim_payload(c) for c in claim_list],
        "verdicts": [_verdict_payload(v) for v in verdicts],
        "flagged": bool(fail_indices or repaired),
        "fail_indices": fail_indices,
        "repaired_indices": repaired_indices,
        "repaired": repaired,
        "repair_participated": repair_participated,
        "repair_records": [
            {k: v for k, v in rec.items() if k != "updated_claim"}
            for rec in repair_records
            if isinstance(rec, dict)
        ],
        "fail_buckets": fail_buckets,
        "unverifiable": sum(1 for v in verdicts if v.status == "UNVERIFIABLE"),
        # 重校验仲裁口径（None = 修复未参与）："agent" = 按被修复分析师（同 citation_node），
        # "all" = 全库。附全库读数供交叉核对（全库口径会把别处的无关 FAIL 算到修复头上）。
        # 修复产出的 claim（下标 → payload；被仲裁拒收时也从这里可查，供 A4 补测核对
        # 「LLM 改写值对不对」与「流水线是否记账」两件事分开读）
        "repaired_claims": {
            str(idx): _claim_payload(claim) for idx, claim in sorted(replaced.items())
        }
        if repair_participated
        else {},
        "reverify_scope": (
            None if not repair_participated else ("agent" if claim_agents else "all")
        ),
        "reverify_global_all_pass": (
            None if not repair_participated else all(v.status != "FAIL" for v in verdicts)
        ),
        # 链末正文（保守口径：重校验未全过时**不**采纳 LLM 改写，见上方仲裁分支）。
        # A4 补测读「正文里还有没有错值」须看它；生产在重校验失败时保留改写并回退
        # 全量重试（citation_node 先回填再重校验），该差异由 `repair_records` 交代。
        "markdown": markdown,
    }


def _reverify_scope(
    updated_claims: list[Any],
    replaced: dict[int, Any],
    claim_agents: Sequence[str] | None,
) -> list[int]:
    """重校验范围：给了归属就取被修复分析师的全部 claim（同 citation_node），否则全库。"""
    if claim_agents is None:
        return list(range(len(updated_claims)))
    agents = {claim_agents[i] for i in replaced}
    return [i for i, agent in enumerate(claim_agents) if agent in agents]


def _claim_payload(claim: Any) -> dict:
    if hasattr(claim, "model_dump"):
        return dict(claim.model_dump())
    return dict(claim)


def _verdict_payload(verdict: Any) -> dict:
    return {
        "status": verdict.status,
        "bucket": verdict.bucket,
        "field_ref": verdict.claim.field_ref,
        "ground_truth": verdict.ground_truth,
        "delta": verdict.delta,
        "unit_normalized": verdict.unit_normalized,
        "coverage_gap": verdict.coverage_gap,
        "claim": _claim_payload(verdict.claim),
    }


def replay_offline(
    product: dict,
    case: InjectionCase,
    *,
    mechanism_on: bool,
    a4_on: bool = False,
    repair_fn: Callable[..., Any] | str | None = None,
) -> dict:
    """离线重放单态：注入 → 在机制开/关下重放校验链 → 返回判定素材（零 LLM）。

    `flagged` 只认**被污染 claim** 上的 FAIL/修复（spec letter + 「误报不进分母」）：
    与污染无关的 FAIL 计入 `unrelated_fails` 供四桶拆报，SHALL NOT 被读成「拦下」。
    """
    polluted = inject_offline(product, case)
    reports = polluted["analyst_reports"]
    claims = list(_flatten_claim_dicts(reports))
    markdown = "\n\n".join(str(reports[a].get("markdown") or "") for a in sorted(reports))
    target = polluted["injection"].get("mutated_flat_index")
    with mechanism_toggle(case.mechanism_id, on=mechanism_on) as toggle:
        state = toggle.transform_state(polluted["snapshot"])
        chain = run_citation_chain(
            claims, state, markdown=markdown, a4_on=a4_on, repair_fn=repair_fn
        )
        marks = freshness_marks_present(state)
        warning = macro_freshness_warning(state) if case.pollution_type == "stale_macro" else None
    fail_indices = list(chain["fail_indices"])
    repaired_indices = list(chain["repaired_indices"])
    # state 侧污染（stale_macro）target 为 None：校验链上无 claim 级标记面 → 无拦截（记 False，
    # 非 None，以免与「没判定过」混淆；该机制的可观测差走 freshness_marks_present / warning）
    flagged = target is not None and _polluted_flagged(
        pollution_type=case.pollution_type,
        verdicts=chain["verdicts"],
        target=target,
        fail_indices=fail_indices,
        repaired_indices=repaired_indices,
    )
    other_fails = [i for i in fail_indices if i != target]
    return {
        "claims": chain["claims"],
        "verdicts": chain["verdicts"],
        "flagged": flagged,
        "polluted_claim_index": target,
        "unrelated_fails": other_fails,
        "repaired": chain["repaired"],
        "repair_participated": chain["repair_participated"],
        "repair_records": chain["repair_records"],
        "fail_buckets": chain["fail_buckets"],
        "unverifiable": chain["unverifiable"],
        "polluted_present": _polluted_present_offline(polluted, chain["claims"], case),
        "freshness_marks_present": marks,
        "freshness_warning_present": warning,
        "mechanism": toggle.payload(),
        "injection": dict(polluted["injection"]),
        "artifact_paths": dict(polluted["paths"]),
        "llm_calls": 0,
    }


def _polluted_flagged(
    *,
    pollution_type: str,
    verdicts: Sequence[dict],
    target: int,
    fail_indices: Sequence[int],
    repaired_indices: Sequence[int],
) -> bool:
    """被污染 claim 是否被拦下——按该型的可测面分型（`FLAG_SURFACE_KIND`）。

    `"traceability"`（A6）：机制只产 PASS ↔ UNVERIFIABLE，拦截 = **未静默放行**
    （判定非 PASS：UNVERIFIABLE/FAIL 都算「有面」）；其余型：拦截 = 被污染 claim 上有
    FAIL 或修复（spec letter）。把 UNVERIFIABLE 记成「拦下」仅限该型——它是该机制的
    全部输出面，不是通用口径。
    """
    if FLAG_SURFACE_KIND.get(pollution_type) == "traceability":
        if target >= len(verdicts):
            return False
        return str(verdicts[target].get("status") or "") != "PASS"
    return target in fail_indices or target in repaired_indices


def macro_freshness_warning(state: dict) -> bool:
    """分析师宏观 context 是否带时效告警（A7 的**输入侧**可观测面，零 LLM）。

    A7（`macro_freshness_mark`）无校验侧消费者：时效标记只被 `_build_macro_context`
    消费（`freshness == "stale"` → 追加「数据滞后」降级提示）。presence 检查复用 context
    构建器单一实现（口径不得复制），且 SHALL NOT 被读成拦截率。
    """
    from finance_agent.nodes.analysts import _build_macro_context

    return "数据滞后" in _build_macro_context(state)


def _flatten_claim_dicts(reports: dict) -> list[dict]:
    return [claim for _, _, claim in _flat_claims(reports)]


def _polluted_present_offline(polluted: dict, claims: list[dict], case: InjectionCase) -> bool:
    """污染值是否仍在送审 claim 里（修复会改写 claim → 可能已不在）。"""
    injection: dict[str, Any] = polluted["injection"]
    if case.pollution_type == "stale_macro":
        return _macro_stale_present(polluted["snapshot"], injection["injected_values"])
    flat = injection.get("mutated_flat_index")
    if flat is None or flat >= len(claims):
        return False
    claim: dict[str, Any] = claims[flat]
    injected: dict[str, Any] = injection["injected_values"]
    if case.pollution_type == "direction_error":
        return bool(claim.get("direction") == injected.get("direction"))
    if case.pollution_type == "unit_error":
        return bool(
            _is_number(claim.get("stated_value"))
            and _close(float(claim["stated_value"]), float(injected["stated_value"]))
        )
    if case.pollution_type == "period_shift":
        return bool(claim.get("period") == injected.get("period"))
    if case.pollution_type == "fabricated_event":
        return str(injected.get("field_ref")) in str(claim.get("field_ref") or "")
    return False


def _macro_stale_present(snapshot: dict, injected: dict) -> bool:
    """宏观时效污染是否仍在 state：任一指标项的 as_of_date/freshness 命中注入值。"""
    macro = snapshot.get("macro_indicators")
    if not isinstance(macro, dict):
        return False
    return any(
        isinstance(value, dict)
        and value.get("as_of_date") == injected.get("as_of_date")
        and value.get("freshness") == injected.get("freshness")
        for value in macro.values()
    )


def _offline_polluted_values(case: InjectionCase) -> list[Any]:
    """离线单元的污染标记：claim 侧取 payload set 值，新闻/宏观取注入的标题/日期。"""
    payload = case.payload
    values: list[Any] = list((payload.get("set") or {}).values())
    values += list(payload.get("claim_text") or [])
    if payload.get("as_of_date"):
        values.append(payload["as_of_date"])
    return values


def classify_offline(*, polluted_present: bool, flagged: bool) -> str:
    """逃逸判定（spec letter）：污染值在终态产物且无 FAIL 标记 = 逃逸。"""
    return classify_state(polluted_present=polluted_present, flagged=flagged)


def run_offline_case(
    product: dict,
    case: InjectionCase,
    *,
    a4_on: bool = False,
    repair_fn: Callable[..., Any] | str | None = None,
) -> tuple[EscapePair, dict]:
    """同一污染输入的机制开/关两态 → (配对, 单元载荷)。"""
    on = replay_offline(product, case, mechanism_on=True, a4_on=a4_on, repair_fn=repair_fn)
    off = replay_offline(product, case, mechanism_on=False, a4_on=a4_on, repair_fn=repair_fn)
    on_state = classify_offline(
        polluted_present=bool(on["polluted_present"]), flagged=bool(on["flagged"])
    )
    off_state = classify_offline(
        polluted_present=bool(off["polluted_present"]), flagged=bool(off["flagged"])
    )
    unit_id = f"{case.case_id}::{LEG_OFFLINE}"
    pair = EscapePair(case.case_id, on_state, off_state)
    unit = {
        "unit_id": unit_id,
        "case_id": case.case_id,
        "ticker": product.get("ticker"),
        "leg": LEG_OFFLINE,
        "cost_class": COST_CLASS[case.pollution_type],
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "on_state": on_state,
        "off_state": off_state,
        "status": STATUS_OK,
        "status_reason": "",
        "llm_calls": 0,
        "evidence_paths": _evidence_paths(product, unit_id),
        "injection": on["injection"],
        "on": on,
        "off": off,
    }
    input_side = _input_side_evidence_block(case.pollution_type, on=on, off=off)
    if input_side:
        unit["input_side"] = input_side
    return pair, unit


def _input_side_evidence_block(pollution_type: str, *, on: dict, off: dict) -> dict | None:
    """输入侧证据块（零 LLM presence 检查）：机制 ON/OFF 时告警是否进模型输入。

    只对 `FLAG_SURFACE_KIND == "input_side"` 的型产出（当前仅 A7/时效标记）——
    该机制在校验链上无消费者，读法按成本分型改走输入侧证据。
    """
    if FLAG_SURFACE_KIND.get(pollution_type) != "input_side":
        return None
    return {
        "surface": "context_freshness_warning",
        "present_on": bool(on.get("freshness_warning_present")),
        "present_off": bool(off.get("freshness_warning_present")),
        "note": (
            "输入侧 presence 检查（非拦截率）：A7 时效标记只喂分析师 context——"
            "ON 时「数据滞后」降级提示进入模型输入，OFF（剥标记）时不进"
        ),
    }


def _evidence_paths(product: dict, unit_id: str) -> list[str]:
    paths = [str(v) for v in (product.get("paths") or {}).values() if v]
    return paths + [f"unit:{unit_id}"]


# ── 冻结重放腿（round-3 三层分工：离线构造层的推荐形态）──


def freeze_analyst_output(
    product: dict, case: InjectionCase, *, frozen_dir: Path | None = None
) -> dict:
    """把「污染 context 下真跑一趟分析师」的产出落盘冻结（供重放与审计）。

    返回冻结产物（与产物同构：snapshot=污染快照 + analyst_reports=该趟产出）；
    `frozen_dir` 给定时同时落盘（`ticker.case_id.json/.pkl`），供人工核验与重放复现。
    """
    polluted, effect = apply_injection(product.get("snapshot") or {}, case)
    frozen = {
        "ticker": product.get("ticker"),
        "snapshot_digest": product.get("snapshot_digest"),
        "snapshot": polluted,
        "analyst_reports": {},
        "citation": {},
        "base_values": dict(product.get("base_values") or {}),
        "paths": dict(product.get("paths") or {}),
        "frozen_from": case.case_id,
    }
    if frozen_dir is not None:
        # 显式 .json 后缀：save_product 用 with_suffix(".pkl") 派生侧车，而
        # `ticker.case_id` 形态（如 600519.600519-value_error-0）的最后一段被当成后缀
        # → 同标的全部单元会写进同一个 .pkl 互相覆盖（冻结批实测踩到）
        frozen["paths"] = save_product(
            Path(frozen_dir) / f"{product.get('ticker')}.{case.case_id}.json", frozen
        )
    return frozen | {"injection_effect": effect.as_dict()}


def _normalize_reports(reports: Any) -> dict[str, dict]:
    """分析师产出归一为 dict（真实运行返回 pydantic 模型；离线载荷是 dict）。"""
    out: dict[str, dict] = {}
    for agent, report in (reports or {}).items():
        if hasattr(report, "model_dump"):
            out[str(agent)] = dict(report.model_dump())
        elif isinstance(report, dict):
            out[str(agent)] = report
    return out


def replay_frozen(
    frozen: dict,
    case: InjectionCase,
    *,
    mechanism_on: bool,
    a4_on: bool = False,
    repair_fn: Callable[..., Any] | str | None = None,
) -> dict:
    """冻结重放单态：对冻结 claim 重放校验链（机制开/关），**零 LLM**。

    `flagged` 只认**携带污染标记的 claim** 上的 FAIL/修复（与离线腿同口径：误报不进分母）。
    """
    reports = frozen.get("analyst_reports") or {}
    claims = list(_flatten_claim_dicts(reports))
    markdown = "\n\n".join(str(reports[a].get("markdown") or "") for a in sorted(reports))
    markers = list((frozen.get("injection_effect") or {}).get("polluted_values") or [])
    with mechanism_toggle(case.mechanism_id, on=mechanism_on) as toggle:
        state = toggle.transform_state(frozen.get("snapshot") or {})
        chain = run_citation_chain(
            claims, state, markdown=markdown, a4_on=a4_on, repair_fn=repair_fn
        )
    fail_indices = list(chain["fail_indices"])
    repaired_indices = list(chain["repaired_indices"])
    carriers = [
        i for i, claim in enumerate(chain["claims"]) if _claim_carries_marker(claim, markers)
    ]
    flagged = any(i in fail_indices or i in repaired_indices for i in carriers)
    return {
        "claims": chain["claims"],
        "verdicts": chain["verdicts"],
        "flagged": flagged,
        "carrier_indices": carriers,
        "fail_buckets": chain["fail_buckets"],
        "unverifiable": chain["unverifiable"],
        "polluted_present": bool(carriers),
        "mechanism": toggle.payload(),
        "llm_calls": 0,
    }


def _claim_carries_marker(claim: dict, markers: Sequence[Any]) -> bool:
    """claim 是否携带污染标记（值级命中：stated_value 与任一标记 close）。"""
    stated = claim.get("stated_value")
    if not _is_number(stated):
        return any(str(m) and str(m) in str(claim.get("field_ref") or "") for m in markers)
    return any(_is_number(m) and _close(float(stated), float(m)) for m in markers)


def run_frozen_case(
    product: dict,
    case: InjectionCase,
    *,
    graph_runner: Callable[..., dict] | None = None,
    llm_meter: Callable[[], int] | None = None,
    frozen_dir: Path | None = None,
    query: str = DEFAULT_QUERY,
) -> tuple[EscapePair, dict]:
    """冻结重放单元：**1 趟真跑（分析师）+ 2 态离线重放**。

    真跑层此前的主要缺陷是配对噪声（两态 = 两次独立 LLM 采样，写不写被污染字段随机）。
    冻结重放把分析师输出冻结为单一事实 → 两态只差机制这一个变量（确定性配对）。
    污染未到达冻结产物（无 claim 携带标记）→ void（**暴露未实现**，不含拦截信息——
    与「拦截」区分，这正是真跑腿此前被误记的那类）。
    """
    if case.pollution_type not in REAL_TYPES:
        raise InjectionError(f"{case.pollution_type} 非真跑型，冻结重放只承接 {REAL_TYPES}")
    frozen = freeze_analyst_output(product, case, frozen_dir=frozen_dir)
    effect_applied = bool((frozen.get("injection_effect") or {}).get("applied"))
    if not effect_applied:
        return _prevoid_real_case(
            product,
            case,
            injected=(frozen.get("injection_effect") or {}),
            variant="analysts",
            reason=f"注入不可施加（{(frozen.get('injection_effect') or {}).get('reason')}）",
        )
    before = _meter(llm_meter)
    state = dict(
        (graph_runner or default_graph_runner)(
            variant="analysts", snapshot=frozen["snapshot"], query=query
        )
    )
    after = _meter(llm_meter)
    calls = None if before is None or after is None else after - before
    frozen["analyst_reports"] = _normalize_reports(state.get("analyst_reports"))
    on = replay_frozen(frozen, case, mechanism_on=True)
    off = replay_frozen(frozen, case, mechanism_on=False)
    if not on["polluted_present"]:
        return _post_graph_void_case(
            product,
            case,
            variant="analysts",
            shared_runs=1,
            shared_calls=calls,
            reason=(
                "污染未到达冻结产物（无 claim 携带污染标记）：暴露未实现，不含拦截信息"
                "（不得按「不含该值 = 拦下」记）"
            ),
            injected=(frozen.get("injection_effect") or {}),
            leg=LEG_FROZEN,
        )
    on_state = classify_state(
        polluted_present=bool(on["polluted_present"]), flagged=bool(on["flagged"])
    )
    off_state = classify_state(
        polluted_present=bool(off["polluted_present"]), flagged=bool(off["flagged"])
    )
    unit_id = f"{case.case_id}::{LEG_FROZEN}"
    unit = {
        "unit_id": unit_id,
        "case_id": case.case_id,
        "ticker": product.get("ticker"),
        "leg": LEG_FROZEN,
        "cost_class": COST_CLASS[case.pollution_type],
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "variant": "analysts",
        "on_state": on_state,
        "off_state": off_state,
        "status": STATUS_OK,
        "status_reason": "",
        "llm_calls": calls,
        "graph_runs": 1,
        "evidence_paths": _evidence_paths(product, unit_id),
        "injection": frozen.get("injection_effect") or {},
        "frozen": {
            "frozen_claims": len(_flatten_claim_dicts(frozen["analyst_reports"])),
            "carrier_indices": on["carrier_indices"],
            "note": (
                "污染 context 下真跑一趟分析师并冻结其 claim 列表；两态重放只差机制开关"
                "（确定性配对，无采样噪声）"
            ),
        },
        "on": on,
        "off": off,
    }
    return EscapePair(case.case_id, on_state, off_state), unit


# ── 真跑腿 ──


def default_graph_runner(*, variant: str, snapshot: dict, query: str = DEFAULT_QUERY) -> dict:
    """默认真跑：复用 evals.ablation 的变体图（与主图同语义的层增量图）。"""
    from typing import cast

    from evals.ablation import Variant, build_variant_graph

    graph = build_variant_graph(cast(Variant, variant))
    return dict(graph.invoke({**snapshot, "focus": query}))


def _surface_for_point(injection_point: str) -> str:
    if injection_point == "context":
        return "context"
    if injection_point == "decision":
        return "decision"
    return "graph"


# 真跑腿的开关面按机制登记（与注入点无关）：A3 的校验链在图里（节点级）、
# A2 的语义头在 context 构建里、A5 的 sanity 在决策层节点里。
_REAL_RUN_SURFACES: dict[str, str] = {"A2": "context", "A3": "graph", "A5": "decision"}

# 管线真正消费的 state 键（分析师 context + 决策层价检的输入面）。真跑腿据此披露注入键
# 是否落在「模型/决策层看得见的输入」上——注入键不在消费面上即注入到不了产物（此时单元
# 判 void，不冒充「拦下」）。口径来源：`nodes/analysts.py` 各 _build_*_context 的 state 读取
# + `nodes/validate.py` 的决策输入（trader_plan/final_trade_decision/kline/price_levels）。
ANALYST_CONSUMED_KEYS: frozenset[str] = frozenset(
    {
        # 行情与个股
        "stock_code",
        "stock_name",
        "stock_quote",
        "kline",
        "benchmark_kline",
        "price_levels",
        # 三大报表与财务指标
        "income_statement",
        "balance_sheet",
        "cash_flow_statement",
        "financial_indicators",
        "profitability_metrics",
        "solvency_metrics",
        "efficiency_metrics",
        "cashflow_metrics",
        "growth_rates",
        "dupont_tree",
        "quarterly_trend",
        "traffic_lights",
        "anomalies",
        "health_score",
        "peer_comparison",
        "relative_valuation",
        "garp_result",
        # 技术序列
        "technical_indicators",
        "derived_series",
        # 宏观与行业
        "macro_indicators",
        "industry_info",
        # 舆情与事件
        "news_list",
        "key_events",
        "announcements",
        "research_reports",
        "share_unlock",
        "block_trades",
        # 决策层（A5 价位 sanity 的输入）
        "trader_plan",
        "final_trade_decision",
        "derived_metrics",
    }
)


def _real_surface(mechanism_id: str, injection_point: str) -> str:
    return _REAL_RUN_SURFACES.get(mechanism_id, _surface_for_point(injection_point))


def classify_terminal(state: dict, case: InjectionCase, *, injected: dict) -> dict:
    """真跑型终态判定：污染标记是否仍在产物（present）+ 是否有告警（flagged）。

    flagged 通道 = citation（被污染 claim 上的 FAIL，逐条结果缺失时退回四桶标量）
    与价位 sanity（fail / corrected）。与污染无关的 FAIL 只随四桶如实记录，
    不计作「拦下」（spec「误报不进分母」）。
    """
    citation = _citation_channels(
        state,
        markers=list(injected.get("polluted_values") or []),
        field_names=injected.get("field_names") or [],
    )
    decision = _decision_channels(state)
    return {
        "polluted_present": _markers_present(state, list(injected.get("polluted_values") or [])),
        "flagged": bool(citation["flagged"] or decision["flagged"]),
        "citation": citation,
        "decision": decision,
    }


def _citation_channels(state: dict, *, markers: list[Any], field_names: list[str]) -> dict:
    from evals.ablation import citation_buckets_from_state

    buckets = citation_buckets_from_state(state)
    results = ((state.get("citation_report") or {}).get("results")) or []
    addressed = 0
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "FAIL":
            continue
        if _claim_carries_pollution(
            result.get("claim") or {}, markers, field_names, str(result.get("ground_truth") or "")
        ):
            addressed += 1
    if results:
        # 逐条结果可得 → 只认被污染 claim 上的 FAIL（误报不冒充拦截）
        flagged = bool(addressed)
        scope = "polluted_claim"
    else:
        # 旧图/stub 不产逐条结果 → 退回四桶标量（如实标注口径）
        flagged = bool(
            buckets["blocked"] or buckets["analyst_true_fail"] or buckets["surgical_repaired"]
        )
        scope = "state_scalar"
    return {
        "buckets": buckets,
        "fail_buckets": dict(state.get("citation_fail_buckets") or {}),
        "unverifiable_text": int(state.get("citation_unverifiable_text") or 0),
        "addressed_fails": addressed,
        "flagged": flagged,
        "scope": scope,
    }


def _claim_carries_pollution(
    claim: dict, markers: list[Any], field_names: list[str], ground_truth: str = ""
) -> bool:
    """该 claim 是否指向污染（字段名命中或申报值/真值命中污染标记）。"""
    field_ref = str(claim.get("field_ref") or "")
    if any(name and name in field_ref for name in field_names):
        return True
    values = [claim.get("stated_value"), ground_truth]
    for value in values:
        if not _is_number(value):
            continue
        if any(
            _close(float(value), cand)  # type: ignore[arg-type]
            for marker in markers
            if _is_number(marker)
            for cand in _scale_candidates(float(marker))
        ):
            return True
    return False


def _decision_channels(state: dict) -> dict:
    check = dict(state.get("price_check") or {})
    return {"price_check": check, "flagged": check.get("result") in ("fail", "corrected")}


def _terminal_text(state: dict) -> str:
    parts: list[str] = []
    if state.get("final_report"):
        parts.append(str(state["final_report"]))
    for report in (state.get("analyst_reports") or {}).values():
        if isinstance(report, dict):
            parts.append(str(report.get("markdown") or ""))
            for claim in report.get("claims") or []:
                if isinstance(claim, dict):
                    parts.append(str(claim.get("interpretation") or ""))
    return "\n".join(parts)


def _terminal_numbers(state: dict) -> list[float]:
    """终态产物中的数值：正文普查 + claim 申报值 + 决策层价位（不含输入侧参考带/行情）。"""
    from finance_agent.citation_coverage import extract_census_numbers

    numbers = [n.value for n in extract_census_numbers(_terminal_text(state))]
    for report in (state.get("analyst_reports") or {}).values():
        if isinstance(report, dict):
            numbers += [
                float(c["stated_value"])
                for c in report.get("claims") or []
                if isinstance(c, dict) and _is_number(c.get("stated_value"))
            ]
    for key in ("trader_plan", "price_check", "derived_metrics"):
        numbers += _numbers_in(state.get(key))
    return numbers


def _numbers_in(obj: Any) -> list[float]:
    if obj is None:
        return []
    if _is_number(obj):
        return [float(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers_in(v)]
    if isinstance(obj, (list, tuple)):
        return [n for v in obj for n in _numbers_in(v)]
    return []


def _changed_keys(original: dict, polluted: dict) -> list[str]:
    changed: list[str] = []
    for key in sorted(set(original) | set(polluted)):
        if not _same_value(original.get(key), polluted.get(key)):
            changed.append(key)
    return changed


def _same_value(a: Any, b: Any) -> bool:
    if hasattr(a, "equals") and hasattr(b, "equals"):
        try:
            return bool(a.equals(b))
        except Exception:  # noqa: BLE001 - 无法比较时按「已变化」处理（保守）
            return False
    try:
        return bool(a == b)
    except Exception:  # noqa: BLE001
        return False


def _markers_present(state: dict, markers: list[Any]) -> bool:
    text = _terminal_text(state)
    for marker in markers:
        if isinstance(marker, str):
            if marker and marker in text:
                return True
            continue
        if not _is_number(marker):
            continue
        if any(
            _close(cand, n)
            for n in _terminal_numbers(state)
            for cand in _scale_candidates(float(marker))
        ):
            return True
    return False


def _scale_candidates(value: float) -> tuple[float, ...]:
    """面值/缩放候选（亿元↔元、百分比↔分数）：与校验器归一候选同量级族。"""
    return (value, value / 100.0, value * 100.0, value / 1.0e8, value * 1.0e8, value / 1.0e4)


def run_real_case(
    product: dict,
    case: InjectionCase,
    *,
    graph_runner: Callable[..., dict] | None = None,
    variant: str | None = None,
    query: str = DEFAULT_QUERY,
    llm_meter: Callable[[], int] | None = None,
    price_check_fn: Callable[[dict], dict] | None = None,
    decision_cache: dict[str, dict] | None = None,
) -> tuple[EscapePair, dict]:
    """真跑型单例：apply_injection → 变体图（ON/OFF 两态）→ 终态判定 → 配对。

    `graph_runner` 可注入（测试零 LLM）；`llm_meter` 为「当前 LLM 调用计数」读取器
    （None = 未知，如实记 None 而非 0——预算不得以单一 run 等效数掩盖）。

    单元载荷的 `injection` 记录注入效果（`applied` / `applied_reason` / `target` /
    `polluted_values` / `field_names` / `changed_keys` / `consumed_overlap`）：注入键
    未落在管线消费面上即判 void（`_real_status`），不冒充「拦下」。

    **预判 void（零消耗）**：注入不可施加（`applied=False`，如缺目标结构）或未改变快照
    （`changed` 为空）在运行前即可确认，此时 SHALL 直接判 void 并跳过图运行——
    P1 真跑腿实证：曾对不可施加的注入跑满 38 次调用才事后判 void。

    **决策层后置注入（P1 重校轮）**：唯一例外是决策点（illegal_price）而产物快照无决策
    dict——污染对象（决策）只有跑图才存在，故此型改走 `_run_post_graph_decision_case`
    （跑图产出决策 → 内存注入 → A5 两态校验），成本如实入账，不再据「快照无决策」预判 void
    （round-1 据此 4/4 判 void，决策层注入拿不到任何信息）。
    """
    runner = graph_runner or default_graph_runner
    resolved_variant = variant or ("full" if case.injection_point == "decision" else "analysts")
    surface = _real_surface(case.mechanism_id, case.injection_point)
    original = product.get("snapshot") or {}
    if case.injection_point == "decision" and not _decision_dicts(original):
        return _run_post_graph_decision_case(
            product,
            case,
            runner=runner,
            variant=resolved_variant,
            surface=surface,
            query=query,
            llm_meter=llm_meter,
            price_check_fn=price_check_fn,
            cache=decision_cache,
            cache_key=f"{resolved_variant}|{query}|{product.get('ticker')}",
        )
    polluted, effect = apply_injection(original, case)
    changed = _changed_keys(original, polluted)
    injected = _injection_record(effect, changed)
    if not effect.applied:
        return _prevoid_real_case(
            product,
            case,
            injected=injected,
            variant=resolved_variant,
            reason=f"注入不可施加（{injected['applied_reason']}）",
        )
    if not changed:
        return _prevoid_real_case(
            product,
            case,
            injected=injected,
            variant=resolved_variant,
            reason="注入未改变快照（该型在本产物上无污染可测，不判拦截）",
        )
    states: dict[str, dict] = {}
    payloads: dict[str, dict] = {}
    for on in (True, False):
        key = "on" if on else "off"
        before = _meter(llm_meter)
        with mechanism_toggle(case.mechanism_id, on=on, surface=surface) as toggle:
            state = dict(runner(variant=resolved_variant, snapshot=polluted, query=query))
            if case.injection_point == "decision":
                from finance_agent.nodes import validate as validate_mod

                fn = price_check_fn or validate_mod.validate_trade_prices
                state = {**state, **fn(state)}
            payload = toggle.payload()
        after = _meter(llm_meter)
        outcome = classify_terminal(state, case, injected=injected)
        states[key] = outcome
        payloads[key] = {
            "ran": True,
            "graph_runs": 1,
            "polluted_present": outcome["polluted_present"],
            "flagged": outcome["flagged"],
            "citation": outcome["citation"],
            "decision": outcome["decision"],
            "mechanism": payload,
            "llm_calls": None if before is None or after is None else after - before,
        }
    on_state = classify_offline(
        polluted_present=bool(states["on"]["polluted_present"]),
        flagged=bool(states["on"]["flagged"]),
    )
    off_state = classify_offline(
        polluted_present=bool(states["off"]["polluted_present"]),
        flagged=bool(states["off"]["flagged"]),
    )
    pair = EscapePair(case.case_id, on_state, off_state)
    status, reason = _real_status(injected, payloads)
    unit_id = f"{case.case_id}::{LEG_REAL}"
    calls = [p["llm_calls"] for p in payloads.values()]
    unit = {
        "unit_id": unit_id,
        "case_id": case.case_id,
        "ticker": product.get("ticker"),
        "leg": LEG_REAL,
        "cost_class": COST_CLASS[case.pollution_type],
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "variant": resolved_variant,
        "on_state": on_state,
        "off_state": off_state,
        "status": status,
        "status_reason": reason,
        "llm_calls": None if any(c is None for c in calls) else sum(c or 0 for c in calls),
        "graph_runs": sum(int(p.get("graph_runs") or 0) for p in payloads.values()),
        "evidence_paths": _evidence_paths(product, unit_id),
        "injection": injected,
        "on": payloads["on"],
        "off": payloads["off"],
    }
    return pair, unit


def _injection_record(effect: Any, changed: list[str], *, post_graph: bool = False) -> dict:
    """单元注入效果记录（真跑腿单一口径）：污染标记/命中字段一律取自注入效果记录，
    不从 payload 猜（payload 只描述目标结构）。`post_graph` 标记决策层后置流程。"""
    return {
        "polluted_values": list(effect.polluted_values),
        "field_names": list(effect.field_names),
        "changed_keys": list(changed),
        "consumed_overlap": sorted(set(changed) & ANALYST_CONSUMED_KEYS),
        "injection_effective": bool(changed),
        "applied": effect.applied,
        "applied_reason": effect.reason,
        "target": effect.target,
        "decision_key": str(effect.target) if (post_graph and effect.applied) else None,
        "post_graph": post_graph,
    }


def _decision_dicts(state: dict) -> dict[str, dict]:
    """state 里的决策 dict（键序同注入载荷 target 顺序：trader_plan 优先）。

    TradeDecision 对象（trader/risk_judge 直接写 state 的形态）先归一为 dict；
    hold/watch 等无价位的决策也照收——「有没有决策」与「能不能注入」是两件事。
    """
    out: dict[str, dict] = {}
    for key in DECISION_KEYS:
        plan: Any = state.get(key)
        if hasattr(plan, "model_dump"):
            plan = plan.model_dump()
        if isinstance(plan, dict) and plan:
            out[key] = plan
    return out


def _run_post_graph_decision_case(
    product: dict,
    case: InjectionCase,
    *,
    runner: Callable[..., dict],
    variant: str,
    surface: str,
    query: str,
    llm_meter: Callable[[], int] | None,
    price_check_fn: Callable[[dict], dict] | None,
    cache: dict[str, dict] | None = None,
    cache_key: str = "",
) -> tuple[EscapePair, dict]:
    """决策层后置注入（P1 重校轮）：产物无决策 dict 时的唯一正确面。

    P1 轮 1 实证：材料步只跑 analysts 变体 → 产物无 `trader_plan`/`final_trade_decision`
    → 旧流程按「注入不可施加」预判 void（4/4 void，决策层注入拿不到任何信息）。污染对象
    （决策）只有跑图才存在，故本流程：

    1. 先跑**一趟**变体图（真跑成本，meter 差量如实计账）；
    2. 取产出决策（`trader_plan` 优先——A5 sanity 读它；回退 `final_trade_decision`）；
    3. 在内存里把污染打到该决策 dict 上（`apply_injection`，同一 op）；
    4. A5 开关 ON/OFF 各跑一次价位 sanity（`validate_trade_prices` 或注入的纯核心）。

    **为什么是「1 次图运行 + 2 次校验」而不是「2 次图运行」**：配对比较要求两态喂同一个
    污染决策；每态各跑一次图只会得到两个不同的 LLM 决策（非确定性），配对失去意义且成本
    翻倍。sanity 是纯规则函数（无 LLM），两态切换的是机制开关、不是输入。图运行成本记在
    单元级 `post_graph`（`shared_graph_runs`/`shared_llm_calls`），两态载荷记
    `shared_graph_run=True` + 0 自有消耗，避免重复计账也不漏计。

    跑图后仍无决策、或产出决策不可注入（hold 无价位要求等）→ 判 void，但**图已跑**：
    成本如实入账（`graph_runs=1` + meter 差量），不得写 0。

    **决策复用（round-3 二阶段）**：同一标的的同类实例（载荷确定性相同）只需**一趟图**——
    首单元跑图后把状态与「不可执行」判词写入 `cache`，同标的后续单元直接复用（0 成本）。
    否则 4 实例各烧一趟全图（round-2 实测 4×38 次调用，其中 3 趟是纯重复）。
    """
    cached = cache.get(cache_key) if (cache is not None and cache_key) else None
    if cached is not None and cached.get("skip_reason"):
        snapshot_effect = apply_injection(product.get("snapshot") or {}, case)[1]
        return _post_graph_void_case(
            product,
            case,
            variant=variant,
            shared_runs=0,
            shared_calls=0,
            reason=f"决策复用（本标的首单元已判定）：{cached['skip_reason']}",
            injected=_injection_record(snapshot_effect, [], post_graph=True),
        )
    if cached is not None:
        state = cached["state"]
        shared_runs, shared_calls = 0, 0
    else:
        before = _meter(llm_meter)
        state = dict(runner(variant=variant, snapshot=product.get("snapshot") or {}, query=query))
        after = _meter(llm_meter)
        shared_calls = None if before is None or after is None else after - before
        shared_runs = 1
        if cache is not None and cache_key:
            cache[cache_key] = {"state": state}
    produced = _decision_dicts(state)
    if not produced:
        snapshot_effect = apply_injection(product.get("snapshot") or {}, case)[1]
        return _post_graph_void_case(
            product,
            case,
            variant=variant,
            shared_runs=shared_runs,
            shared_calls=shared_calls,
            reason=(
                "跑图后产物仍无决策 dict（trader_plan/final_trade_decision 均不在跑图产物）："
                "决策层注入无目标"
            ),
            injected=_injection_record(snapshot_effect, [], post_graph=True),
        )
    polluted_decisions, effect = apply_injection(produced, case)
    changed = _changed_keys(produced, polluted_decisions)
    injected = _injection_record(effect, changed, post_graph=True)
    if not effect.applied or not changed:
        reason = (
            f"注入不可施加（{injected['applied_reason']}）"
            if not effect.applied
            else "注入未改变产出的决策 dict（该型在本跑图产物上无污染可测）"
        )
        if cache is not None and cache_key in cache:
            # 决策本身不可执行（hold/watch 豁免价位要求）→ 同标的后续实例零成本跳过
            cache[cache_key]["skip_reason"] = reason
        return _post_graph_void_case(
            product,
            case,
            variant=variant,
            shared_runs=shared_runs,
            shared_calls=shared_calls,
            reason=f"跑图产出决策后仍不可注：{reason}",
            injected=injected,
        )
    decision_key = str(effect.target)
    polluted_decision = polluted_decisions[decision_key]
    check_state = {**state, decision_key: polluted_decision}
    if decision_key != "trader_plan":
        # A5 sanity 只读 trader_plan：终态决策键不是它时按 sanity 的消费面喂同一决策对象
        # （不改数值、不复制字段）——否则 sanity 看到空 plan 恒放行，开关两态无差。
        check_state["trader_plan"] = polluted_decision
    payloads: dict[str, dict] = {}
    for on in (True, False):
        key = "on" if on else "off"
        with mechanism_toggle(case.mechanism_id, on=on, surface=surface) as toggle:
            from finance_agent.nodes import validate as validate_mod

            fn = price_check_fn or validate_mod.validate_trade_prices
            check = fn(check_state)
        payload = toggle.payload()
        outcome = classify_terminal({**check_state, **check}, case, injected=injected)
        payloads[key] = {
            "ran": True,
            "shared_graph_run": True,  # 图只跑一趟：两态共用同一污染决策输入
            "graph_runs": 0,
            "polluted_present": outcome["polluted_present"],
            "flagged": outcome["flagged"],
            "citation": outcome["citation"],
            "decision": outcome["decision"],
            "mechanism": payload,
            "llm_calls": 0,  # 校验是纯规则（无 LLM）：本态自有消耗为 0
        }
    on_state = classify_offline(
        polluted_present=bool(payloads["on"]["polluted_present"]),
        flagged=bool(payloads["on"]["flagged"]),
    )
    off_state = classify_offline(
        polluted_present=bool(payloads["off"]["polluted_present"]),
        flagged=bool(payloads["off"]["flagged"]),
    )
    on_check = (payloads["on"].get("decision") or {}).get("price_check") or {}
    if str(on_check.get("result")) == "pass" and "不可用" in str(on_check.get("note") or ""):
        # 价位数 sanity 的消费面（price_levels）缺失 → 校验被跳过：该单元**没测到**
        # （与「注入不可施加」/「暴露未实现」同级），SHALL NOT 记成「逃逸」——否则
        # 机制背上不属于它的账（P1 illegal 批实测：4/4 ok 单元实为 sk- 跳过）
        return _post_graph_void_case(
            product,
            case,
            variant=variant,
            shared_runs=shared_runs,
            shared_calls=shared_calls,
            reason=(
                f"价位校验面缺失（price_levels 不可用 → sanity 跳过：{on_check.get('note')}）："
                "该单元不含拦截信息，不得记作「逃逸」"
            ),
            injected=injected,
        )
    pair = EscapePair(case.case_id, on_state, off_state)
    status, reason = _real_status(injected, payloads)
    unit_id = f"{case.case_id}::{LEG_REAL}"
    unit = {
        "unit_id": unit_id,
        "case_id": case.case_id,
        "ticker": product.get("ticker"),
        "leg": LEG_REAL,
        "cost_class": COST_CLASS[case.pollution_type],
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "variant": variant,
        "on_state": on_state,
        "off_state": off_state,
        "status": status,
        "status_reason": reason,
        "llm_calls": shared_calls,  # 两态自有消耗 0；共享图运行量即全部已知消耗
        "graph_runs": shared_runs,  # 本单元自有图运行（复用命中 = 0）
        "evidence_paths": _evidence_paths(product, unit_id),
        "injection": injected,
        "post_graph": {
            "decision_key": decision_key,
            "shared_graph_runs": shared_runs,
            "shared_llm_calls": shared_calls,
            "validations_per_state": 1,
            "note": (
                f"决策层后置注入：图只跑 {'一趟（本单元采集）' if shared_runs else '零趟（复用本标的首单元决策）'}"
                "（两态共用同一污染决策输入），A5 sanity 为纯规则、开关两态各跑一次；"
                "图运行成本记在此块与单元级字段"
            ),
        },
        "on": payloads["on"],
        "off": payloads["off"],
    }
    return pair, unit


def _post_graph_void_case(
    product: dict,
    case: InjectionCase,
    *,
    variant: str,
    shared_runs: int,
    shared_calls: int | None,
    reason: str,
    injected: dict | None = None,
    leg: str = LEG_REAL,
) -> tuple[EscapePair, dict]:
    """后置流程的 void：图已跑（成本如实入账）但单元不含拦截信息。

    与 `_prevoid_real_case`（运行前预判，0 成本）区别：这里图运行已经发生，`graph_runs`
    与 `llm_calls` 必须如实记，不得写成 0；两态载荷 `polluted_present`/`flagged` 记 None
    （未观测，不得与「看过，没有」的 False 混淆）。
    """
    status_reason = f"void：{reason}；图已跑 {shared_runs} 次（成本如实计入，不写 0）"
    skipped = {
        "ran": True,  # 图确实跑了；但该单元无污染决策可供两态校验
        "shared_graph_run": True,
        "graph_runs": 0,
        "skip_reason": status_reason,
        "polluted_present": None,
        "flagged": None,
        "citation": {},
        "decision": {},
        "mechanism": None,
        "llm_calls": 0,
    }
    unit_id = f"{case.case_id}::{leg}"
    unit = {
        "unit_id": unit_id,
        "case_id": case.case_id,
        "ticker": product.get("ticker"),
        "leg": leg,
        "cost_class": COST_CLASS[case.pollution_type],
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "variant": variant,
        "on_state": "",  # 未判定（同 error 单元口径）：不得读成 caught
        "off_state": "",
        "status": STATUS_VOID,
        "status_reason": status_reason,
        "llm_calls": shared_calls,
        "graph_runs": shared_runs,
        "evidence_paths": _evidence_paths(product, unit_id),
        "injection": dict(injected or {}),
        "post_graph": {
            "decision_key": None,
            "shared_graph_runs": shared_runs,
            "shared_llm_calls": shared_calls,
            "validations_per_state": 0,
            "note": "决策层后置注入：图已跑但无（可注入的）产出决策 → 该单元不含拦截信息",
        },
        "on": dict(skipped),
        "off": dict(skipped),
    }
    # 配对占位：void 单元不进配对（`pilot_report` 只对 status=ok 的单元配对），此处仅为返回契约
    return EscapePair(case.case_id, "caught", "caught"), unit


def _prevoid_real_case(
    product: dict,
    case: InjectionCase,
    *,
    injected: dict,
    variant: str,
    reason: str,
) -> tuple[EscapePair, dict]:
    """预判 void：运行前即可确认注入不含信息 → 跳过图运行（0 次 run / 0 次 LLM 调用）。

    触发条件（都在 `apply_injection` 之后、任何图运行之前可知）：注入不可施加
    （`applied=False`）或注入未改变快照（`changed` 为空）。两态载荷记 `ran=False` +
    `mechanism=None`——未运行即无开关记录，不得伪造「开态/关态已判定」。

    决策点（illegal_price）产物无决策 dict **不走此路**：污染对象只有跑图才存在，走
    `_run_post_graph_decision_case`（图已跑，成本如实入账，不写 0）。
    """
    status_reason = f"void：{reason}；预判于图运行之前（0 次图运行 / 0 次 LLM），该单元不含拦截信息"
    skipped = {
        "ran": False,
        "skip_reason": status_reason,
        "polluted_present": None,  # 未观测：不得写成 False（False 是「看过，没有」）
        "flagged": None,
        "citation": {},
        "decision": {},
        "mechanism": None,
        "graph_runs": 0,
        "llm_calls": 0,
    }
    unit_id = f"{case.case_id}::{LEG_REAL}"
    unit = {
        "unit_id": unit_id,
        "case_id": case.case_id,
        "ticker": product.get("ticker"),
        "leg": LEG_REAL,
        "cost_class": COST_CLASS[case.pollution_type],
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "variant": variant,
        "on_state": "",  # 未判定（同 error 单元口径）：不得读成 caught
        "off_state": "",
        "status": STATUS_VOID,
        "status_reason": status_reason,
        "llm_calls": 0,
        "graph_runs": 0,
        "evidence_paths": _evidence_paths(product, unit_id),
        "injection": injected,
        "on": dict(skipped),
        "off": dict(skipped),
    }
    # 配对占位：void 单元不进配对（`pilot_report` 只对 status=ok 的单元配对），此处仅为返回契约
    return EscapePair(case.case_id, "caught", "caught"), unit


def _real_status(injected: dict, payloads: dict[str, dict]) -> tuple[str, str]:
    """跑后单元有效性：两态产物都无污染痕迹 → 不含拦截信息（判 void）。

    运行前可知的两类非适用（`applied=False` / 快照未改变）不在此处判定：
    它们在 `run_real_case` 里已被 `_prevoid_real_case` 拦下（零图运行）。
    """
    traced = any(p["polluted_present"] or p["flagged"] for p in payloads.values())
    if not traced:
        reason = "void：两态终态产物均无污染值且无告警（污染未到达产物，该单元不含拦截信息）"
        if not injected.get("consumed_overlap"):
            reason += "；注入键未落在分析师消费键上（当前接线到不了模型输入，须修注入映射或产物）"
        return STATUS_VOID, reason
    return STATUS_OK, ""


def _meter(meter: Callable[[], int] | None) -> int | None:
    return None if meter is None else int(meter())


# ── 报告 ──


def assert_launch_allowed(
    prereg_dir: Path | None = None, *, name_contains: str = PREREG_P1_NAME
) -> Preregistration:
    """跑批入口门禁：无有效预登记即拒绝（spec「缺预登记拒绝跑批」）。

    默认只认本 pilot 的预登记文档（`PREREG_P1_NAME` 子串）——同目录后来者
    （P2 族 B / A4 补测）不得顶替本实验的门禁口径。
    """
    return assert_preregistered(
        Path(prereg_dir) if prereg_dir else DEFAULT_PREREG_DIR, name_contains=name_contains
    )


def mcnemar_exact_p(b: int, c: int) -> float:
    """McNemar 精确检验（双侧二项，α=0.05 判显著）：阳性对照灵敏度判据。"""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / (2.0**n)
    return min(1.0, 2.0 * tail)


def _adjudicable_surface(pollution_type: str) -> bool:
    """该型是否可进终裁清单：输入侧面（A7）无「拦截/逃逸」语义，判读走输入侧证据块。"""
    return FLAG_SURFACE_KIND.get(pollution_type, "none") != "input_side"


def _excluded_from_worklist(units: Sequence[dict]) -> dict[str, dict]:
    """被排除出终裁清单的型及原因（SHALL NOT 静默减少清单条数）。"""
    excluded: dict[str, dict] = {}
    for unit in units:
        pollution_type = str(unit["pollution_type"])
        if unit.get("off_state") == "escaped" and not _adjudicable_surface(pollution_type):
            block = excluded.setdefault(
                pollution_type,
                {
                    "units": 0,
                    "reason": (
                        "输入侧面（无校验侧拦截语义）：判读走 input_side_evidence 的 "
                        "presence 检查，不进逃逸终裁清单"
                    ),
                },
            )
            block["units"] += 1
    return excluded


def traffic_columns(units: Sequence[dict]) -> dict:
    """三栏口径（owner 裁决②）：拦截率（主）/ 暴露率（辅）/ 关态穿透率（对照）+ 跑批口径（附录）。

    - **拦截率** = 开态被拦 / 开态到达机制面前——机制只对「它见到的」负责（因果责任对齐）；
    - **暴露率** = 开态到达产物 / 全部注入单元——void 的去向在此交代；
    - **关态穿透率** = 关态到达产物 / 全部注入单元——阳性对照（污染本身能穿透管线）；
    - **跑批口径**（附录）= 关态逃逸 / 全部注入单元——混合暴露与拦截两件事，
      SHALL NOT 当主指标（会让机制背上不属于它的账）。

    输入侧面型（A7）不进任一栏（无「拦截/逃逸」语义，读数见 `input_side_evidence`）。
    分母为 0 时比率记 None（「0」与「没判定过」不混）。
    """
    population = [
        u for u in units if FLAG_SURFACE_KIND.get(str(u["pollution_type"]), "none") != "input_side"
    ]
    injected = len(population)
    ok = [u for u in population if u.get("status") == STATUS_OK]
    on_reached = [u for u in ok if (u.get("on") or {}).get("polluted_present")]
    off_reached = [u for u in ok if (u.get("off") or {}).get("polluted_present")]
    caught = [u for u in on_reached if (u.get("on") or {}).get("flagged")]
    escapes = [u for u in off_reached if not (u.get("off") or {}).get("flagged")]

    def _rate(num: int, den: int) -> float | None:
        return (num / den) if den else None

    return {
        "interception_rate": {
            "label": "拦截率（主指标）",
            "numerator": len(caught),
            "denominator": len(on_reached),
            "value": _rate(len(caught), len(on_reached)),
            "note": "开态被拦 / 开态到达机制面前：机制只对「它见到的」负责",
        },
        "exposure_rate": {
            "label": "暴露率（辅报）",
            "numerator": len(on_reached),
            "denominator": injected,
            "value": _rate(len(on_reached), injected),
            "note": "开态到达产物 / 全部注入单元：void 的去向在此交代",
        },
        "off_state_penetration": {
            "label": "关态穿透率（对照）",
            "numerator": len(off_reached),
            "denominator": injected,
            "value": _rate(len(off_reached), injected),
            "note": "关态到达产物 / 全部注入单元：阳性对照——污染本身能穿透管线",
        },
        "batch_ratio_appendix": {
            "label": "跑批口径（附录，非主指标）",
            "numerator": len(escapes),
            "denominator": injected,
            "value": _rate(len(escapes), injected),
            "note": "关态逃逸 / 全部注入单元：混合暴露与拦截，SHALL NOT 当主指标",
        },
        "population": {"units_total": len(units), "injected": injected},
    }


def _input_side_evidence(units: Sequence[dict]) -> dict[str, dict]:
    """按型汇总输入侧证据（`input_side` 块）：presence 检查读数，非拦截率。

    判词只在「ON 全有告警且 OFF 全无」时给「机制生效」——其余如实记「需人工判读」，
    不把部分命中读成生效。
    """
    blocks: dict[str, dict] = {}
    for unit in units:
        block = unit.get("input_side")
        if not isinstance(block, dict):
            continue
        pollution_type = str(unit["pollution_type"])
        agg = blocks.setdefault(
            pollution_type,
            {
                "surface": block.get("surface"),
                "units": 0,
                "present_on": 0,
                "present_off": 0,
                "note": block.get("note"),
            },
        )
        agg["units"] += 1
        agg["present_on"] += int(bool(block.get("present_on")))
        agg["present_off"] += int(bool(block.get("present_off")))
    for agg in blocks.values():
        if agg["present_on"] == agg["units"] and agg["present_off"] == 0:
            agg["verdict"] = "机制生效（ON 全部有告警 / OFF 全部无）"
        else:
            agg["verdict"] = "需人工判读（ON/OFF 未全一致）"
    return blocks


def _pair_of(unit: dict) -> EscapePair:
    return EscapePair(str(unit["case_id"]), str(unit["on_state"]), str(unit["off_state"]))


def _calibration(pairs: list[EscapePair]) -> dict:
    if len(pairs) < MIN_PAIRS_FOR_CALIBRATION:
        return {
            "verdict": "insufficient",
            "n_pairs": len(pairs),
            "min_pairs": MIN_PAIRS_FOR_CALIBRATION,
            "advice": "样本不足不下校准判词（停止规则：不硬扩样本，先补单元）",
        }
    return dict(calibration_verdict(pairs))


def _positive_control(units: list[dict]) -> dict:
    """阳性对照：已知劣化变体（关 verify_citations）须测出显著更多逃逸，否则本批阴性作废。"""
    ok = [u for u in units if u.get("status") == STATUS_OK]
    pairs = [_pair_of(u) for u in ok]
    table = mcnemar_table(pairs) if pairs else {"b": 0.0, "c": 0.0, "discordant_ratio": 0.0}
    b, c = int(table["b"]), int(table["c"])
    p_value = mcnemar_exact_p(b, c)
    confirmed = bool(pairs) and b > c and p_value < 0.05
    return {
        "n_pairs": len(pairs),
        # 组成披露：离线腿与真跑腿的 A3 单元都可作阳性对照（都是「关 verify_citations」）
        "legs": sorted({str(u.get("leg")) for u in ok}),
        "b": b,
        "c": c,
        "discordant_ratio": table["discordant_ratio"],
        "exact_p": p_value,
        "alpha": 0.05,
        "sensitivity_confirmed": confirmed,
        "void_negative_results": not confirmed,
        "ratio_criterion_met": table["discordant_ratio"] >= 0.5,
        "advice": (
            "阳性对照显著（已知劣化变体逃逸更多）：管线灵敏度成立，阴性结论可发表"
            if confirmed
            else "阳性对照未测出显著差异：管线灵敏度未证实，本轮全部阴性结论作废（spec「阳性对照失灵作废本轮」）"
        ),
    }


def _cost_class(unit: dict) -> str:
    """单元的成本分型（`cost_class` 缺失时回落 `leg`：两字段取值同名，见 COST_CLASS）。"""
    return str(unit.get("cost_class") or unit.get("leg") or "")


def _budget_block(units: list[dict], *, llm_meter_total: int | None = None) -> dict:
    """预算分型申报（spec「注入成本结构二分」/「预算分型申报」）：两型分列，SHALL NOT 合并。

    真跑腿 SHALL 对**全部已尝试单元**（ok + void + error）计账——只算 ok 单元会把
    void/error 的消耗静默丢掉（P1 真跑腿实证低估 ≈10×）。`llm_calls` 只对**已知计量**
    求和，None 计量的单元单列在 `llm_calls_unknown_units`（此时 `llm_calls` 是下界）。
    `runs` 的单位是图运行（每单元 2 态；预判 void 的单元 0 次；决策层后置注入的单元 1 趟
    两态共用），运行数未知的 error 单元单列在 `runs_unknown_units`。meter 绝对读数独立交叉
    核对两处数字。
    """
    off_units = [u for u in units if _cost_class(u) == LEG_OFFLINE]
    real_units = [u for u in units if _cost_class(u) == LEG_REAL]
    calls = [u.get("llm_calls") for u in real_units]
    runs = [u.get("graph_runs") for u in real_units]
    known_calls = sum(int(c) for c in calls if c is not None)
    statuses = [str(u.get("status") or STATUS_ERROR) for u in real_units]
    by_status = {s: statuses.count(s) for s in (STATUS_OK, STATUS_VOID, STATUS_ERROR)}
    for status in sorted(set(statuses) - set(by_status)):  # 未登记状态不得静默消失
        by_status[status] = statuses.count(status)
    return {
        LEG_OFFLINE: {
            "units": len(off_units),
            "llm_calls": sum(int(u.get("llm_calls") or 0) for u in off_units),
            "zero_llm_verified": all(u.get("llm_calls") == 0 for u in off_units),
        },
        LEG_REAL: {
            "units": len(real_units),
            "units_by_status": by_status,
            "runs": sum(int(r) for r in runs if r is not None),
            "runs_unknown_units": sum(1 for r in runs if r is None),
            "llm_calls": known_calls,
            "llm_calls_unknown_units": sum(1 for c in calls if c is None),
            "llm_calls_by_status": {
                status: sum(
                    int(raw)
                    for unit, raw in zip(real_units, calls, strict=True)
                    if str(unit.get("status") or STATUS_ERROR) == status and raw is not None
                )
                for status in by_status
            },
            "llm_meter_total_calls": llm_meter_total,
            "llm_meter_matches_unit_sum": (
                None if llm_meter_total is None else int(llm_meter_total) == known_calls
            ),
            "note": (
                "计账单位 = 已尝试真跑单元（ok + void + error 全计，SHALL NOT 只算 ok）；"
                "`runs` = 图运行次数（每单元 2 态，预判 void 的单元 0 次，"
                "决策层后置注入的单元 1 趟且两态共用），"
                "运行数未知的单元计入 `runs_unknown_units`；"
                "`llm_calls` = 已知计量单元之和，None 计量单元单列于 `llm_calls_unknown_units`"
                "（此时该值是下界）；`llm_meter_total_calls` 为 meter 绝对读数，仅供独立交叉核对"
                "（续跑或同进程多批时含本进程其他消耗，不等即非异常）"
            ),
        },
        "note": "两类成本量级不同，SHALL NOT 合并为单一 run 等效数（spec 注入成本结构二分）",
    }


def pilot_report(
    units: list[dict],
    *,
    prereg_dir: Path | None = None,
    positive_control_units: list[dict] | None = None,
    llm_meter_total: int | None = None,
    adjudicated: set[str] | None = None,
) -> dict:
    """P1 批报告：分型逃逸率（未终裁 rate=None）+ 校准判词 + 阳性对照 + 预算 + 终裁清单。

    主指标 rate 以 `adjudicated` 结算：缺省空集（人工终裁前只报 pending 计数，
    「0 逃逸」与「没判定过」不得混为一谈，spec「逃逸须终裁确认」）；回填终裁清单后
    传入 `adjudication.adjudicated_case_ids(...)` 的集合即可出数。

    `llm_meter_total` = 报告生成时的 meter 绝对读数（独立交叉核对，缺省 None = 未接线）。
    """
    prereg = assert_launch_allowed(prereg_dir)
    positive_control_units = list(positive_control_units or [])
    adjudicated = set(adjudicated or set())
    ok = [u for u in units if u.get("status") == STATUS_OK]
    pairs = [_pair_of(u) for u in ok]
    by_type: dict[str, dict] = {}
    for pollution_type in sorted({str(u["pollution_type"]) for u in ok}):
        type_pairs = [
            pair
            for unit, pair in zip(ok, pairs, strict=True)
            if unit["pollution_type"] == pollution_type
        ]
        by_type[pollution_type] = {
            "mechanism_id": next(
                str(u["mechanism_id"]) for u in ok if u["pollution_type"] == pollution_type
            ),
            "cost_class": COST_CLASS.get(pollution_type),
            "n_pairs": len(type_pairs),
            "mcnemar": mcnemar_table(type_pairs),
            "calibration": _calibration(type_pairs),
            "escape_rate": escape_rate(type_pairs, adjudicated=adjudicated),
            "verification_surface": VERIFICATION_FLAG_SURFACE.get(pollution_type, False),
            # 可测面分型（fail / traceability / input_side / none）——读分型判词前先读它
            "flag_surface": FLAG_SURFACE_KIND.get(pollution_type, "none"),
        }
    adjudicable_pairs = [
        pair
        for unit, pair in zip(ok, pairs, strict=True)
        if FLAG_SURFACE_KIND.get(str(unit["pollution_type"]), "none") != "input_side"
    ]
    overall_rate = escape_rate(adjudicable_pairs, adjudicated=adjudicated)
    # 合计人口 = 可终裁人口（排除输入侧面型）：与 escape_rate / metric_columns 同口径，
    # 否则 stale_macro 那类无拦截语义的单元会把合计不一致比稀释（本批 0.880 → 0.702）
    discordant = (
        mcnemar_table(adjudicable_pairs)
        if adjudicable_pairs
        else {"b": 0.0, "c": 0.0, "discordant_ratio": 0.0}
    )
    worklist = [
        {
            "unit_id": u["unit_id"],
            "case_id": u["case_id"],
            "ticker": u.get("ticker"),
            "pollution_type": u["pollution_type"],
            "mechanism_id": u["mechanism_id"],
            "leg": u["leg"],
            "on_state": u["on_state"],
            "off_state": u["off_state"],
            "evidence_paths": list(u.get("evidence_paths") or []),
            "polluted_values": list((u.get("injection") or {}).get("polluted_values") or []),
        }
        for u in ok
        if u["off_state"] == "escaped" and _adjudicable_surface(str(u["pollution_type"]))
    ]
    statuses = [str(u.get("status") or STATUS_ERROR) for u in units]
    positive_control = _positive_control(positive_control_units)
    report = {
        # 结论注册表生命周期字段（spec「结论注册表生命周期」）
        "status": "active",
        "pilot": "p1-injection-pilot",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "preregistration": {
            "path": prereg.path.as_posix(),
            "valid": prereg.valid,
            "fields": dict(prereg.fields),
            "issues": list(prereg.issues),
        },
        "counts": {
            "units_total": len(units),
            "ok": len(ok),
            "void_injection": statuses.count(STATUS_VOID),
            "error": statuses.count(STATUS_ERROR),
        },
        "types_absent": sorted(set(POLLUTION_TYPES) - {str(u["pollution_type"]) for u in ok}),
        # 成本分型清单（读报告者据此知道哪些型走哪条腿）
        "types_planned": {"offline_replay": list(OFFLINE_TYPES), "real_run": list(REAL_TYPES)},
        "discordant": {
            "overall": discordant,
            "by_type": {t: v["mcnemar"] for t, v in by_type.items()},
        },
        "calibration": {
            "overall": _calibration(adjudicable_pairs),
            "by_type": {t: v["calibration"] for t, v in by_type.items()},
            "min_pairs": MIN_PAIRS_FOR_CALIBRATION,
        },
        "escape_rate": {
            "overall": overall_rate,
            "by_type": {t: v["escape_rate"] for t, v in by_type.items()},
            "adjudicated": len(adjudicated),
            "note": "未终裁单元不入分子分母：rate=None 表示尚无终裁（spec「逃逸须终裁确认」）",
        },
        # 三栏口径（owner 裁决②）：拦截率（主）/ 暴露率（辅）/ 关态穿透率（对照）+ 附录
        "metric_columns": traffic_columns(units),
        "by_type": by_type,
        "positive_control": positive_control,
        "negative_results_status": "ok" if positive_control["sensitivity_confirmed"] else "void",
        "budget": _budget_block(units, llm_meter_total=llm_meter_total),
        "stop_rules": {
            "discordant_ratio_overall": discordant["discordant_ratio"],
            "too_easy": discordant["discordant_ratio"] < DISSONANT_MIN,
            "too_hard": discordant["discordant_ratio"] > DISSONANT_MAX,
            "void_units": statuses.count(STATUS_VOID),
            "adjudication_worklist_size": len(worklist),
            "adjudication_limit": ADJUDICATION_LIMIT,
            "adjudication_limit_exceeded": len(worklist) > ADJUDICATION_LIMIT,
            "action": _stop_action(discordant["discordant_ratio"], len(worklist)),
        },
        "adjudication_worklist": worklist,
        # 清单排除项（输入侧面型）：减少清单条数必须可见，不得静默
        "worklist_excluded": _excluded_from_worklist(ok),
        # 输入侧证据（零 LLM presence 检查）：机制无校验侧消费者时按成本分型改读的读数
        "input_side_evidence": _input_side_evidence(ok),
        # 盲区只列「无任何已定义可测面」的型（kind=none）；traceability/input_side 已有面
        "blind_spots": [
            {
                "pollution_type": t,
                "mechanism_id": v["mechanism_id"],
                "reason": (
                    "该型无任何已定义可测面（机制在 context 侧且无输入侧 presence 检查）："
                    "拦截率 0 不代表机制无价值，须先定义可测面再读"
                ),
            }
            for t, v in by_type.items()
            if v["flag_surface"] == "none"
        ],
        "units": units,
    }
    return report


def _stop_action(ratio: float, worklist_size: int) -> str:
    if ratio < DISSONANT_MIN:
        return "污染太易被拦：停跑并重校注入强度，不硬扩样本（预登记停止规则）"
    if ratio > DISSONANT_MAX:
        return "污染太难被拦：降低注入强度或校验收紧，不硬扩样本（预登记停止规则）"
    if worklist_size > ADJUDICATION_LIMIT:
        return f"终裁量 {worklist_size} 超上限 {ADJUDICATION_LIMIT}：收窄污染矩阵而非放宽终裁"
    return "不一致对子比例落在健康区间，可进入人工终裁"


def to_unit_judgment(unit: dict, *, run: str | None = None) -> UnitJudgment:
    """单元载荷 → 单元级判定记录（确定性判定 → method=code，confidence=1.0）。"""
    return UnitJudgment(
        unit_id=str(unit["unit_id"]),
        ticker=str(unit.get("ticker") or ""),
        run=run or str(unit["case_id"]),
        variant=str(unit["leg"]),
        unit_type="claim",
        judgment=f"{unit['pollution_type']} 关态={unit['off_state']} 开态={unit['on_state']}",
        method="code",
        confidence=1.0,
    )


def error_unit(
    case: InjectionCase, *, ticker: str, reason: str, llm_calls: int | None = None
) -> dict:
    """失败单元的显式记录（跑批不得静默丢单元：错误进报告 counts.error，不进配对）。

    `llm_calls` / `graph_runs` 缺省 None = 未知（中途失败后调用数不可归因）——预算块
    以 `llm_calls_unknown_units` / `runs_unknown_units` 单列，不得当成 0。
    """
    unit_id = f"{case.case_id}::{COST_CLASS[case.pollution_type]}"
    return {
        "unit_id": unit_id,
        "case_id": case.case_id,
        "ticker": ticker,
        "leg": COST_CLASS[case.pollution_type],
        "cost_class": COST_CLASS[case.pollution_type],
        "pollution_type": case.pollution_type,
        "injection_point": case.injection_point,
        "mechanism_id": case.mechanism_id,
        "on_state": "",
        "off_state": "",
        "status": STATUS_ERROR,
        "status_reason": reason,
        "llm_calls": llm_calls,
        "graph_runs": None,
        "evidence_paths": [],
        "injection": {},
        "on": {},
        "off": {},
    }


# ── 工具 ──


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _close(a: float, b: float) -> bool:
    """容差比对（max(0.01, 0.5%)），与校验器同族口径。"""
    return abs(a - b) < max(0.01, 0.005 * max(abs(a), abs(b)))
