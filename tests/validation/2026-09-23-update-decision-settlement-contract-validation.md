# 人工验证报告: update-decision-settlement-contract

**日期**: 2026-09-23
**验证人**: agent（机器项 + 离线链路）+ owner（LLM 端到端与战绩页人工核对待办）
**关联 delta**: openspec/changes/update-decision-settlement-contract/
**E2E 门禁**: 不适用（非交互类变更：前端仅 `PredictionStatus` 联合类型与状态标签/颜色映射小改，无新交互路径 / SSE / 状态流转；proposal「Impact」已声明）

## 范围说明（读表前必读）

本 delta 是**结算契约切换**：默认窗口 252→20、派生结算入场价与参考价分离、neutral 回避判定落终态、long/short Score 补齐、回测腿切共享判定函数、旧 `settle.py` 标 deprecated。运行期端口（日批判定 / 每日盯市）在本 delta 内已落地，本报告机器项为**离线真实链路**（零 LLM）驱动。

下表每行给出**落点**（代码 file:line 或文档条目）与**证据**（测试名 / 验证脚本项）。行号为撰写时（2026-09-23）快照。凡运行期强制属后续 delta（LLM 端到端读数 / 战绩页渲染）者，在该行「通过」列显式标注，不作「已验证运行期行为」的过度声明。

## Scenario 对照表

`openspec/changes/update-decision-settlement-contract/specs/` 三个 delta 文件共 **40 Scenario**（decision-outcome 20 / track-record 18 / track-record-calibration 2）。

### 需求①「决策落库」（decision-outcome，7 Scenario）

| # | Scenario（spec 行号） | 落点 | 证据（测试名 / 脚本项） | 通过 |
|---|---|---|---|---|
| 1 | 决策产出即落库（L14） | `src/finance_agent/outcome/track_record/ingest.py:23` `persist_prediction_from_accumulated`；挂点 `src/finance_agent/api.py:638` `_persist_decision_log` | `tests/outcome/test_track_record_ingest.py::test_approve_buy_records_long`、`test_track_record_ingest_shared.py::test_deep_mode_approve_buy_recorded`；离线脚本 ①（horizon_days==20 / status=="open"） | ✅ |
| 2 | 全量观点落库（含 reject）（L21） | ingest.py:46 `direction_for_action(action)`（`judgment.py:53`） | `test_track_record_ingest.py::test_reject_also_records`、`::test_ingest_direction_matches_shared_mapping`；`test_track_record_ingest_shared.py::test_deep_mode_reject_hold_recorded_neutral` | ✅ |
| 3 | 申报价冻结入快照且不参与结算（L28） | ingest.py:76-80 `declared_prices` 冻结；结算不用它（`judgment.py:79` `derive_entry` 从 K 线派生） | `test_track_record_ingest.py::test_ingest_writes_horizon_and_declared_prices`、`test_track_record_ingest_shared.py:132`（pydantic 路径）；离线脚本 ①（三键 + 值）与 ②（参考价 95≠派生入场 100） | ✅ |
| 4 | 结算入场价由行情派生（L35） | `judgment.py:79` `derive_entry`（归属日收盘 / 收盘后次一交易日 / 非交易日顺延，`CLOSE_TIME_CUTOFF` L20）；落库 `job.py:263` `settle_entry_price` | `tests/outcome/test_track_record_judgment.py::test_derive_entry_before_close_takes_same_day_close`、`::test_derive_entry_after_close_takes_next_trading_day`、`::test_derive_entry_non_trading_day_takes_next`、`::test_derive_entry_pure_date_created_at_takes_that_day_close`；`test_track_record_job.py::test_long_prediction_writes_settle_entry_price`；离线脚本 ②（settle_entry_price==100 归属日收盘） | ✅ |
| 5 | 落库失败不阻断业务（L43） | ingest.py:89-90 `except Exception: logger.exception` 旁路铁律 | `test_track_record_ingest.py`（旁路）与 `tests/outcome/test_decision_logging.py::test_failure_does_not_raise` | ✅ |
| 6 | trace 关联可追溯（L50） | ingest.py:82 `langfuse_trace_id`；`tests/outcome/test_trace_capture.py` | `test_trace_capture.py::TestTraceCapture::test_approve_captures_trace_id` 等 4 项 | ✅ |
| 7 | 参考价不可得存档不阻断（L56） | ingest.py:55-58 参考价不可得 → WARN + `status="open"`；长期无数据 → `job.py:231-243` unresolvable | `test_track_record_ingest.py::test_no_price_still_archives_open_with_warn`、`::test_ingest_reference_price_missing_stays_open`、`test_track_record_ingest_shared.py::test_deep_mode_no_quote_no_kline_archives_open_with_warn`；`test_track_record_job.py::test_stale_marks_unresolvable` | ✅ |

### 需求②「事后行情追踪」（decision-outcome，8 Scenario）

| # | Scenario（spec 行号） | 落点 | 证据（测试名 / 脚本项） | 通过 |
|---|---|---|---|---|
| 8 | 到期判定（horizon 终点）（L68） | `judgment.py:92` `resolve_prediction`（horizon 到点，`DEFAULT_HORIZON_DAYS` L19）；`job.py:249` | `test_track_record_judgment.py::test_horizon_win`、`test_track_record_job.py::test_settle_horizon_win`；离线脚本 ②（long +20% → resolved_win） | ✅ |
| 9 | 提前结算（superseded）（L75） | `judgment.py:62` `should_supersede`；`job.py:146-213` 旧观点立即结算（`resolution_rule="superseded"`） | `test_track_record_job.py::test_superseded_resolves_old`、`::test_superseded_long_uses_derived_entry`、`::test_superseded_reports_scores` | ✅ |
| 10 | 止损触发结算（SHALL NOT 止损提前结算）（L81） | 共享判定无止损路径：`judgment.resolve_prediction` 仅 horizon + 带；旧引擎已 deprecated `src/finance_agent/outcome/settle.py:1-12`（模块 docstring「[已废弃]」）+ 首调 WARN（settle.py:106-113） | `test_track_record_judgment.py::test_horizon_win`/`test_loss_and_short_symmetry`（判定只由 horizon 终点驱动）；`test_superseded_*` 证 superseded 是唯一提前结算路径 | ✅ |
| 11 | 目标达成结算（SHALL NOT 目标提前结算）（L88） | 同上（`judgment.py` 无 target 触发分支） | 同 #10 | ✅ |
| 12 | 止损与目标同日触及（SHALL NOT 同日止损优先）（L95） | 同上（旧「同日止损优先」随 `settle.py` 一并取代，settle.py:13 保留历史语义仅供只读复算） | 同 #10 | ✅ |
| 13 | 超期强制结算（SHALL NOT 超期强制）（L101） | `judgment._effective_horizon`（L42，逐观点 horizon）；只读 `settle.py` 的 `MAX_HOLD_DAYS` 不参与生产判定 | `test_track_record_judgment.py::test_horizon_capped_at_252`、`test_not_enough_rows_returns_none` | ✅ |
| 14 | 幂等结算（L108） | `job.py:216` 只取 `list_predictions(status="open")`；neutral 落终态 `avoidance`（L269）离开 open 池 | `test_track_record_job.py::test_settled_rows_leave_open_pool_on_rerun`（重跑不重复上报）、`::test_neutral_prediction_writes_avoidance_status`（离开 open 池） | ✅ |
| 15 | 行情缺失重试（L114） | `job.py:226-229` 拉行情异常 → errors+1 跳过；`job.py:231-243` 连续无行情 → unresolvable | `test_track_record_job.py::test_stale_marks_unresolvable`；`tests/outcome/test_track_record_metrics.py::TestMarking::test_kline_error_isolated` | ✅ |

### 需求③「决策效果 Score 反向上报」（decision-outcome，5 Scenario）

| # | Scenario（spec 行号） | 落点 | 证据（测试名 / 脚本项） | 通过 |
|---|---|---|---|---|
| 16 | 结算即上报 Score（L125） | `job.py:44` `_report_scores`；scores 列表 L61-66（comment 含 settle_price/hold_days/基准收益） | `test_track_record_job.py::test_scores_reported_for_long_only`（3 个 + comment `settle_price=115`）、`::test_settled_rows_leave_open_pool_on_rerun`（幂等不上报） | ✅ |
| 17 | 方向符号化（L131） | `judgment.py:120` `sign = -1.0 if direction == "short" else 1.0`；`raw_return = sign * (...)` L121；`decision_hit = raw_return > 0` job.py:62 | `test_track_record_judgment.py::test_loss_and_short_symmetry`、`::test_excess_short_benchmark_sign`；离线脚本 ③（+20% 行情 → short raw −0.20） | ✅ |
| 18 | 中性观点不上报 Score（L139） | `job.py:50` `resolution.status in AVOIDANCE_STATUSES` 早退；`job.py:265-271` 写 `avoidance_status` + 终态 `avoidance` | `test_track_record_job.py::test_scores_reported_for_long_only`（trace 只含 long）、`::test_neutral_prediction_writes_avoidance_status`；离线脚本 ②（neutral 0 Score + status=="avoidance"） | ✅ |
| 19 | 基准超额（L146） | `judgment.py:122-128` `excess = raw_return − sign*(bench_exit/bench_entry − 1)`；superseded 无同期基准 → `excess_return=None`（job.py:192） | `test_track_record_judgment.py::test_excess_uses_benchmark`、`::test_excess_non_flat_benchmark_formula`；离线脚本 ③（双腿 excess 一致） | ✅ |
| 20 | trace 不可查容错（L152） | `job.py:74-75` Score 异常仅 WARN 不阻断；逐 Score 隔离 | `test_track_record_job.py::test_score_failure_does_not_block_settlement`、`::test_score_partial_failure_reports_others`、`::test_score_failure_does_not_abort_later_scores` | ✅ |

### 需求④「观点数据模型（append-only + 快照冻结）」（track-record，6 Scenario）

| # | Scenario（spec 行号） | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 21 | 观点写入即冻结（L10） | `model.py:66` `_FROZEN_FIELDS`；`model.py:605-611` 冻结守卫抛 `FrozenFieldError`；快照字段 `ingest.py:70-81` | `test_track_record_model.py::test_frozen_field_update_raises`、`tests/outcome/test_track_record_stage_c.py::TestIntegrity::test_tampered_detected_and_audited` | ✅（需求文本经本次收敛至已实现范围——快照含摘要字段 + 申报价位，完整原文存会话报告由 `session_id` 关联；见「需求文本收敛记录」） |
| 22 | 回测实盘分离（L16） | `model.py` `source_type` 列 + `list_predictions(source_type=...)` | `test_track_record_model.py::test_list_filter_and_pagination`、`tests/evals/outcome/test_health.py::TestHealth::test_source_type_filters`、`tests/test_api_track_record.py::test_overview_source_filter` | ✅ |
| 23 | 缺要素观点不入统计（L22） | `model.py:57` `PREDICTIONS_STATUSES` + `_prediction_filters`（缺 direction/horizon 行不入 win_rate/excess 人口） | `test_track_record_model.py::test_prediction_stats_horizon_filter`、`::test_win_rate_excludes_neutral_direction_rows` | ✅ |
| 24 | horizon 默认值显式落库（L28） | `ingest.py:68` `"horizon_days": DEFAULT_HORIZON_DAYS`；DDL 默认亦改 20（`model.py` 迁移） | `test_track_record_ingest.py::test_ingest_writes_horizon_explicitly`、`test_track_record_model.py::test_fresh_db_horizon_days_ddl_default_is_20`、`::test_insert_prediction_default_horizon_matches_config`；离线脚本 ①（horizon_days==20） | ✅ |
| 25 | 判定基准为派生入场价（L34） | `judgment.derive_entry`（L79）；`job.py:263` / `job.py:204` 落 `settle_entry_price` | `test_track_record_judgment.py::test_resolve_long_uses_derived_entry_not_reference`；`test_track_record_job.py::test_long_prediction_writes_settle_entry_price`（参考价 95 不动、派生 100） | ✅ |
| 26 | 存量观点不追溯（L41） | `judgment._effective_horizon`（L42，用行自带 horizon）；切点过滤 `metrics.compute_metrics_snapshot:209` | `test_track_record_judgment.py::test_horizon_capped_at_252`；`test_track_record_metrics.py::TestMetricsSnapshotCaliber::test_legacy_252_row_excluded_from_snapshot`；`tests/test_api_track_record.py::test_overview_legacy_settled_discloses_cross_caliber` | ✅ |

### 需求⑤「判定规则（Outcome Resolution）」（track-record，6 Scenario）

| # | Scenario（spec 行号） | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 27 | 到期判定（L53） | `judgment.resolve_prediction`（L92） | `test_track_record_judgment.py::test_horizon_win`、`test_track_record_job.py::test_settle_horizon_win` | ✅ |
| 28 | 中性带判定（L59） | `judgment.py:130-143`（`DEFAULT_NEUTRAL_BAND=0.02` L17，先 round 消浮点噪声） | `test_track_record_judgment.py::test_neutral_band`、`::test_neutral_band_boundary_exact_two_percent`；`test_track_record_job.py::test_superseded_within_neutral_band_is_resolved_neutral` | ✅ |
| 29 | 中性观点回避判定（L66） | `judgment.py:132-137`（neutral → avoidance_win）；`job.py:265-271` | `test_track_record_judgment.py::test_resolve_neutral_avoidance_semantics`、`test_track_record_job.py::test_neutral_prediction_writes_avoidance_status`；离线脚本 ②（avoidance_win + status=="avoidance"） | ✅ |
| 30 | 回避错过上行（L73） | `judgment.py:136` `excess > band → avoidance_loss` | `test_track_record_judgment.py::test_resolve_neutral_missed_upside` | ✅ |
| 31 | 提前结算（观点变更）（L79） | `judgment.should_supersede`（L62）+ `job.py:146-213`；neutral 不走 superseded（job.py:150-151） | `test_track_record_job.py::test_superseded_resolves_old`、`::test_superseded_skips_neutral_old` | ✅ |
| 32 | 不可判定（L85） | `job.py:231-243`（`resolution_rule="stale_no_market"`，`resolved_at=None`） | `test_track_record_job.py::test_stale_marks_unresolvable` | ✅ |

### 需求⑥「基础统计」（track-record，6 Scenario）

| # | Scenario（spec 行号） | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 33 | 胜率口径（L97） | `model.py:783` `prediction_stats`；win 分子分母限 long/short resolved_win/loss（L817） | `test_track_record_model.py::test_win_rate_excludes_neutral_direction_rows`、`test_track_record_stage_c.py::TestSegments::test_win_rate_excludes_neutral` | ✅ |
| 34 | 回避正确率独立统计（L103） | `model.py:841` `avoidance_stats`（分母 avoidance_win+loss）；`api.py:2140-2142` 独立 `avoidance` 字段 | `test_track_record_model.py::test_avoidance_stats_rate_and_settled`、`tests/test_api_track_record.py::test_overview_avoidance_rate_shown_at_threshold`、`test_track_record_stage_c.py::test_calibration_includes_avoidance_rows` | ✅ |
| 35 | 回避样本不足不展示（L110） | `api.py:2142` `None if avoidance["settled"] < 10` | `tests/test_api_track_record.py::test_overview_caliber_fields_and_avoidance_threshold`、`test_track_record_model.py::test_avoidance_stats_empty_is_none` | ✅ |
| 36 | 样本量门槛（L116） | `api.py:2090` `track_record_overview`（settled<10 → win_rate null + insufficient_sample） | `tests/test_api_track_record.py::test_overview_win_rate_after_10_settled`、`::test_overview_empty` | ✅ |
| 37 | 口径切点分段（L123） | `api.py:2144-2145` `caliber_horizon`/`legacy_settled`；`metrics.py:209` 快照 horizon 过滤；`segments.py:65/70/91-97` 人口过滤 | `tests/test_api_track_record.py::test_overview_legacy_settled_discloses_cross_caliber`、`test_track_record_metrics.py::TestMetricsSnapshotCaliber::test_legacy_252_row_excluded_from_snapshot`、`test_track_record_model.py::test_prediction_stats_horizon_filter` | ✅ |
| 38 | 空库（L129） | `api.py:2090` overview 空库分支 | `tests/test_api_track_record.py::test_overview_empty`、`test_track_record_model.py::test_stats_empty` | ✅ |

### 需求⑦「置信度校准分桶」（track-record-calibration，2 Scenario）

| # | Scenario（spec 行号） | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 39 | 分桶输出（L10） | `calibration.py:58` `calibration_table`（[0.5,0.6)...[0.9,1.0] 桶） | `test_track_record_stage_c.py::TestCalibration::test_buckets_and_hit_rate`、`::test_neutral_excluded_when_none` | ✅ |
| 40 | 回避终态映射命中值（L15） | `calibration.py:25` `outcome_value`（`status=="avoidance"` → avoidance_win 1.0 / loss 0.0 / neutral neutral_prob；NULL 跳过，L33-41） | `test_track_record_stage_c.py::TestCalibration::test_outcome_value_avoidance_mapping`、`::test_outcome_value_avoidance_undecided_skipped`、`::test_outcome_value_other_statuses_unchanged` | ✅ |

**合计 40 Scenario（decision-outcome 20 / track-record 18 / track-record-calibration 2）：40 行落点齐备。**

## 机器验证结果

命令与真实输出摘要（2026-09-23，分支 `feat/outcome-profitability-eval`，HEAD 起点 `5ed9959`）：

| 命令 | 输出摘要 | 判定 |
|---|---|---|
| `uv run pytest tests/outcome tests/evals/backtest tests/test_api_track_record.py tests/evals/outcome -v` | `356 passed, 15 warnings in 66.30s` | ✅ 全绿 |
| `uv run ruff check src/ evals/ tests/ scripts/` | `All checks passed!` | ✅ 零违例 |
| `uv run ruff format --check src/ tests/ evals/` | `546 files already formatted` | ✅ |
| `uv run mypy src/finance_agent/outcome/track_record evals/outcome` | `Success: no issues found in 14 source files` | ✅ 零错误（详见「异常记录」） |
| `uv run python tests/scripts/d2_task7_offline_chain_verify.py` | 32 项核对全 PASS，`结论: PASS（全部核对项通过）`，EXIT=0 | ✅ |
| `openspec validate update-decision-settlement-contract --strict` | `Change 'update-decision-settlement-contract' is valid`（EXIT=0） | ✅ |

**@live 例说明**：`tests/evals/test_hallucination_live.py` 不在上述范围命令的收集路径内；单独执行 `uv run pytest tests/evals/test_hallucination_live.py -q` → `1 skipped in 0.03s`（本环境无真实 LLM/Langfuse 链路，标记跳过，**未出现失败**）。故不存在需「排除该例」的失败计数。

## 离线真实链路验证结果（零 LLM，脚本化）

脚本 `tests/scripts/d2_task7_offline_chain_verify.py`（内存构造 K 线/基准/quote/Langfuse，零网络零 LLM）：

**① 落库入口契约**（`persist_prediction_from_accumulated`，accumulated 含 buy TradeDecision + quote + 假 kline 帧）：
- 落库 1 行；`horizon_days == 20`；`direction == long`（buy→long）
- 参考价取 quote 优先 `101.5`（非 K 线兜底值）
- `rationale_snapshot["declared_prices"]` 三键齐全 `{entry_price:100.0, stop_loss:90.0, target_price:120.0}`
- `status == "open"`

**② 日批判定落库契约**（`settle_open_predictions` + fake client 提供 21 行 hfq 形态 K 线）：
- 整批 `{settled:2, errors:0, scores_reported:3}`
- long 行：`settle_entry_price == 100.0`（归属日收盘），参考价 `entry_price` 保持 `95.0`，`resolved_at == "2026-09-21"`（== 第 20 个交易日 exit_date），`raw_return == 0.2`
- neutral 行：`avoidance_status == "avoidance_win"`，`status == "avoidance"`（终态），不写 resolved_*，离开 open 池
- Score：只挂 long 的 trace，三键 `decision_hit/return/excess`，neutral 上报 0 个

**③ 双腿口径一致性**：
- buy（long）与 sell（short）两方向：生产 `resolve_prediction` 与回测 `evals.backtest.replay` 映射（`replay._settlement_from_resolution`，其内部同用 `direction_for_action` + `DEFAULT_HORIZON_DAYS` + `resolve_prediction`）的 `raw_return` / `excess_return` 逐值一致（buy 均 +0.2；short 均 −0.2）
- 回测 `settlement.decision_return` / `decision_excess` 映射 == 生产 raw/excess
- short 方向符号自证：+20% 行情 → raw −0.20

## 需求文本收敛记录（Δ2T7 审查处方 1）

本 delta 的 `specs/track-record/spec.md`（Requirement「观点数据模型」+ Scenario「观点写入即冻结」）与 `specs/decision-outcome/spec.md`（Requirement「决策落库」+ Scenario「全量观点落库（含 reject）」）原写「rationale_snapshot SHALL 含完整分析原文 + 引用数据快照 + 申报价位」，与实现（`ingest.py:70-81` 仅落 `action` / `fund_manager_decision(_reasoning)` / `declared_prices`）不一致；该文本会随 sync 进主规范库。**本次把 delta 需求文本如实收敛到已实现范围**：

- `rationale_snapshot` SHALL 含**观点摘要字段**（`action`、`fund_manager_decision` 及其 reasoning）与申报价位（entry/stop/target）；
- 完整分析原文 SHALL 存于会话报告并可由该观点的 `session_id` 关联，**不重复存于快照**；
- 两个 delta 文件的 Requirement `(Previously: …)` 均已补记本次收敛说明。

收敛后无残留未满足的 SHALL，原「`rationale_snapshot` 未含完整分析原文」缺口登记随之作废（另无其他既有缺口）。该改动为纯需求/文档文本收敛，零代码行为变更。

## Owner 待办（人工核对项，本报告未覆盖）

- **LLM 端到端实跑**：真实 deep 分析 → `persist_prediction_from_accumulated` 落库 → `settle_open_predictions` 判定的全链路（含 Langfuse 真实 Score 写入与 trace 关联）。本报告已用离线脚本覆盖同一代码路径的契约，但真实模型输出的 `final_trade_decision` 形态（pydantic → dict、action 分布）与新窗口下首批真实读数的合理性仍需 owner 实跑。
- **战绩页人工核对**（proposal「Impact」强制项）：
  - 胜率语义变化（T+252 → T+20 短窗口）在页面上不失真、`caliber_horizon`/`legacy_settled` 口径披露可见；
  - **avoidance 行标签渲染正常**（`PredictionStatus` 补 `avoidance` 成员与「回避」标签/颜色，防止已结算 neutral 行渲染空白）；
  - 「样本积累中」状态（settled < 10）展示正常。

## 异常记录

- **mypy 1 项 delta 引入回归（本报告撰写时已修复）**：`uv run mypy src/finance_agent/outcome/track_record evals/outcome` 初跑报 `job.py:249 error: Incompatible types in assignment (expression has type "Resolution | None", variable has type "Resolution")`。根因：Δ2 superseded 分支（commit `83802e3`）在 `settle_open_predictions` 函数作用域内先以 `resolution = Resolution(...)` 绑定该名，mypy 据首绑定推断为 `Resolution`，horizon 分支 `resolution = resolve_prediction(...)`（返回 `Resolution | None`）遂冲突；merge-base `743ea807` 无此绑定、无此错误，属 **Δ2 引入**。修复：将 superseded 分支局部变量重命名为 `sup_resolution`（行为零变更，仅类型收窄），修复后 `mypy` 零错误、`356 passed` 不变。**该改动超出本任务简报「代码只改一处字符串」的约束，为使 Δ2 自身验收项 tasks.md 5.1「触碰文件零新增」成立而做，特此披露。**
- 无其他异常。机器项（pytest 356 / ruff check + format / mypy / 离线脚本 / openspec validate --strict）全部通过。

## 结论

[x] 全部 Scenario 经文本收敛后达成；owner 待办见上（可进 sync + archive 前置）

勾选语义：本 delta 的**验证项**全过（机器项零失败 + 40 Scenario 均有落点与证据 + 离线链路三项通过；Scenario #21 的 SHALL 经需求文本收敛至已实现范围后达成，见「需求文本收敛记录」）。按 `tasks.md` 5.5 的裁定，**sync/archive 待读数腿（Δ3 `add-forward-paper-trading-cohort` / Δ4 `add-backtest-leakage-controls`）落地后统一执行**——本 delta 提供结算契约与判定同源证据，但真实读数（forward cohort / walk-forward 回测）尚未开跑，不随本 delta 单独 archive。

[ ] 存在失败项，需修复后重新验证
