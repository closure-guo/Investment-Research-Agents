# 交接提示词：收口在途 delta 并完成 v2 消融框架

> 用法：把本文件全文作为提示词交给接手的 agent。它是自包含的——接手方无需本对话历史。
> 生成时间：2026-09-16（夜间执行交接）；生成方分支：`revamp-ablation-v2-causal-claims` @ `8ecf71a`

---

## 0. 你的任务

仓库 `D:\WorkSpace\finance_analysis_agent`（LangGraph 多 Agent A 股分析系统）。你接手一个**多 delta 并行收口**任务，按 Phase A → Phase B 顺序执行，把下列四个在途 change 全部推进到 archive（或明确标注的唯一未决项）：

| change | 位置/分支 | 当前状态 | 你的动作 |
|---|---|---|---|
| `ground-comparative-delta-claims` | 主工作区（分支同名） | tasks 17/19 | 完成剩余 2 项 → 验证 → archive |
| `update-ablation-driver-parity-and-report-status` | 主工作区（改动**未提交**） | tasks 21/21 done，待 6.4 archive | **提交改动** → 验证 → archive |
| `add-debate-argument-anchors` | 主工作区 | tasks 回填完成，含 3 个门控项（发布/真实验证/消融接线） | 按门控条件推进或明确标注阻塞原因 |
| `revamp-ablation-v2-causal-claims` | `.worktrees/ablation-v2`（分支同名） | 框架部分完成（18 提交），GATED 段阻塞 | **解阻后**执行 G1–G6 |

强制纪律：每次会话开始先执行 `using-superpowers` 技能，再读 `docs/project-workflow.md`；按 `AGENTS.md` 任务路由走管线。

---

## 1. 仓库当前状态（已核实，勿重复核查）

- 主工作区 `D:\WorkSpace\finance_analysis_agent`：分支 `ground-comparative-delta-claims`，工作区含**未提交改动**，属于 `update-ablation-driver-parity-and-report-status`（`evals/ablation.py`、`tests/scripts/ablation_pilot.py`、`tests/evals/test_ablation*.py`、`tests/scripts/ablation_aggregate_90.py`、`evals/ablation/results/pilot.md`、`docs/evals/2026-09-03-消融n10权威结果.md`、`docs/evals/metrics.md`、`README.md`、`openspec/changes/BACKLOG.md`）。
- 隔离工作区 `.worktrees/ablation-v2`：分支 `revamp-ablation-v2-causal-claims`，基点 `52b1e41`，18 个提交，工作区干净，全量回归 **2533 passed / 0 failed**。
- 另有 worktree `.worktrees/evals-boot`（分支 `feat/harden-citation-semantic-coverage`）——**不是你的目标，不要动**。

**已知的连锁事实（影响执行顺序）**：
1. `update-ablation-driver-parity-and-report-status` 修改了 `evaluation::数据对齐消融实验` 这条 Requirement，`revamp-ablation-v2-causal-claims` 也修改同一条——按 project-workflow §6，后者必须 rebase 到前者的合并结果上，所以 Phase A 必须先完成。
2. 该 parity delta 的改动若长期不提交，v2 框架的 GATED 段会永久阻塞（GATED 任务要编辑同一批文件、同一函数区域）。
3. `revamp-ablation-v2-causal-claims` 的 `evaluation` delta spec 与 parity delta 的同名 Requirement 冲突；rebase/合并时以**保留双方实质**为原则：parity 侧保留 `diff_mean` 重命名、配对单元披露、驱动 digest 核验；v2 侧保留「指标层置换 + 结论句式纪律 + 跑批入口唯一化」表述。

---

## 2. Phase A：收口在途 delta（先做，且必须提交落库）

### A1. 提交并 archive `update-ablation-driver-parity-and-report-status`

1. 在主工作区逐文件确认改动内容与 tasks.md 一致（重点核对：`diff_mean` 字段、`pairing_unit: ticker` 披露、驱动改调 `evals.ablation._applicable_dims`、每条 run 前 `snapshot_digest` 核验不一致即 `RuntimeError`、`pilot.md` 的 `status: superseded-by` 头、n10 报告的四条适用口径披露、README 消融节诚实边界）。
2. 跑验证并把**新鲜输出**贴进报告：`uv run pytest -q -m "not live"`、`uv run ruff check`、`uv run mypy src/`（CI 口径为 `|| true`，只需与基线一致）。
3. 按 tasks 6.4 完成 sync + archive（`openspec sync` 语义：delta spec 合并进 `openspec/specs/evaluation/spec.md`；变更目录移入 `openspec/changes/archive/2026-09-16-update-ablation-driver-parity-and-report-status/`）。
4. 归档后必须确认：`evals/ablation.py` 中出现 `diff_mean`，`tests/scripts/ablation_pilot.py` 中出现 `_applicable_dims` 与 `snapshot_digest`，且两文件**无未提交改动**。

### A2. 完成 `ground-comparative-delta-claims` 剩余 2 项

按其 `tasks.md` 未勾项执行（任务 3 与 6 的收尾），跑完其规定的验证命令，落人工验证报告到 `tests/validation/`，然后 sync + archive。

### A3. 处理 `add-debate-argument-anchors` 的 3 个门控项

按该 delta `tasks.md` 中标注的门控条件逐项判定：可解阻的执行并验证；依赖外部条件（如 Langfuse 在线发布 prompt）无法满足的，在 tasks.md 中写明阻塞原因与解阻条件，**不要**用「跳过」蒙混。注意：改 prompt 后必须 `uv run python scripts/deploy_prompts.py` 发布，否则 eval 门禁拒绝运行。

---

## 3. Phase B：解阻并完成 `revamp-ablation-v2-causal-claims`

### B0. 前置判据（**必须先跑，且必须打印 UNLOCKED**）

```bash
cd /d/WorkSpace/finance_analysis_agent
OK=1
grep -q '"diff_mean"' evals/ablation.py || OK=0
grep -q '_applicable_dims' tests/scripts/ablation_pilot.py || OK=0
grep -q 'snapshot_digest' tests/scripts/ablation_pilot.py || OK=0
git diff --quiet -- evals/ablation.py tests/scripts/ablation_pilot.py || OK=0
[ "$OK" = 1 ] && echo UNLOCKED || echo BLOCKED
```

> **陷阱（已踩过）**：不要用 `git log ... | grep 消融测量收口` 之类**只看提交信息**的判据——提交 `a86986a` 的信息含该字样但不含 `diff_mean` 重命名，会给出假 UNLOCKED。必须检查文件内容标记 + 无未提交改动。

判定 `BLOCKED` 时：回到 Phase A，不要动 GATED 任务。

### B1. 合并基点

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/ablation-v2
git fetch . ground-comparative-delta-claims   # 或直接 rebase
git rebase ground-comparative-delta-claims
uv run pytest tests/evals/causal_ablation -q  # 期望 147 passed（回归自检）
```

冲突处理原则见 §1 第 3 条。rebase 后若 `docs/evals/metrics.md` 出现冲突，取双方并集并保持 parity 已写入的「消融口径切点」段落完整。

### B2. 执行计划的 GATED 段（G1–G6）

完整计划与每步期望输出见 `docs/superpowers/plans/2026-09-15-causal-ablation-framework.md` 末尾「GATED」段。摘要：

- **G1（对应 delta tasks 1.2，F0 后半）**：`tests/scripts/ablation_pilot.py` 的 judge 判分与 `judge_vars` 落盘逻辑移入库侧 `evals/ablation.py`，驱动只留续跑/记账/coverage 包装。**先写失败测试**：薄壳化后现有跑批行为不变（现有 `tests/evals/test_ablation_pilot.py` 全绿即是回归网）。
- **G2（tasks 1.3，F3）**：消融聚合的 citation 腿接入四桶拆报口径（`blocked / analyst_true_fail / surgical_repaired / verifier_normalized`——桶名以 `evals/causal_ablation/escape.py::VERIFIER_BUCKETS` 为准），`citation_pass` 标量退出层增量比较路径。
- **G3（tasks 1.4，F8 文档部分）**：用 `evals/causal_ablation/status_index.py`（已实现并测试）扫描 `docs/evals/` 生成索引与状态徽章；核对存量报告（至少 `evals/ablation/results/pilot.md`、`docs/evals/2026-09-03-消融n10权威结果.md`）的 status 头是否正确；未标注的存量报告列入索引的「未标注生命周期」段，不要伪填 status。
- **G4（tasks 3.3）**：`docs/evals/metrics.md` §1 口径先行变更——登记因果下游指标口径、逃逸率口径（分母 = 已终裁单元数，未终裁 `rate=None`）、结论两句式 + MDE 强制、结论注册表；时间线加一行。
- **G5（tasks 1.5）**：`TESTING=1` stub 模式下 3 标的通路验证，复用 `reports/ablation/resume.json` 断点续跑设施，确认 digest 核验生效（构造一次 digest 不一致即应 `RuntimeError`）。
- **G6（tasks 3.5）**：把 G5 结果补进 `tests/validation/2026-09-16-causal-ablation-framework-validation.md`，形成完整人工验证报告。

### B3. 收口

1. 更新 `openspec/changes/revamp-ablation-v2-causal-claims/tasks.md` 勾选状态（当前已回填阻塞原因，解阻后改为完成）。
2. `openspec validate revamp-ablation-v2-causal-claims --strict` 必须通过。
3. 更新 `openspec/changes/BACKLOG.md`：「v2 消融框架（尚未立项）」条目改指向 change id `revamp-ablation-v2-causal-claims`。
4. **archive 除外**：本 delta 的验证报告含 4 项待 owner 决策（登记表主张文本、P1 决策阈值换算依据、逃逸率分母语义、GATED 是否继续）。这 4 项未获 owner 明确答复前**不得 archive**——把 delta 停在「验证通过、待 owner 复核」，并把这 4 项列成清单发给 owner。其余三个 delta 的 archive 不受此限。

---

## 4. 可复用的既有接口（**禁止重复实现**）

Phase B 的所有新逻辑必须调用下列已实现并有测试覆盖的库侧函数（包 `evals/causal_ablation/`，测试 `tests/evals/causal_ablation/`，覆盖率 100%）：

| 用途 | 接口 |
|---|---|
| 目标准入 | `claims.assert_admissible(target_id)`、`claims.DEFAULT_REGISTRY` |
| 预登记门禁 | `preregister.assert_preregistered(dir)`（跑批入口必须先过这道门） |
| 单元落库 | `units.UnitJudgment`、`units.write_units` / `read_units` |
| 聚类推断 | `aggregate.cluster_mean_diff_ci(prev_by_cluster, cur_by_cluster)`、`design_effect`、`effective_n` |
| 结论句式 | `conclusion.conclude(ci, mde=..., threshold=...)`、`assert_sentence_legal` |
| 注入污染 | `injection.build_injection_cases(...)`、`apply_injection(snapshot, case)`、`COST_CLASS`、`MECHANISM_SWITCHES` |
| 逃逸率与拆报 | `escape.classify_state`、`mcnemar_table`、`escape_rate(pairs, adjudicated=...)`、`split_verifier_buckets` |
| 族 B 指标 | `family_b.risk_point_diff`、`absorption_rate`、`grounding_rate`、`pairwise_assign`、`majority_verdict` |
| 校准门控 | `calibration_gate.assert_calibrated(method, judge_labels, human_labels)` |
| 结论注册表 | `report_status.parse_status`、`status_badge`、`status_index.collect_status_index`、`render_status_index` |
| 注入强度校准 | `pilot_calibration.calibration_verdict(pairs)` |

硬性约束（spec 原文，违反即返工）：
- 判定方式取值域只有 `code | nli | judge`；污染类型只有 8 类（见 `injection.POLLUTION_TYPES`）。
- 校准门控阈值 0.80，覆盖**所有** `method ∈ {nli, judge}` 的指标（含 B1/B2/B3/B5）。
- 聚类 bootstrap 的重采样单元是**标的**，B=10_000，seed=42；报告必须披露设计效应折算的有效 n。
- 逃逸率未终裁时返回 `None`，**不得报 0%**；校验器误报走四桶拆报，不进逃逸率分母。
- B6（track-record 结算）**不得**用于层间归因，只作 full 绝对质量线与未来裁剪的版本分段对照。
- 每轮跑批必须携带阳性对照（关 `verify_citations` 的已知劣化变体）；阳性对照测不出显著差异则本轮阴性结论作废。
- 休眠机制（`CITATION_AUTO_RETRY_ENABLED=False`）不进消融矩阵。

---

## 5. 纪律红线

- **TDD**：每个改动先写失败测试（红）→ 最小实现（绿）→ 提交。没有失败测试的实现一律删除重写。
- **验证证据**：声称完成前必须跑新鲜命令并读取完整输出（`uv run pytest -q -m "not live"` 约需 9–10 分钟；`uv run ruff check`；`uv run mypy evals/causal_ablation`）。禁止用「应该通过」「上次跑过」代替。
- **不许动他人的未提交改动**：发现工作区有你不认识的未提交改动时，先 `git log`/`git status` 判定归属再动手；不属于你当前 delta 的不要提交、不要 stash、不要回滚。
- **并发**：`.worktrees/ablation-v2` 同一时刻只允许一个 agent 在其中工作；不要在主工作区与 worktree 同时跑全量测试（资源竞争会显著拖长）。
- **不制造平行真相**：文档结论按 `docs/evals/metrics.md` 台账口径走；报告头部 `status` 字段遵循注册表约定；指标口径变更「先改 metrics.md §1 再动代码」。
- **不改** `openspec/specs/` 主规范库（只经 delta + sync）；不新建 `docs/adr/`（人工维护）。

---

## 6. 交付与报告格式

最终给 owner 的报告必须包含：

1. **四个 change 的状态表**：change / 是否 archive / archive 路径 / 关键提交 SHA；
2. **验证证据**：`pytest`（失败数、通过数、耗时）、`ruff`、`mypy` 的原始输出摘要，以及 `openspec validate --strict` 结果；
3. **GATED 任务逐条完成情况**（G1–G6，含 G5 通路验证的实际命令与输出）；
4. **未决项清单**：v2 delta 的 4 项 owner 决策 + 任何无法解阻的门控项（附解阻条件）；
5. **异常与偏离**：任何与计划不一致的实现、任何测试从红转绿之外的改动、任何被判为不可达而加 `# pragma: no cover` 的分支及理由。

禁止在报告中出现：「未获统计支持」这类**裸结论**（必须附 MDE）、任何未跑命令的完成声称、任何把 `citation_pass` 标量当层增量证据的表述。
