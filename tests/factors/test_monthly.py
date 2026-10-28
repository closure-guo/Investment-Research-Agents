"""add-monthly-batch-scheduling 3.1：因子提案月批入口 ``run_factor_proposal_monthly``。

spec factor-proposal delta「提案月批排程」两个场景：
- 提案过闸入库：3 条候选 → 1 条过五道闸入因子库（active）、2 条留事件史（原因码），
  摘要 ``{proposed: 3, admitted: 1, rejected: 2}``；
- 网关不可用 fail loud：运行历史记 failed（``run_job`` 侧，见
  tests/outcome/test_ops_jobs.py），因子库零新增（MUST NOT 以 builtin 顶替或静默跳过）。

零真实网络：stub ``ProposalLLMClient`` + 合成面板（固定种子）；prompt 模板走本地兜底。
闸③（置换 + BH-FDR）与闸①②④⑤为真实判定——合成面板使
``ts_mean(volume,20)`` 带真实预测成分（闸② 实测 ICIR ≈ 1.65），两条噪声候选在闸②被拒。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors import monthly
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import add_factor, list_events, list_factors, record_ic
from finance_agent.factors.monthly import run_factor_proposal_monthly
from finance_agent.factors.propose import FactorProposal, ProposalBatchResult
from finance_agent.factors.validate import GATE_SINGLE_FACTOR

# 合成面板（固定种子；实测结论见模块 docstring）
_PANEL_SEED = 20261015
_PANEL_DATES = 120
_PANEL_CODES = 40
_DRIFT_BETA = 0.006
_RETURN_NOISE = 0.02

HYPOTHESIS_OK = "合成提案的经济学假设文本：成交量水平代理规模与流动性，预期与下一期收益相关。"
# 本批三条候选：① 有真实预测成分 → 过闸入库；②③ 纯噪声 → 闸② WEAK_ICIR
ADMITTED_EXPRESSION = "ts_mean(volume,20)"
# 深度批专用候选（同源预测成分、不同窗口——与 ADMITTED_EXPRESSION 不同构）
DEEP_EXPRESSION = "ts_mean(volume,10)"
FAIL_EXPRESSIONS = ("ts_std(amount,20)", "ts_mean(amount,5)")


def _seed_snapshot_dates(db_path: Path, n: int) -> None:
    """向快照库写 ``n`` 个交易日的单票行（面板构造所需列齐备；深度测试用）。"""
    from finance_agent.data.snapshot import write_snapshot_rows

    for offset in range(n):
        trade_date = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
        write_snapshot_rows(
            trade_date,
            [
                {
                    "trade_date": trade_date,
                    "code": "600519",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close_raw": 10.2,
                    "close_qfq": 10.0,
                    "volume": 1000.0,
                    "amount": 1_000_000.0,
                }
            ],
            db_path=db_path,
        )


def test_admission_lookback_independent_of_monitor():
    """准入面板深度常量独立（raise-admission-panel-depth-500）：500；监控口径不动。"""
    from finance_agent.factors import constraints, monitor

    assert constraints.ADMISSION_PANEL_LOOKBACK_DAYS == 500
    assert monitor._PANEL_LOOKBACK_DAYS == constraints.DECAY_WINDOW + 30  # 衰减监控口径逐字不变


def test_panel_from_snapshot_takes_admission_lookback(tmp_path):
    """520 日可用 → 准入面板取最近 500 日（独立于监控面板的 90 日口径）。"""
    from finance_agent.factors.constraints import ADMISSION_PANEL_LOOKBACK_DAYS

    snap = tmp_path / "snap.db"
    _seed_snapshot_dates(snap, n=520)

    panel = monthly._panel_from_snapshot(snap)

    assert len(panel.fields["close"].index) == ADMISSION_PANEL_LOOKBACK_DAYS


def test_panel_from_snapshot_shallow_fails_loud(tmp_path):
    """可用深度 < 下限 → ValueError（MUST NOT 以浅面板放行评估）。"""
    snap = tmp_path / "snap.db"
    _seed_snapshot_dates(snap, n=499)

    with pytest.raises(ValueError, match="深度不足"):
        monthly._panel_from_snapshot(snap)


def _labels(dates: int, codes: int) -> tuple[list[str], list[str]]:
    days = [(date(2026, 5, 1) + timedelta(days=offset)).isoformat() for offset in range(dates)]
    return days, [f"c{index:03d}" for index in range(codes)]


def _signal_panel() -> MarketPanel:
    """合成面板：每代码恒定漂移 ``0.006*q`` + iid 噪声；``volume = exp(0.5*q)`` 恒常。

    于是 ``ts_mean(volume,20)`` 的横截面排序 = q 排序，而次日收益 = 漂移 + 噪声——
    该候选带真实预测成分；``amount`` 为与 q 无关的噪声字段（两条噪声候选的载体）。
    """
    rng = np.random.default_rng(_PANEL_SEED)
    dates, codes = _labels(_PANEL_DATES, _PANEL_CODES)
    quality = rng.normal(size=_PANEL_CODES)
    returns = _DRIFT_BETA * quality[None, :] + _RETURN_NOISE * rng.normal(
        size=(_PANEL_DATES, _PANEL_CODES)
    )
    returns[0] = 0.0
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)
    volume = np.ones((_PANEL_DATES, 1)) * np.exp(0.5 * quality)[None, :]
    amount = 1e9 * np.exp(0.5 * rng.normal(size=(_PANEL_DATES, _PANEL_CODES)))

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=dates, columns=codes)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(volume),
            "amount": frame(amount),
        }
    )


def _proposals(*expressions: str) -> dict[str, Any]:
    """提案响应 payload（``{"proposals": [...]}``，合同逐字：expression/hypothesis/category）。"""
    categories = ("价量", "波动率", "流动性")
    return {
        "proposals": [
            {
                "expression": expression,
                "hypothesis": HYPOTHESIS_OK,
                "category": categories[index % len(categories)],
                "alignment_score": 0.8,
            }
            for index, expression in enumerate(expressions)
        ]
    }


class _StubClient:
    """提案客户端 stub：首轮返回本批候选，补充轮（n=20 目标下的 top-up）返回空批。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    def complete_json(
        self, prompt: str, *, purpose: Any, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self._payload if len(self.prompts) == 1 else {"proposals": []}


class _RoutingStubClient:
    """按 prompt 内容分流返回（广度批 vs 深度批），对修复/补轮循环鲁棒。"""

    def __init__(self, *, broad_payload: dict[str, Any], deep_payload: dict[str, Any]) -> None:
        self._broad = broad_payload
        self._deep = deep_payload
        self.prompts: list[str] = []

    def complete_json(
        self, prompt: str, *, purpose: Any, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self._deep if "深度改进" in prompt else self._broad


class _BoomClient:
    """网关整体不可用：任何调用直接抛（fail loud，不得静默跳过）。"""

    def complete_json(
        self, prompt: str, *, purpose: Any, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        raise RuntimeError("LLM 网关整体不可用")


@pytest.fixture(autouse=True)
def _local_prompts_only(monkeypatch):
    """prompt 模板走本地兜底：加载器客户端钉成 None + 清 TTL 缓存（零网络，同 test_propose）。"""
    from finance_agent.prompts import loader

    monkeypatch.setattr(loader, "_get_client", lambda: None)
    loader._clear_cache()
    yield
    loader._clear_cache()


@pytest.fixture
def db(tmp_path: Path) -> str:
    return str(tmp_path / "factor_library.db")


class TestDeepImprovementBatch:
    """unify-quant-research-architecture：深度改进批接线（拒收分布回喂 + 同五道闸）。

    spec factor-proposal「提案月批排程」MODIFIED（深度批依序运行）与「拒收原因回喂」。
    """

    def test_deep_prompt_carries_rejection_distribution_and_admits(self, db: str):
        client = _RoutingStubClient(
            broad_payload=_proposals(FAIL_EXPRESSIONS[0]),  # 广度批 1 条 → 闸②拒收
            deep_payload=_proposals(ADMITTED_EXPRESSION),  # 深度批 1 条 → 过闸入库
        )

        summary = run_factor_proposal_monthly(client=client, panel=_signal_panel(), db_path=db)

        deep_prompts = [prompt for prompt in client.prompts if "深度改进" in prompt]
        assert len(deep_prompts) == 1  # 深度批恰发起一次（deep 档位路由）
        assert "WEAK_ICIR" in deep_prompts[0]  # 本批拒收分布回喂
        assert summary["deep"] == {"proposed": 1, "admitted": 1, "rejected": 0, "errors": 0}
        assert summary["admitted"] == 1  # 仅深度批候选过闸
        assert summary["rejected"] >= 1  # 广度批候选闸②拒收（n=20 目标下含补轮重复，不钉计数）
        assert [row["expression"] for row in list_factors(db_path=db)] == [ADMITTED_EXPRESSION]

    def test_recent_rejection_feedback_reads_job_runs_and_degraded(self, db: str):
        """聚合来源：运行历史 rejections（gate/reason 计数）+ 因子库 degraded 复盘。"""
        from finance_agent.factors.library import set_status
        from finance_agent.outcome.ops.model import finish_job_run, insert_job_run

        run_id = insert_job_run("factor_proposal_monthly", "manual", "running", source="manual")
        finish_job_run(
            run_id,
            status="ok",
            summary={
                "rejections": [
                    {"expression": "delta(close,20)", "gate": 2, "reason": "WEAK_ICIR"},
                    {
                        "expression": "div(volume,ts_mean(volume,20))",
                        "gate": 2,
                        "reason": "WEAK_ICIR",
                    },
                    {"expression": "foo(close,3)", "gate": None, "reason": "DSLParseError: ..."},
                ]
            },
        )
        degraded_id = add_factor(
            expression="ts_std(delta(close,1),20)",
            hypothesis="测试用：衰减复盘样本（长度达标）。",
            category="波动率",
            status="active",
            db_path=db,
        )
        set_status(degraded_id, "degraded", db_path=db)

        feedback = monthly._recent_rejection_feedback(db_path=db)

        aggregated = next(
            item for item in feedback if item.get("拒收原因", "").startswith("WEAK_ICIR")
        )
        assert aggregated["次数"] == 2
        assert aggregated["典型表达式"] in {"delta(close,20)", "div(volume,ts_mean(volume,20))"}
        assert any(item.get("衰减因子") == "ts_std(delta(close,1),20)" for item in feedback)

    def test_recent_rejection_feedback_empty_is_empty(self, db: str):
        """两类来源均空 → []（渲染侧如实标注「暂无」，不虚构分布）。"""
        assert monthly._recent_rejection_feedback(db_path=db) == []


class TestBatchAdmission:
    """spec 场景「提案过闸入库」：1 过 2 不过（库 + 事件史两处断言）。"""

    def test_one_admitted_two_rejected_with_reason_codes(self, db: str):
        client = _StubClient(_proposals(ADMITTED_EXPRESSION, *FAIL_EXPRESSIONS))

        summary = run_factor_proposal_monthly(client=client, panel=_signal_panel(), db_path=db)

        # 摘要计数（proposal 批产出 3 条，1 条过闸）
        assert summary["proposed"] == 3
        assert summary["admitted"] == 1
        assert summary["rejected"] == 2
        assert summary["errors"] == 0
        # 未过者留事件史（原因码）：逐条 expression + 闸口 + 原因
        assert [item["expression"] for item in summary["rejections"]] == list(FAIL_EXPRESSIONS)
        assert [item["reason"] for item in summary["rejections"]] == ["WEAK_ICIR", "WEAK_ICIR"]
        assert [item["gate"] for item in summary["rejections"]] == [GATE_SINGLE_FACTOR] * 2
        assert summary["proposed"] == summary["admitted"] + summary["rejected"] + summary["errors"]

        # 库：仅过闸者入库（active、来源 proposal），未过者零行
        rows = list_factors(db_path=db)
        assert len(rows) == 1
        assert rows[0]["expression"] == ADMITTED_EXPRESSION
        assert rows[0]["status"] == "active"
        assert rows[0]["source"] == "proposal"
        # 事件史：入库者落 created + status_change(ADMITTED) 事件
        events = list_events(int(rows[0]["id"]), db_path=db)
        assert [event["event"] for event in events] == ["created", "status_change"]
        assert events[-1]["to_status"] == "active"
        # ADMITTED detail 带 orientation（issue #514）；符号与因子行持久化值一致
        assert events[-1]["detail"]["reason"] == "ADMITTED"
        assert events[-1]["detail"]["orientation"] == rows[0]["orientation"]

    def test_prompt_carries_library_bucket_stats_and_operator_ic(self, db: str):
        """跨桶采样上下文来自真实因子库（分桶存量 + 算子历史入库 IC 均值）。"""
        seeded = add_factor(
            expression="delta(close,3)",
            hypothesis=HYPOTHESIS_OK,
            category="动量",
            status="candidate",
            db_path=db,
        )
        record_ic(seeded, "2026-09-01", 0.02, db_path=db)
        record_ic(seeded, "2026-09-02", 0.02, db_path=db)
        client = _StubClient(_proposals(ADMITTED_EXPRESSION, *FAIL_EXPRESSIONS))

        run_factor_proposal_monthly(client=client, panel=_signal_panel(), db_path=db)

        first_prompt = client.prompts[0]
        assert "动量 1 条" in first_prompt  # 分桶存量（该桶 1/1 = 100%）
        assert "delta: 0.0200" in first_prompt  # 算子历史入库 IC 均值

    def test_single_bad_expression_does_not_block_batch(self, db: str, monkeypatch):
        """逐条失败不阻断整批：一条表达式不可解析 → errors=1，其余照常裁决。"""
        batch = ProposalBatchResult(
            proposals=(
                FactorProposal(
                    expression="unknown_op(close)", hypothesis=HYPOTHESIS_OK, category="价量"
                ),
                FactorProposal(
                    expression=ADMITTED_EXPRESSION, hypothesis=HYPOTHESIS_OK, category="价量"
                ),
                FactorProposal(
                    expression=FAIL_EXPRESSIONS[1], hypothesis=HYPOTHESIS_OK, category="流动性"
                ),
            ),
            rejected=(),
            trace_metadata={},
        )
        monkeypatch.setattr(monthly, "propose_broad_batch", lambda **_kwargs: batch)
        monkeypatch.setattr(
            monthly,
            "propose_deep_improvement",
            lambda **_kwargs: ProposalBatchResult(proposals=(), rejected=(), trace_metadata={}),
        )
        client = _StubClient(_proposals(ADMITTED_EXPRESSION))

        summary = run_factor_proposal_monthly(client=client, panel=_signal_panel(), db_path=db)

        assert summary["proposed"] == 3
        assert summary["errors"] == 1
        assert summary["admitted"] == 1
        assert summary["rejected"] == 1
        assert [item["expression"] for item in summary["rejections"]] == [
            "unknown_op(close)",
            FAIL_EXPRESSIONS[1],
        ]
        assert summary["rejections"][0]["gate"] is None
        assert "unknown_op" in summary["rejections"][0]["reason"]
        assert [row["expression"] for row in list_factors(db_path=db)] == [ADMITTED_EXPRESSION]

    def test_gateway_unavailable_fails_loud_with_zero_library_writes(self, db: str):
        """spec 场景「网关不可用 fail loud」：异常上抛，因子库零新增（无 builtin 顶替）。"""
        with pytest.raises(RuntimeError, match="LLM 网关整体不可用"):
            run_factor_proposal_monthly(client=_BoomClient(), panel=_signal_panel(), db_path=db)
        assert list_factors(db_path=db) == []

    def test_empty_snapshot_fails_loud_before_any_llm_call(self, tmp_path: Path, db: str):
        """无面板可用（快照库为空）→ fail loud；LLM 调用不发生（先建面板后提案）。"""
        client = _StubClient(_proposals(ADMITTED_EXPRESSION, *FAIL_EXPRESSIONS))
        with pytest.raises(ValueError, match="快照库"):
            run_factor_proposal_monthly(
                client=client,
                db_path=db,
                snapshot_db_path=str(tmp_path / "empty_snapshot.db"),
            )
        assert client.prompts == []
        assert list_factors(db_path=db) == []


class TestPostAdmissionReviewHook:
    """add-factor-post-admission-review：月批尾部入库后复核挂载。"""

    def test_monthly_summary_includes_review_block(self, db: str):
        client = _RoutingStubClient(
            broad_payload=_proposals(FAIL_EXPRESSIONS[0]),
            deep_payload=_proposals(ADMITTED_EXPRESSION),
        )
        summary = run_factor_proposal_monthly(client=client, panel=_signal_panel(), db_path=db)
        assert "review" in summary
        assert set(summary["review"].keys()) == {"reviewed", "degraded", "skipped"}
        # 新入库因子无 IC 样本 → 复核样本不足，不判定
        assert summary["review"]["reviewed"] == 0

    def test_monthly_review_degrades_weak_seeded_factor(self, db: str):
        """预置 80 条恒定 IC 的 active 因子（ICIR=0）→ 月批复核降级。"""
        fid = add_factor(
            expression="ts_std(close,10)",
            hypothesis="h",
            category="测试",
            status="active",
            db_path=db,
        )
        for i in range(80):
            record_ic(fid, (date(2025, 1, 1) + timedelta(days=i)).isoformat(), 0.04, db_path=db)
        client = _RoutingStubClient(
            broad_payload=_proposals(FAIL_EXPRESSIONS[0]),
            deep_payload=_proposals(ADMITTED_EXPRESSION),
        )
        summary = run_factor_proposal_monthly(client=client, panel=_signal_panel(), db_path=db)
        assert fid in summary["review"]["degraded"]
        rows = {row["id"]: row for row in list_factors(db_path=db)}
        assert rows[fid]["status"] == "degraded"
        events = list_events(fid, db_path=db)
        assert events[-1]["to_status"] == "degraded"
        assert events[-1]["detail"]["reason"] == "POST_ADMISSION_REVIEW"
