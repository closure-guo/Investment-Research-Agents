"""TESTING=1 专用测试造数实现（C6③：自 api.py 迁出，docs/architecture-debt/c6-small-items.md 小件③）。

端点薄壳仍在 api.py（TESTING 门控注册）；本模块承载全部造数编排。
非 TESTING 环境不 import 本模块（api 端点函数体内懒加载）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# 依赖方向说明：本模块是 api 的 TESTING 附属实现——api 在端点函数体内懒加载本模块，
# 此时 api 模块已完全初始化，故此处反向 import api 私有 helper 安全（非循环）。
from finance_agent.api import (  # noqa: E402
    _get_analyze_queue,
    _now,
    _reset_analyze_queue,
    _seed_broker_account,
    _track_as_of,
)
from finance_agent.factors.library import FACTOR_STATES  # noqa: E402
from finance_agent.session_store import (  # noqa: E402
    append_chat,
    create_session,
    update_pipeline_snapshot,
    update_pipeline_timelines,
    update_session_usage,
)


async def run_test_seed(req: dict[str, Any]) -> dict[str, Any]:
    """测试数据造数端点。

    接收 display_name / session_type / chat_history（含可选 thinking + tool_calls
    + agentTimeline），内部经 session_store.create_session + append_chat 写入真实存储；
    顶层可选 pipeline_timelines（{node: [TimelineItem]}）与 pipeline_snapshot（dict），
    分别经 update_pipeline_timelines / update_pipeline_snapshot 落库，
    供历史会话恢复等 E2E 确定性构造会话（persist-full-session-timeline delta）；
    顶层可选 track_record（{equity_curve, index_closes, metrics_snapshot,
    predictions, paper_equity}）写战绩页造数表（add-index-performance-compare /
    add-portfolio-beta-alpha / add-prediction-pool-integrity / add-paper-broker），
    无需 chat_history 也可单独使用，且不创建会话；track_record_reset（bool：清
    战绩造数面五表（predictions/daily_marks/equity_curve/agent_metrics_daily/
    index_closes）+ paper_equity，供战绩页 E2E 套件全量复位）同不受前置约束；
    顶层可选 snapshot（dict：写 snapshot_meta 键）与 snapshot_reset（bool：先清
    快照三表）造行情快照告警态（add-market-snapshot D6），同样无需 chat_history。
    顶层可选 screening（dict：``batch`` 造批次行（含 error 造失败横幅）、``pool``
    造精选池行、``queue`` 造批量分析队列徽标态；``batches[]/batch`` 行可选
    ``tier``（档位，缺省 main）、``frontier_gate``、``exec_constraints``
    （add-microcap-pool-ui））与 screening_reset（bool：清
    screening 五表**并丢弃进程内队列单例**）造选股页确定性状态
    （add-screening-pipeline S6）；库路径于调用期读 SCREENING_DB_PATH（导入期
    冻结会让 E2E 注入失效）。
    顶层可选 memory（dict：``clauses`` 造条款，逐条经既有落库 + 状态机置
    pending/confirmed/rejected；可带 source 与 evidence（JSON 文本，结算样本）
    造记忆确认页确定性状态）与 memory_reset（bool：清条款表）造记忆确认页
    确定性状态（delta add-memory-confirmation T1/2.3）；库路径于调用期读
    SESSIONS_DB_PATH（semantic 模块 env 口径，E2E 指向独立测试库）。
    顶层可选 factors（dict：``factors`` 造因子，逐条经既有落库 +
    状态机走到目标态（active 记 ADMITTED，与准入路径同源；degraded/retired 的
    detail 由行内 ``detail`` 给出；``via_degraded`` 为真时 retired 先经
    degraded(DECAY) 落衰减史，供「衰减标记」列造态）；行内可带 ic
    （[[trade_date, rank_ic]...] 经 record_ic 幂等写入）与
    pool_increment/alignment_score/ast_json）与 factors_reset（bool：清因子库
    三表）造因子库页确定性状态（delta add-factor-library-page T1.2）；库路径
    于调用期读 FACTOR_LIBRARY_DB_PATH（library 模块 env 口径，E2E 指向独立
    测试库，绝不落到开发库 data/factor_library.db）。
    顶层可选 broker（dict：``cash`` 写账户余额、``positions`` 造持仓、
    ``orders`` 造订单（四态/原因码/证据链照填，``order_id`` 缺省按
    PaperBroker.order_id_for 同源派生））与 broker_reset（bool：清账户三表
    orders/positions/cash）造执行台账页确定性状态（delta add-broker-console
    T1.2）；库路径于调用期读 EXECUTION_DB_PATH（E2E 指向独立测试库）。
    顶层可选 regime（list：逐行按 ``regime_daily`` DDL 列名造数（含
    ``cap_source``，缺省字段如实落 NULL），经 upsert_regime_daily 写入）与
    regime_reset（bool：清 ``regime_daily``）造选股页市场温度计三态/空态
    （delta add-crowding-regime-positioning T8）；库路径于调用期读
    REGIME_DB_PATH（E2E 指向独立测试库 data/test-e2e-regime.db，绝不落到
    开发库 data/regime.db）。
    顶层可选 events（dict：``forecasts``/``triage``/``watchlist``/``journal``
    逐条经既有落库入口写入：预告 upsert（幂等）/分诊/清单（去重）/记账）与
    events_reset（bool：清事件线四表）造选股页事件观察区块三态/空态
    （delta add-event-driven-lane）；库路径于调用期读 EVENTS_DB_PATH
    （E2E 指向独立测试库 data/test-e2e-events.db，绝不落到开发库 data/events.db）。
    顶层可选 ops（dict：``now`` 冻结 ops 状态时钟（ISO 时刻；null 解除冻结，
    add-ops-missed-today-badge）、``runs`` 造 job_runs 行（job_id/kind/status/
    source/summary/error + started_at/finished_at 回拨））与 ops_runs_reset
    （bool：清 job_runs 全表）造日批运维页漏跑/失败/未到点确定性状态；库路径
    于调用期读 SESSIONS_DB_PATH（ops model env 口径，E2E 指向独立测试库）。
    顶层 research_reset（bool：清研报直投面三表 report_documents/
    report_extractions/collection_runs，issue #519）：research-ingest.spec.ts
    的「空态（隔离库首启）」断言在第二次本地重跑时因上传行持久化必失败——
    spec beforeEach 经本动词复位；库路径于调用期读 RESEARCH_REPORTS_DB_PATH
    （E2E 指向独立测试库 data/test-e2e-research-reports.db）。
    """
    # 旧版 smoke 断言（{symbol}）保持占位响应，避免破坏既有契约；
    # track_record / snapshot / screening / memory / factors / broker / regime
    # 造数块不受 chat_history 前置约束
    if (
        "chat_history" not in req
        and not isinstance(req.get("track_record"), dict)
        and not isinstance(req.get("snapshot"), dict)
        and not req.get("snapshot_reset")
        and not isinstance(req.get("screening"), dict)
        and not req.get("screening_reset")
        and not req.get("track_record_reset")
        and not isinstance(req.get("memory"), dict)
        and not req.get("memory_reset")
        and not isinstance(req.get("factors"), dict)
        and not req.get("factors_reset")
        and not isinstance(req.get("broker"), dict)
        and not req.get("broker_reset")
        and not isinstance(req.get("regime"), list)
        and not req.get("regime_reset")
        and not isinstance(req.get("events"), dict)
        and not req.get("events_reset")
        and not isinstance(req.get("daily_brief"), dict)
        and not isinstance(req.get("ops"), dict)
        and not req.get("ops_runs_reset")
        and not req.get("research_reset")
    ):
        return {"status": "ok", "mode": "testing"}
    # add-paper-broker：战绩造数复位（清 equity_curve + paper_equity 两表）。双曲线
    # E2E 每例前后 reset，避免种子残留破坏同套件其它 spec 的空态前提（隔离纪律与
    # screening_reset 同款）。库路径于调用期读 SESSIONS_DB_PATH / EXECUTION_DB_PATH。
    if req.get("track_record_reset"):
        from finance_agent.execution.store import delete_all_paper_equity
        from finance_agent.outcome.track_record.model import delete_all_track_record_state

        # 全量复位（2026-10-13 E2E 修复）：此前只清 equity_curve + paper_equity，
        # 残留的 predictions/daily_marks 令「库内只有自己造的行」类断言误红；
        # 现清五张造数面表 + paper_equity，与 CI 全新起库等价。
        delete_all_track_record_state()
        delete_all_paper_equity()
    # add-index-performance-compare:战绩页 E2E 造数(equity_curve/index_closes
    # 写入真实存储层;SESSIONS_DB_PATH 已被 e2e webServer 指向独立测试库)。
    # seed 是纯造数:track_record-only 请求到此为止,不创建会话,避免 E2E 会话
    # 侧栏被无 chat_history 的空会话污染（review fix）。
    track_seed = req.get("track_record")
    if isinstance(track_seed, dict):
        from finance_agent.outcome.track_record.model import (
            init_track_record_tables,
            upsert_equity_point,
            upsert_index_closes,
            upsert_metrics_daily,
        )

        init_track_record_tables()
        for pt in track_seed.get("equity_curve", []):
            upsert_equity_point(
                pt["curve_date"],
                pt["agent_nav"],
                benchmark_nav=pt.get("benchmark_nav"),
            )
        upsert_index_closes(
            [
                (r["index_code"], r["trade_date"], r["close"])
                for r in track_seed.get("index_closes", [])
            ]
        )
        # add-portfolio-beta-alpha:指标快照造数(当日 upsert,缺省列自动置空;
        # 只写 agent_metrics_daily,不触碰 equity_curve/index_closes)
        metrics_snapshot = track_seed.get("metrics_snapshot")
        if isinstance(metrics_snapshot, dict):
            upsert_metrics_daily(_track_as_of(), metrics_snapshot)
        # predictions 造数（add-prediction-pool-integrity）：duplicate 徽标 E2E 用。
        # open 行原样插入；非 open 状态经 update_prediction_status 置终态，与生产
        # dedup/判定同一条状态变更路径（产生审计行）。逐行落 predictions 表，
        # 库路径与上方造数同源（模块默认,随 SESSIONS_DB_PATH 指向测试库）。
        seed_predictions = track_seed.get("predictions")
        if isinstance(seed_predictions, list):
            from finance_agent.outcome.track_record.model import (
                insert_daily_mark as _seed_insert_mark,
            )
            from finance_agent.outcome.track_record.model import (
                insert_prediction as _seed_insert_prediction,
            )
            from finance_agent.outcome.track_record.model import (
                update_prediction_status as _seed_update_status,
            )

            for row in seed_predictions:
                pid = _seed_insert_prediction(
                    {
                        "source_type": "live",
                        "symbol": row["symbol"],
                        "symbol_name": row.get("symbol_name", row["symbol"]),
                        "direction": row.get("direction", "neutral"),
                        "entry_price": row.get("entry_price", 10.0),
                        "target_price": row.get("target_price"),
                        "horizon_days": row.get("horizon_days", 20),
                        "confidence": row.get("confidence", 0.6),
                        # 快照透传（add-price-polarity-contract，#606）：极性标注
                        # E2E 需造带 declared_prices.polarity 的行；缺省维持 {}
                        "rationale_snapshot": row.get("rationale_snapshot", {}),
                        "langfuse_trace_id": None,
                        "created_at": row["created_at"],
                        "resolution_rule": None,
                    }
                )
                if row.get("status") and row["status"] != "open":
                    resolved: dict[str, Any] = {
                        "status": row["status"],
                        "resolution_rule": row.get("resolution_rule", row["status"]),
                    }
                    if row.get("resolved_at"):
                        resolved["resolved_at"] = row["resolved_at"]
                    # update-track-record-settle-price-display：结算字段透传
                    # （走 update_prediction_status，与生产判定写入同一路径），
                    # 供结算口径展示 E2E 造「同口径可比」的已结算行
                    for _fld in (
                        "settle_entry_price",
                        "exit_price",
                        "raw_return",
                        "excess_return",
                        # update-outcome-band-volatility-scaled：判定所用中性带
                        "neutral_band_used",
                    ):
                        if row.get(_fld) is not None:
                            resolved[_fld] = row[_fld]
                    _seed_update_status(pid, resolved)
                # update-track-record-display-clarity:行内 marks 子数组——浮动收益
                # E2E 造数（prediction_id 服务端生成，顶层数组无法引用，按行携带）
                for mk in row.get("marks") or []:
                    _seed_insert_mark(
                        pid,
                        mk["mark_date"],
                        mk.get("mark_price"),
                        mk.get("cum_return"),
                        mk.get("cum_excess"),
                        mk.get("benchmark_price"),
                    )
        # add-paper-broker：paper 净值造数（战绩页双曲线 E2E）——写 execution 库
        # （库路径调用期读 EXECUTION_DB_PATH，见 execution/store.py 的 env 口径）
        seed_paper = track_seed.get("paper_equity")
        if isinstance(seed_paper, list):
            from finance_agent.execution.store import upsert_paper_equity

            for row in seed_paper:
                upsert_paper_equity(
                    row["trade_date"],
                    row["cash"],
                    row["positions_value"],
                    row["equity"],
                )
    # add-market-snapshot（D6/T8）：行情快照告警造态——可选先 reset（清三表）再写
    # meta。两处都在**调用期**经 snapshot 模块 _default_db_path() 读
    # SNAPSHOT_DB_PATH（导入期冻结会让 E2E 的 env 注入失效）。
    if req.get("snapshot_reset"):
        from finance_agent.data.snapshot import reset_snapshot_db

        reset_snapshot_db()
    snapshot_seed = req.get("snapshot")
    if isinstance(snapshot_seed, dict) and snapshot_seed:
        from zoneinfo import ZoneInfo

        from finance_agent.data.snapshot import set_meta_many

        snapshot_meta = {str(key): str(value) for key, value in snapshot_seed.items()}
        # D5：fresh-DB 分支（无 last_attempt_at）不告警——造 stale 态必须补上
        snapshot_meta.setdefault(
            "last_attempt_at", datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        )
        set_meta_many(snapshot_meta)
    # add-screening-pipeline S6：选股页造数——可选先 reset（清五表 + 丢弃进程内
    # 队列单例）再写批次/池/队列。库路径调用期读 SCREENING_DB_PATH（store 各入口
    # env 口径）；队列为进程内单例（徽标态造数，不启动轮询/补位）。
    if req.get("screening_reset"):
        from finance_agent.screening.store import reset_screening_db

        reset_screening_db()
        # 队列状态在内存（库外）：清库不换实例会让旧徽标条目跨 reset 残留
        _reset_analyze_queue()
    screening_seed = req.get("screening")
    if isinstance(screening_seed, dict):
        from finance_agent.screening.store import record_screening_batch as _seed_batch

        # add-daily-ops-page T1.2：``batches`` = 历史多批次（每行可自带 ``pool``，
        # pool_size 随该行的池行数）；先落历史、后落单数 ``batch``——同日撞键时
        # 单数（当前批次）胜出，既有 payload 语义零漂移。
        batches_seed = screening_seed.get("batches")
        if isinstance(batches_seed, list):
            for history_row in batches_seed:
                trade_date = history_row["trade_date"]
                _seed_batch(
                    trade_date,
                    status=history_row.get("status", "ok"),
                    error=history_row.get("error"),
                    started_at=history_row.get("started_at", _now()),
                    finished_at=history_row.get("finished_at", _now()),
                    l1_passed=history_row.get("l1_passed", 0),
                    l2_top=history_row.get("l2_top", 0),
                    l3_keep=history_row.get("l3_keep", 0),
                    pool_rows=history_row.get("pool") or [],
                    selection_mode=history_row.get("selection_mode", "factor"),
                    # add-microcap-pool-ui：档位/门禁/约束透传。既有 ``pool`` 键是池行列表
                    # （历史语义不动），档位走独立 ``tier`` 键（缺省 main，缺省路径零漂移）
                    pool=history_row.get("tier", "main"),
                    frontier_gate=history_row.get("frontier_gate"),
                    exec_constraints=history_row.get("exec_constraints"),
                )
        batch_seed = screening_seed.get("batch")
        if isinstance(batch_seed, dict):
            trade_date = batch_seed["trade_date"]
            _seed_batch(
                trade_date,
                status=batch_seed.get("status", "ok"),
                error=batch_seed.get("error"),
                started_at=batch_seed.get("started_at", _now()),
                finished_at=batch_seed.get("finished_at", _now()),
                l1_passed=batch_seed.get("l1_passed", 0),
                l2_top=batch_seed.get("l2_top", 0),
                l3_keep=batch_seed.get("l3_keep", 0),
                pool_rows=screening_seed.get("pool") or [],
                selection_mode=batch_seed.get("selection_mode", "factor"),
                pool=batch_seed.get("tier", "main"),
                frontier_gate=batch_seed.get("frontier_gate"),
                exec_constraints=batch_seed.get("exec_constraints"),
            )
        queue_seed = screening_seed.get("queue")
        if isinstance(queue_seed, list):
            _get_analyze_queue().seed(queue_seed)
    # 每日汇报造数（add-auto-pool-analysis-daily-brief）：E2E/前端测试用；
    # 同日期 upsert，不产生重复行。
    daily_brief_seed = req.get("daily_brief")
    if isinstance(daily_brief_seed, dict):
        from finance_agent.outcome.daily_brief import upsert_daily_brief as _seed_brief

        _seed_brief(
            str(daily_brief_seed.get("date") or datetime.now().strftime("%Y-%m-%d")),
            str(daily_brief_seed.get("status") or "ok"),
            str(daily_brief_seed.get("markdown") or ""),
            model=daily_brief_seed.get("model"),
            prompt_version=daily_brief_seed.get("prompt_version"),
            error=daily_brief_seed.get("error"),
        )
    # ops 造数（delta add-ops-missed-today-badge，issue #453）：可选先 reset（清
    # job_runs 全表）再写运行行——逐条经既有落库入口 insert_job_run（生产同源），
    # started_at/finished_at 经 set_job_run_timestamps 回拨（单测 _backdate 同语义）。
    # 另可 ``now`` 冻结 ops 状态时钟（ops_api._now_override；null 解除冻结）——
    # 真实时钟下全部日批排程均在 15:35 后，E2E 无法自然构造「已过点未跑」态。
    # 库路径调用期读 SESSIONS_DB_PATH（ops model env 口径，E2E 指向独立测试库）。
    if req.get("ops_runs_reset"):
        from finance_agent.outcome.ops.model import clear_job_runs as _seed_clear_runs

        _seed_clear_runs()
    # 研报直投复位（issue #519）：清直投面三表，research-ingest.spec.ts 的
    # 「空态（隔离库首启）」断言在第二次本地重跑也成立。库路径调用期读
    # RESEARCH_REPORTS_DB_PATH（store env 口径，E2E 指向独立测试库）。
    if req.get("research_reset"):
        from finance_agent.data.research_reports_store import (
            delete_all_ingest_state as _seed_clear_research,
        )

        _seed_clear_research()
    ops_seed = req.get("ops")
    if isinstance(ops_seed, dict):
        from finance_agent import ops_api as _seed_ops_api
        from finance_agent.outcome.ops.model import (
            insert_job_run as _seed_insert_run,
        )
        from finance_agent.outcome.ops.model import (
            set_job_run_timestamps as _seed_run_timestamps,
        )

        if "now" in ops_seed:
            raw_now = ops_seed.get("now")
            _seed_ops_api._now_override = datetime.fromisoformat(str(raw_now)) if raw_now else None
        for run_row in ops_seed.get("runs") or []:
            run_id = _seed_insert_run(
                str(run_row["job_id"]),
                str(run_row.get("kind", "scheduled")),
                str(run_row.get("status", "ok")),
                source=str(run_row.get("source", "scheduled")),
                summary=run_row.get("summary"),
                error=run_row.get("error"),
            )
            if run_row.get("started_at") or run_row.get("finished_at"):
                _seed_run_timestamps(
                    run_id,
                    started_at=run_row.get("started_at"),
                    finished_at=run_row.get("finished_at"),
                )
    # 语义记忆造数（delta add-memory-confirmation T1）：可选先 reset（清条款表）再写
    # 条款——逐条经既有落库（去重口径同生产：同文本已存在则跳过）后再按 status 走
    # 既有状态机置态，与真实确认/驳回路径同源。库路径调用期读 SESSIONS_DB_PATH。
    if req.get("memory_reset"):
        from finance_agent.memory.semantic import reset_clauses

        reset_clauses()
    memory_seed = req.get("memory")
    if isinstance(memory_seed, dict):
        from finance_agent.memory.semantic import (
            confirm_clause as _seed_confirm_clause,
        )
        from finance_agent.memory.semantic import (
            reject_clause as _seed_reject_clause,
        )
        from finance_agent.memory.semantic import (
            store_candidate_clauses as _seed_store_clauses,
        )

        for clause_row in memory_seed.get("clauses") or []:
            new_ids = _seed_store_clauses(
                [str(clause_row["text"])],
                source=clause_row.get("source", "monthly_batch"),
                # evidence 原样落库（生产为 JSON 文本）：供记忆页结算样本展示造数
                evidence=clause_row.get("evidence"),
            )
            # 同文本已存在（去重口径）→ 不重复入库，seed 保持幂等
            if not new_ids:
                continue
            if clause_row.get("status") == "confirmed":
                _seed_confirm_clause(new_ids[0])
            elif clause_row.get("status") == "rejected":
                _seed_reject_clause(new_ids[0])
    # 因子库造数（delta add-factor-library-page T1.2）：可选先 reset（清三表）再
    # 写因子——逐条经既有落库 + 状态机走到目标态（candidate→active→degraded/
    # retired，与生产准入/退役路径同源），IC 经 record_ic 幂等写入。库路径调用期
    # 读 FACTOR_LIBRARY_DB_PATH（library 模块 env 口径）。
    if req.get("factors_reset"):
        from finance_agent.factors.library import reset_factor_library

        reset_factor_library()
    factors_seed = req.get("factors")
    if isinstance(factors_seed, dict):
        from finance_agent.factors.library import add_factor as _seed_add_factor
        from finance_agent.factors.library import record_ic as _seed_record_ic
        from finance_agent.factors.library import (
            set_pool_increment as _seed_set_pool_increment,
        )
        from finance_agent.factors.library import set_status as _seed_set_status

        for factor_row in factors_seed.get("factors") or []:
            target = str(factor_row.get("status", "candidate"))
            if target not in FACTOR_STATES:
                # fail loud：静默回落会让 E2E 断言对着一条状态不符预期的行变红难归因
                raise ValueError(f"非法种子状态 {target!r}；允许: {list(FACTOR_STATES)}")
            factor_id = _seed_add_factor(
                expression=str(factor_row["expression"]),
                hypothesis=str(factor_row.get("hypothesis", "")),
                category=str(factor_row.get("category", "seed")),
                status="candidate",
                ast_json=factor_row.get("ast_json"),
                source=str(factor_row.get("source", "proposal")),
                alignment_score=factor_row.get("alignment_score"),
            )
            if factor_row.get("pool_increment") is not None:
                _seed_set_pool_increment(factor_id, float(factor_row["pool_increment"]))
            if target != "candidate":
                # active 的闸门结论与 validate.admit_candidates 逐字同源（ADMITTED）
                _seed_set_status(factor_id, "active", detail={"reason": "ADMITTED"})
            if target == "retired" and factor_row.get("via_degraded"):
                # 衰减后退役（真实路径 degraded → retired）：先落 DECAY 降级事件
                # （detail 缺省 {"reason": "DECAY"}）再退役——供「衰减标记」列造态
                _seed_set_status(
                    factor_id,
                    "degraded",
                    detail=factor_row.get("degraded_detail") or {"reason": "DECAY"},
                )
            if target in ("degraded", "retired"):
                # detail 由 seed 行给出（缺省 None——不臆造原因）
                _seed_set_status(factor_id, target, detail=factor_row.get("detail"))
            for trade_date, rank_ic in factor_row.get("ic") or []:
                _seed_record_ic(factor_id, str(trade_date), float(rank_ic))
    # 执行台账造数（delta add-broker-console T1.2）：可选先 reset（清 orders/
    # positions/cash 三表）再写 cash/positions/orders——逐条经既有落库入口
    # （ensure_account/write_cash/write_position/insert_order），与 paper 生产写
    # 路径同源；库路径调用期读 EXECUTION_DB_PATH（E2E 指向独立测试库
    # data/test-e2e-execution.db，绝不落到开发库 data/execution.db）。
    if req.get("broker_reset"):
        from finance_agent.execution.store import delete_all_broker_state

        delete_all_broker_state()
    broker_seed = req.get("broker")
    if isinstance(broker_seed, dict):
        _seed_broker_account(broker_seed)
    # 市场温度计造数（delta add-crowding-regime-positioning T8）：可选先 reset
    # （清 regime_daily 全表）再写行——行字段与 regime_daily DDL 列名逐字一致
    # （含 cap_source），经 upsert_regime_daily（生产同源写入口；trade_date 唯一
    # → 同日覆盖）落库；缺省字段如实落 NULL（不编造）。库路径调用期读
    # REGIME_DB_PATH（model._default_db_path env 口径，E2E 指向独立测试库
    # data/test-e2e-regime.db，绝不落到开发库 data/regime.db）。
    if req.get("regime_reset"):
        from finance_agent.outcome.regime.model import reset_regime_db

        reset_regime_db()
    regime_seed = req.get("regime")
    if isinstance(regime_seed, list):
        from finance_agent.outcome.regime.indicators import (
            INDICATOR_KEYS as _REGIME_INDICATOR_KEYS,
        )
        from finance_agent.outcome.regime.model import (
            PCTILE_COLUMNS as _REGIME_PCTILE_COLUMNS,
        )
        from finance_agent.outcome.regime.model import (
            upsert_regime_daily as _seed_regime_daily,
        )

        for regime_row in regime_seed:
            _seed_regime_daily(
                str(regime_row["trade_date"]),
                {key: regime_row.get(key) for key in _REGIME_INDICATOR_KEYS},
                {key: regime_row.get(key) for key in _REGIME_PCTILE_COLUMNS},
                computed_at=str(regime_row.get("computed_at") or _now()),
                cap_source=regime_row.get("cap_source"),
            )
    # delta add-event-driven-lane：事件线造数（四表经既有落库入口写入：预告
    # upsert 幂等、分诊覆盖、清单去重、记账唯一）。库路径调用期读 EVENTS_DB_PATH
    # （model._default_db_path env 口径，E2E 指向独立测试库 data/test-e2e-events.db，
    # 绝不落到开发库 data/events.db）。events_reset 先清四表，供同套件多 spec 复位。
    if req.get("events_reset"):
        from finance_agent.outcome.events.model import delete_all_events_state

        delete_all_events_state()
    events_seed = req.get("events")
    if isinstance(events_seed, dict):
        from finance_agent.outcome.events.model import (
            insert_journal as _seed_event_journal,
        )
        from finance_agent.outcome.events.model import (
            insert_watchlist as _seed_event_watchlist,
        )
        from finance_agent.outcome.events.model import (
            record_triage as _seed_event_triage,
        )
        from finance_agent.outcome.events.model import (
            settle_journal as _seed_event_settle,
        )
        from finance_agent.outcome.events.model import (
            upsert_forecasts as _seed_forecasts,
        )

        forecasts = events_seed.get("forecasts") or []
        if forecasts:
            _seed_forecasts([dict(row) for row in forecasts])
        for triage_row in events_seed.get("triage") or []:
            _seed_event_triage(
                str(triage_row["forecast_key"]),
                verdict=str(triage_row["verdict"]),
                reason=str(triage_row.get("reason") or ""),
                evidence=triage_row.get("evidence") or [],
                model=triage_row.get("model"),
                prompt_fingerprint=triage_row.get("prompt_fingerprint"),
                triaged_at=triage_row.get("triaged_at"),
            )
        for item in events_seed.get("watchlist") or []:
            _seed_event_watchlist(
                str(item["forecast_key"]),
                source=str(item.get("source") or "earnings"),
                status=str(item.get("status") or "pending"),
                session_id=item.get("session_id"),
                created_at=item.get("created_at"),
            )
        for row in events_seed.get("journal") or []:
            row_id = _seed_event_journal(
                forecast_key=str(row["forecast_key"]),
                source=str(row.get("source") or "earnings"),
                symbol=str(row["symbol"]),
                entry_date=str(row["entry_date"]),
                entry_price=float(row["entry_price"]),
                horizon_days=int(row.get("horizon_days") or 5),
                note=row.get("note"),
            )
            if str(row.get("status") or "open") == "settled":
                _seed_event_settle(
                    row_id,
                    settle_date=str(row["settle_date"]),
                    settle_price=float(row["settle_price"]),
                    raw_return=float(row["raw_return"]),
                    note=row.get("note"),
                )
    # track_record-only（含 {} 空 dict）造数后即返回占位响应，不落会话
    if "chat_history" not in req:
        return {"status": "ok", "mode": "testing"}
    session_id = create_session(
        session_type=req.get("session_type", "chat"),
        display_name=req.get("display_name"),
        report_markdown=req.get("report_markdown", ""),
        status=req.get("status", "completed"),
    )
    for entry in req.get("chat_history", []):
        append_chat(
            session_id,
            role=entry["role"],
            content=entry["content"],
            thinking=entry.get("thinking"),
            tool_calls=entry.get("tool_calls"),
            agent_timeline=entry.get("agentTimeline"),
        )
    # 顶层管线字段（persist-full-session-timeline）：E2E 造「已完成管线会话」需要
    # pipeline_snapshot（走 completed 分支恢复分层时间轴）与 pipeline_timelines（恢复节点时序）
    pipeline_timelines = req.get("pipeline_timelines")
    if isinstance(pipeline_timelines, dict):
        update_pipeline_timelines(session_id, pipeline_timelines)
    pipeline_snapshot = req.get("pipeline_snapshot")
    if isinstance(pipeline_snapshot, dict):
        update_pipeline_snapshot(session_id, pipeline_snapshot)
    # 用量造数（add-composer-stat-pills）：E2E 需要可断言的 token/上下文峰值，
    # 而 TESTING stub 不产生 usage。字段全部可选，缺省不写（保持 0）。
    usage = req.get("usage")
    if isinstance(usage, dict):
        update_session_usage(
            session_id,
            prompt=int(usage.get("prompt", 0) or 0),
            completion=int(usage.get("completion", 0) or 0),
            total=int(usage.get("total", 0) or 0),
            context_peak=int(usage.get("context_peak", 0) or 0),
        )
    return {"session_id": session_id}
