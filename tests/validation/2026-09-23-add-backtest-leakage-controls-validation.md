# 人工验证报告: add-backtest-leakage-controls

**日期**: 2026-09-23（离线跑批与台账登记于 2026-09-24 执行）
**验证人**: agent（机器项 + 离线通路端到端）+ owner（真实探针 LLM 预算 / 首个正式批窗口与样本量）
**关联 delta**: openspec/changes/add-backtest-leakage-controls/
**E2E 门禁**: 不适用（非交互类变更：评估侧回测准入与报告生命周期，零前端 / SSE / 状态流转改动）

## 范围说明（读表前必读）

本 delta 给回测腿补「读数可信性护栏」：复权口径统一（后复权）→ 结算同源核对 → 泄漏探针 → 干净窗口与批次准入 → 报告生命周期与索引。五条 requirement（3 MODIFIED + 2 ADDED）全部有落点。

**验证分层**（诚实声明）：

- **机器项**：范围跑批 `1119 passed, 2 deselected` + `tests/evals` 其余 `473 passed, 2 skipped`（两段合计覆盖整个 `tests/evals` 与 `tests/data` / `tests/outcome`，全部 exit 0）+ `tests/evals/backtest` 单跑 `186 passed` + ruff 零违例 + mypy 触碰文件零新增（含修掉 1 处本 delta 引入的类型错误）。
- **离线端到端（零网络零 LLM）**：脚本 `tests/scripts/d4_task5_pathway_offline.py` 以假 client（构造行情/新闻）+ 假 LLM（依**脚本侧独立实现**的真值作答）+ 假 replay，走完「探针三层题 → 批次准入 → 报告渲染 → status 头 → 台账索引」闭环，**52/52 项核对通过**（exit 0）。
- **真实 LLM 探针与正式批（成本真实发生）**：属 **owner 预算门控项**——正式批窗口与样本量由预登记 MDE 反算后 owner 批预算。故本报告**不声称**已完成真实探针跑批；`tasks.md` 中正式批相关待办转 owner。

## Scenario 对照表（15 Scenario）

### 需求①「历史离线回放」（MODIFIED，4 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 1 | 时点截断 | `evals/backtest/data_snapshot.py::truncate_state`（L125）/ `_truncate_kline`（L48）/ `_truncate_reports`（L53）/ `_truncate_macro`（L102）/ `disclosure_deadline`（L30） | `tests/evals/backtest/test_data_snapshot.py`（26 passed，含「财报按披露日判可得性」「无日期新闻条目剔除」）；pilot 试跑截断核查（`reports/backtest/truncation-check-002412.json`） | ✅ |
| 2 | 可审计快照 | `data_snapshot.py::build_snapshot`（L169）→ `metadata`：`excluded_fields`（L197）、`prompt_versions`（L198）、model | `test_data_snapshot.py::TestBuildSnapshotMetadata`（3 例：prompt 版本字典非空 / 无 env 记 `unspecified` / excluded_fields 覆盖纯当下数据） | ✅ |
| 3 | 结算同源 | `evals/backtest/replay.py` 导入 `finance_agent.outcome.track_record.judgment`（L21）并派生 `_settlement_from_resolution`（L85）；`settle.py` 已 deprecated（Δ2 Task 6） | `tests/evals/backtest/test_replay.py`；**代码面零残留**（复验命令与实测，按文件类型分开计数以免被文档自身文本干扰）：`grep -rn "from finance_agent.outcome.settle" --include=*.py evals/backtest/ tests/scripts/` → **0**（无活 import）；`grep -rn "evaluate_decision" --include=*.py evals/backtest/` → **0**（无代码调用）；`grep -rn "outcome\.settle" --include=*.py evals/backtest/` → **1**，`replay.py:68` 的 docstring（说明所对齐的历史契约）。**文档面**：`grep -rn "outcome\.settle" --include=*.md evals/backtest/results/` → **2**，均为 `results/pilot-2023-shock.md`（`:31` 原文 + `:95` 本轮追加的过时表述指认段），非代码引用 | ✅ |
| 4 | 干净窗口校验 | `report.py::assert_clean_window`（L78，交易日口径）；`report.py::build_conclusion`（L319）剥离 skill 句 | `tests/evals/backtest/test_clean_window.py`；离线 **[5]**（远端 as_of → 通过「最少 60 个交易日」；近端 → 不通过且理由含实测交易日数）、离线 **[10]**（formal + 窗口未过 → 「通路验证定位（干净窗口未过）」） | ✅ |

### 需求②「绩效指标与基线对比」（MODIFIED，3 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 5 | 四指标对比报告 | `evals/backtest/performance.py::perf_metrics`（CR/ARR/Sharpe/MDD）；`run_backtest` 聚合 `perf_table` + `best_baseline`；`report.py::_perf_table_lines`（L484） | `tests/evals/backtest/test_performance.py`、`test_run_backtest.py`；离线 **[6]**（md「## 4. 绩效表」段渲染 system + 四基线） | ✅ |
| 6 | 结算语义一致 | 结算复用 `judgment.resolve_prediction`（一字板递延/停牌顺延/方向符号化在 Δ2 已落）；回测不另造引擎 | `test_replay.py`；`replay.py` 顶部 docstring 声明同源 | ✅ |
| 7 | 复权口径核对 | `akshare_client.py`：`SETTLEMENT_ADJUST = "hfq"`（L39）、`fetch_kline(..., *, adjust: str = "qfq")`（L464，默认仍 qfq）、结算/回测路径显式传 hfq（L488/L497/L515）；`run_backtest` 取数用 `SETTLEMENT_ADJUST` | `tests/data/test_akshare_client.py`（+80 行：adjust 透传与默认值）；`tests/outcome/test_track_record_job.py`（+19 行：除权对照）；离线 **[6]**（`methodology.adjust` 含 hfq） | ✅ |

### 需求③「分层市场状态抽样」（MODIFIED，2 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 8 | 分层覆盖 | `evals/backtest/sampling.py::classify_regime`（L19）/ `stratified_sample`；`run_backtest` 输出 `perf_by_regime` | `tests/evals/backtest/test_sampling.py`；离线 **[6]**（样本标 regime、报告含 regime 段） | ✅ |
| 9 | 干净窗口下的覆盖限定 | `report.py::build_disclosures` 的 `regime_coverage`（L289）+ `build_conclusion` 的 `regime_limited` 限定句（L319） | `tests/evals/backtest/test_report_render.py`；离线 **[6]**（md「## 3. regime 覆盖」段含 covered/是否受限） | ✅ |

### 需求④「知识泄漏探针」（ADDED，3 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 10 | 探针执行与披露 | `leakage_probe.py::run_leakage_probe`（L271）：三层题模板（L63/L67/L72）、裸问（无 as-of 快照）、`_sample_tickers`（L124）确定性抽样、真值走 `SETTLEMENT_ADJUST`；`report.py::_probe_lines`（L411）披露段 | `tests/evals/backtest/test_leakage_probe.py`；离线 **[1]**（`state=downgraded / direction=1.0 / unknown=0.0 / probe_n=6`，两窗口）；离线 **[4]**（事件题真值不可得 → 事件率 None，方向率不受影响） | ✅ |
| 11 | 超阈降级句式 | `report.py::build_conclusion`（L319）降级分支（含命中率与阈值、`不得单独作为赚钱能力主张`） | `test_run_backtest.py`；离线 **[8]**（formal + 临时预登记 + 超阈 → 「泄漏污染下的上界证据（真实 skill ≤ 读数）…探针方向命中率 100% > 阈值 60%（n=6），不得单独作为赚钱能力主张」） | ✅ |
| 12 | 探针失败不静默 | `leakage_probe.py::_grade`（L225）三态：`truth_unavailable` / `refused_or_unparseable` 计未知，**不折算**答对答错；`aggregate_probes` 不可测态三率一并置 None | 离线 **[2]**（全拒答 → `state=unmeasurable`、`direction=None`、`unknown=1.0`、`downgraded=False`）、离线 **[3]**（空 K 线 → 原因恒 `truth_unavailable`）、离线 **[9]**（formal + 不可测 → 「通路验证定位（探针不可测）」） | ✅ |

### 需求⑤「回测批次预登记与报告生命周期」（ADDED，3 Scenario）

| # | Scenario | 落点 | 证据 | 通过 |
|---|---|---|---|---|
| 13 | 无预登记拒跑正式批 | `run_backtest.py::run_backtest`（L124）formal 分支：`assert_preregistered(..., required_fields=OUTCOME_REQUIRED_FIELDS)` + `valid=False` 亦拒；CLI `main`（L351）同款前置门禁 | `test_run_backtest.py`；离线 **[7]**（空预登记目录 → `MissingPreregistrationError`，库层与 CLI 入口两处均拒绝且理由含路径） | ✅ |
| 14 | 报告 status 头校验 | `report.py::render_backtest_report_md`（L515）渲染后自校 `assert_outcome_report`；`causal_ablation/report_status.py::parse_status`；`docs/evals/README.md` 扫描范围含 `evals/backtest/results/*.md` | `test_report_render.py`；离线 **[6]**（落盘 md 的 `**status**: active` 可解析、`assert_status_valid` 通过、六个固定段落齐）、离线 **[12]**（索引命中 `pilot-2023-shock.md`） | ✅ |
| 15 | 深历史批次定位标注 | `evals/backtest/results/pilot-2023-shock.md`：补 `**status**: active`（第 3 行）+ 追加「定位标注」段（通路验证 + 泄漏风险） | 离线 **[12]**；`git diff 743ea80 HEAD -- evals/backtest/results/pilot-2023-shock.md` → **纯新增**（仅 2 处 hunk：status 头一行 + 末段追加），原文与全部数字未改 | ✅ |

**合计 15 Scenario：15 行落点齐备。**

## 机器验证结果

命令与真实输出摘要（分支 `feat/outcome-profitability-eval`，工作树 `.worktrees/outcome-eval`，HEAD `321f0df` + 本任务改动）：

| 命令 | 输出摘要 | 判定 |
|---|---|---|
| `uv run pytest tests/evals/backtest tests/evals/causal_ablation tests/evals/outcome tests/data tests/outcome -q -m "not live"` | `1119 passed, 2 deselected, 44 warnings in 632.23s`（exit 0） | ✅ 范围全绿 |
| `uv run pytest tests/evals --ignore=tests/evals/backtest --ignore=tests/evals/causal_ablation --ignore=tests/evals/outcome -q -m "not live"` | `473 passed, 2 skipped, 6 deselected in 18.51s`（exit 0；补齐 `tests/evals` 其余部分，与上一行合起来覆盖整个 `tests/evals`） | ✅ |
| `uv run pytest tests/evals/backtest -q` | `186 passed in 4.57s` | ✅ 本 delta 主表面单跑全绿 |
| `uv run pytest tests/evals/backtest/test_data_snapshot.py -q` | `26 passed in 0.96s` | ✅ |
| `uv run ruff check evals/ src/ tests/ scripts/` | `All checks passed!` | ✅ 零违例 |
| `uv run ruff format --check tests/ evals/ src/` | `564 files already formatted` | ✅ |
| `uv run mypy evals/backtest evals/outcome evals/causal_ablation src/finance_agent/outcome src/finance_agent/data/akshare_client.py` | 触碰文件 **零新增**（修掉 1 处本 delta 引入的 `report.py:244` type-var 错误后，`evals/backtest` 归零；`akshare_client.py` 8 例与基线 8 例持平；`pilot_runner.py`/`extract.py` 为既有） | ✅ |
| `openspec validate add-backtest-leakage-controls --strict` | `Change 'add-backtest-leakage-controls' is valid` | ✅ |

**mypy 基线口径**：`git worktree` 检出基线 `743ea80` 后以同一 venv 跑同一目标集（`Found 83 errors in 21 files`，其中 `evals/backtest` 零错误、`akshare_client.py` 8 例）。对比 HEAD：唯一新增为 `evals/backtest/report.py:244`（`max(list[Any|None])` 的 type-var），已改用同文件既有的 `_max_optional` 助手（与相邻 `magnitude/event` 两行同款），复跑后 `evals/backtest` 零错误。

**全量 `uv run pytest` 未跑 / 不声称全绿**：全量跑含 `tests/evals/test_hallucination_live.py`（`@live`，凭据泄漏环境下不再 skip 并撞本地 `data/sessions.db` 前置）的 1 例**既有失败**，**非本 delta 引入**，**已立 issue #158**（OPEN）。本 delta 的机器证据为**范围跑批 1119 passed**（与 CI 门禁 `-m "not live"` 同口径）。

## 离线通路跑批结果（零网络零 LLM）

脚本：`tests/scripts/d4_task5_pathway_offline.py`（`uv run python tests/scripts/d4_task5_pathway_offline.py` → `结论: PASS（全部 52 项核对通过）`，exit 0）。构造 4 标的 × 2 distinct 决策日（`2024-06-03` / `2024-07-15`，分属 sideways / bull）+ 沪深300 基准；`002412` 记 `hold` 以走「非可执行决策整条排除」路径。

| 段 | 场景 | 关键实测 |
|---|---|---|
| **[1]** | 探针三层题（假 LLM 依脚本侧独立真值作答） | `state=downgraded / direction_hit_rate=1.0 / unknown_ratio=0.0 / probe_n=6 / probe_window=['2024-06-03','2024-07-15']`；逐窗口读数 2 条、每项带**它自己**的窗口。**命中率 1.0 是「探针判分正确」的实证**（真值由脚本独立实现，非模块内自证） |
| **[2]** | 探针全拒答 | `state=unmeasurable / direction_hit_rate=None / unknown_ratio=1.0 / downgraded=False`（不报 0、不冒充降级） |
| **[3]** | 空 K 线 → 真值不可得 | 命中率 None；全部条目 `reason=truth_unavailable` |
| **[4]** | 空新闻源 → 事件题真值不可得 | `event_hit_rate=None`；方向题不受影响（`1.0`） |
| **[5]** | 干净窗口判定 | `as_of=2024-10-07 → passed=True`（「最少 60 个交易日」）；`as_of=2024-07-19 → passed=False`（理由含实测交易日距离） |
| **[6]** | pathway 通路批 | `conclusion=通路验证定位（batch_kind=pathway）：本批不产出 skill / 赚钱能力结论句，读数仅用于设施通路验证`；`positioning=pathway`；五键齐；`excluded_non_executable=1`（hold 排除）；`methodology.adjust` 含 hfq；落盘 md `**status**: active` 可解析、六段齐、逐窗口读数表在（1576 字符）；Δ1 句式守卫 `assert_outcome_sentence_legal` 对该结论**抛错**（证明未产出 skill 结论句） |
| **[7]** | formal 无预登记 | `assert_preregistered` 与 `run_backtest` 两入口均抛 `MissingPreregistrationError`（理由含路径），**不静默降级** |
| **[8]** | formal + 临时预登记 + 超阈探针 | `positioning=skill`；`conclusion=泄漏污染下的上界证据（真实 skill ≤ 读数）：无显著差异；探针方向命中率 100% > 阈值 60%（n=6），不得单独作为赚钱能力主张`；`preregister={path, valid: True}` |
| **[9]** | formal + 探针不可测 | `positioning=pathway`；结论写明「探针不可测」 |
| **[10]** | formal + 干净窗口未过 | `positioning=pathway`；结论写明「干净窗口未过」；披露段如实记 `passed=False` |
| **[11]** | 真实门禁读数 | 仓库既有 `evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md` 在 `OUTCOME_REQUIRED_FIELDS`（7 字段）下 `valid=True` |
| **[12]** | 台账索引 | `pilot-2023-shock.md` status 头 `active` 可解析；索引扫描 `evals/backtest/results` 命中该文件、未标注 0 份 |

## 口径披露

**① 探针命中率分母**：只含**可解析答题数**（`unknown` 条目不入分母）；分母为 0 → `None`（**不报 0%**——0 是「全答错」的强主张）。`unknown_ratio` 分母为全部题量。汇总态优先级：不可测 > 超阈 > 可测；不可测态下三率**一并置 None**（避免「不可测」却带子读数误导），`unknown_ratio` 保留（它正是不测的证据）。

**② 干净窗口以交易日计**（基准指数日期序列），不是自然日；基准不可得 / 空样本 → **保守判不通过**（不静默放行）。

**③ 复权口径**：结算/回测区间收益走**后复权（hfq）**，分析输入保持前复权（改它会移动全体分析师行为基线，须独立 delta + 观测电池）；指数无复权概念，基准取原始点位。盯市回退仍用参考价（qfq）口径并已测试钉住——两口径在报告 `methodology` 分列披露，不混算。

**④ 通路验证定位**：`--batch-kind pathway`（默认）结论恒标「通路验证定位」且**不含 skill / 赚钱能力结论句**；深历史批次（如 2023）**永久**通路验证——记忆泄漏不可根除，只可披露与降级。`formal` 身份 = 有效预登记 + 干净窗口通过 + 探针可测，三者缺一即回退通路验证。

**⑤ 非可执行决策排除**：`hold/watch`（neutral，回避语义）整条排除、不产生方向化 system 收益，且基线侧同除以保持序列对齐（§1.8②）；被排除的已一致标的数见 `methodology.excluded_non_executable`。

## 异常记录

**⓪ 全分支终审修复轮（2026-09-24，1 Important + 6 Minor 处置）**：终审给出「Needs fixes」——1 Important（本报告原先误称 `grep -rn "outcome.settle" evals/backtest/` 零命中，实际 3 处且含 1 处对废弃模块的活 import）+ 7 Minor。本轮处置：

| 项 | 处置 |
|---|---|
| **Important：报告证据不实 + 废弃模块活 import** | ① 本报告需求①③ 行证据已改为真实命令与真实命中（**按文件类型分开计数**——第一次改正时仍写错：pilot md 本轮新增的指认段自身含被搜字面量，把 `outcome.settle`/`evaluate_decision` 各 +1，且 `evaluate_decision` 从来不是零命中；复审查抓出后改为 `--include=*.py` 限定代码面）；② `evals/backtest/run_backtest.py:48` 与 `tests/scripts/backtest_pilot_2023.py:41` 的 `BENCHMARK_CODE` 改从非废弃源 `finance_agent.outcome.track_record.job` 取（取值同为 `"000300"`；**不改用 `evals/outcome/caliber.py::BENCHMARK_CODE`——那是 `"000300.SH"` 带后缀，喂给 `fetch_index_kline` 会取错**）；③ pilot md 追加段补「过时表述指认」（只指认不改原文，满足「原文与数字保留」） |
| Minor 1：spec/proposal 预登记字段名与实际门禁不符（写「决策窗口/探针阈值」，实际为 `OUTCOME_REQUIRED_FIELDS` 的「决策阈值/泄漏控制」） | 已改 `specs/decision-backtest/spec.md`（预登记条文）与 `proposal.md`，列出真实 7 字段 |
| Minor 2：design D5 称「两句式结论 = Δ1 收口工具」，但回测结论句未接 Δ1 守卫 | 已改 `design.md` D5：写明回测结论句实际由 `build_conclusion` 单独实现（三种合法形态），Δ1 两句式**未接线**，并列为 owner 待办（见下 §owner 待办 5）——**不改代码**：正式批结论句该用什么效应量（Sharpe 对比 vs T+20 超额）属预登记要固定的设计决策，不宜在收口轮预断 |
| Minor 3：降级句吞掉 regime 限定后缀 | 已修 `report.py::build_conclusion`（抽出 `_regime_suffix`，降级句同样带限定）+ 2 个新用例（含反向：不受限时不得出现限定句） |
| Minor 4：md 探针段缺「题目构成」与幅度/事件率（spec 要求报告含四者） | 已修 `_probe_lines`（补 probe_n / questions_per_ticker / magnitude_hit_rate / event_hit_rate 行）+ 新用例 `TestProbeCompositionDisclosure` |
| Minor 5：「干净窗口以交易日计」无判别性测试 | 已加 `TestTradingDayCaliber`（构造「自然日差 60 天但仅 6 个交易日」的基准帧） |
| Minor 6：渲染自校调用无判别性测试 | 已加 `TestRenderSelfCheckWiring`（patch 守卫抛错 → 必须传出渲染函数） |
| Minor 7：两处低风险披露缺口（`kline is None` 静默跳过不计披露；`PREREGISTER_NAME_CONTAINS="outcome"` 子串过滤） | **留待后续**（当前目录仅 1 份含 `outcome` 的预登记，无旁路；`kline is None` 为既有行为）——已记入 ledger |

**变异复验（本轮新增用例的判别力，实测）**：把 `_trading_days_between` 改成自然日差 → `test_calendar_days_pass_but_trading_days_fail` **RED**；删掉 `render_backtest_report_md` 末尾的 `assert_outcome_report(text)` → `TestRenderSelfCheckWiring` **RED**；降级分支去掉 `regime_suffix` → `TestDowngradedConclusionKeepsRegimeLimit` **RED**。（三例在终审时均为 GREEN，即原先未被钉住；现已钉住。）

**① 本 delta 引入的 mypy 错误（已修）**：`evals/backtest/report.py:244` 的 `max(list[Any | None])` 触发 `type-var` 错误（mypy 不对「条件里重复调用 `dict.get`」做收窄）。改用同文件既有 `_max_optional` 助手，与相邻 `magnitude_hit_rate`/`event_hit_rate` 两行同款。以基线 worktree 对比确认这是本 delta 唯一新增错误。

**② 范围跑批的三例慢测试（既有环境依赖，非本 delta 引入，不阻断）**：`tests/evals/backtest/test_data_snapshot.py::TestBuildSnapshotMetadata` 三例各耗 ≈185s（`--durations` 实测 187.42s / 186.03s / 184.33s），范围跑批因此从 ~1 分钟拉长到 632s（**仍全绿，exit 0**）。诊断：会话内已有真实 Langfuse 客户端单例（faulthandler 转储可见 SDK 后台线程 `langfuse/_utils/prompt_cache.py`、`score_ingestion_consumer.py`、`OtelBatchSpanRecordProcessor`），其后 `build_snapshot` 的 prompt 版本拉取走真实网络（默认 host 不可达，SDK `backoff` 重试 → 每例 ≈185s）。**与 delta 无关的证据**：`data_snapshot.py` 与 `test_data_snapshot.py` 相对基线 `743ea80` **逐字节未改**（`git diff --stat` 为空），单跑该文件 `26 passed in 0.96s`；本机 Docker/Langfuse 未启动（`docker ps` 失败、`localhost:3000` 连不上）。**处置**：不阻断本 delta 收口；转 owner/后续作为测试隔离项（prompt 版本拉取在无 Langfuse 时应有快速失败或离线守卫）。

## owner 待办（本 delta 不自行开启）

1. **真实泄漏探针跑批**：探针走 `complete_text(purpose="judge")`，需 LLM 预算；本 delta 只以假 LLM 验证判分与降级接线。
2. **首个正式批的窗口与样本量**：由预登记 MDE（胜率 1.4008/√n、均值 0.2802/√n）反算后 owner 批预算；跑批命令 `uv run python -m evals.backtest.run_backtest --codes ... --batch-kind formal --as-of <日期>`（formal 强制探针）。
3. **探针降级阈值裁决**：默认 0.60（`evals/outcome/caliber.py::LEAKAGE_PROBE_THRESHOLD`），owner 可在预登记中调整。
4. **issue #158 处置**：`@live` 凭据泄漏用例，阻断「全量测试绿」验收。
5. **首个正式批的结论句形态（终审 Minor 2）**：回测 skill 结论句当前由 `_conclude(sharpe_ci)` 产出（超额 Sharpe 对比句），**未经 Δ1 `assert_outcome_sentence_legal` 校验**（Δ1 要求显著句带效应量 + CI、非显著句带 MDE）。首个正式批的预登记须固定该批结论句的形态（用 Sharpe 对比还是 T+20 超额均值，效应量与 CI/MDE 如何写）并接上守卫——属设计决策，不在本 delta 实施内（无正式批实跑，风险未落地）。
6. **两处低风险披露缺口（终审 Minor 7）**：① `run_backtest` 聚合中「方向一致但缺 K 线」的标的被静默移出绩效（无计数披露）；② `PREREGISTER_NAME_CONTAINS = "outcome"` 为文件名字串过滤 + 取排序最后一个，若日后出现另一含 `outcome` 的预登记会被读成本实验门禁。当前均无实际旁路。
