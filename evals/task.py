# evals/task.py
"""实验 task 函数:按 item.input.mode 分派管线,输出 JSON 可序列化结果。

- deep:离线 graph.invoke(initial_state),judge_vars 经 extract_judge_vars 预提取
- quick:ReAct agent run_sync(quick 不进 5 层图,无辩论/决策层)
- follow_up / should_clarify:首版跳过(skipped 原因,不报错)

序列化铁律:不得把含 DataFrame 的原始 state 放进返回值。

设计说明:build_5layer_graph / build_agent / get_callback_handler 在模块级导入,
使测试可用 ``@patch("evals.task.build_5layer_graph")`` 等替换——mock.patch 的字符串
目标要求属性在模块命名空间存在(function-local 导入会令 patch 抛 AttributeError,
且即便 patch 成功也绕过 mock)。这三个符号无重名,模块级导入对业务零侵入
(`evals/` 不属 `src/finance_agent/`,仅单向消费业务 API)。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
from collections.abc import Coroutine
from typing import Any

from langchain_core.runnables import RunnableConfig

from evals.extract import anchor_coverage, extract_judge_vars
from finance_agent.agent_factory import build_agent
from finance_agent.graph import build_5layer_graph
from finance_agent.langfuse_tracing import get_callback_handler


def _await_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """运行协程;已在运行 loop 内时迁移到新线程(langfuse run_experiment 上下文兼容)。

    langfuse ``run_experiment`` 是同步函数,内部经 ``run_async_safely`` 在运行中的
    loop L1 内同步调 ``_run_task`` → 我们的 ``run_task`` → ``_run_quick``。此时直接
    ``asyncio.run`` 会抛 ``RuntimeError: cannot be called from a running event loop``。
    检测到运行中的 loop 时,把协程丢进单线程池在新线程跑 ``asyncio.run``,绕过嵌套限制。

    线程池分支用 ``contextvars.copy_context()`` 复制提交线程的上下文,使 langfuse SDK
    靠 contextvars 关联的 experiment → item → 子 span 链在新线程内仍然生效
    (``ThreadPoolExecutor.submit`` 默认不复制提交线程的 contextvars,会导致 quick item
    的 LLM span 脱离 item trace 父节点)。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        ctx = contextvars.copy_context()
        return ex.submit(lambda: ctx.run(asyncio.run, coro)).result()


def _run_deep(inp: dict) -> dict:
    graph = build_5layer_graph()
    initial_state = {
        "stock_code": inp["stock_code"],
        "stock_name": inp.get("stock_name", ""),
        "analysis_type": "comprehensive",
        "peer_codes": None,
        "enable_web_search": False,
        "api_key": None,
        "focus": inp.get("query"),
        "llm_config": None,
    }
    # langfuse 配置时注入回调,使管线 span 挂到实验 item trace 下;
    # 未配置时 get_callback_handler() 返回 None → config=None(零开销,业务无感知)
    handler = get_callback_handler()
    config: RunnableConfig | None = {"callbacks": [handler]} if handler else None
    state = graph.invoke(initial_state, config=config)
    # add-debate-argument-anchors：论点锚点覆盖率（零 LLM）+ 拆项进实验输出；
    # 无论点（quick/旧 trace）→ None，不计入均值
    anchor = anchor_coverage(state.get("debate_anchor_checks") or [])
    return {
        "report": state.get("final_report"),
        "ticker": inp["stock_code"],
        "focus_summary": state.get("focus_summary") or "",
        "judge_vars": extract_judge_vars(state, query=inp.get("query", "")),
        "mode": "deep",
        "skipped": None,
        # harden-citation-semantic-coverage：引用指标进实验报告（citation_pass
        # 布尔转 0/1；citation_coverage 缺失→None 不计入）
        "citation_pass": (
            float(state["citation_pass"]) if state.get("citation_pass") is not None else None
        ),
        "citation_coverage": (
            float(state["citation_coverage"])
            if state.get("citation_coverage") is not None
            else None
        ),
        # rework-citation-gate-attribution 阶段 5：拆报指标（incident 026——
        # 混合 citation_pass 会被读作分析师引用质量）
        "citation_blocked": (
            float(state["citation_blocked"]) if state.get("citation_blocked") is not None else None
        ),
        "citation_analyst_true_fail": (
            float(state["citation_analyst_true_fail"])
            if state.get("citation_analyst_true_fail") is not None
            else None
        ),
        "citation_verifier_normalized": (
            float(state["citation_verifier_normalized"])
            if state.get("citation_verifier_normalized") is not None
            else None
        ),
        "citation_unverifiable_text": (
            float(state["citation_unverifiable_text"])
            if state.get("citation_unverifiable_text") is not None
            else None
        ),
        "citation_unverifiable_unregistered": (
            float(state["citation_unverifiable_unregistered"])
            if state.get("citation_unverifiable_unregistered") is not None
            else None
        ),
        "citation_unverifiable_comparative_delta": (
            float(state["citation_unverifiable_comparative_delta"])
            if state.get("citation_unverifiable_comparative_delta") is not None
            else None
        ),
        # surgical-citation-repair：单点修复成功回填数（观测修复触发/成功率）。
        # 注意与 analyst_true_fail 的关系——修复数已计入真错口径，此处单独计数
        # 只为让「修复有没有在干活」可观测，两者不可互相替代。
        "citation_surgical_repaired": (
            float(state["value_mismatch_repaired"])
            if state.get("value_mismatch_repaired") is not None
            else None
        ),
        # add-debate-argument-anchors：锚点覆盖率 + 拆项（detail 去 value——
        # value 已单独成键，拆项只留分桶计数供归因）
        "argument_anchor_coverage": anchor["value"] if anchor else None,
        "argument_anchor_coverage_detail": (
            {k: v for k, v in anchor.items() if k != "value"} if anchor else None
        ),
    }


def _run_quick(inp: dict) -> dict:
    agent = build_agent(mode="quick", session_id=inp.get("session_id"))
    # Agent.run_sync 是 async def(harness/loop.py);用 _await_sync 驱动以兼容
    # langfuse run_experiment 在运行 loop 内同步调本函数的嵌套场景(C1)。
    # inp.get("query", "") 容错缺失键(与 _run_deep 一致)。
    query = inp.get("query", "")
    answer = _await_sync(agent.run_sync(query))
    return {
        "report": answer,
        "ticker": inp.get("ticker"),
        # quick 无辩论/决策层,用 extract_judge_vars 补齐 9 键(缺失键给空串),
        # 下游 evaluator 不会 KeyError;deep-only 项由 run.py _JUDGE_DEEP_ONLY 过滤。
        "judge_vars": extract_judge_vars({"final_report": answer or ""}, query=query),
        "mode": "quick",
        "skipped": None,
    }


def run_task(*, item, expected_output: dict | None = None, **kwargs: Any) -> dict:
    """langfuse run_experiment TaskFunction 兼容签名(task(*, item, **kwargs))。

    item 可以是 DatasetItemClient(有 .input)或本地 dict(有 ["input"])。
    expected_output 优先取显式参数,其次 item.expected_output(DatasetItemClient
    自带),最后空 dict。should_clarify 项首版跳过(不进自动化实验)。
    """
    inp = item.input if hasattr(item, "input") else item["input"]
    # expected_output 优先:显式参数 > DatasetItemClient 属性 > 本地 dict 键(I1)。
    # langfuse LocalExperimentItem 把 expected_output 放 dict 键,hasattr 对 dict
    # 返回 False,需额外 item.get("expected_output") 兜底,否则 should_clarify 漏读。
    exp = (
        expected_output
        or (item.expected_output if hasattr(item, "expected_output") else None)
        or (item.get("expected_output") if isinstance(item, dict) else None)
        or {}
    )
    mode = inp.get("mode", "deep")
    if exp.get("should_clarify"):
        return {
            "report": None,
            "ticker": None,
            "judge_vars": {},
            "mode": mode,
            "skipped": "意图澄清项首版不进自动化实验",
        }
    if mode == "deep":
        return _run_deep(inp)
    if mode == "quick":
        return _run_quick(inp)
    return {
        "report": None,
        "ticker": None,
        "judge_vars": {},
        "mode": mode,
        "skipped": f"mode={mode} 首版不支持(follow_up 需 session fixture)",
    }
