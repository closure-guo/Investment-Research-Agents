"""P2 族 B 材料与 code 腿：full 变体一趟全图 → B3 出处率 / B4 sanity 遥测（零额外 LLM）。

**为什么只跑 full 一趟**：族 B 的层间对比臂是「analysts（无辩论/决策/风控）」——那批产物
已在 P1 材料里（同标的、同快照、同模型），本模块只需补 `full` 臂。两臂共用**同一份快照**
（从 P1 产物读入），故「同一快照、只差编排层」的对照成立。

**状态捕获按排除式**（incident 030 的教训：白名单会漂移丢面）：除 LLM 连接类键外全量落盘
（pickle 承载），并逐键记大小摘要——B1/B2/B5 的判读材料（debate_history / 锚点 / 决策 /
风控辩论 / FM 记录）都在里面，将来加读数不必重跑。

**B3（风控数字出处率，code）**：决策申报的执行参数（entry/stop/target）能否在上游产物里
找到同值来源——上游池 = 工具参考带 `price_levels` ∪ 分析师 claim 申报值 ∪ 代码派生
`derived_metrics` ∪ 上游计划（量的是 `final_trade_decision` 时含 `trader_plan`）。
容差复用校验器单一实现 `citation.value_close`（口径不得复制）。
**B4（sanity 打回率 / 修正触发率，code）**：`price_check.result` 三态 + `price_check_attempts`
+ `price_level_corrected`，分母按「机制见到的」= 需申报价位的决策（buy/sell）。

两腿都是 `code` 判定 → 不受校准门控；B1/B2/B5（nli/judge）须先过校准（人工一致率 ≥0.80）。
"""

from __future__ import annotations

import copy
import json
import pickle
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from evals.causal_ablation import pilot_runner as pr

P2_VARIANT = "full"
P2_LEG = "real_run"

# 排除项只留「连接/控制类」键；其余全量落盘（排除式，白名单会漂移丢面）
_EXCLUDE: frozenset[str] = frozenset({"llm_config", "api_key", "callbacks", "writer", "query"})
# B3 的参数名（执行参数；position_size 是档位词，不进数字出处率）
_B3_PARAM_KEYS: tuple[str, ...] = ("entry_price", "stop_loss", "target_price")
_ACTION_DIRECTION = {"buy": "long", "sell": "short"}


def capture_state(state: dict) -> dict:
    """按排除式捕获全图终态（深拷贝；不可 pickle 的键单独列名，不静默丢）。"""
    out: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in state.items():
        if key in _EXCLUDE or key.startswith("_"):
            continue
        try:
            pickle.dumps(value)
        except Exception:  # noqa: BLE001 - 记名跳过（如 LangGraph 内部对象）
            dropped.append(key)
            continue
        out[key] = copy.deepcopy(value)
    out["__capture_dropped__"] = dropped
    return out


def _normalize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _numeric_leaves(obj: Any, *, depth: int = 0) -> list[float]:
    """递归取数值叶子（dict/list 任意层；深度上限防环）。"""
    if depth > 8:
        return []
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numeric_leaves(v, depth=depth + 1)]
    if isinstance(obj, (list, tuple)):
        return [n for v in obj for n in _numeric_leaves(v, depth=depth + 1)]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [float(obj)]
    return []


def _upstream_pool(state: dict, *, decision: dict) -> list[float]:
    """上游数字池：工具参考带 ∪ 分析师 claim 值 ∪ 派生指标 ∪ 上游计划（含 trader_plan）。"""
    values: list[float] = []
    values += _numeric_leaves(state.get("price_levels") or {})
    values += _numeric_leaves(state.get("derived_metrics") or {})
    for report in (state.get("analyst_reports") or {}).values():
        report = _normalize(report) or {}
        for claim in report.get("claims") or []:
            claim = _normalize(claim) or {}
            stated = claim.get("stated_value")
            if isinstance(stated, (int, float)) and not isinstance(stated, bool):
                values.append(float(stated))
    plan = _normalize(state.get("trader_plan")) or {}
    for key in _B3_PARAM_KEYS:
        value = plan.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    # 决策自身不算来源（出处查的是「从上游来」）
    return [v for v in values if v not in (None, 0.0)]


def b3_unit(state: dict) -> dict:
    """B3：终态决策的执行参数出处率（上游同值来源比例）。"""
    from finance_agent.citation import value_close

    decision = _normalize(state.get("final_trade_decision")) or _normalize(state.get("trader_plan"))
    decision = decision or {}
    params = {
        key: decision.get(key)
        for key in _B3_PARAM_KEYS
        if isinstance(decision.get(key), (int, float)) and not isinstance(decision.get(key), bool)
    }
    if not params:
        return {
            "params_total": 0,
            "params_grounded": 0,
            "grounding_rate": None,
            "note": "该标的无数值执行参数（hold/watch 或未申报）",
        }
    pool = _upstream_pool(state, decision=decision)
    grounded = sorted(
        key
        for key, value in params.items()
        if any(value_close(float(cast(Any, value)), candidate) for candidate in pool)
    )
    refs = list(decision.get("evidence_refs") or [])
    return {
        "params_total": len(params),
        "params_grounded": len(grounded),
        "grounded_params": grounded,
        "ungrounded_values": {k: v for k, v in params.items() if k not in grounded},
        "grounding_rate": len(grounded) / len(params),
        "evidence_refs": len(refs),
        "action": decision.get("action"),
        "pool_size": len(pool),
    }


def b4_unit(state: dict) -> dict:
    """B4：sanity 遥测（打回 / 修正 / 跳过）。分母按「机制见到的」= 需申报价位的决策。"""
    check = _normalize(state.get("price_check")) or {}
    decision = _normalize(state.get("final_trade_decision")) or _normalize(state.get("trader_plan"))
    action = str((decision or {}).get("action") or "")
    needs_price = action in _ACTION_DIRECTION
    return {
        "action": action,
        "needs_price": needs_price,
        "result": check.get("result"),
        "note": check.get("note"),
        "reason": check.get("reason"),
        "attempts": state.get("price_check_attempts"),
        "corrected": bool(state.get("price_level_corrected")),
        "correction_reason": state.get("price_level_correction_reason"),
        "fund_manager_decision": state.get("fund_manager_decision"),
        "return_count": state.get("return_count"),
    }


def b4_unit_with_sanity(state: dict) -> dict:
    """B4：变体图**没有** sanity 节点（`build_variant_graph` 不含 validate）→ 用生产同一实现
    `validate_trade_prices` 在产物决策上补算，得到「该决策进生产 sanity 会怎样」。

    与真跑主图的差异（如实披露）：生产主图在 fail 时还会**打回 Trader 重出一次**
    （`price_check_feedback` 回路），本函数只跑单次校验——故读的是「首判分布」，
    不是完整回路的打回率。
    """
    from finance_agent.nodes.validate import validate_trade_prices

    try:
        produced = validate_trade_prices(dict(state))
    except Exception as exc:  # noqa: BLE001 - 补算失败如实标注，不静默当 pass
        return {
            "params_total": 0,
            "needs_price": None,
            "result": None,
            "note": f"validate_trade_prices 补算失败：{type(exc).__name__}: {exc}",
            "computed_by": "validate_trade_prices（失败）",
        }
    merged = {**state, **produced}
    unit = b4_unit(merged)
    unit["computed_by"] = "validate_trade_prices（同一实现；变体图无该节点，单次校验无打回回路）"
    return unit


def unit_from_state(
    ticker: str, state: dict, *, llm_calls: int | None, variant: str = P2_VARIANT
) -> dict:
    b4 = b4_unit(state) if state.get("price_check") else b4_unit_with_sanity(state)
    return {
        "unit_id": f"{ticker}::b34",
        "ticker": ticker,
        "leg": P2_LEG,
        "variant": variant,
        "b3": b3_unit(state),
        "b4": b4,
        "llm_calls": llm_calls,
    }


# ── 材料跑批（真跑，逐标的续跑） ──


def material_path(materials_dir: Path, ticker: str, variant: str = P2_VARIANT) -> Path:
    return Path(materials_dir) / f"{ticker}.{variant}.pkl"


def save_material(
    materials_dir: Path,
    ticker: str,
    *,
    state: dict,
    llm_calls: int | None,
    snapshot_digest: str,
    variant: str = P2_VARIANT,
) -> dict[str, str]:
    """落盘：pickle 承载全状态 + JSON 摘要（键名/大小/关键读数）。"""
    materials_dir = Path(materials_dir)
    materials_dir.mkdir(parents=True, exist_ok=True)
    pickle_path = material_path(materials_dir, ticker, variant)
    payload = {
        "ticker": ticker,
        "variant": variant,
        "snapshot_digest": snapshot_digest,
        "state": state,
        "llm_calls": llm_calls,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with pickle_path.open("wb") as fh:
        pickle.dump(payload, fh)
    summary = {
        "ticker": ticker,
        "variant": variant,
        "snapshot_digest": snapshot_digest,
        "llm_calls": llm_calls,
        "keys": sorted(k for k in state if k != "__capture_dropped__"),
        "capture_dropped": state.get("__capture_dropped__") or [],
        "b3": b3_unit(state),
        "b4": b4_unit(state),
        "saved_at": payload["saved_at"],
        "pickle": pickle_path.as_posix(),
    }
    json_path = materials_dir / f"{ticker}.{variant}.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pickle": pickle_path.as_posix(), "json": json_path.as_posix()}


def load_material(materials_dir: Path, ticker: str, variant: str = P2_VARIANT) -> dict:
    path = material_path(materials_dir, ticker, variant)
    if not path.exists():
        raise pr.MissingProductError(f"缺 P2 材料：{path.as_posix()}（先跑 materials 腿）")
    with path.open("rb") as fh:
        return cast(dict, pickle.load(fh))  # noqa: S301 - 自产自用的本地材料


def run_materials(
    tickers: Sequence[str],
    *,
    snapshot_materials_dir: Path,
    out_dir: Path,
    graph_runner: Callable[..., dict],
    llm_meter: Callable[[], int] | None = None,
    query: str = pr.DEFAULT_QUERY,
    variant: str = P2_VARIANT,
) -> list[dict]:
    """逐标的跑指定变体（同快照）；已有材料跳过（续跑不重烧 token）。

    `variant="full"` = 层间对照臂（对 P1 的 analysts 产物）；`variant="analysts"` = B5 盲评
    需要的另一臂渲染报告（P1 材料只存了 claims/markdown，没有 `final_report`）。
    """
    units: list[dict] = []
    for ticker in tickers:
        path = material_path(Path(out_dir), ticker, variant)
        if path.exists():
            payload = load_material(Path(out_dir), ticker, variant)
            print(f"[跳过] {ticker}：已有材料（{path.name}）", flush=True)
            units.append(
                unit_from_state(
                    ticker, payload["state"], llm_calls=payload["llm_calls"], variant=variant
                )
            )
            continue
        product = pr.load_product(pr.product_path(Path(snapshot_materials_dir), ticker))
        snapshot = dict(product.get("snapshot") or {})
        before = pr._meter(llm_meter)
        state = dict(graph_runner(variant=variant, snapshot=snapshot, query=query))
        after = pr._meter(llm_meter)
        calls = None if before is None or after is None else after - before
        captured = capture_state(state)
        save_material(
            Path(out_dir),
            ticker,
            state=captured,
            llm_calls=calls,
            snapshot_digest=str(product.get("snapshot_digest") or ""),
            variant=variant,
        )
        units.append(unit_from_state(ticker, captured, llm_calls=calls, variant=variant))
        print(f"[材料] {ticker} → {path.name}（calls={calls}）", flush=True)
    return units


# ── 报告 ──


def family_b_code_report(units: Sequence[dict], *, ticker_count: int | None = None) -> dict:
    """B3/B4 描述性读数（code；不受校准门控）+ 簇 bootstrap CI（按标的）。"""
    from evals.causal_ablation.aggregate import cluster_mean_diff_ci  # noqa: F401 - 语义对齐

    total_params = sum(u["b3"]["params_total"] for u in units)
    grounded = sum(u["b3"]["params_grounded"] for u in units)
    with_params = [u for u in units if u["b3"]["params_total"]]
    price_units = [u for u in units if u["b4"]["needs_price"]]
    results = [u["b4"]["result"] for u in price_units]
    corrected = sum(1 for u in price_units if u["b4"]["corrected"])
    returned = sum(1 for u in price_units if u["b4"]["result"] == "fail")
    return {
        "family": "B",
        "leg": P2_LEG,
        "variant": P2_VARIANT,
        "units_total": len(units),
        "units_with_params": len(with_params),
        "units_needing_price": len(price_units),
        "ticker_count": ticker_count if ticker_count is not None else len(units),
        "b3": {
            "metric": "风控数字出处率（执行参数在上游有同值来源的比例）",
            "method": "code",
            "params_total": total_params,
            "params_grounded": grounded,
            "rate": (grounded / total_params) if total_params else None,
            "per_ticker_rate": {u["ticker"]: u["b3"]["grounding_rate"] for u in with_params},
            "ungrounded_sample": [
                {"ticker": u["ticker"], "values": u["b3"].get("ungrounded_values")}
                for u in with_params
                if u["b3"].get("ungrounded_values")
            ][:10],
        },
        "b4": {
            "metric": "sanity 打回率 / 修正触发率",
            "method": "code",
            "denominator_note": "分母 = 需申报价位的决策（buy/sell）；hold/watch 无价位要求，不进分母",
            "computed_by": "validate_trade_prices（生产同一实现；消融变体图不含 sanity 节点，"
            "故在产物决策上补算——单次校验，不含生产主图的打回 Trader 重出回路）",
            "checked_units": len(price_units),
            "results": {r: results.count(r) for r in sorted({str(r) for r in results})},
            "fail_rate": (returned / len(price_units)) if price_units else None,
            "correction_rate": (corrected / len(price_units)) if price_units else None,
            "skipped_by_action": sorted(u["ticker"] for u in units if not u["b4"]["needs_price"]),
            "skipped_reasons": sorted(
                {
                    str(u["b4"]["note"])
                    for u in units
                    if u["b4"]["result"] == "pass" and u["b4"]["note"]
                }
            ),
        },
        "cost": {
            "llm_calls": _sum_optional(u.get("llm_calls") for u in units),
            "note": "full 变体每标的 1 趟全图（P2 材料腿）；B3/B4 判定本身零 LLM",
        },
        "reading_notes": [
            "B3/B4 是 code 判定 → 不受校准门控；B1/B2/B5（nli/judge）未过校准前不得进结论",
            "有效分母受暴露率限制（可执行决策 ≈25%）：n≈5 时按 §4 的 MDE 表**无分辨率**，"
            "本轮只作描述性读数（点估计 + 逐标的明细），不作显著性裁决",
            "出处池 = 参考带 ∪ 分析师 claim 值 ∪ 派生指标 ∪ 上游计划；容差复用校验器 value_close",
        ],
    }


def _sum_optional(values: Iterable[Any]) -> int | None:
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None
