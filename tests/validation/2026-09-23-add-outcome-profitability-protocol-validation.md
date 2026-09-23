# 人工验证报告: add-outcome-profitability-protocol

**日期**: 2026-09-23
**验证人**: agent（机器项）+ owner（口径确认项）
**关联 delta**: openspec/changes/add-outcome-profitability-protocol/
**E2E 门禁**: 不适用（非交互类变更，零前端/SSE/状态流转改动）

## 范围说明（读表前必读）

本 delta 是**口径先行**协议：只登记口径与收口纪律，不产读数、不改结算行为、零生产代码变更。因此三条需求中「双腿互证」的部分 Scenario 其**运行期强制点**落在已声明的后续 delta（`proposal.md` L34 依赖顺序：本 delta → `update-decision-settlement-contract`（Δ2）→ `add-forward-paper-trading-cohort` ∥ `add-backtest-leakage-controls`（Δ3/Δ4 读数腿））。

下表每行都给出**本 delta 内的落点**（口径条目 / 门禁字段 / 代码函数或测试）与证据；凡运行期实现属后续 delta 者，在该行「通过」列显式标注，不作「已验证运行期行为」的过度声明。行号为撰写时（2026-09-23）快照。

## Scenario 对照表

### 需求①「Outcome 收益指标口径与预登记」（4 Scenario）

| # | Scenario | 落点 | 证据（测试名 / 命令） | 通过 |
|---|---|---|---|---|
| 1 | 首个正式批收口时口径与预登记齐备 | `docs/evals/metrics.md` §1.8 全表①–⑦（L94–108）+ §2 切点行（L132）；预登记 `evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md` §0 门禁字段 7 项（L8–14） | `test_caliber_doc.py::test_section_1_8_registered_with_core_caliber`（§1.8 在 §2 之前 + 13 个口径锚点落在小节内）；`::test_timeline_has_outcome_switchpoint`（切点行钉死 delta 名 + 标「未跑批」）；`test_preregister.py::TestOutcomeGate::test_real_outcome_preregister_document_valid`（真实文档 `valid=True`，字段集合 == `OUTCOME_REQUIRED_FIELDS`） | ✅ 前置齐备（正式批未开跑；本 delta 不产读数） |
| 2 | 样本不足不报胜率 | `metrics.md` §1.8④（L103 红线）；`evals/outcome/caliber.py::MIN_SETTLED_FOR_WINRATE=10`（L13）；`evals/outcome/conclusion.py::conclude_outcome` 前置校验（L37–41 抛 `ValueError`） | `test_caliber.py::test_caliber_values_pinned`；`test_conclusion.py::TestConclude::test_below_red_line_raises`（`n=3` → `match="红线"`） | ✅ |
| 3 | 无预登记批次只能标通路验证 | `evals/causal_ablation/preregister.py::OUTCOME_REQUIRED_FIELDS`（L19–27 门禁字段）；`metrics.md` §1.8⑥（L105「深历史批次永久定位通路验证」）；预登记 §5（L56 深历史永久通路验证） | `test_preregister.py::TestOutcomeGate::test_outcome_missing_leakage_field_reported`（删「泄漏控制」行 → `valid=False` 且 issues 指名）；`::test_assert_preregistered_passes_required_fields_through`（不传 `required_fields` → `MissingPreregistrationError`）；`TestOutcomeLookup::test_name_contains_isolates_outcome_docs` | ✅ 门禁解析/断言就位；读数腿接线属 Δ3/Δ4 |
| 4 | 回避指标不进主结论 | `metrics.md` §1.8②（L101「neutral … SHALL NOT 进主结论」）；预登记 §1「辅助人口」行（L24「独立字段、不进主结论」） | 文档断言：口径条目字面钉死 + 预登记门禁字段齐备（同上 `test_real_outcome_preregister_document_valid`）；运行期主结论段人口过滤属 Δ2（`avoidance_status` 独立字段，不混入 `resolved_*`） | ✅ 口径已钉死 |

### 需求②「Outcome 读数收口纪律」（4 Scenario）

| # | Scenario | 落点 | 证据（测试名 / 命令） | 通过 |
|---|---|---|---|---|
| 5 | 收口顺序完整性 | `metrics.md` §1.8⑤（L104 五步链）；预登记 §3 收口流程①–⑤（L38）；`evals/outcome/health.py::collect_outcome_health`（L30）、`conclusion.py::conclude_outcome`（L26）、`report.py::assert_outcome_report`（L10） | `test_health.py`（11 项：结算成功率/不可判定率/integrity/告警/过滤/CLI 退出码）、`test_conclusion.py`（9 项）、`test_report.py`（5 项）全绿 | ✅ |
| 6 | 结论句合法性校验 | `conclusion.py::assert_outcome_sentence_legal`（L62–70）+ `conclude_outcome` 三式（L43–59） | `test_conclusion.py::TestSentenceLegality::test_bare_no_support_illegal`、`::test_significant_with_ci_but_bare_no_support_illegal`、`::test_significant_without_ci_illegal`、`::test_inconclusive_without_mde_illegal`；`TestConclude::test_forms_are_legal_set` | ✅ |
| 7 | 低胜率先归因后处置 | `metrics.md` §1.8⑤（L104「异常行人工终裁（对照表落 `tests/validation/`）」）；`conclusion.py` 负向句强制带归因措辞（L49–54「处置前须先分桶归因」） | `test_conclusion.py::TestConclude::test_negative_when_ci_upper_below_zero`（`assert "归因" in c.sentence` + 句法合法） | ✅ 口径 + 句式机器强制；分桶归因执行在读数腿 |
| 8 | 异常行不静默消失 | `health.py`：`unresolvable` 独立计数与 `unresolvable_rate`（L66–69）、重复指纹告警（`health.py:102-105` 告警文本处）、`entry_price_missing`/`bookkeeping_completeness`（L70–75、L118）；`metrics.md` §1.8⑤ | `test_health.py::TestHealth::test_unresolvable_majority_fails_unresolvable`、`::test_warnings_for_trace_missing_and_duplicate_hash`、`::test_empty_db_no_rates_and_not_passed`（空库报 `None` 不报 0%）、`TestCli::test_cli_exit_one_when_failed` | ✅ |

### 需求③「Forward 与回测双腿互证」（3 Scenario）

| # | Scenario | 落点 | 证据（测试名 / 命令） | 通过 |
|---|---|---|---|---|
| 9 | 并列报告腿别标注 | `metrics.md` §1.8⑥（L105）；预登记 §2 双腿设计（L30–34「读数与 forward 并列报告、标注腿别」）+ §0 停止规则②（L12） | 预登记门禁字段非空解析（`test_real_outcome_preregister_document_valid`）；**运行期分列渲染属 Δ3/Δ4** | ✅ 口径落点齐备（运行期实现属后续 delta，proposal L34 已声明） |
| 10 | 分歧未归因不下结论 | 预登记 §0 停止规则②（L12「两腿分歧超阈未归因 → 结论挂起（标记「待归因」）」）；`metrics.md` §1.8⑥（L105「两腿分歧先归因」） | 「停止规则」门禁字段非空断言（同 `test_real_outcome_preregister_document_valid`）；**运行期门控属 Δ3/Δ4** | ✅ 口径落点齐备 |
| 11 | 回测腿强制泄漏披露 | `metrics.md` §1.8⑥（L105「探针披露（默认阈值 0.60）」）；`evals/outcome/caliber.py::LEAKAGE_PROBE_THRESHOLD=0.60`（L15）；预登记 §0「泄漏控制」（L14）+ §5 表（L53–56）+ 停止规则③（L12 超阈降级「上界证据」句式） | `test_caliber.py::test_caliber_values_pinned`（0.60 钉死）；`test_preregister.py::TestOutcomeGate::test_outcome_missing_leakage_field_reported`（缺泄漏控制字段即门禁失败）；**探针实现属 Δ4 `add-backtest-leakage-controls`** | ✅ 阈值与门禁就位 |

**合计 11 Scenario（4+4+3）：11 行落点齐备；需求①③的 4/9/10 行为文档落点 + 门禁解析证据，运行期强制属 Δ2/Δ3/Δ4（proposal 已声明依赖）。**

## 机器验证结果

命令与真实输出摘要（2026-09-23，分支 `feat/outcome-profitability-eval`）：

| 命令 | 输出摘要 | 判定 |
|---|---|---|
| `uv run pytest tests/evals/outcome tests/evals/causal_ablation -v` | `543 passed in 11.14s`（outcome 30 + causal_ablation 513） | ✅ 全绿 |
| `uv run pytest tests/evals/outcome -q` | `30 passed in 0.70s` | ✅ |
| `uv run ruff check evals/outcome evals/causal_ablation/preregister.py tests/evals/outcome` | `All checks passed!` | ✅ 零违例 |
| `uv run mypy evals/outcome` | `Success: no issues found in 5 source files` | ✅ 零错误 |
| `uv run pytest tests/evals/outcome/test_caliber_doc.py -v`（§1.8⑤ 措辞补强后复跑） | `3 passed in 0.07s` | ✅ 护栏未被措辞改动打破 |
| `openspec validate add-outcome-profitability-protocol --strict` | `Change 'add-outcome-profitability-protocol' is valid`（EXIT=0） | ✅ |
| `uv run pytest -q`（全量，含 @live） | `1 failed, 3186 passed, 2 skipped in 803.15s`——唯一失败 `tests/evals/test_hallucination_live.py::test_hallucination_live_report`（`@live` 标记，需真实 Langfuse/LLM 链路与本地 `data/sessions.db` 数据；CI 门禁以 `-m "not live"` 排除） | ⚠️ 非本 delta 引入、非本 delta 范围；本 delta 的机器证据为范围跑批 `543 passed`（与 CI 门禁同口径） |

outcome 侧用例分布（`--collect-only` 计数）：`test_caliber.py` 2 / `test_caliber_doc.py` 3 / `test_conclusion.py` 9 / `test_health.py` 11 / `test_report.py` 5。

### 文档一致性补强（Task 4 审查采纳，本次落地）

`metrics.md` §1.8⑤ 同格两处修订（口径措辞，零代码行为变更）：
1. 健康检查括注补阈值语义：`记账完整率` → `记账完整率——读数不设阈值`（与 `health.py` 实现一致：`checks` 仅含 `settlement_success` / `unresolvable` / `integrity` 三项判据，`bookkeeping_completeness` 只读不判）；
2. 同格补口径披露：「integrity 为全库扫描（不受 source_type/since 过滤），其余读数按过滤口径（`integrity_scope` 字段随报告披露）」——与 `health.py::collect_outcome_health` 的 `integrity_scope="whole-db"` 及 `test_health.py::TestHealth::test_all_resolved_passes` 的断言一致。

**终审修复轮追加（2026-09-23）**：
3. §1.8⑤ 第二项阈值由「行情缺失率」正名为「**不可判定率（unresolvable/settleable，与结算成功率互补、同一分母）≤0.10**」——代码跟随重命名：`market_missing_rate` → `unresolvable_rate`、`checks["market_missing"]` → `checks["unresolvable"]`、`MAX_MARKET_MISSING_RATE` → `MAX_UNRESOLVABLE_RATE`（`health.py`），测试同步改名 `test_unresolvable_majority_fails_unresolvable`；
4. §1.8① 补人口与推断锁死：均值人口 = long/short 全部已判定观点（三态，排除 unresolvable）、CI 按标的簇 bootstrap（B=10,000）、胜率描述性/结论句由均值 CI 驱动、`BENCHMARK_CODE` 被环境覆盖则本口径失效须重新预登记。

## Owner 决策点确认

- 主窗口 T+20：**默认接受**（「开始」指令）；如变更走 metrics.md §1 修订 + 新预登记版本
- 泄漏探针阈值 0.60 / 两腿预算：默认接受，Δ3/Δ4 实施前可再次裁决

（决策点来源：`proposal.md` L35；口径落点见上表 Scenario 11 与 `caliber.py:15`。）

## 异常记录

- 无。机器项（pytest 543 / ruff / mypy / openspec validate --strict）全部通过；未发现需修补的代码缺陷。
- 说明（非本 delta 异常）：全量 `uv run pytest` 的 1 例失败为 `tests/evals/test_hallucination_live.py::test_hallucination_live_report`（`@live` 标记，CI 以 `-m "not live"` 排除，依赖本地 `data/sessions.db` 数据）——非本 delta 引入，详见机器验证表末行。
- **遗留项收口记录（2026-09-23 复审查 A–C 项）**：预登记文档 L12（停止规则①）与 L38（§3 收口流程①）的旧词「行情缺失率」**已正名为「不可判定率」**，§1 红线措辞同步对齐 §1.8④ 完整表述；§2 切点行（L132）**已加同日实施期修订注记**（预数据口径澄清：未跑批、无任何读数、无数据依赖选择之前完成，预登记措辞对齐、门禁字段无实质变更），故**不触发新预登记版本 + 新切点行**。门禁解析未受影响（`停止规则` 字段只查非空，不查内容）。Delta 工件（`specs/evaluation/spec.md` L36/L58、`tasks.md` L16、`proposal.md` L16）同词已对齐，防误名 sync 进主规范库。
- 说明（非异常）：Scenario 9–11 的运行期强制点按 `proposal.md` L34 声明的依赖顺序落在 Δ3/Δ4，本 delta 只承担口径与门禁落点——该分工为计划设计，不是缺口。

## 结论

[x] 全部通过（可进 sync/archive 流程）

勾选语义：本 delta 的**验证项**全过（机器项零失败 + 11 Scenario 均有落点与证据 + owner 决策点已确认），非「立即 archive」。按 `tasks.md` 4.3 的裁定，sync/archive 待读数腿（Δ2 契约 + Δ3/Δ4 两腿）落地后统一执行——口径先行，不随本 delta 单独 archive。
