"""因子提案月批（delta add-monthly-batch-scheduling）：ops 通道入口。

``run_factor_proposal_monthly`` 是「因子提案」月批任务（每月 1 日 10:00
Asia/Shanghai）的编排入口，走统一日批通道（``ops.jobs.run_job``：调度 / 运行历史 /
手动补跑 / 单飞锁）：

1. **面板**：快照库最近 ``_PANEL_LOOKBACK_DAYS`` 个交易日 → ``MarketPanel``
   （取数口径与 ``monitor.run_factor_decay_monitor`` 单一来源）；快照库为空 /
   ``as_of`` 前无数据 → ``ValueError``（fail loud，先建面板后提案——网关调用
   不发生）；
2. **提案**：``propose_broad_batch``（跨桶采样：分桶存量 + 算子历史入库 IC 均值）；
   ``client`` 缺省 = ``GatewayProposalClient``（双档路由快档）；**网关不可用 →
   异常照实上抛**（job 记 failed，月批可重试，不静默、不伪造候选）；
3. **准入**：逐条经五道闸 ``admit_candidates``——**同批一次调用**（闸③ 置换检验 +
   BH-FDR 的口径是「同批次候选」，见 ``validate.admit_candidates``）；逐条 DSL
   预检兜住单条异常（不阻断整批，计入 ``errors``）。

摘要（运行历史）``{"proposed", "admitted", "rejected", "errors", "rejections"}``：

- ``proposed`` = 提案批产出候选数；``admitted`` = 过闸入库数（``factor_id`` 非空；
  同批保种立即退役者亦计入「已入库」）；``rejected`` = 闸口拒收数；``errors`` =
  预检失败数（``proposed = admitted + rejected + errors``）；
- ``rejections`` = 未过者**原因码**逐条（``{expression, gate, reason}``；预检失败
  ``gate=None``）——五道闸的拒收事实此前无落点：``factor_events.factor_id`` 为
  NOT NULL，被拒候选无因子行（``admit_candidates`` 明确「被拒候选项不落库」），
  故统一落运行历史摘要（``job_runs`` 是运维可查的事件史）。

口径说明（本模块内的确定性选择）：分桶存量与算子 IC 均值均以**库内全部因子行**
（含 retired；retired 允许重提，仍计存量）为口径；算子 IC = 该因子近 ``IC_WINDOW``
个交易日 RankIC 均值，按表达式涉及的算子归属后逐算子取均值（无 IC 记录者不计）。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from finance_agent.data.snapshot import (
    _parse_date_param,  # noqa: PLC2701 - 私有跨模块导入与 monitor 同先例（日期严格解析单一来源）
    init_snapshot_db,
    latest_snapshot_date,
)
from finance_agent.factors.dsl import (
    AstNode,
    Call,
    DSLParseError,
    FactorComplexityError,
    parse,
)
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import list_factors, list_ic_series
from finance_agent.factors.monitor import (  # noqa: PLC2701 - 面板取数口径单一来源（同包先例）
    _PANEL_LOOKBACK_DAYS,
    _load_panel,
    _recent_trade_dates,
)
from finance_agent.factors.propose import (
    MIN_BROAD_BATCH_N,
    GatewayProposalClient,
    ProposalLLMClient,
    propose_broad_batch,
)
from finance_agent.factors.tools import IC_WINDOW
from finance_agent.factors.validate import AdmissionResult, admit_candidates

logger = logging.getLogger(__name__)

# 闸③ 置换检验的取样种子（固定默认 → 同面板同候选逐位可复现；ops 批次先例 seed=42）
DEFAULT_PROPOSAL_SEED = 42

# 提案来源标签（落库 source；与 builtin 的 "builtin" 并列，界面按此区分 AI 提案）
PROPOSAL_SOURCE = "proposal"


def _bucket_stats(*, db_path: str | Path | None = None) -> dict[str, int]:
    """因子库分桶存量（category → 行数；库空 → {}，prompt 渲染为「因子库为空」）。"""
    counts: dict[str, int] = {}
    for row in list_factors(db_path=db_path):
        category = str(row.get("category") or "")
        if not category:
            continue
        counts[category] = counts.get(category, 0) + 1
    return counts


def _operators_of(expression: str) -> set[str]:
    """表达式涉及的算子名集合（AST 收集）；不可解析（历史脏行）→ 空集（不归属）。"""
    try:
        node = parse(expression, enforce_complexity=False)
    except DSLParseError:
        logger.warning("因子表达式不可解析，算子 IC 归属跳过: %r", expression)
        return set()
    operators: set[str] = set()
    stack: list[AstNode] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, Call):
            operators.add(current.op)
            stack.extend(current.args)
    return operators


def _operator_ic(*, db_path: str | Path | None = None, window: int = IC_WINDOW) -> dict[str, float]:
    """算子历史入库 IC 均值（P4 prompt 先验）：逐因子近 ``window`` 日 RankIC 均值 →
    按算子归属后取均值；无 IC 记录的因子不计。"""
    by_operator: dict[str, list[float]] = {}
    for row in list_factors(db_path=db_path):
        values = [
            float(value)
            for _trade_date, value in list_ic_series(int(row["id"]), limit=window, db_path=db_path)
        ]
        if not values:
            continue
        mean = math.fsum(values) / len(values)
        for operator in _operators_of(str(row.get("expression") or "")):
            by_operator.setdefault(operator, []).append(mean)
    return {op: math.fsum(means) / len(means) for op, means in by_operator.items()}


def _panel_from_snapshot(
    snapshot_db_path: str | Path | None, *, as_of: str | None = None
) -> MarketPanel:
    """快照库 → 准入面板（fail loud：空库/窗口内无数据 → ``ValueError``）。"""
    init_snapshot_db(snapshot_db_path)  # 幂等建表：空库读也确定（不抛 no such table）
    resolved = as_of
    if resolved is None:
        resolved = latest_snapshot_date(snapshot_db_path)
        if resolved is None:
            raise ValueError("快照库为空：无法构造因子准入面板（先跑 market_snapshot_daily）")
    else:
        resolved = _parse_date_param(resolved, param="as_of").isoformat()
    dates = _recent_trade_dates(snapshot_db_path, as_of=resolved, limit=_PANEL_LOOKBACK_DAYS)
    if not dates:
        raise ValueError(f"快照库在 {resolved} 前无数据：无法构造因子准入面板")
    return _load_panel(snapshot_db_path, dates=dates)


def _rejection_reason(result: AdmissionResult) -> str:
    """拒收原因码（实际执行过的闸口中首个未过者；编排契约保证拒收必有未过闸口）。"""
    for gate in result.gates:
        if not gate.passed:
            return str(gate.reason)
    return "UNKNOWN"


def _preflight(
    candidates: list[dict[str, Any]],
) -> tuple[list[int], list[dict[str, Any]], dict[int, str]]:
    """逐条 DSL 预检：不可解析者不进批量准入（单条失败不阻断整批）。

    预检 = 与提案合同同一套 ``parse``（含默认复杂度硬约束）。返回（可准入候选的
    原位置, 可准入候选, {原位置: 错误原文}）；预检失败明细由调用方计入 ``errors``
    与 ``rejections``（gate=None）。位置显式携带——批量准入结果按可准入顺序返回，
    逐字重复的候选不得靠值反查位置（``list.index`` 会错配）。
    """
    positions: list[int] = []
    admissible: list[dict[str, Any]] = []
    failures: dict[int, str] = {}
    for position, candidate in enumerate(candidates):
        try:
            parse(str(candidate["expression"]))
        except (DSLParseError, FactorComplexityError) as exc:
            failures[position] = f"{type(exc).__name__}: {exc}"
            continue
        positions.append(position)
        admissible.append(candidate)
    return positions, admissible, failures


def run_factor_proposal_monthly(
    *,
    client: ProposalLLMClient | None = None,
    panel: MarketPanel | None = None,
    n: int = MIN_BROAD_BATCH_N,
    seed: int = DEFAULT_PROPOSAL_SEED,
    llm_config: dict[str, Any] | None = None,
    as_of: str | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """月批入口（job 零参数调用）：提案批 → 逐条五道闸 → 过闸入库 / 未过留事件史。

    ``panel`` 缺省从快照库构造（``snapshot_db_path`` 调用期读 env）；``client``
    缺省 = 网关提案客户端。网关不可用/编排级异常一律上抛（job 记 failed）。
    """
    if panel is None:
        panel = _panel_from_snapshot(snapshot_db_path, as_of=as_of)
    proposal_client = (
        client if client is not None else GatewayProposalClient(mode="broad", llm_config=llm_config)
    )
    batch = propose_broad_batch(
        n=n,
        client=proposal_client,
        llm_config=llm_config,
        bucket_stats=_bucket_stats(db_path=db_path),
        operator_ic=_operator_ic(db_path=db_path),
    )
    candidates: list[dict[str, Any]] = [
        {
            "expression": item.expression,
            "hypothesis": item.hypothesis,
            "category": item.category,
            "source": PROPOSAL_SOURCE,
            "alignment_score": item.alignment_score,
        }
        for item in batch.proposals
    ]
    positions, admissible, preflight_failures = _preflight(candidates)
    # 同批一次过闸（闸③ BH-FDR 口径 = 同批次候选；逐条预检已隔离单条异常）
    results = admit_candidates(admissible, panel, seed=seed, db_path=db_path) if admissible else []

    outcomes: dict[int, dict[str, Any]] = {
        position: {"expression": candidates[position]["expression"], "gate": None, "reason": error}
        for position, error in preflight_failures.items()
    }
    admitted = 0
    rejected = 0
    for position, candidate, result in zip(positions, admissible, results, strict=True):
        if result.factor_id is not None:
            admitted += 1
            continue
        rejected += 1
        outcomes[position] = {
            "expression": candidate["expression"],
            "gate": result.rejected_gate,
            "reason": _rejection_reason(result),
        }
    summary = {
        "proposed": len(candidates),
        "admitted": admitted,
        "rejected": rejected,
        "errors": len(preflight_failures),
        "rejections": [outcomes[position] for position in sorted(outcomes)],
    }
    logger.info("因子提案月批完成（过闸入库 / 未过留事件史）: %s", summary)
    return summary
