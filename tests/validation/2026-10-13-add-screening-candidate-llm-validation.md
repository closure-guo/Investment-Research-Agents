# 人工验证报告: add-screening-candidate-llm

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-screening-candidate-llm/`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 1–3（T1 合同+并发+降级 / T2 screening 库+稳定性门槛 / T3 live 抽检与收口）
+ 前置评审修复（ranker ICIR 常量守卫与文档修正）
**全量回归时 HEAD**: `8258e38d`（含评审修复 `1e81dc48`；此后仅新增验证脚本/artifact/本报告、
`tasks.md` 回填与 plan 收入，**零 src 改动**）

> **硬约束声明（只记录实际发生）**：spec/proposal 要求「对 2026-09 一个完整周的真实候选跑 L3、
> 人工抽 20 条 drop 理由核对」，但 2026-09 真实候选不可得（东财行情域 IP 封禁持续生效，
> incident 033 / issue #325）→ 以 25 只合成候选 + 注入事件替代。本次真跑验证的是「输出合同
> 与 LLM 行为（verdict/reason 是否可用、降级是否安全）」，**不构成真实市场有效性的证据**；
> 真实一周候选抽检挂 follow-up（issue #325）。

## 0. 命令证据（当场跑、逐字记录）

### 0.1 指定测试命令与全量回归

```
$ uv run pytest tests/screening -q
108 passed in 2.55s

$ uv run pytest tests/ -q -m "not live"
1 failed, 5150 passed, 6 skipped, 12 deselected, 30 warnings in 921.04s (0:15:21)
（唯一失败：tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default
 ——既有环境性 flake，归因见 §2）

$ uv run pytest "tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default" -q
1 passed in 0.05s
```

### 0.2 Lint 与类型检查

```
$ uv run ruff check src/finance_agent/screening tests/screening tests/factors/test_builtin.py \
    tests/validation/2026-10-13-add-screening-candidate-llm-live-triage.py
All checks passed!

$ uv run ruff format --check src/finance_agent/screening tests/screening \
    tests/validation/2026-10-13-add-screening-candidate-llm-live-triage.py
12 files already formatted

$ uv run mypy src/finance_agent/screening
Success: no issues found in 6 source files
```

### 0.3 live 真跑（逐字尾部输出）

```
$ uv run python tests/validation/2026-10-13-add-screening-candidate-llm-live-triage.py
[env] dotenv: D:\WorkSpace\finance_analysis_agent\.env
[profile] env:openai/k3-256k | model=openai/k3-256k | host=api.kimi.com | key_present=True
[run] 25 candidates x 1 quick call (max_workers=8) ...
[status] ok (elapsed 34.6s)
[distribution] {'keep': 20, 'drop': 5, 'degraded': 0}
[reason] max_len=39 all_le_50=True
[risky drop rate] 1.0 {'600519': 'drop', '000858': 'drop', '300750': 'drop', '002594': 'drop', '601318': 'drop'}
[spec 场景 rank5 立案] {'symbol': '600519', 'rank': 5, 'event': '收到中国证监会立案告知书',
                      'verdict': 'drop', 'reason': '2026-09-28被证监会立案调查，属重大风险',
                      'reason_mentions_lizhan': True}
[db readback] 25 rows == candidates 25: True
[artifact] tests/validation/2026-10-13-add-screening-candidate-llm-live.json
```

（旁路报错为预期且零业务影响：Langfuse 离线 → prompt loader 回退本地 `.md` 并打 WARN、
trace 观测 best-effort 跳过；调用本身全部经真实 gateway quick profile。）

### 0.4 prompt 部署尝试（→ 待部署）

```
$ uv run python scripts/deploy_prompts.py
[ERROR] 缺少环境变量: 'LANGFUSE_PUBLIC_KEY'
发现 16 个 prompt 文件，排除 0 个
目标 Langfuse: http://localhost:3000
标签: ['production']
exit=1
```

→ `prompts/screening_triage.md` 状态：**待部署**（本地权威源已入库；Langfuse 实例离线
——live 轮 `WinError 10061 连接被拒` 同证——且本机缺 `LANGFUSE_*` 凭据）。Langfuse 恢复后
执行 `uv run python scripts/deploy_prompts.py` 即完成部署（deploy 是 eval 门禁的前置）。

## 1. Task 1–2 覆盖矩阵（单测证据）

| spec / plan 要求 | 用例（`tests/screening/`） |
|---|---|
| 合同逐字：verdict ∈ {keep, drop}；reason ≤ 50 字（去首尾空白，中文按字符数） | `test_validate_*`（50/51 字边界、非枚举 verdict、缺失/空白/非字符串 reason） |
| 违约重试 1 次仍违约 → keep + degraded + warning（不无限重试） | `test_contract_violation_twice_degrades_to_keep_with_warning`（调用数恰为 2）、`test_contract_violation_retries_once_then_accepts` |
| spec「单只失败降级」：50 只中 1 只 typed error → 其余 49 只正常、失败者 keep、warning | `test_single_typed_error_does_not_block_batch_of_50`（LLMTimeoutError 注入） |
| 并发上限默认 8 可配置；结果顺序 = 输入序（并发不改变确定性） | `test_concurrency_respects_max_workers`（sleep 桩峰值 ≤3 且 ≥2）、`test_result_order_is_input_order_when_completions_reverse` |
| C2 prompt 含综合分/贡献摘要/事件标题；空事件 → 占位 | `test_build_triage_prompt_contains_score_contribs_and_events`、`test_build_triage_prompt_renders_raw_dict_and_empty_placeholder`（「无最近事件」） |
| C1 purpose 固定 quick（默认 client 钉死） | `test_gateway_client_pins_quick_and_parses_json`（调用方传 deep 仍走 quick）、`test_legal_payload_produces_decision_with_quick_purpose` |
| C5 决策落库每只一行（含降级） | `test_every_decision_is_persisted_once` + live DB 回读 25/25 |
| 稳定性门槛：spec 断档场景（1/2 在榜→3 出榜→4/5 在榜 → 第 5 日 = 2） | `test_consecutive_days_resets_after_gap_spec_scenario` |
| 2 日 False / 3 日 True / min_days 可配置；as_of 不在榜 → 0；跨非交易日不打断 | `test_eligible_for_selection_boundaries`、`test_consecutive_days_returns_zero_when_absent_on_as_of`、`test_consecutive_days_ignores_non_trading_day_gaps` |
| 两表 DDL 幂等 + 两表同日重跑幂等 upsert | `test_init_screening_db_is_idempotent_and_creates_both_tables`、`test_record_l2_candidates_is_idempotent_upsert`、`test_record_triage_decision_upserts_same_day` |
| 读取过滤 | `test_list_triage_decisions_filters`、`test_record_l2_candidates_is_idempotent_upsert`（rank 升序/空日期） |

单测全部 stub client 零网络（autouse fixture 钉死 prompt loader 为本地兜底）；
新增 32 例：`test_candidate_llm.py` 20 + `test_screening_store.py` 12。

## 2. 失败归因（llm env flake，非本 change）

① 单独运行该用例通过（§0.1 第三条命令，0.05s）；
② 同一用例在上一轮全量回归（`2026-10-13-add-screening-ranker-validation.md`）即失败，
已归因宿主 `.env` 泄漏 `LLM_MAX_CONTEXT=256000`（本轮复核仍在
`/d/WorkSpace/finance_analysis_agent/.env:40`）；
③ 本 change 的 src diff **不触 `llm/`**：`git diff --name-only 6cf873b1..HEAD -- src/` =
`src/finance_agent/screening/ranker.py`、`screening/candidate_llm.py`、`screening/store.py`、
`prompts/screening_triage.md`；
④ 增量核对：5150 − 5118（上一轮 passed）= 32 = 20 + 12（本 change 新增用例）；
`tests/screening` 108 − 76 = 32 ✓——失败是既有环境 flake，非本轮回归。

## 3. live 抽检结果（T3）

### 3.1 分布与合同

| 指标 | 结果 |
|---|---|
| 状态 / 耗时 | ok / 34.6s（25 只 × 1 次 quick 调用，max_workers=8） |
| keep / drop / degraded | **20 / 5 / 0** |
| risk 5 只 drop 率 | **1.0**（600519/000858/300750/002594/601318 全 drop） |
| 中性 20 只 keep 率 | 1.0 |
| reason 长度 | 最长 **39** 字，全部 ≤ 50 ✓ |
| 落库回读 | 25/25 行（triage_decisions，含 verdict/reason/degraded） |
| warnings | 0（无失败、无合同违约重试耗尽） |

两轮真跑（调整风险事件 rank 分布前后各一轮，同一脚本）分布一致：keep20/drop5/degraded0；
最终 artifact 为对齐 spec GIVEN 的第二轮（rank 5 = 立案候选）。

### 3.2 spec 场景复现

- **「事件雷剔除」**：rank 5 的 600519 注入「收到中国证监会立案告知书」→ `verdict=drop`，
  reason「2026-09-28被证监会立案调查，属重大风险」明确提及立案；决策落库（DB 回读）。
- **「单只失败降级」**：live 未出现真实失败（degraded=0，如实记录）——该场景的降级路径
  证据为单测 `test_single_typed_error_does_not_block_batch_of_50`（50 只中 1 只 typed error）。

### 3.3 「整齐得可疑」审查（AGENTS.md 评估纪律：完美分离先解释再放行）

5/5 风险 drop、20/20 中性 keep 的完美分离，逐条审查后解释如下：

- 注入事件是**逐字风险披露**（立案告知书 / 集中减持 / 业绩预亏），输入可分性极高，
  完美分离是任务难度的直接结果，不是泄漏或桩路径；
- 抽检的 20 条理由（§3.4）逐条引用具体事件与风险类别（形态如「2026-10-09公告董事及高管
  集中减持计划，属大额减持重大风险事件」），keep 理由均说明「仅 X，无重大风险事件」——
  是面对输入的真实判断，而非模板化输出；
- degraded=0 → 无兜底文案混入（不存在「兜底谎称」形态）；
- **结论**：该结果支持「合同与理由可用性」结论，**不可外推**为对真实市场模糊事件
  （一次性利空、行业性下修、语义含糊公告）的判别力证据。

### 3.4 人工终审材料（20 条理由逐条，5 风险 + 前 15 中性固定抽样）

| 组别 | symbol | verdict | reason 长度 | reason（原始） |
|---|---|---|---|---|
| risky | 600519 | drop | 24 | 2026-09-28被证监会立案调查，属重大风险 |
| risky | 000858 | drop | 23 | 控股股东涉嫌信披违法违规被证监会立案，重大风险 |
| risky | 300750 | drop | 20 | 控股股东拟减持不超过3%，属大额减持风险 |
| risky | 002594 | drop | 35 | 2026-10-09公告董事及高管集中减持计划，属大额减持重大风险事件 |
| risky | 601318 | drop | 27 | 2026-10-10公告三季报业绩预亏，属重大风险事件 |
| neutral | 600036 | keep | 33 | 综合分排名第1，近期仅披露半年报，无立案、减持或业绩暴雷等风险事件 |
| neutral | 601398 | keep | 27 | 综合分第2且无重大风险事件，股东大会决议属常规治理公告 |
| neutral | 600030 | keep | 17 | 综合分靠前，仅政府补助，无重大风险 |
| neutral | 000001 | keep | 21 | 综合分第4，仅回购进展公告，无重大风险事件 |
| neutral | 600887 | keep | 22 | 综合分第6，近期仅新品发布会，无重大风险事件 |
| neutral | 601166 | keep | 25 | 综合分靠前，仅董事会换届属正常治理事件，无风险信号 |
| neutral | 000333 | keep | 23 | 综合分第10，仅见中期分红实施，无重大风险事件 |
| neutral | 600276 | keep | 37 | 综合分2.0排名第11，近期仅获药品注册证书属正面经营动态，无重大风险事件 |
| neutral | 002415 | keep | 22 | 综合分靠前，重大合同属经营事项，未见风险事件 |
| neutral | 600309 | keep | 22 | 综合分靠前，仅子公司工商变更，无重大风险事件 |
| neutral | 601012 | keep | 25 | 无重大风险事件，员工持股进展属中性，综合分排名靠前 |
| neutral | 000651 | keep | 21 | 综合分靠前，近期仅经营简报，无重大风险事件 |
| neutral | 600900 | keep | 25 | 综合分靠前，近期仅参与设立产业基金，无重大风险事件 |
| neutral | 601888 | keep | 27 | 综合分排名第19，近期仅接待投资者调研，无重大风险事件 |
| neutral | 002475 | keep | 22 | L2第20，仅注册资本与章程变更，无重大风险 |

每条另附原始 prompt 摘要（`prompt_excerpt`，模板渲染后前 400 字）于 JSON
`tests/validation/2026-10-13-add-screening-candidate-llm-live.json`。
**人工终审状态：材料齐备，签字待补。**

## 4. 提交与产物

| 提交 | 内容 |
|---|---|
| `1e81dc48` | fix(screening): ICIR constant-series guard + docstring corrections (review findings)（评审 4 项全修；`[0.1,0.1,0.1]` 反例先红（5.88e15）后绿） |
| `00aa602f` | feat(screening): L3 LLM triage with contract, concurrency and keep-fallback (T1) |
| `8258e38d` | feat(screening): screening store + listing-stability threshold (T2) |
| 本提交 | docs(openspec): add-screening-candidate-llm validation report + tasks 回填（含 plan 文件收入与 live 脚本/JSON） |

产物：`src/finance_agent/screening/{candidate_llm,store}.py`、
`src/finance_agent/prompts/screening_triage.md`、
`tests/screening/{test_candidate_llm,test_screening_store}.py`、
`tests/validation/2026-10-13-add-screening-candidate-llm-live-triage.py/.json`、本报告。

## 5. 遗留与 follow-up

- **prompt 待部署**：`screening_triage.md` 未发布 Langfuse（离线+缺凭据，§0.4）；恢复后
  执行 `uv run python scripts/deploy_prompts.py`。live 轮 prompt 为本地兜底层（已核无版本漂移）。
- **真实候选抽检挂 issue #325**（东财封禁解除后补真实一周候选的 L3 分层核对）。
- 事件数据口径：本 change 以 `events_by_symbol` 参数注入（docstring 已披露），生产由
  pipeline 从公告/新闻源装配——proposal 中「复用 events/ 模块」按实际代码修正。
- archive 前置检查：`tasks.md` 全勾 ✓；verification 通过 ✓；E2E 门禁**不适用**（本 change
  零前端/SSE/会话状态改动，无交互类变更）；人工验证报告落本文件 ✓（人工签字待补）。
