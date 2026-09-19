# 人工验证报告: revamp-ablation-v2-causal-claims（框架部分）

**日期**: 2026-09-16（夜间自主执行，待 owner 复核）
**验证人**: agent（自主执行）→ **owner 复核待办**
**关联 delta**: `openspec/changes/revamp-ablation-v2-causal-claims/`
**E2E 门禁**: 不适用（评估框架内部变更，非交互类——不涉及前端 UI / SSE / 会话切换 / 状态流转）
**隔离工作区**: `.worktrees/ablation-v2`（分支 `revamp-ablation-v2-causal-claims`，rebase 于 `52b1e41`）

## 交付范围

本轮交付 delta 的**框架部分**（执行计划 Task 1–14），全部为新增代码，落在新包 `evals/causal_ablation/`，**未触碰**任何被他方 delta 持有的文件。

| 模块 | 职责 | 对应 delta 任务 |
|---|---|---|
| `claims.py` | 因果主张登记表（A1–A7 / B1–B6）+ 准入拒绝校验 | 2.1 |
| `preregister.py` | 预登记 schema + 跑批门禁（阈值须附换算依据） | 2.2 |
| `units.py` | 单元级判定记录与落库 | 2.3 |
| `aggregate.py` | 标的聚类 bootstrap + 设计效应/有效 n | 2.4 |
| `conclusion.py` | 结论两句式纪律 + MDE 强制 | 新增（spec 结论句式） |
| `injection.py` | 8 类污染确定性构造 + 机制开关注册 + 成本分型 | 2.5 |
| `escape.py` | 逃逸率统计（终裁分母）+ 四桶误报分离 | 2.6 |
| `family_b.py` | B1 差集/吸收、B3 出处率、B5 位置随机化与多数决 | 2.7 |
| `calibration_gate.py` | nli/judge 判定一致率 ≥80% 门控 | 2.8 |
| `report_status.py` | 报告 status 头解析/校验/徽章 | 1.4（代码部分） |
| `status_index.py` | 结论注册表索引渲染（只读扫描，不改报告） | 1.4（代码部分） |
| `pilot_calibration.py` | 注入强度校准判据（too_easy / ok / too_hard） | 3.2 |
| `evals/ablation/preregister/2026-09-16-p1-injection-pilot.md` | P1 预登记文档 | 3.4 |

## 验证结果

| 验证项 | 命令 | 预期 | 实际结果 | 通过 |
|---|---|---|---|---|
| 新包单元测试 | `uv run pytest tests/evals/causal_ablation -q` | 全绿 | 147 passed | ✅ |
| 覆盖率 | `--cov=evals.causal_ablation --cov-report=term-missing` | 无未解释分支 | **100%**（424 stmts / 0 miss） | ✅ |
| Lint（新代码） | `uv run ruff check evals/causal_ablation tests/evals/causal_ablation` | 0 | All checks passed | ✅ |
| Lint（全树） | `uv run ruff check` | 0 | All checks passed | ✅ |
| 类型（新代码） | `uv run mypy evals/causal_ablation` | 0 | Success: no issues in 13 files | ✅ |
| 类型（src，CI 口径） | `uv run mypy src/`（CI 为 `\|\| true`） | 与基线一致 | 75 errors / 18 files——**全部为存量**（api.py 等），非本轮引入 | ✅（基线一致） |
| 全量回归（pre-rebase 基点） | `uv run pytest -q -m "not live"` | 0 fail | 1 failed / 2515 passed——唯一失败为他方未同步的测试期望，见异常 1 | ⚠️ 已定位 |
| 全量回归（rebase `52b1e41` 后） | 同上 | 0 fail | **2533 passed / 2 skipped / 12 deselected / 0 failed**（570.60s） | ✅ |
| 阳性对照灵敏度 | `test_positive_control.py` | 已知劣化可检出 | 不一致对子比例 ≥50%，管线灵敏度证实 | ✅ |
| 注入确定性复现 | `test_injection.py::TestDeterminism` | 同 seed 同 payload | 成立（`test_same_seed_same_payload`） | ✅ |
| TDD 纪律 | 先红后绿 | 提交前测试先失败 | `tests/evals/causal_ablation` 首次收集 13 errors（模块未创建），实现后 147 passed | ✅ |

### 最终回归

```
2533 passed, 2 skipped, 12 deselected, 62 warnings in 570.60s (0:09:30)
```

（命令：`uv run pytest -q -m "not live"`；rebase 到 `52b1e41` 后执行，0 失败。2 skipped / 12 deselected 为非 live 层的既有排除项。）

## GATED 任务状态（依赖 `update-ablation-driver-parity-and-report-status`）

前置判据（修正版，见执行计划）：

```bash
grep -q '"diff_mean"' evals/ablation.py && grep -q '_applicable_dims' tests/scripts/ablation_pilot.py \
  && grep -q 'snapshot_digest' tests/scripts/ablation_pilot.py \
  && git diff --quiet -- evals/ablation.py tests/scripts/ablation_pilot.py
```

**判定：BLOCKED**——parity delta 的改动（`diff_mean` 重命名 / 驱动接回 `_applicable_dims` / 驱动侧 digest 核验）在 2026-09-16 夜晚仍为**未提交**状态（其 tasks.md 已全勾、BACKLOG 标「待 6.4 archive」）。

| GATED 任务 | 状态 | 阻塞原因 |
|---|---|---|
| 1.1 rebase 到 parity 合并结果 | 阻塞 | parity 未提交 |
| 1.2 驱动薄壳化（F0 后半） | 阻塞 | 同一函数区域（`tests/scripts/ablation_pilot.py`）有未提交改动 |
| 1.3 citation 腿接四桶拆报（F3） | 阻塞 | `evals/ablation.py` 聚合路径 + `evals/run.py`（另一 agent 在途） |
| 1.4 文档部分（docs/evals 索引 + 存量报告核对） | 阻塞 | `docs/evals/` 正被改动（**代码部分已完成**） |
| 1.5 薄壳化后 3 标的通路验证 | 阻塞 | 依赖 1.2 |
| 3.3 `docs/evals/metrics.md` §1 口径变更 | 阻塞 | 该文件正被改动 |
| 3.5 薄壳化通路验证段 | 部分完成 | 框架段见本报告；薄壳化段依赖 1.2 |

## 异常记录

1. **全量回归唯一失败（已定位为他方在途产物，非本轮引入）**
   - 现象：`tests/evals/test_extract.py::TestPydanticStateCompat::test_key_arguments_prepended_to_debate_messages` 失败，实际输出 `论点: {'text': '净息差改善', 'kind': 'unspecified', 'anchors': []}`，断言仍为旧纯文本格式 `论点: 净息差改善`。
   - 根因：本分支基点 `3062a5f` 已含「论点结构化模型」提交（`9634da5`/`dfd4f4f`），但配套的测试期望更新在后一个提交 `52b1e41`（另一 agent，2026-09-16 提交于 `ground-comparative-delta-claims`）。
   - 处置：rebase 到 `52b1e41`（16 提交无冲突重放），主工作区已实测该测试通过（1 passed）。
   - 结论：与 `evals/causal_ablation/` 新包无关（该包不被 extract 引用，全部为新增文件）。

2. **GATED 前置判据假阳性（已修正）**
   - 现象：初版判据 `git log --oneline -1 -- evals/ablation.py | grep 消融测量收口` 返回 UNLOCKED。
   - 根因：匹配到 `a86986a` 的提交信息（含「消融测量收口」），但该提交**不含** `diff_mean` 重命名等 parity 实际改动。
   - 处置：判据改为检查文件内容标记 + `git diff --quiet`（无未提交改动），已回写执行计划，重判为 BLOCKED。

3. **mypy `src/` 存量错误 75 个**：CI 以 `uv run mypy src/ || true` 放行，属既有技术债，本轮未触碰 `src/`。

## 待 owner 复核项

- [ ] 因果主张登记表 A1–A7 / B1–B6 的**主张文本与效应量预期**是否认可（尤其 A5 已按 incident 027 修正为「待遥测实证」）
- [ ] P1 预登记文档的**决策阈值与换算依据**（拦截率 <50% 判薄防线）是否可作为 P1 跑批依据
- [ ] 逃逸率语义确认：**分母 = 已终裁单元数**，未终裁时 `rate=None`（不报 0%）——是否与产品口径一致
- [ ] 是否启动 P1/P2 正式跑批（GATED 段 1.2/1.3/1.4/3.3 已全部执行完毕；跑批按预登记另行启动，待此决策）
- [x] ~~G3 两处解析器小瑕疵~~ **已修（G7，`96a1faf`）**：状态解析行界化（malformed `superseded-by` 现被校验拒绝）+ 徽章携真实目标；索引 README 已重渲染（逐字节核验）
- [x] ~~G2 桶结论方向措辞~~ **已按默认处置（G7，`96a1faf`）**：四桶层条目加 `lower_is_better` 元数据（blocked/analyst_true_fail=True；telemetry 两桶=None），**措辞未动**、由消费方按元数据解读——如需方向感知措辞请 owner 另行裁决
- [x] ~~aggregate_90 citation 层行~~ **已修（G7，`96a1faf`）**：四桶渲染 + 标量行标注「不入层增量结论」；路径/阈值/参数化未动（另属登记遗留）
- [x] ~~`--judge-repeats` 未透传~~ **已修（G7，`96a1faf`）**：CLI 契约修复，端到端断言（kwargs + 产物 config + 库侧判分 K=5）

## 结论

- [x] 框架部分（Task 1–14）实现完成，测试/lint/类型/覆盖率全绿，验证证据见上表
- [ ] 全部通过，可 archive —— **未达成**：GATED 已解阻完成（G1–G6 + G7 收尾），技术跟进项已按默认处置；但 owner 内容决策未完成（4 项 + 是否启动跑批），**本 delta 不 archive**
- [ ] 存在失败项，需修复后重新验证

## 执行记录

- 分支与提交：`revamp-ablation-v2-causal-claims`（rebase 于 `52b1e41`），17 个提交（Task 1–14 + 计划/文档/本报告）
- 隔离工作区：`D:\WorkSpace\finance_analysis_agent\.worktrees\ablation-v2`（主工作区未受影响；执行期间另一 agent 持续在主工作区提交，最近为 `35f760a`）
- 未消耗 LLM token（仅 stub/离线单元测试；P1/P2 跑批按预登记另行启动）

## 解阻后的后续动作（owner 或下一轮会话）

1. 等 `update-ablation-driver-parity-and-report-status` 提交并 archive 后，本分支再 rebase 一次 → 执行计划 GATED 段 G1–G6；
2. `openspec/changes/BACKLOG.md` 中「v2 消融框架（尚未立项）」条目需更新为本 delta 的 change id（该文件当前由另一 delta 持有，未在本轮改动）；
3. 本 delta 的 `evaluation::数据对齐消融实验` MODIFIED 与 parity delta 的同名 Requirement 冲突按 project-workflow §6 处理（本 delta 为后到者）；
4. `docs/evals/metrics.md` §1 口径变更（G4）须在 parity 落库后执行，避免与其已写入的口径切点段冲突。

## GATED 段执行记录（2026-09-16 解阻后，G1–G5 + G6）

前置：B0 UNLOCKED 判据全过（parity 已提交 `b24341c` 并归档）；rebase 21 提交零冲突（基点 `52b1e41` → 现含 gcc/adba/parity 全部落库内容），因果套件自检 147 passed。

| 任务 | commit | 结果 |
|---|---|---|
| G1 驱动薄壳化（1.2） | `94ee58e` | 判分/维度过滤/材料落盘移入库侧 `score_run`；驱动仅续跑/记账/coverage；红 14 → 绿 56 passed；审查 Spec ✅ / Approved |
| G2 citation 四桶拆报（1.3） | `83f161e` | `layer["citation_<bucket>"]`×4（配对口径同 judge 维度）+ `citation_pass` 退出层增量结论（`not_for_conclusions`）；红 22 → 绿 70 passed；审查 Spec ✅ / Approved |
| G3 结论注册表（1.4） | `a869996` | `docs/evals/README.md` 索引（与 `render_status_index` 现场重跑逐字节一致）；pilot.md `superseded-by` / n10 `active` 核对通过；**未标注 12 份如实列出（未伪填）** |
| G4 metrics §1.7 + 时间线（3.3） | `a869996` | 四类口径先行登记（因果下游指标/逃逸率分母/两句式+MDE/注册表）+「消融 v2 口径切点」段 |
| G5 通路验证（1.5） | （脚本入 `tests/scripts/`） | `TESTING=1` stub（judge 进程内确定性注入，零 token）3 标的 × 3 变体 = **9 runs** 跑通；同参数续跑**零新增**；篡改登记 digest → **RuntimeError**（含标的与两侧 digest） |
| G5 缺陷修复 | `1c33a53` | 见下「高危缺陷」；红 5 → 绿 79 passed；跨进程（`PYTHONHASHSEED` 0/1/12345）同 digest |
| 审查 | — | 两 commit 均 Spec ✅ / Approved（含红态独立复现） |

**G5 发现的高危缺陷（如实记录）**：parity delta 新引入的 digest 核验实现为 `values.tobytes()`——对象列（含字符串列的真实/桩数据）序列化的是**内存指针**，同一数据两次构建 digest 必不同（实测 `4ebad2a5` vs `ccd02072`）。后果：**核验在真实批次必然误报 `RuntimeError`，会阻塞全部消融跑批**。修复：`pd.util.hash_pandas_object` 内容稳定哈希 + 两向测试（同内容放行 / 篡改失败）。**该缺陷在 parity delta 归档时未被发现**（其测试用假 digest、未跑真实批次）——登记为 parity delta 的追认缺陷，修复落在本 delta（`1c33a53`）。

**spec 合并（project-workflow §6 后到者义务）**：本 delta 的 `evaluation::数据对齐消融实验` MODIFIED 已按「保留双方实质」并入 parity 的三段（维度适用性唯一实现 / 快照一致性核验 / 字段名与配对单元）+ 两条 scenario，并补 digest 内容稳定性条款（`1c33a53` 的实现口径）；`openspec validate --strict` 通过。

**未覆盖/推迟**：P1/P2 正式跑批（按预登记另行启动）；阳性对照的注入强度 pilot 真实材料跑批（同前）。

**最终回归（2026-09-16，本 worktree 全量，含 G7 收尾修复后）**：`uv run pytest -q -m "not live"` → **2636 passed / 2 skipped / 12 deselected / 0 failed（8:33）**（G7 前基线 2619 passed）；`uv run ruff check` / `uv run mypy`（改动文件）全绿；`openspec validate revamp-ablation-v2-causal-claims --strict` 通过。
