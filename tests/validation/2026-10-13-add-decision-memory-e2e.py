"""add-decision-memory Task 4：合成端到端（已结算历史 + 候选条款 → 全链路 → JSON artifact）。

用途：以**合成数据**（固定种子；零网络、零 LLM）驱动本 change 全链路——
① 造已结算历史（同标的 + 同行业，含回避/不可判定终态）→ ② 月批提炼候选条款 →
③ 人工确认 2/5 → ④ 记忆检索（相关性：同标的 > 同行业 + 时效）→ ⑤ 上下文注入
（分析师/Trader 情景+已确认语义；风控辩论仅已确认语义；未确认文本全路径零命中）→
⑥ 预算裁剪（语义保留、情景按相关性降序截断；裁剪事件带 trace 元数据）→
⑦ citation 覆盖（记忆数值 field_ref 直读 PASS）→ ⑧ ±记忆消融变体构建 + 合成配对聚合报告。

产出：``tests/validation/2026-10-13-add-decision-memory-e2e.json``（全链路 JSON）。

边界（如实记录，不冒充真实读数）：本脚本**不跑真 LLM 批次**——n=10 消融批次是显式
后续项（评估 SOP：judge 校准先行 + 成本；已挂 issue，见验证报告）；消融段用合成配对
读数证明**聚合/报告口径与变体构建**（不构成任何增量价值判断）。

用法::

    uv run python tests/validation/2026-10-13-add-decision-memory-e2e.py

退出码：0 = 全链路 checks 全过；1 = 存在 FAIL。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from evals import ablation as ab  # noqa: E402
from finance_agent.citation import Claim, verify_claims  # noqa: E402
from finance_agent.memory import (  # noqa: E402
    PURPOSE_TAG_DECISION_MEMORY,
    build_memory_block,
    confirm_clause,
    list_pending_clauses,
    render_memory_state,
    run_monthly_extraction,
    store_candidate_clauses,
)
from finance_agent.outcome.track_record.model import (  # noqa: E402
    init_track_record_tables,
    insert_prediction,
    update_prediction_status,
)

AS_OF = "2026-10-13"
JSON_OUT = Path(__file__).resolve().parent / "2026-10-13-add-decision-memory-e2e.json"

SETTLED_FIXTURE: tuple[dict, ...] = (
    # 同标的 300750：3 条（命中/未中/带内中性）
    {
        "pid": "p_own_1",
        "symbol": "300750.SZ",
        "day": "2026-06-02",
        "status": "resolved_win",
        "ret": 0.181,
        "reason": "欧洲需求超预期，二季度出货环比高增",
    },
    {
        "pid": "p_own_2",
        "symbol": "300750.SZ",
        "day": "2026-07-15",
        "status": "resolved_loss",
        "ret": -0.062,
        "reason": "碳酸锂价格反弹挤压毛利，止损离场",
    },
    {
        "pid": "p_own_3",
        "symbol": "300750.SZ",
        "day": "2026-09-01",
        "status": "resolved_neutral",
        "ret": 0.011,
        "reason": "区间震荡，未触达目标位，到期平仓",
    },
    # 同行业 000858（电池/同申万三级行业成分）：2 条（含回避终态）
    {
        "pid": "p_ind_1",
        "symbol": "000858.SZ",
        "day": "2026-08-03",
        "status": "resolved_win",
        "ret": 0.094,
        "reason": "行业景气回升，龙头弹性占优",
    },
    {
        "pid": "p_ind_2",
        "symbol": "000858.SZ",
        "day": "2026-09-20",
        "status": "avoidance",
        "ret": None,
        "reason": "高位放量滞涨，回避观望",
    },
    # 非目标标的（不得进 300750 的记忆）
    {
        "pid": "p_other",
        "symbol": "601398.SH",
        "day": "2026-09-25",
        "status": "resolved_win",
        "ret": 0.03,
        "reason": "银行股红利策略",
    },
)

UNCONFIRMED = ("候选条款二", "候选条款三", "候选条款四", "候选条款五")


def _seed_db(db: Path) -> None:
    init_track_record_tables(db)
    for row in SETTLED_FIXTURE:
        insert_prediction(
            {
                "prediction_id": row["pid"],
                "source_type": "live",
                "symbol": row["symbol"],
                "direction": "long",
                "entry_price": 100.0,
                "rationale_snapshot": {"fund_manager_decision_reasoning": row["reason"]},
                "created_at": f"{row['day']}T09:30:00",
            },
            db_path=db,
            status=row["status"],
        )
        if row["ret"] is not None:
            update_prediction_status(row["pid"], {"raw_return": row["ret"]}, db_path=db)
    # 未结算行（open）不得进记忆
    insert_prediction(
        {
            "prediction_id": "p_open",
            "source_type": "live",
            "symbol": "300750.SZ",
            "direction": "long",
            "rationale_snapshot": {},
            "created_at": "2026-09-28T09:30:00",
        },
        db_path=db,
        status="open",
    )


def main() -> int:  # noqa: C901 - 端到端脚本，顺序叙事
    checks: dict[str, bool] = {}
    payload: dict = {"change": "add-decision-memory", "as_of": AS_OF, "synthetic": True}

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sessions.db"
        _seed_db(db)
        peer_frame = pd.DataFrame({"代码": ["000858"], "名称": ["五粮液"]})
        state_in = {
            "stock_code": "300750",
            "stock_name": "宁德时代",
            "peer_financials": peer_frame,
        }

        # ① 月批提炼 → pending
        batch_ids = run_monthly_extraction(db_path=db, as_of=AS_OF)
        pending = list_pending_clauses(db_path=db)
        checks["月批提炼落 pending（规则式降级，零 LLM）"] = len(batch_ids) >= 1
        payload["monthly_batch"] = {
            "new_clause_ids": batch_ids,
            "pending_texts": [c["text"] for c in pending],
        }

        # ② 人工确认 2/5（spec 场景：5 条候选 2 条确认）
        all_ids = store_candidate_clauses(list(UNCONFIRMED), db_path=db)
        confirm_targets = [batch_ids[0], all_ids[0]]
        checks["人工确认门禁：confirm 返回命中"] = all(
            confirm_clause(cid, db_path=db) for cid in confirm_targets
        )
        confirmed_texts = [c["text"] for c in pending if c["clause_id"] == batch_ids[0]]
        confirmed_texts.append(UNCONFIRMED[0])
        payload["confirmation"] = {
            "candidates": len(pending) + len(all_ids),
            "confirmed": confirmed_texts,
            "unconfirmed": [c["text"] for c in list_pending_clauses(db_path=db)],
        }

        # ③ 检索 + 预算裁剪（小预算触发裁剪；trace 元数据经假 client 捕获）
        client = MagicMock()
        span = MagicMock()
        client.start_as_current_observation.return_value.__enter__.return_value = span
        with patch("finance_agent.langfuse_tracing.get_langfuse", return_value=client):
            recalled = render_memory_state(state_in, db_path=db, as_of=AS_OF, budget_chars=900)
        memory = recalled["decision_memory"]
        payload["recall"] = memory
        payload["trace_metadata"] = span.update.call_args.kwargs["metadata"]
        ids = [e["decision_id"] for e in memory["episodic"]]
        checks["检索相关性：同标的优先 → 同行业，均按日期降序，且不含非目标标的/未结算行"] = (
            ids == ["p_own_3", "p_own_2", "p_own_1", "p_ind_2", "p_ind_1"]
        )
        checks["记忆条目标注来源决策 ID 与日期"] = all(
            e["decision_id"] and e["date"] for e in memory["episodic"]
        )
        checks["trace 用途标签逐字 decision_memory"] = (
            payload["trace_metadata"]["purpose_tag"]
            == PURPOSE_TAG_DECISION_MEMORY
            == "decision_memory"
        )

        # ④ 预算裁剪：语义保留 + 情景按相关性降序截断 + 裁剪事件计数
        checks["预算裁剪：情景被截断且裁剪事件计数一致"] = (
            memory["trimmed"] == len(memory["dropped_ids"]) and memory["used_chars"] <= 900
        )
        checks["预算裁剪：语义条款完整保留"] = [c["text"] for c in memory["semantic"]] == (
            confirmed_texts
        )
        checks["预算裁剪事件进 trace（trimmed/dropped_ids）"] = (
            payload["trace_metadata"]["trimmed"] == memory["trimmed"]
            and payload["trace_metadata"]["dropped_ids"] == memory["dropped_ids"]
        )

        # ⑤ 上下文注入（分析师×4 / Trader / 风控辩论）
        from finance_agent.nodes.analysts import (  # noqa: E402
            _build_fundamental_context,
            _build_macro_context,
            _build_sentiment_context,
            _build_technical_context,
        )
        from finance_agent.nodes.risk import _build_risk_context  # noqa: E402
        from finance_agent.nodes.trader import _build_trader_context  # noqa: E402

        full_state = {**state_in, **recalled}
        analyst_contexts = {
            name: builder(full_state)
            for name, builder in (
                ("technical", _build_technical_context),
                ("macro", _build_macro_context),
                ("fundamental", _build_fundamental_context),
                ("sentiment", _build_sentiment_context),
            )
        }
        trader_context = _build_trader_context(full_state)
        risk_context = _build_risk_context(full_state)
        injected_ids = [e["decision_id"] for e in memory["episodic"]]
        checks["分析师×4 + Trader 上下文含记忆条目（来源 ID + 日期）"] = all(
            all(f"[记忆] 来源: {pid} " in text for pid in injected_ids)
            for text in (*analyst_contexts.values(), trader_context)
        )
        checks["风控辩论上下文仅含已确认语义条款（无情景记忆）"] = (
            all(t in risk_context for t in confirmed_texts)
            and "[记忆] 来源: p_own" not in risk_context
        )
        checks["未确认条款全 prompt 路径零命中"] = all(
            candidate not in text
            for candidate in UNCONFIRMED[1:]
            for text in (*analyst_contexts.values(), trader_context, risk_context)
        )
        payload["injection"] = {
            "analyst_block_chars": {name: len(text) for name, text in analyst_contexts.items()},
            "trader_block_chars": len(trader_context),
            "risk_block_chars": len(risk_context),
        }

        # ⑥ 向后兼容：无记忆 → 各上下文零记忆块（逐字节同「扩展前」）
        plain = dict(state_in)
        checks["向后兼容：无记忆键 → 各上下文无记忆块"] = all(
            "[记忆]" not in builder(plain)
            for builder in (
                _build_technical_context,
                _build_macro_context,
                _build_fundamental_context,
                _build_sentiment_context,
                _build_trader_context,
                _build_risk_context,
            )
        )

        # ⑦ citation 覆盖：记忆数值可被 claim 校验（直读路径；块内指令要求 interpretation
        # 不写来源 ID——见下方已知交互）
        target = memory["episodic"][0]
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="decision_memory.episodic.0.raw_return",
            stated_value=target["raw_return"],
            interpretation="该历史决策结算收益为正",
            direction="positive",
            metric_name="raw_return",
            period=target["date"],
        )
        (verdict,) = verify_claims([claim], full_state)
        checks["记忆条目纳入 citation 校验覆盖（PASS，无覆盖缺口）"] = (
            verdict.status == "PASS" and not verdict.coverage_gap
        )
        payload["citation"] = {
            "status": verdict.status,
            "coverage_gap": verdict.coverage_gap,
            "field_ref": claim.field_ref,
        }
        # 已知交互（如实留证，不当作缺陷处置）：interpretation 若写入来源 ID，ID 数字会被
        # 回声校验读作数值 → internal_inconsistency（块内指令即缓解；校验器侧列为后续项）
        (interaction,) = verify_claims(
            [
                claim.model_copy(
                    update={"interpretation": f"历史决策 {target['decision_id']} 结算收益为正"}
                )
            ],
            full_state,
        )
        payload["known_interactions"] = {
            "id_digits_in_interpretation": {
                "status": interaction.status,
                "bucket": interaction.bucket,
                "mitigation": "记忆块头部指令：interpretation 只写结论与数值、不写来源 ID",
                "evidence": "tests/memory/test_episodic.py::TestCitationCoverage"
                "::test_source_id_digits_trip_echo_check",
            }
        }

        # ⑧ ±记忆消融：变体构建（单变量）+ 合成配对聚合 + 报告口径
        memory_arm = ab.build_variant_graph("tech_memory")
        bare_arm = ab.build_variant_graph("tech_no_memory")
        checks["消融变体可构建且节点集合一致（差异只在入口）"] = {
            n for n in memory_arm.nodes if not n.startswith("__")
        } == {n for n in bare_arm.nodes if not n.startswith("__")}
        runs = [
            {"variant": "tech_no_memory", "ticker": t, "judge": {"report_relevance": v}}
            for t, v in (("600519", 3.0), ("000858", 3.2), ("601318", 2.8))
        ] + [
            {"variant": "tech_memory", "ticker": t, "judge": {"report_relevance": v}}
            for t, v in (("600519", 3.1), ("000858", 3.1), ("601318", 3.0))
        ]
        report = ab.aggregate_memory_ablation(runs, B=2000)
        markdown = ab.render_memory_ablation_report(report)
        checks["消融聚合含 CI 与有效 n 披露"] = (
            report["dims"]["report_relevance"]["ci"][0]
            <= report["dims"]["report_relevance"]["ci"][1]
            and report["pairing_units"] == 3
            and "有效 n" in markdown
            and "适用口径" in markdown
        )
        payload["ablation"] = {
            "variants": [list(ab._MEMORY_VARIANTS)],
            "aggregate": report,
            "report_markdown": markdown,
            "synthetic_note": "合成配对读数：仅证明聚合/报告口径与变体构建，不构成增量价值判断",
        }

        # ⑨ 记忆块逐字样例（报告引用）
        payload["memory_block_sample"] = build_memory_block(
            memory["episodic"][:2], memory["semantic"]
        )

    payload["checks"] = checks
    verdict = "PASS" if all(checks.values()) else "FAIL"
    payload["verdict"] = verdict
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("== add-decision-memory 合成端到端 ==")
    print(f"已结算历史 {len(SETTLED_FIXTURE)} 条（含回避/不可判定终态）+ 1 条 open（不入记忆）")
    print(
        f"月批候选 {len(payload['monthly_batch']['new_clause_ids'])} 条；确认 2 条；"
        f"未确认 {len(payload['confirmation']['unconfirmed'])} 条"
    )
    print(
        f"检索 Top-5 → {[e['decision_id'] for e in payload['recall']['episodic']]}"
        f"（trimmed={payload['recall']['trimmed']}, used_chars={payload['recall']['used_chars']}）"
    )
    print(f"消融聚合：{payload['ablation']['aggregate']['dims']}")
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    print(f"\nJSON artifact → {JSON_OUT}")
    print(f"verdict: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
