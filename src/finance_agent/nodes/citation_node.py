"""引用校验图节点 - 从 analyst_reports 提取 claims，批量校验。

在 Layer I 分析师产出后执行，校验所有 Claim 的数据引用。
citation_pass 驱动 after_citation 路由：PASS -> 渲染，FAIL -> 重试。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, cast

from finance_agent.citation import (
    CitationReport,
    CitationResult,
    Claim,
    value_close,
    verify_claims,
)
from finance_agent.citation_coverage import (
    CoverageReport,
    compute_coverage,
    extract_census_numbers,
)
from finance_agent.langfuse_tracing import get_langfuse, update_current_span
from finance_agent.models import AnalystReport
from finance_agent.nodes.citation_repair import repair_claims
from finance_agent.routing import citation_retry_stagnated

logger = logging.getLogger("finance_agent.citation")

# refine-citation-coverage-v3 D2/D4：growth_rates 双条件补登记。
# 取整感知容差：整数百分比取整最大误差 0.5 个百分点
_ROUNDING_PP_TOL = 0.5


def _sentence_split(markdown: str) -> list[str]:
    return [s for s in re.split(r"[。\n！？!?；;]", markdown) if s.strip()]


def _event_values(state: dict) -> list[float]:
    """从 state 事件源（key_events/news_list）提取数值（D5，供 event_covered 标记）。

    事件数字（如新闻「出厂价由 969 元上调」）不建数值 claim 而按事件豁免，
    普查命中 event_covered 而非 unmatched。
    """
    items: list[dict | str] = []
    items.extend(state.get("key_events") or [])
    items.extend(state.get("news_list") or [])
    texts = []
    for it in items:
        if isinstance(it, dict):
            for k in ("title", "summary", "content", "text", "date"):
                v = it.get(k)
                if isinstance(v, str):
                    texts.append(v)
        elif isinstance(it, str):
            texts.append(it)
    values: list[float] = []
    for t in texts:
        for n in extract_census_numbers(t):
            if n.value not in values:
                values.append(n.value)
    return values


def supplement_anomaly_claims(
    markdown: str, state: dict, existing_claims: list[Claim | dict]
) -> list[Claim]:
    """state growth_rates 双条件共现自动补登记（D2 吸收 D4）。

    触发双条件：① 同一句中指标名与正文数值共现；② 数值与
    growth_rates.{dim}.{metric} 的整数百分比渲染按 0.5 个百分点容差匹配。
    field_ref 指向结构化真值 growth_rates.{dim}.{metric}（字符串只定位不验证，
    防循环验证）。与人工申报 claim 同 field_ref 的去重；state 无真值的不补
    （反洗白：编造数字不满足双条件）。

    来源为整个 growth_rates（而非仅 anomalies）——D4 吸收：可重算增速数字
    （如 FCF 同比 96.6%）即使未触发 anomaly（|growth|≤0.5）也能补登记；
    anomalies 是 growth_rates 的 |growth|>0.5 子集，天然覆盖。
    """
    growth_rates = state.get("growth_rates") or {}
    sentences = _sentence_split(markdown)
    existing_refs = {
        (c.field_ref if isinstance(c, Claim) else c.get("field_ref")) for c in existing_claims
    }
    out: list[Claim] = []
    for dim, metrics in growth_rates.items():
        for metric, growth in metrics.items():
            if not isinstance(growth, int | float) or growth is None:
                continue
            pct = int(round(growth * 100))  # 整数百分比渲染（compute.py:.0% 同源）
            for sent in sentences:
                if metric not in sent:
                    continue
                # 句中数值与 growth 整数百分比 0.5pp 容差匹配（percent 面值一致）
                hit = any(
                    abs(n.value - pct) <= _ROUNDING_PP_TOL
                    for n in extract_census_numbers(sent)
                    if n.kind == "percent"
                )
                if not hit:
                    continue
                ref = f"growth_rates.{dim}.{metric}"
                if ref in existing_refs:
                    continue
                out.append(
                    Claim(
                        claim_type="numerical",
                        source_type="data",
                        field_ref=ref,
                        stated_value=pct / 100,  # 分数制，与既有 growth claim 口径一致
                        interpretation=f"{metric} 变化率 {pct}%",
                        metric_name=metric,
                    )
                )
                existing_refs.add(ref)
                break
    return out


def _unverifiable_class_counts(results: list[CitationResult]) -> dict[str, int]:
    """UNVERIFIABLE 三类拆报计数（文本 / 未注册 / 比较型差值）——报告与 trace 共用同一口径。"""
    return {
        "text": sum(
            1
            for r in results
            if r.status == "UNVERIFIABLE"
            and (r.claim.claim_type in ("entity", "regulatory") or r.claim.source_type == "event")
        ),
        "unregistered": sum(
            1
            for r in results
            if r.status == "UNVERIFIABLE"
            and r.claim.claim_type not in ("entity", "regulatory")
            and r.claim.source_type != "event"
            and r.bucket != "comparative_delta_unregistered"
        ),
        "comparative_delta": sum(
            1 for r in results if r.bucket == "comparative_delta_unregistered"
        ),
    }


def verify_citations(state: dict) -> dict:
    """从 analyst_reports 提取所有 Claim，批量校验（按分析师归属聚合）。

    harden-citation-semantic-coverage：FAIL 分桶；值级 FAIL 产出定向重试目标
    与失败明细（供 after_citation 分流与重试上下文注入）；正文 markdown 数字
    普查产出 citation_coverage（监控告警，不进路由）。
    """
    reports = state.get("analyst_reports") or {}

    per_agent: dict[str, list[CitationResult]] = {}
    claims_by_agent: dict[str, list[Claim]] = {}
    for agent, report in reports.items():
        claims = _extract_claims(report)
        claims_by_agent[agent] = claims
        per_agent[agent] = verify_claims(claims, state)

    # D2（refine-citation-coverage-v3）：state anomalies 自动补登记——正文数字 +
    # 指标名同句共现且 state 有结构化真值 → 补 claim，走与人工申报完全相同的校验路径。
    # 编造数字（state 无对应）不满足双条件，保持 unmatched（反洗白）。
    markdown = "\n\n".join(_markdown_of(r) for r in reports.values())
    supplement = supplement_anomaly_claims(
        markdown,
        state,
        [c for claims in claims_by_agent.values() for c in claims],
    )
    if supplement:
        claims_by_agent["anomaly_supplement"] = supplement
        per_agent["anomaly_supplement"] = verify_claims(supplement, state)

    results = [r for rs in per_agent.values() for r in rs]
    report = CitationReport.from_results(results)

    # 分桶聚合：仅 value_mismatch 触发定向重试（D3）；格式类 FAIL 直判不重试
    retry_targets: list[str] = []
    retry_feedback: dict[str, list[dict]] = {}
    fail_buckets: dict[str, int] = {}
    for agent, rs in per_agent.items():
        for r in rs:
            if r.status != "FAIL" or r.bucket is None:
                continue
            fail_buckets[r.bucket] = fail_buckets.get(r.bucket, 0) + 1
            # 定向重试桶：value_mismatch + direction_mismatch（ehr-style-claim-direction，
            # 与值级同级——符号冲突同样需要分析师重写申报组合）
            if r.bucket not in ("value_mismatch", "direction_mismatch"):
                continue
            if agent not in retry_targets:
                retry_targets.append(agent)
            item: dict = {
                "field_ref": r.claim.field_ref,
                "stated_value": r.claim.stated_value,
                "ground_truth": r.ground_truth,
                "delta": r.delta,
                "interpretation": r.claim.interpretation,
            }
            if r.bucket == "direction_mismatch":
                # ehr-style-claim-direction：方向冲突反馈携带真值符号 + 申报示例，
                # 引导修正 stated_value 与 direction 的组合而非仅改数值。
                sign = "负" if float(r.ground_truth or 0) < 0 else "正"
                item["bucket"] = "direction_mismatch"
                item["direction_hint"] = (
                    f"ground_truth 符号为{sign}（{r.ground_truth}）；"
                    "若正文以正向数值表述负向事实，请登记 stated_value=绝对值 + "
                    "direction=negative（示例：下滑 10.05% → stated_value=10.05, "
                    "direction=negative）；若正文直接写 signed 值，登记 stated_value=真值, "
                    "direction=positive"
                )
            retry_feedback.setdefault(agent, []).append(item)
    retry_targets.sort()

    # surgical-citation-repair：value_mismatch 稀疏失败（该分析师同轮 <3 处）→
    # 单点修复分流（出错句叙事改写 + 回填 + 强制重校验）；≥3 或修复调用失败回退
    # 全量定向重试。原 value_mismatch 分桶计数保留（prompt 归因信号不吞），追加
    # value_mismatch_repaired 遥测；同处重校验仍 FAIL 不二次修复；修复轮与全量
    # 重试共享 iteration_count/停滞降级语义（成功率按重校验后 0.0 记入 fail_rates）。
    value_mismatch_repaired = 0
    # incident 029 处置（任务 3）：按 claim 记账——重校验后目标 claim PASS 即计，
    # 不受同分析师无关 FAIL 拦截；旧 all_passed 口径保留一轮（deprecated 跨口径对照）。
    value_mismatch_repaired_claims = 0
    _repair_records: list[dict] = []
    if fail_buckets.get("value_mismatch", 0):
        for agent, rs in per_agent.items():
            vm = [r for r in rs if r.bucket == "value_mismatch"]
            if agent == "anomaly_supplement" or not vm or len(vm) >= 3:
                continue
            rpt = reports.get(agent)
            if rpt is None:
                continue
            md = _markdown_of(rpt)
            if not md:
                continue
            try:
                failures = [
                    {"agent": agent, "claim": r.claim, "ground_truth": r.ground_truth} for r in vm
                ]
                new_md, records = repair_claims(md, failures, llm_config=state.get("llm_config"))
            except Exception:
                logger.warning(
                    "单点修复调用失败，回退目标分析师全量重试：agent=%s", agent, exc_info=True
                )
                continue
            reports[agent] = _with_markdown(reports[agent], new_md)
            # repair records 按序对应 vm 失败（每个失败一条 updated_claim）——按
            # 对象身份写回，field_ref 可能为占位值（测试/fixture 用 "x"）。
            replaced: dict[int, Claim] = {}
            for rec, r in zip(records, vm, strict=False):
                if rec.get("updated_claim"):
                    replaced[id(r.claim)] = rec["updated_claim"]
            claims_by_agent[agent] = [replaced.get(id(c), c) for c in claims_by_agent[agent]]
            reports[agent] = _with_claims(reports[agent], claims_by_agent[agent])
            re_results = verify_claims(_extract_claims(reports[agent]), state)
            re_report = CitationReport.from_results(re_results)
            _re_by_identity = {id(x.claim): x for x in re_results}
            for _r0 in vm:
                _upd = replaced.get(id(_r0.claim), _r0.claim)
                _rr = _re_by_identity.get(id(_upd))
                if _rr is not None and _rr.status == "PASS":
                    value_mismatch_repaired_claims += 1
            if re_report.all_passed:
                value_mismatch_repaired += len(records)
                _repair_records.extend(rec for rec in records if isinstance(rec, dict))
                if agent in retry_targets:
                    retry_targets.remove(agent)
                retry_feedback.pop(agent, None)
                per_agent[agent] = re_results

    # 修复成功时：aggregate report / fail_rate 以重校验后的结果为准
    # （markdown 已回填 reports，下方覆盖率普查自动用修复后文本）；遥测留痕
    # （surgical-citation-repair 3.1/3.3：修前句/修后句/真值/重校验结果上 trace）。
    if value_mismatch_repaired:
        results = [r for rs in per_agent.values() for r in rs]
        report = CitationReport.from_results(results)
        try:
            update_current_span(
                metadata={
                    "surgical_repairs": [
                        {
                            "agent": rec.get("agent"),
                            "before": rec.get("before"),
                            "after": rec.get("after") or rec.get("repaired_sentence"),
                            "ground_truth": rec.get("ground_truth"),
                            "repaired": rec.get("repaired"),
                        }
                        for rec in _repair_records
                        if isinstance(rec, dict)
                    ][:10],
                    "value_mismatch_repaired": value_mismatch_repaired,
                }
            )
        except Exception:
            logger.warning("surgical repair 遥测上报失败（不阻断）", exc_info=True)

    # 正文覆盖率：合并四分析师 markdown 普查，claim stated_value 全集为认领池
    # （含 anomaly_supplement 补登记 claim，认领池同步扩充）
    all_stated = [
        float(c.stated_value)
        for claims in claims_by_agent.values()
        for c in claims
        if _is_float(c.stated_value)
    ]
    coverage = compute_coverage(markdown, all_stated, event_values=_event_values(state))

    # 阶段 4 auto-claim（incident 026）：正文未认领数字在 state 结构化条目中
    # 「唯一匹配」时自动合成 claim（auto=True）——契约病修契约，覆盖缺口不再
    # 靠重跑分析师补。多匹配/零匹配不合成（防歧义洗白）。
    auto_claims: list[Claim] = []
    auto_paths: list[str] = []
    for raw in coverage.unmatched:
        val = _census_value_from_raw(raw)
        if not val:
            continue
        path = _unique_state_match(state, val)
        if path:
            auto_claims.append(
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref=path,
                    stated_value=val,
                    interpretation=raw,
                    auto=True,
                )
            )
            auto_paths.append(path)
    if auto_claims:
        auto_results = verify_claims(auto_claims, state)
        results = results + auto_results
        claims_by_agent["auto_claim"] = auto_claims
        per_agent["auto_claim"] = auto_results
        report = CitationReport.from_results(results)
        all_stated = all_stated + [float(c.stated_value) for c in auto_claims]
        coverage = compute_coverage(markdown, all_stated, event_values=_event_values(state))
        update_current_span(
            metadata={"auto_claims": len(auto_claims), "auto_claim_paths": auto_paths}
        )

    _report_to_langfuse(report, coverage, markdown=markdown)

    # 递增 iteration_count，使 after_citation 的重试上限（< 3）真正生效。
    # 否则 citation_pass=False 时会无限重试，图永远无法推进到辩论/报告阶段。
    iteration_count = state.get("iteration_count", 0)

    # citation-retry-policy delta：记录各轮失败率，供 after_citation 做重试
    # 降级（失败率停滞时提前放行渲染，不再全量重跑分析师）。
    fail_count = sum(1 for r in results if r.status == "FAIL")
    fail_rate = fail_count / len(results) if results else 0.0
    fail_rates = list(state.get("citation_fail_rates") or []) + [fail_rate]

    minor_fail = (not report.all_passed) and fail_count <= 1 and fail_rate <= 0.05
    if minor_fail:
        # skip-citation-retry-on-minor-failures：单点/近零失败直接放行渲染，
        # 不重跑分析师（校验器确定性，同 claim 重跑必复现——incident 022 实测
        # 汉森/茅台 1/46=2.2% FAIL 仍全量重跑 1-2 轮空转）。
        update_current_span(
            metadata={"citation_minor_fail_deescalated": True, "fail_rates": fail_rates},
            level="WARNING",
        )

    # 格式类 FAIL 直判放行（D3）：有 FAIL 但无值级重试目标 → incident 候选可观测
    if not report.all_passed and not retry_targets:
        update_current_span(
            metadata={
                "citation_format_fail_incident_candidate": True,
                "fail_buckets": fail_buckets,
            },
            level="WARNING",
        )

    # D6 打回补 claim：普查 unmatched（D2/D4 自动补 + D5 事件豁免后）按来源分析师打回，
    # 复用重试机制（iteration<3 上限 + 停滞降级语义不变）
    coverage_gap_feedback: dict[str, list[dict]] = {}
    if coverage.unmatched:
        for raw in coverage.unmatched:
            val = _census_value_from_raw(raw)
            for agent, rpt in reports.items():
                if raw in _markdown_of(rpt):
                    coverage_gap_feedback.setdefault(agent, []).append(
                        {
                            "kind": "coverage_gap",
                            "raw": raw,
                            "value": val,
                            # ehr-style-claim-direction：D6 打回提示补登记时同步申报方向
                            "direction_hint": "补登记 claim 时请一并申报 direction"
                            "（positive/negative/flat；None 旧格式会计覆盖缺口）",
                        }
                    )
                    break
    gap_targets = sorted(coverage_gap_feedback)
    if gap_targets:
        retry_targets = sorted(set(retry_targets) | set(gap_targets))
        for agent, items in coverage_gap_feedback.items():
            retry_feedback.setdefault(agent, []).extend(items)
    citation_coverage_gap = bool(gap_targets)

    if not report.all_passed and iteration_count + 1 < 3 and citation_retry_stagnated(fail_rates):
        # 降级决策须可观测：路由将因失败率停滞跳过下一轮重试
        update_current_span(
            metadata={"citation_retry_deescalated": True, "fail_rates": fail_rates},
            level="WARNING",
        )

    # 阶段 0 停滞保护（incident 026）：记录本轮重试目标的输出哈希；若上一轮已记录
    # 且目标分析师重跑后哈希全部未变（重写文本不变），立即放行，不等失败率停滞判定
    prev_hash: dict[str, str] = dict(state.get("citation_retry_prev_hash") or {})
    cur_hash = {
        agent: hashlib.md5(
            _markdown_of(reports[agent]).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        for agent in retry_targets
        if agent in reports
    }
    no_progress = (
        bool(prev_hash)
        and bool(cur_hash)
        and all(prev_hash.get(a) == h for a, h in cur_hash.items())
    )
    if no_progress:
        update_current_span(
            metadata={"citation_retry_no_progress": True, "targets": sorted(cur_hash)},
            level="WARNING",
        )

    counts = _unverifiable_class_counts(results)

    return {
        "citation_report": report.model_dump(),
        "citation_pass": report.all_passed,
        "iteration_count": iteration_count + 1,
        "citation_fail_rates": fail_rates,
        "citation_minor_fail": minor_fail,
        "citation_retry_prev_hash": cur_hash or prev_hash,
        "citation_retry_no_progress": no_progress,
        "citation_retry_targets": retry_targets,
        "citation_retry_feedback": retry_feedback,
        "citation_fail_buckets": fail_buckets,
        "citation_coverage": coverage.coverage,
        "citation_coverage_gap": citation_coverage_gap,
        # 阶段 5 门禁三层分置 + 指标拆报（incident 026）
        "citation_blocked": report.failed > 0,
        # 分析师真错数 = 残余 FAIL + 单点修复已用真值回填的数（修复会把真错藏进 PASS）；
        # 计数用按 claim 新口径（incident 029：旧 all_passed 口径漏计已修 claim）
        "citation_analyst_true_fail": report.failed + int(value_mismatch_repaired_claims or 0),
        "citation_coverage_warn": coverage.coverage < 0.90,
        "citation_unverifiable_text": counts["text"],
        "citation_unverifiable_unregistered": counts["unregistered"],
        "citation_unverifiable_comparative_delta": counts["comparative_delta"],
        "citation_verifier_normalized": sum(
            1 for r in results if r.status == "PASS" and r.unit_normalized is not None
        ),
        "auto_claims": len(auto_claims),
        # surgical-citation-repair：单点修复遥测 + 修复后正文回填（供渲染/下游）
        "value_mismatch_repaired": value_mismatch_repaired,
        "value_mismatch_repaired_claims": value_mismatch_repaired_claims,
        "analyst_reports": reports,
    }


def _is_float(v: object) -> bool:
    try:
        float(v)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


def _markdown_of(report: AnalystReport | dict) -> str:
    if isinstance(report, AnalystReport):
        return report.markdown
    if isinstance(report, dict):
        return str(report.get("markdown") or "")
    return ""


def _with_markdown(report: AnalystReport | dict, new_markdown: str) -> AnalystReport | dict:
    """返回带修复后 markdown 的报告副本（AnalystReport model_copy / dict 浅拷贝）。"""
    if isinstance(report, AnalystReport):
        return report.model_copy(update={"markdown": new_markdown})
    if isinstance(report, dict):
        return {**report, "markdown": new_markdown}
    return report


def _with_claims(report: AnalystReport | dict, claims: list[Claim]) -> AnalystReport | dict:
    """返回带更新后 claim 列表的报告副本（修复 records 的 updated_claim 写回）。"""
    if isinstance(report, AnalystReport):
        return report.model_copy(update={"claims": claims})
    if isinstance(report, dict):
        return {**report, "claims": claims}
    return report


def _report_to_langfuse(
    report: CitationReport, coverage: CoverageReport, markdown: str = ""
) -> None:
    """上报 citation 校验结果到 Langfuse（trace 级 boolean score + span 明细）。

    citation_unverifiable_ratio（spec「UNVERIFIABLE 占比监控」）是数据层退化的
    先行指标；上报失败记 WARN（spec：Langfuse 不可用 SHALL 记 WARN 且不阻断）。
    """
    try:
        client = get_langfuse()
        if client is None:
            logger.warning("Langfuse 未配置, citation score 未上报")
            return
        fail_count = sum(1 for r in report.results if r.status == "FAIL")
        total = len(report.results)
        client.score_current_trace(
            name="citation_pass",
            value=1.0 if report.all_passed else 0.0,
            data_type="BOOLEAN",
            comment=f"{'PASS' if report.all_passed else 'FAIL'}: {fail_count}/{total} claims failed",
            metadata={"all_passed": report.all_passed, "fail_count": fail_count, "total": total},
        )
        ratio = (report.unverifiable / total) if total else 0.0
        counts = _unverifiable_class_counts(report.results)
        client.score_current_trace(
            name="citation_unverifiable_ratio",
            value=round(ratio, 4),
            data_type="NUMERIC",
            comment=f"UNVERIFIABLE {report.unverifiable}/{total}; coverage_gaps={report.coverage_gaps}",
            metadata={
                "unverifiable_text": counts["text"],
                "unverifiable_unregistered": counts["unregistered"],
                "unverifiable_comparative_delta": counts["comparative_delta"],
            },
        )
        # citation_coverage（harden-citation-semantic-coverage）：NUMERIC 0-1，
        # 只监控不进路由；< 0.8 告警（span WARNING + 日志）
        client.score_current_trace(
            name="citation_coverage",
            value=round(coverage.coverage, 4),
            data_type="NUMERIC",
            comment=f"{coverage.matched}/{coverage.total} numbers claimed",
            metadata={"unmatched": coverage.unmatched[:20]},
        )
        if coverage.coverage < 0.8:
            logger.warning(
                "citation_coverage %.4f < 0.8：%d/%d 未认领 %s",
                coverage.coverage,
                len(coverage.unmatched),
                coverage.total,
                coverage.unmatched[:10],
            )
            client.update_current_span(
                metadata={
                    "citation_coverage_alert": True,
                    "citation_coverage": coverage.coverage,
                    "unmatched": coverage.unmatched[:20],
                },
                level="WARNING",
            )
        client.update_current_span(
            metadata={
                "citation_report": report.model_dump(),
                # 普查输入的合并报告 markdown 随 span 落库：coverage 问题可离线重判，
                # 无需重跑管线（record doc 2026-09-02：trace 不落库 markdown 的债）
                "report_markdown": markdown,
            },
        )
    except Exception as e:
        logger.warning("Langfuse citation score 上报失败: %s", e)


def _extract_claims(report: AnalystReport | dict) -> list[Claim]:
    """从 AnalystReport（Pydantic 或 dict）中提取 Claim 列表。"""
    if isinstance(report, AnalystReport):
        return list(report.claims)

    if isinstance(report, dict):
        raw_claims = report.get("claims", [])
        return [Claim.model_validate(c) if isinstance(c, dict) else c for c in raw_claims]

    return []


_AUTO_MATCH_ROOT_DFS = (
    "income_statement",
    "balance_sheet",
    "cash_flow_statement",
    "financial_indicators",
)
_AUTO_MATCH_ROOT_DICTS = (
    "profitability_metrics",
    "solvency_metrics",
    "efficiency_metrics",
    "cashflow_metrics",
    "growth_rates",
    "risk_metrics",
)


def _unique_state_match(state: dict, value: float, max_nodes: int = 20000) -> str | None:
    """阶段 4：正文数值在 state 注册根键中「唯一匹配」时返回其 field_ref 路径。

    遍历限于白名单根键的数值叶子（DataFrame 单元格 / dict 数值叶子），相对容差
    0.5%；多匹配或零匹配返回 None（防同值多字段歧义洗白覆盖缺口）。
    """
    matches: list[str] = []
    scanned = 0

    def _visit(path: str, obj: object, depth: int) -> None:
        nonlocal scanned
        scanned += 1
        if scanned > max_nodes or len(matches) > 1:
            return
        if isinstance(obj, bool) or obj is None:
            return
        if isinstance(obj, int | float):
            if value_close(float(obj), value):
                matches.append(path)
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                _visit(f"{path}.{k}", v, depth + 1)
            return
        if isinstance(obj, list | tuple):
            for i, v in enumerate(obj):
                _visit(f"{path}.{i}", v, depth + 1)
            return
        if hasattr(obj, "columns") and hasattr(obj, "iloc"):  # DataFrame
            df_obj = cast("Any", obj)
            for _, row in df_obj.iterrows():
                row_key = None
                for cand in ("报告日", "日期", "date"):
                    if cand in obj.columns:
                        row_key = row[cand]
                        break
                key_str = str(row_key) if row_key is not None else "row"
                for col in obj.columns:
                    v = row[col]
                    if isinstance(v, int | float) and not isinstance(v, bool):
                        _visit(f"{path}.{key_str}.{col}", v, depth + 1)

    for root in _AUTO_MATCH_ROOT_DFS:
        obj = state.get(root)
        if obj is not None:
            _visit(root, obj, 0)
    for root in _AUTO_MATCH_ROOT_DICTS:
        obj = state.get(root)
        if isinstance(obj, dict):
            for k, v in obj.items():
                _visit(f"{root}.{k}", v, 1)
    return matches[0] if len(matches) == 1 else None


def _census_value_from_raw(raw: str) -> float:
    """unmatched 原文 → 解析值（与 census 缩放一致，供打回反馈）。"""
    num = "".join(ch for ch in raw if ch.isdigit() or ch in ".-")
    try:
        v = float(num)
    except ValueError:
        return 0.0
    return v * (1e8 if "亿" in raw else 1e4 if "万" in raw else 1.0)
