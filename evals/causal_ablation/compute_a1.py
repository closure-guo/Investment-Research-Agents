"""A1（确定性指标注入）补测：无 compute 变体 vs 对照——「没有代码算指标，LLM 会自算并算错」。

**A1 的登记主张**（`claims.py`）：没有 compute 节点的确定性指标注入，LLM 会自己算指标并算错；
失败模式 = 计算幻觉；登记主指标 = 计算型 claim 数值错误率；判定方式 = code。

**为什么这样构造 OFF 态**：A1 的机制就是「`compute_metrics` 把派生指标写进 state → 分析师
context 直读」。OFF 态 = 把 `compute_metrics` 的**输出键**从分析师所见快照里去掉（原始三表 /
kline / 新闻 / 宏观原样保留），LLM 只能自己算或回避。输出键清单**不手抄**：对该快照跑一遍
`compute_metrics` 取返回键（单一实现），故 compute 将来加指标口径自动跟随。

**两态都用同一个「尺子」量**：claim 判定走 `pilot_runner.run_citation_chain`（= 生产 citation
链路，A3 ON），且**两态都拿完整快照做校验**——重算路由（incident 028 修复后按 field_ref 根
无条件重算）保证「计算型 claim 的真值来自原始数据重算」，与派生字典在不在无关。尺子不动，
动的只有分析师看到的输入。

**读法**：主指标 = 可重算根 claim 的值级错误率（`value_mismatch` / 可重算 claim 数）；
Δ = OFF − ON（按标的配对，簇 bootstrap）。附读数：claim 产出量（`claims_total`，机制少了
会不会连数都不给）、UNVERIFIABLE / 路径不可解析占比（自算值无出处时会落到这两类）。

**成本**：真跑型——每标的 2 趟分析师（OFF/ON），零额外校验成本（离线链不调 LLM；
citation 节点的稀疏修复可能在 ON 态触发，按 meter 归属）。
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from evals.causal_ablation import pilot_runner as pr

LEG_A1 = "real_run"
COST_CLASS_A1 = "real_run"
VARIANT = "analysts"
ARM_ON = "compute_on"
ARM_OFF = "compute_off"

# 原始输入（分析师 context 的原料；OFF 态保留）——只是审计对照，判据取自 compute_metrics 返回键
RAW_INPUT_HINT: tuple[str, ...] = (
    "kline",
    "benchmark_kline",
    "income_statement",
    "balance_sheet",
    "cash_flow_statement",
    "financial_indicators",
    "macro_indicators",
    "news_list",
    "industry_info",
    "key_events",
    "announcements",
    "research_reports",
)


def compute_output_keys(snapshot: dict) -> set[str]:
    """该快照上 `compute_metrics` 的输出键（单一实现取键，不手抄清单）。"""
    from typing import cast

    from finance_agent.nodes.compute import compute_metrics
    from finance_agent.state import AnalysisState

    return set(compute_metrics(cast(AnalysisState, dict(snapshot))).keys())


def strip_compute_outputs(snapshot: dict) -> dict:
    """OFF 态快照：去掉 compute 输出键（深拷贝，不动入参）。

    `compute_metrics` 在该快照上跑不动（缺原始输入）时显式报错——不静默产出一个
    「看起来像 OFF 态、其实只去掉了半个机制」的快照。
    """
    off = copy.deepcopy(snapshot)
    keys = compute_output_keys(snapshot)
    stripped = sorted(k for k in keys if k in off)
    for key in stripped:
        off.pop(key, None)
    off["__a1_stripped_keys__"] = stripped
    return off


def _normalize_reports(reports: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for agent, report in (reports or {}).items():
        if hasattr(report, "model_dump"):
            out[str(agent)] = dict(report.model_dump())
        elif isinstance(report, dict):
            out[str(agent)] = report
    return out


def _claim_stats(claims: Sequence[dict], ruler: dict) -> dict:
    """用生产 citation 链量一组 claim：可重算根的值级错误率 + 其余分型计数。"""
    from finance_agent import citation as citation_mod

    parsed: list[Any] = []
    for claim in claims:
        try:
            parsed.append(citation_mod.Claim.model_validate(claim))
        except Exception:  # noqa: BLE001 - 解析不了的 claim 单列，不混进比率
            parsed.append(None)
    ok: list[tuple[int, Any]] = [(i, c) for i, c in enumerate(parsed) if c is not None]
    verdicts = pr.run_citation_chain([c for _, c in ok], ruler)["verdicts"] if ok else []
    recomputable = value_fail = unresolvable = unverifiable = other_fail = 0
    per_claim: list[dict] = []
    for (index, claim), verdict in zip(ok, verdicts, strict=True):
        is_recomputable = citation_mod._recomputable_root(claim)
        status = str(verdict.get("status") or "")
        bucket = verdict.get("bucket")
        if is_recomputable:
            recomputable += 1
            if status == "FAIL" and bucket == "value_mismatch":
                value_fail += 1
        if status == "FAIL" and bucket == "path_unresolvable":
            unresolvable += 1
        elif status == "UNVERIFIABLE":
            unverifiable += 1
        elif status == "FAIL":
            other_fail += 1
        per_claim.append(
            {
                "agent": None,
                "index": index,
                "field_ref": claim.field_ref,
                "stated_value": claim.stated_value,
                "recomputable": is_recomputable,
                "status": status,
                "bucket": bucket,
                "ground_truth": verdict.get("ground_truth"),
            }
        )
    return {
        "claims_total": len(claims),
        "claims_parsed": len(ok),
        "claims_unparsed": len(claims) - len(ok),
        "recomputable_claims": recomputable,
        "recomputable_value_fail": value_fail,
        "recomputable_error_rate": (value_fail / recomputable) if recomputable else None,
        "path_unresolvable": unresolvable,
        "unverifiable": unverifiable,
        "other_fail": other_fail,
        "per_claim": per_claim,
    }


def run_arm(
    snapshot: dict,
    *,
    arm: str,
    graph_runner: Callable[..., dict],
    ruler: dict,
    query: str = pr.DEFAULT_QUERY,
    llm_meter: Callable[[], int] | None = None,
) -> dict:
    """单态一趟分析师 + 生产 citation 链判定（尺子 = `ruler`，两态必须同一份）。"""
    before = pr._meter(llm_meter)
    state = dict(
        graph_runner(
            variant=VARIANT,
            snapshot=strip_compute_outputs(snapshot) if arm == ARM_OFF else snapshot,
            query=query,
        )
    )
    after = pr._meter(llm_meter)
    reports = _normalize_reports(state.get("analyst_reports"))
    claims = pr._flatten_claim_dicts(reports)
    stats = _claim_stats(claims, ruler)
    stats["arm"] = arm
    stats["llm_calls"] = None if before is None or after is None else after - before
    stats["citation_pass"] = bool(state.get("citation_pass"))
    stats["citation_fail_buckets"] = dict(state.get("citation_fail_buckets") or {})
    return stats


def run_a1_case(
    product: dict,
    *,
    graph_runner: Callable[..., dict],
    llm_meter: Callable[[], int] | None = None,
    query: str = pr.DEFAULT_QUERY,
    arms: Sequence[str] = (ARM_OFF, ARM_ON),
) -> dict:
    """单标的 A1 单元：同一快照跑两态（OFF = 剥 compute 输出；ON = 原样），配对比较。

    两态的 claim 判定都用**同一份完整快照**做尺子（`ruler`）——尺子不动，动的只有分析师输入。
    """
    ticker = str(product.get("ticker") or "")
    snapshot = dict(product.get("snapshot") or {})
    ruler = {k: v for k, v in snapshot.items() if k != "__a1_stripped_keys__"}
    unit: dict[str, Any] = {
        "unit_id": f"{ticker}::a1",
        "case_id": f"{ticker}-a1",
        "ticker": ticker,
        "leg": LEG_A1,
        "cost_class": COST_CLASS_A1,
        "mechanism_id": "A1",
        "variant": VARIANT,
        "status": pr.STATUS_OK,
        "status_reason": "",
        "arms": {},
        "llm_calls": None,
    }
    try:
        stripped_keys = sorted(compute_output_keys(snapshot))
    except Exception as exc:  # noqa: BLE001 - 缺原始输入 → 该产物测不了 A1，显式 void
        unit.update(status=pr.STATUS_VOID, status_reason=f"compute_metrics 跑不动：{exc}")
        return unit
    missing = [k for k in stripped_keys if k not in snapshot]
    unit["compute_output_keys"] = stripped_keys
    unit["compute_output_keys_absent"] = missing
    if not stripped_keys or missing:
        unit.update(
            status=pr.STATUS_VOID,
            status_reason=(
                f"快照缺 compute 输出键 {missing}" if missing else "compute_metrics 无输出键"
            ),
        )
        return unit

    calls_before = pr._meter(llm_meter)
    for arm in arms:
        unit["arms"][arm] = run_arm(
            snapshot,
            arm=arm,
            graph_runner=graph_runner,
            ruler=ruler,
            query=query,
            llm_meter=llm_meter,
        )
    calls_after = pr._meter(llm_meter)
    unit["llm_calls"] = (
        None if calls_before is None or calls_after is None else calls_after - calls_before
    )
    missing_arms = [a for a in (ARM_OFF, ARM_ON) if a not in unit["arms"]]
    if missing_arms:
        unit.update(
            status=pr.STATUS_VOID, status_reason=f"缺臂 {missing_arms}：配对不成立，不作读写数"
        )
        return unit
    off_rate = unit["arms"][ARM_OFF]["recomputable_error_rate"]
    on_rate = unit["arms"][ARM_ON]["recomputable_error_rate"]
    unit["recomputable_error_rate_off"] = off_rate
    unit["recomputable_error_rate_on"] = on_rate
    unit["recomputable_error_rate_delta"] = (
        None if off_rate is None or on_rate is None else off_rate - on_rate
    )
    if off_rate is None or on_rate is None:
        unit.update(
            status=pr.STATUS_VOID,
            status_reason="某一态没有可重算 claim（分母为 0）：该标的对主指标无信息",
        )
    return unit


def _structure(units: Sequence[dict], arm: str) -> dict:
    """该臂的 claim 产出结构：总量 / 派生指标引用（compute 产出根）/ 不可解析占比。"""
    total = derived = unresolvable = 0
    for unit in units:
        payload = (unit.get("arms") or {}).get(arm)
        if payload is None:
            continue
        roots = set(unit.get("compute_output_keys") or [])
        for claim in payload.get("per_claim") or []:
            total += 1
            if str(claim.get("field_ref") or "").split(".")[0] in roots:
                derived += 1
            if claim.get("status") == "FAIL" and claim.get("bucket") == "path_unresolvable":
                unresolvable += 1
    return {
        "claims_total": total,
        "compute_root_claims": derived,
        "compute_root_share": (derived / total) if total else None,
        "path_unresolvable": unresolvable,
        "path_unresolvable_share": (unresolvable / total) if total else None,
    }


def arm_of(unit: dict) -> str | None:
    """该单元是否跑过臂（成本记账口径：已尝试即计，不看 ok/void）。"""
    return "arms" if unit.get("arms") else None


def a1_report(units: Sequence[dict], *, tickers: Sequence[str] = ()) -> dict:
    """A1 批报告：计算型 claim 值级错误率（OFF/ON）+ 配对 Δ 的簇 bootstrap CI。"""
    ok = [u for u in units if u.get("status") == pr.STATUS_OK]
    void = [u for u in units if u.get("status") == pr.STATUS_VOID]
    errored = [u for u in units if u.get("status") == pr.STATUS_ERROR]

    rates_off = {str(u["ticker"]): [float(u["recomputable_error_rate_off"])] for u in ok}
    rates_on = {str(u["ticker"]): [float(u["recomputable_error_rate_on"])] for u in ok}
    ci = None
    if len(rates_off) >= 2:
        from evals.causal_ablation.aggregate import cluster_mean_diff_ci

        ci = cluster_mean_diff_ci(rates_off, rates_on)

    attempted = [u for u in units if arm_of(u) is not None]  # 含 void：成本按已尝试单元全计

    def _agg(arm: str, field: str) -> int:
        return int(
            sum((u["arms"][arm].get(field) or 0) for u in attempted if arm in u.get("arms", {}))
        )

    def _arm_units(arm: str) -> list[dict]:
        return [u for u in attempted if arm in u.get("arms", {})]

    off_claims = _agg(ARM_OFF, "recomputable_claims")
    on_claims = _agg(ARM_ON, "recomputable_claims")
    off_fail = _agg(ARM_OFF, "recomputable_value_fail")
    on_fail = _agg(ARM_ON, "recomputable_value_fail")
    return {
        "mechanism_id": "A1",
        "primary_metric": "计算型 claim 数值错误率（可重算根 claim 的 value_mismatch 占比）",
        "units_total": len(units),
        "units_ok": len(ok),
        "units_void": len(void),
        "units_error": len(errored),
        "void_reasons": [u.get("status_reason") for u in void],
        "error_reasons": [
            {"unit_id": u.get("unit_id"), "reason": u.get("status_reason")} for u in errored
        ],
        ARM_OFF: {
            "recomputable_claims": off_claims,
            "recomputable_value_fail": off_fail,
            "rate": (off_fail / off_claims) if off_claims else None,
            "claims_total": _agg(ARM_OFF, "claims_total"),
            "path_unresolvable": _agg(ARM_OFF, "path_unresolvable"),
            "unverifiable": _agg(ARM_OFF, "unverifiable"),
            "llm_calls": _sum_optional(
                u["arms"][ARM_OFF]["llm_calls"] for u in attempted if ARM_OFF in u.get("arms", {})
            ),
        },
        ARM_ON: {
            "recomputable_claims": on_claims,
            "recomputable_value_fail": on_fail,
            "rate": (on_fail / on_claims) if on_claims else None,
            "claims_total": _agg(ARM_ON, "claims_total"),
            "path_unresolvable": _agg(ARM_ON, "path_unresolvable"),
            "unverifiable": _agg(ARM_ON, "unverifiable"),
            "llm_calls": _sum_optional(
                u["arms"][ARM_ON]["llm_calls"] for u in attempted if ARM_ON in u.get("arms", {})
            ),
        },
        # 主指标（登记口径）：只对**配对可比**的单元算 Δ/CI；分母塌缩的单元不可配对，
        # 其塌缩事实单列在 claim_structure，不得混进「0 差异」里读
        "delta_rate": ((off_fail / off_claims) - (on_fail / on_claims))
        if off_claims and on_claims
        else None,
        "delta_paired_units": len(ok),
        "claim_structure": {
            "note": "两态产出结构（含 void 单元；分母塌缩本身是读数，不混进 Δ）",
            "off": _structure(units, ARM_OFF),
            "on": _structure(units, ARM_ON),
        },
        "cluster_ci95": list(ci) if ci else None,
        "cluster_ci95_note": (
            f"配对单元 {len(ok)}/{len(units)}（其余因某一态分母为 0 不可配对）"
            "——CI 仅覆盖可配对子集，分母塌缩的单元无贡献，不得读作「差异为 0 且区间窄」"
            if len(ok) < len(units)
            else "全部单元配对可比"
        ),
        "per_ticker": [
            {
                "ticker": u["ticker"],
                "rate_off": u["recomputable_error_rate_off"],
                "rate_on": u["recomputable_error_rate_on"],
                "delta": u["recomputable_error_rate_delta"],
                "claims_off": u["arms"][ARM_OFF]["recomputable_claims"],
                "claims_on": u["arms"][ARM_ON]["recomputable_claims"],
            }
            for u in ok
        ],
        "llm_calls_total": _sum_optional(u.get("llm_calls") for u in units),
        "tickers": list(tickers),
        "reading_notes": [
            "两态用同一尺子（完整快照 + 生产 citation 链）判定；动的只有分析师所见输入",
            "重算路由（incident 028 修复）保证可重算根的 claim 真值来自原始数据重算，"
            "与派生字典在不在无关——OFF 态的自算值因此可被同一判据抓住",
            "claim 数（claims_total）与产出形态变化也是读数：机制少了会不会连数都不给",
        ],
    }


def _sum_optional(values: Iterable[Any]) -> int | None:
    """可选计量求和：全部未知记 None（不得伪造成 0）。"""
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None
