"""数据对齐消融实验（spec evaluation「数据对齐消融实验」；design D4）。

三变体（spec 原文「单分析师直出」按「仅分析师层直出」实现）：
- analysts: 4 分析师并行 → citation → 报告（无辩论/决策层）
- plus_debate: analysts + Bull/Bear 两轮辩论 + research_manager
- full: 完整五层（辩论 + trader + 两轮风险辩论 + fund_manager）

所有变体接收完全相同的 fetch_data+compute_metrics state 快照（重放，不重取数），
差异只可归因于编排架构。每标的先构建一次快照，三变体 × repeats 次共用。

连线与主图（finance_agent.graph.build_5layer_graph）语义一致（对 brief 的修正）：
- StateGraph(AnalysisState) 而非 dict：analyst_reports 的 merge_dicts、
  debate/risk_history 的 add reducer 只在 TypedDict schema 下生效，dict 会因
  并行节点写同一 key 抛 InvalidUpdateError；
- verify_citations 经 after_citation 条件路由：PASS→render（目标随层级递增），
  FAIL→analysts_entry 重试（上限 3，语义同主图）；
- 辩论/风险轮次间用 entry 汇聚节点做 barrier（主图 debate_r2_entry 同款），
  保证第 2 轮能看到第 1 轮双方输出；
- full 的风险层为两轮（r1→r2→risk_judge），fund_manager 保留 after_fund_manager
  退回 trader 循环；
- 不含 generate_file（导出副作用，非被评架构）与 timed_node 包裹（纯遥测）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from evals.causal_ablation.escape import VERIFIER_BUCKETS, split_verifier_buckets
from evals.extract import anchor_coverage, extract_judge_vars
from evals.judges import run_judge_mean
from evals.stats import paired_bootstrap_ci
from finance_agent.nodes.analysts import (
    fundamental_analyst,
    macro_analyst,
    sentiment_analyst,
    technical_analyst,
)
from finance_agent.nodes.citation_node import verify_citations
from finance_agent.nodes.compute import compute_metrics
from finance_agent.nodes.debate import bear_debater, bull_debater
from finance_agent.nodes.fetch import fetch_data
from finance_agent.nodes.fund_manager import fund_manager
from finance_agent.nodes.report import generate_report
from finance_agent.nodes.research_manager import research_manager
from finance_agent.nodes.risk import (
    aggressive_debater,
    conservative_debater,
    neutral_debater,
    risk_judge,
)
from finance_agent.nodes.trader import trader
from finance_agent.routing import after_citation, after_fund_manager
from finance_agent.state import AnalysisState

Variant = Literal["analysts", "plus_debate", "through_trader", "full"]
JUDGE_DIMS = ["report_relevance", "debate_quality", "decision_grounding", "consistency"]
_VARIANTS: tuple[Variant, ...] = ("analysts", "plus_debate", "through_trader", "full")
# through_trader（B5 修正臂，2026-09-18）：analysts+辩论+研究经理+Trader，无风控辩论/FM——
# 两臂都含决策层，修 analysts 对 full 的同义反复口径（analysts 臂无决策章节而判据问决策）。


def _applicable_dims(variant: Variant) -> tuple[str, ...]:
    """judge 维度按变体适用性过滤（#112）。

    plus_debate 无 Trader/风控辩论/RJ/FM 层——consistency/decision_grounding
    评不存在的层只会产伪影（宽容评虚层 vs 挑剔评真层的不对称）。
    """
    if variant == "analysts":
        return ("report_relevance",)
    if variant == "plus_debate":
        return ("report_relevance", "debate_quality")
    if variant == "through_trader":
        # 有 Trader 决策层、无风控辩论/FM——consistency 评的是全链闭合，裁层臂不评
        return ("report_relevance", "debate_quality", "decision_grounding")
    return tuple(JUDGE_DIMS)


_ANALYST_NODES = (
    "technical_analyst",
    "macro_analyst",
    "fundamental_analyst",
    "sentiment_analyst",
)


def _entry(state: dict) -> dict:
    """扇出 entry 节点（passthrough），与主图 _passthrough 同角色。"""
    return {}


def _node(fn: Callable[[dict], dict]) -> Callable[..., dict]:
    """add_node 泛型适配（运行时恒等）。

    业务节点签名 (dict)->dict，而 StateGraph(AnalysisState) 的 add_node 要求
    StateNode[AnalysisState,...]；TypedDict 与 dict 在 mypy 模型下互不兼容，
    主图经 timed_node→未参数化 Callable 达成同样适配，此处不包计时故显式转换。
    """
    return fn


def build_variant_graph(variant: Variant) -> CompiledStateGraph:
    """三变体共用「analysts 并行 → verify_citations」前段，按层级递增编排。

    并行扇出用直连边（entry→N 节点）：与主图 Send 派发语义等价——同一份
    state 并行进入各节点，输出经 reducer（merge_dicts/add）合并。
    """
    if variant not in _VARIANTS:
        raise ValueError(f"未知变体: {variant}")
    g = StateGraph(AnalysisState)  # pyrefly: ignore[bad-specialization]

    # ── 共用前段：分析师并行扇出 → 引用校验汇聚 ──
    g.add_node("analysts_entry", _node(_entry))
    g.add_node("technical_analyst", _node(technical_analyst))
    g.add_node("macro_analyst", _node(macro_analyst))
    g.add_node("fundamental_analyst", _node(fundamental_analyst))
    g.add_node("sentiment_analyst", _node(sentiment_analyst))
    g.add_node("verify_citations", _node(verify_citations))
    g.add_edge(START, "analysts_entry")
    for n in _ANALYST_NODES:
        g.add_edge("analysts_entry", n)
        g.add_edge(n, "verify_citations")

    # citation 校验 PASS 后的渲染入口，随层级递增
    render_target = "generate_report"

    # ── Layer II: Bull/Bear 两轮辩论（轮次间 barrier）──
    if variant in ("plus_debate", "through_trader", "full"):
        g.add_node("debate_r1_entry", _node(_entry))
        g.add_node("bull_r1", _node(bull_debater))
        g.add_node("bear_r1", _node(bear_debater))
        g.add_node("debate_r2_entry", _node(_entry))
        g.add_node("bull_r2", _node(bull_debater))
        g.add_node("bear_r2", _node(bear_debater))
        g.add_node("research_manager", _node(research_manager))
        g.add_edge("debate_r1_entry", "bull_r1")
        g.add_edge("debate_r1_entry", "bear_r1")
        g.add_edge("bull_r1", "debate_r2_entry")
        g.add_edge("bear_r1", "debate_r2_entry")
        g.add_edge("debate_r2_entry", "bull_r2")
        g.add_edge("debate_r2_entry", "bear_r2")
        g.add_edge("bull_r2", "research_manager")
        g.add_edge("bear_r2", "research_manager")
        render_target = "debate_r1_entry"

    # ── Layer III-V: trader → 两轮风险辩论 → risk_judge → fund_manager ──
    if variant == "full":
        g.add_node("trader", _node(trader))
        g.add_node("risk_r1_entry", _node(_entry))
        g.add_node("aggressive_r1", _node(aggressive_debater))
        g.add_node("conservative_r1", _node(conservative_debater))
        g.add_node("neutral_r1", _node(neutral_debater))
        g.add_node("risk_r2_entry", _node(_entry))
        g.add_node("aggressive_r2", _node(aggressive_debater))
        g.add_node("conservative_r2", _node(conservative_debater))
        g.add_node("neutral_r2", _node(neutral_debater))
        g.add_node("risk_judge", _node(risk_judge))
        g.add_node("fund_manager", _node(fund_manager))
        g.add_edge("research_manager", "trader")
        g.add_edge("trader", "risk_r1_entry")
        for n in ("aggressive_r1", "conservative_r1", "neutral_r1"):
            g.add_edge("risk_r1_entry", n)
            g.add_edge(n, "risk_r2_entry")
        for n in ("aggressive_r2", "conservative_r2", "neutral_r2"):
            g.add_edge("risk_r2_entry", n)
            g.add_edge(n, "risk_judge")
        g.add_edge("risk_judge", "fund_manager")
        # 退回 trader（最多 1 次）或放行生成报告——同主图 after_fund_manager
        g.add_conditional_edges("fund_manager", after_fund_manager)
    elif variant == "through_trader":
        g.add_node("trader", _node(trader))
        g.add_edge("research_manager", "trader")
        g.add_edge("trader", "generate_report")
    elif variant == "plus_debate":
        g.add_edge("research_manager", "generate_report")

    # ── 引用校验路由（语义同主图）：PASS→render，FAIL→重试分析师（≤3 次）──
    g.add_node("generate_report", _node(generate_report))
    g.add_conditional_edges(
        "verify_citations",
        after_citation,
        {"render": render_target, "retry": "analysts_entry"},
    )
    g.add_edge("generate_report", END)
    return g.compile()


def build_snapshot(ticker: str, *, client: Any = None, cache: Any = None) -> dict:
    """fetch_data + compute_metrics 一次，输出可重放的 state 快照（含 DataFrame）。"""
    base = {"stock_code": ticker, "enable_web_search": False}
    state: dict = {**base, **fetch_data(base, client=client, cache=cache)}
    state.update(compute_metrics(state))  # type: ignore[arg-type]
    return state


def snapshot_digest(state: dict) -> str:
    """快照摘要（审计用）：各 DataFrame shape + 内容哈希，证明三变体输入一致。

    DataFrame/Series 走 `pd.util.hash_pandas_object`（逐行内容哈希，混合 dtype 与
    object 列均稳定）；`ndarray.tobytes()` 对 object 列序列化的是 PyObject 指针，
    同内容两次构建即得不同摘要，会让驱动侧的续跑核验恒失败（G5 通路验证缺陷），
    故只保留给其他带 shape 的非 DataFrame 对象。摘要串格式不变：`{key}:{shape}:{hash8}`。
    """
    parts: list[str] = []
    for key in sorted(state):
        value: Any = state[key]
        shape = getattr(value, "shape", None)
        if shape is not None:
            values: Any = getattr(value, "values", None)
            if values is not None:
                if isinstance(value, (pd.DataFrame, pd.Series)):
                    # hash_pandas_object 的 .values 类型签名为 ndarray | ExtensionArray，
                    # 二者皆有 tobytes（uint64 原始字节，与分配无关）
                    row_hashes: Any = pd.util.hash_pandas_object(value, index=True).values
                    raw = row_hashes.tobytes()
                else:
                    raw = str(values.tobytes()).encode()
                digest = hashlib.md5(raw, usedforsecurity=False)
                parts.append(f"{key}:{shape}:{digest.hexdigest()[:8]}")
        elif isinstance(value, (str, int, float, bool)) or value is None:
            parts.append(f"{key}:{value!r}")
        else:
            parts.append(f"{key}:type={type(value).__name__}")
    return "|".join(parts)


def citation_buckets_from_state(state: dict) -> dict[str, int]:
    """citation 腿四桶拆报（F3）：state 键 → 桶计数，校验/补零走库侧共享实现。

    桶名与校验唯一来源 `evals.causal_ablation.escape`（VERIFIER_BUCKETS /
    split_verifier_buckets），本层不另建映射副本。状态键缺省或 None → 记 0
    （旧图 / stub 不产该键时不得伪造成「有逃逸」，也不得 KeyError）。
    """
    return split_verifier_buckets(
        {
            # blocked 是布尔（归一后残余 FAIL > 0），桶口径为计数
            "blocked": int(bool(state.get("citation_blocked"))),
            "analyst_true_fail": int(state.get("citation_analyst_true_fail") or 0),
            # state 键 value_mismatch_repaired = 桶名 surgical_repaired
            "surgical_repaired": int(state.get("value_mismatch_repaired") or 0),
            "verifier_normalized": int(state.get("citation_verifier_normalized") or 0),
            # 值槽类型错填（eval-driven-contract-fixes 任务 2）：计数在 fail_buckets
            "claim_contract_error": int(
                (state.get("citation_fail_buckets") or {}).get("claim_contract_error") or 0
            ),
        }
    )


def run_variant_once(variant: Variant, snapshot: dict, query: str) -> dict:
    """单次变体运行：快照重放 + citation_pass/四桶 + judge 变量提取 + 锚点覆盖率。

    锚点覆盖率为确定性指标（零 LLM）：无辩论层（analysts）或旧格式 → None。
    四桶拆报为计数（零 LLM），标量 citation_pass 仅保留向后兼容。
    """
    graph = build_variant_graph(variant)
    state: dict = graph.invoke({**snapshot, "focus": query})
    judge_vars = extract_judge_vars(state, query=query)
    return {
        "final_report": state.get("final_report"),
        "citation_pass": bool(state.get("citation_pass")),
        "judge_vars": judge_vars,
        "decision": state.get("final_trade_decision"),
        # 论点锚点覆盖率（delta add-debate-argument-anchors 4.4；口径见 evals/extract）
        "anchor_coverage": anchor_coverage(state.get("debate_anchor_checks") or []),
        # citation 腿四桶拆报（F3）：层增量结论的 citation 口径，替代 citation_pass 标量
        "citation_buckets": citation_buckets_from_state(state),
    }


def material_filename(variant: str, ticker: str, repeat: int) -> str:
    """judge 材料文件名（按 run 三元组）。"""
    return f"{variant}-{ticker}-{repeat}.json"


def persist_materials(
    materials_dir: Path, variant: str, ticker: str, repeat: int, judge_vars: dict
) -> str:
    """落盘单条 run 的 judge 输入材料，返回该文件路径（POSIX 风格，跨平台可复现）。"""
    materials_dir.mkdir(parents=True, exist_ok=True)
    path = materials_dir / material_filename(variant, ticker, repeat)
    path.write_text(json.dumps(judge_vars, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.as_posix()


def judge_detail(result: dict) -> dict:
    """judge 结果 → run 记录用明细。

    含 debate v6 封顶/枚举遥测，以及均值协议的每次分数与极差（round11 实测
    单次调用在 5/4 边界翻转 1/5——噪声须随 run 可见，不静默平均掉）。
    """
    detail: dict[str, Any] = {"score": result.get("score")}
    if result.get("reason") is not None:
        detail["reason"] = str(result.get("reason"))[:300]
    for key in ("scores", "score_spread", "judge_repeats", "judge_failures"):
        if result.get(key) is not None:
            detail[key] = result[key]
    for key in ("qualitative_points", "cap_applied", "enumeration_missing"):
        if key in result:
            detail[key] = result[key]
    return detail


def score_run(
    variant: Variant,
    out: dict,
    *,
    ticker: str,
    repeat: int,
    judge_repeats: int = 3,
    materials_dir: Path | None = None,
) -> dict:
    """单条 run 的判分 + judge_vars 落盘——库侧唯一实现（spec「跑批入口唯一化」）。

    维度适用性过滤（#112）、K 次均值判分、judge 明细塑形、材料落盘全在此处；
    `run_ablation` 与跑批驱动均只调用本函数，SHALL NOT 各自内联同名逻辑。

    `out` 为 `run_variant_once` 的返回值；`materials_dir=None` 时不落盘
    （`materials_path` 记 None），调用方决定材料目录。
    """
    judge_scores: dict[str, float | None] = {}
    judge_details: dict[str, dict] = {}
    applicable = _applicable_dims(variant)
    for dim in JUDGE_DIMS:
        if dim not in applicable:
            judge_scores[dim] = None  # 该变体无对应层，维度不适用（#112）
            continue
        result = run_judge_mean(dim, out["judge_vars"], repeats=judge_repeats)
        judge_scores[dim] = float(result["score"]) if result["score"] is not None else None
        judge_details[dim] = judge_detail(result)
    coverage = out.get("anchor_coverage")
    return {
        "judge": judge_scores,
        "judge_detail": judge_details,
        "materials_path": (
            persist_materials(materials_dir, variant, ticker, repeat, out["judge_vars"])
            if materials_dir is not None
            else None
        ),
        # 论点锚点覆盖率（delta add-debate-argument-anchors 4.4）：analysts 无辩论层
        # -> None，不得伪造 0；拆项去 value（value 已单独成键），供归因
        "argument_anchor_coverage": (coverage or {}).get("value"),
        "argument_anchor_coverage_detail": (
            {k: v for k, v in coverage.items() if k != "value"} if coverage else None
        ),
    }


# citation 四桶的方向元数据（G7/⑥），键域 = `escape.VERIFIER_BUCKETS`。
#
# `conclusion_for_layer` 的措辞按**计数增减方向**生成（count-direction 通用，与桶语义
# 无关：计数下降恒写「显著退步」）；因此质量解读**不**在措辞里，消费者读
# `layer["citation_<bucket>"]` 时必须结合本字段：True → 计数下降即改善；None → 遥测，
# 无方向。故意不改 `conclusion_for_layer` 措辞（不引入第二套措辞口径）。
_CITATION_BUCKET_LOWER_IS_BETTER: dict[str, bool | None] = {
    # 质量计数：下降（diff < 0）即改善
    "blocked": True,  # 归一后残余 FAIL > 0 的阻断
    "analyst_true_fail": True,  # 残余 FAIL + 单点修复回填（真错，须计入）
    # 工作量遥测：无好坏方向（校验器/修复的触发量与解析债）
    "surgical_repaired": None,  # 单点修复成功回填条数
    "verifier_normalized": None,  # 归一后由 FAIL 转 PASS 的计数
    # claim 契约错（变化量入水平槽等）：LLM 契约缺陷计数，下降即改善
    "claim_contract_error": True,
}


def conclusion_for_layer(ci: tuple[float, float]) -> str:
    """层级增量结论措辞：CI 整体>0 显著改进；整体<0 显著退步；含 0 未获支持。"""
    if ci[0] > 0:
        return "显著改进"
    if ci[1] < 0:
        return "显著退步"
    return "该层价值未获统计支持"


def aggregate_results(runs: list[dict], *, B: int = 10_000, seed: int = 42) -> dict:  # noqa: N803
    """runs: [{variant, ticker, citation_pass, cite buckets, judge, argument_anchor_coverage}]。

    层级增量 = 上一变体 → 本变体的配对 bootstrap CI（按 ticker 中位数配对），覆盖
    judge 维度、citation 四桶（F3）与论点锚点覆盖率三类指标。

    citation 腿的层增量结论 SHALL 只认四桶拆报（`citation_<bucket>`，spec evaluation
    「citation 腿拆报呈现」）；`citation_pass_rate` 层块仅留点估计并标记
    `not_for_conclusions`，SHALL NOT 参与层增量解读。
    """
    report: dict[str, Any] = {"variants": {}, "layers": {}}
    for variant in _VARIANTS:
        v_runs = [r for r in runs if r["variant"] == variant]
        report["variants"][variant] = {
            "n_runs": len(v_runs),
            "citation_pass_rate": (
                sum(1 for r in v_runs if r["citation_pass"]) / len(v_runs) if v_runs else None
            ),
            "judge_medians": {
                dim: _median([r["judge"][dim] for r in v_runs if r["judge"].get(dim) is not None])
                for dim in JUDGE_DIMS
            },
            # 论点锚点覆盖率（4.4）：变体内中位数（跳过 None；全无可比值 → None）
            "anchor_coverage_median": _median(
                [
                    r["argument_anchor_coverage"]
                    for r in v_runs
                    if r.get("argument_anchor_coverage") is not None
                ]
            ),
            # citation 四桶（F3）：逐桶中位数（跳过 None/旧记录；全无该键 → None）。
            # 不含 total：中位数的和 ≠ 和的位数，避免读者误读
            "citation_buckets_median": _citation_bucket_medians(v_runs),
        }
    layer_names = {"plus_debate": "debate", "full": "full"}
    for variant in ("plus_debate", "full"):
        prev = "analysts" if variant == "plus_debate" else "plus_debate"
        prev_by_ticker = _by_ticker_median(runs, prev)
        cur_by_ticker = _by_ticker_median(runs, variant)
        common = sorted(set(prev_by_ticker) & set(cur_by_ticker))
        # 配对单元披露：bootstrap 的重采样单位是标的（不是 run）——「3 标的 × 10 重复」
        # 的有效 n 是 3，读报告的人须能直接看见，否则会把 CI 宽度读成效应波动
        layer: dict[str, Any] = {"pairing_unit": "ticker", "pairing_units": len(common)}
        for dim in JUDGE_DIMS:
            # 按 ticker 配对：两侧都有该维度分数才进入序列（保证等长配对）
            seq_prev: list[float] = []
            seq_cur: list[float] = []
            for t in common:
                p = prev_by_ticker[t].get(dim)
                c = cur_by_ticker[t].get(dim)
                if p is not None and c is not None:
                    seq_prev.append(p)
                    seq_cur.append(c)
            if not seq_prev:
                continue
            lo, hi = paired_bootstrap_ci(seq_cur, seq_prev, B=B, seed=seed)
            layer[f"judge_{dim}"] = {
                # 点估计为均值差（与 paired_bootstrap_ci 的 mean-diff 口径一致）；
                # 配对单元本身是逐标的中位数，但聚合统计量不是中位数——勿读作 median
                "diff_mean": round(sum(seq_cur) / len(seq_cur) - sum(seq_prev) / len(seq_prev), 4),
                "ci": (round(lo, 4), round(hi, 4)),
                "conclusion": conclusion_for_layer((lo, hi)),
            }
        # citation_pass 率层块：标量已退出层增量结论路径（F3；spec evaluation「citation
        # 腿拆报呈现」——SHALL NOT 以 citation_pass 标量作层增量比较：不再出 CI / 结论，
        # 只留点估计供向后兼容与健康监控，并显式标记 not_for_conclusions。
        # 层增量解读一律看下方 citation_<bucket> 四桶拆报。
        layer["citation_pass_rate"] = {
            "prev": report["variants"][prev]["citation_pass_rate"],
            "current": report["variants"][variant]["citation_pass_rate"],
            "not_for_conclusions": True,
        }
        # citation 腿四桶拆报（F3）：口径同 judge 维度——逐标的中位 + 配对 bootstrap
        # mean-diff CI。旧 run 记录无 citation_buckets 或任一侧无可配对标的 → 整桶跳过，
        # 不产伪层结论、不伪造 0。结论措辞按计数增减方向生成（conclusion_for_layer 同
        # 口径，count-direction 通用）；质量解读随桶语义（blocked / analyst_true_fail
        # 下降即改善）——以 `lower_is_better` 元数据随条目落盘，消费者须据此读（G7/⑥）。
        prev_bucket_by_ticker = _by_ticker_bucket_median(runs, prev)
        cur_bucket_by_ticker = _by_ticker_bucket_median(runs, variant)
        common_bucket_tickers = sorted(set(prev_bucket_by_ticker) & set(cur_bucket_by_ticker))
        for bucket in VERIFIER_BUCKETS:
            seq_bucket_prev: list[float] = []
            seq_bucket_cur: list[float] = []
            for t in common_bucket_tickers:
                p = prev_bucket_by_ticker[t].get(bucket)
                c = cur_bucket_by_ticker[t].get(bucket)
                if p is not None and c is not None:
                    seq_bucket_prev.append(p)
                    seq_bucket_cur.append(c)
            if not seq_bucket_prev:
                continue
            lo, hi = paired_bootstrap_ci(seq_bucket_cur, seq_bucket_prev, B=B, seed=seed)
            layer[f"citation_{bucket}"] = {
                "diff_mean": round(
                    sum(seq_bucket_cur) / len(seq_bucket_cur)
                    - sum(seq_bucket_prev) / len(seq_bucket_prev),
                    4,
                ),
                "ci": (round(lo, 4), round(hi, 4)),
                "conclusion": conclusion_for_layer((lo, hi)),
                # 方向元数据（G7/⑥）：措辞是计数口径，质量解读须按本字段施加
                "lower_is_better": _CITATION_BUCKET_LOWER_IS_BETTER[bucket],
            }
        # 论点锚点覆盖率层增量（4.4）：与 judge 维度同口径——逐标的中位数（跳过 None）
        # 配对 + mean-diff 配对 bootstrap CI。任一侧无可配对的标的（如 prev=analysts
        # 无辩论层、旧 run 记录缺该键）则整条跳过，不产伪层结论、不伪造 0。
        prev_anchor = _by_ticker_anchor_median(runs, prev)
        cur_anchor = _by_ticker_anchor_median(runs, variant)
        anchor_prev: list[float] = []
        anchor_cur: list[float] = []
        for t in sorted(set(prev_anchor) & set(cur_anchor)):
            p = prev_anchor[t]
            c = cur_anchor[t]
            if p is not None and c is not None:
                anchor_prev.append(p)
                anchor_cur.append(c)
        if anchor_prev:
            lo, hi = paired_bootstrap_ci(anchor_cur, anchor_prev, B=B, seed=seed)
            layer["argument_anchor_coverage"] = {
                "diff_mean": round(
                    sum(anchor_cur) / len(anchor_cur) - sum(anchor_prev) / len(anchor_prev), 4
                ),
                "ci": (round(lo, 4), round(hi, 4)),
                "conclusion": conclusion_for_layer((lo, hi)),
            }
        report["layers"][layer_names[variant]] = layer
    return report


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _by_ticker_median(runs: list[dict], variant: str) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict[str, float | None]] = {}
    tickers = {r["ticker"] for r in runs if r["variant"] == variant}
    for t in tickers:
        t_runs = [r for r in runs if r["variant"] == variant and r["ticker"] == t]
        out[t] = {
            dim: _median([r["judge"][dim] for r in t_runs if r["judge"].get(dim) is not None])
            for dim in JUDGE_DIMS
        }
    return out


def _by_ticker_anchor_median(runs: list[dict], variant: str) -> dict[str, float | None]:
    """按 ticker 聚合 argument_anchor_coverage 中位数（跳过 None）；口径同 judge 维度。

    旧 run 记录（本 delta 之前）无该键：按「无值」跳过，不得 KeyError 或伪造成 0；
    标的全为 None（如 analysts 无辩论层）-> 中位数 None，整层配对随之跳过。
    """
    out: dict[str, float | None] = {}
    tickers = {r["ticker"] for r in runs if r["variant"] == variant}
    for t in tickers:
        t_runs = [r for r in runs if r["variant"] == variant and r["ticker"] == t]
        out[t] = _median(
            [
                r["argument_anchor_coverage"]
                for r in t_runs
                if r.get("argument_anchor_coverage") is not None
            ]
        )
    return out


def _citation_bucket_medians(v_runs: list[dict]) -> dict[str, float | None] | None:
    """变体内四桶逐桶中位数（跳过缺键的旧记录）；全无该键 → None（不得伪造 0）。"""
    buckets = [r["citation_buckets"] for r in v_runs if r.get("citation_buckets")]
    if not buckets:
        return None
    return {
        bucket: _median([float(b[bucket]) for b in buckets if b.get(bucket) is not None])
        for bucket in VERIFIER_BUCKETS
    }


def _by_ticker_bucket_median(runs: list[dict], variant: str) -> dict[str, dict[str, float | None]]:
    """按 ticker 聚合 citation 四桶中位数（跳过 None/旧记录）；口径同 judge 维度。

    旧 run 记录（本 delta 之前）无 `citation_buckets`：按「无值」跳过，不得 KeyError
    或伪造成 0；残缺记录中缺失的桶同样按无值处理。
    """
    out: dict[str, dict[str, float | None]] = {}
    tickers = {r["ticker"] for r in runs if r["variant"] == variant}
    for t in tickers:
        t_runs = [r for r in runs if r["variant"] == variant and r["ticker"] == t]
        out[t] = {
            bucket: _median(
                [
                    float(r["citation_buckets"][bucket])
                    for r in t_runs
                    if (r.get("citation_buckets") or {}).get(bucket) is not None
                ]
            )
            for bucket in VERIFIER_BUCKETS
        }
    return out


def run_ablation(
    tickers: Sequence[str],
    *,
    repeats: int = 3,
    judge_repeats: int = 3,
    snapshot_builder: Callable[[str], dict] | None = None,
    query: str = "综合评估投资价值",
) -> dict:
    """消融主流程：每标的一次快照 → 3 变体 × repeats 次 → 聚合报告。

    判分走 K 次均值（`run_judge_mean(judge_repeats)`）：round11 实测单次 judge 调用
    在 5/4 边界双峰翻转（σ≈0.5，与待测层增量同阶），单次调用不足以支撑层间比较。

    实际跑批消耗 LLM token（3 标的 × 3 变体 × 3 次 ≈ 27 次深度分析），属人工触发的
    评估动作；本函数不做静默降级。
    """
    builder = snapshot_builder or build_snapshot
    runs: list[dict] = []
    snapshots: dict[str, str] = {}
    for ticker in tickers:
        snapshot = builder(ticker)
        snapshots[ticker] = snapshot_digest(snapshot)
        for variant in _VARIANTS:
            for repeat in range(repeats):
                out = run_variant_once(variant, snapshot, query)
                # 判分/过滤/明细塑形只经库侧唯一入口（spec「跑批入口唯一化」）；
                # 本路径不落材料（materials_dir=None → materials_path 记 None）
                runs.append(
                    {
                        "variant": variant,
                        "ticker": ticker,
                        **score_run(
                            variant, out, ticker=ticker, repeat=repeat, judge_repeats=judge_repeats
                        ),
                        # citation 腿：标量保留（向后兼容）+ 四桶拆报（F3 层增量口径）
                        "citation_pass": out["citation_pass"],
                        "citation_buckets": out["citation_buckets"],
                    }
                )
    report = aggregate_results(runs)
    from evals.run import _collect_prompt_versions

    report["prompt_versions"] = _collect_prompt_versions()  # 复现证据：prompt 版本随报告落盘
    report["snapshot_digests"] = snapshots  # 三变体共用同一 digest = 输入对齐证据
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return report


def main() -> None:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="数据对齐消融实验")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--judge-repeats",
        type=int,
        default=3,
        help="judge 每维度重复次数（取均值，降 5/4 边界噪声）",
    )
    args = parser.parse_args()
    report = run_ablation(
        list(args.tickers), repeats=args.repeats, judge_repeats=args.judge_repeats
    )
    out_dir = Path("reports/ablation")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ablation-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"消融报告已写入 {path}")


if __name__ == "__main__":
    main()
