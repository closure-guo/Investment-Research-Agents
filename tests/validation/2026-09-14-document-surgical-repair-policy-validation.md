# 验证记录：document-surgical-repair-policy（单点修复授权与边界）

**日期**：2026-09-14
**类型**：规范授权条款（spec-only，**无代码变更**）
**delta**：`openspec/changes/document-surgical-repair-policy/`

## 校验

- `openspec validate --strict document-surgical-repair-policy` ✅ 通过
- 无交互/行为变更 → 不触发 E2E 门禁；本记录即验证证据

## 实现对照（护栏逐条成立，行号为当日 HEAD）

| 规范护栏 | 代码证据 |
|---|---|
| ① 稀疏阈值 <3 处才触发 | `nodes/citation_node.py:202` `len(vm) >= 3` → 跳过 |
| ② 不隐藏真错（计入 analyst_true_fail） | `nodes/citation_node.py:411` `report.failed + int(value_mismatch_repaired or 0)` |
| ③ 重校验仲裁 | `nodes/citation_node.py:229` 回填后 `verify_claims` 整体重校验；`:231` 仅 `all_passed` 才记修复 |
| ④ 最小改写（禁新增数字） | `nodes/citation_repair.py:20-25` REPAIR_SYSTEM_PROMPT「禁止改写其他内容、禁止引入新数字」 |
| ⑤ trace 可审计 | `nodes/citation_node.py:248` `surgical_repairs`（修前句/修后句/真值/重校验结果） |
| ⑥ 失败直接阻断放行、不重跑 | `routing.py:71-72` `CITATION_AUTO_RETRY_ENABLED=False → render`（阶段 0） |
| ⑦ 独立计数进拆报 | `evals/task.py:117` + `evals/run.py:199-202` + `docs/evals/metrics.md` §1.3 `citation_surgical_repaired`（随本批一并落地，TDD 先红后绿：`tests/evals/test_run.py::test_fourteen_evaluators`、`tests/evals/test_task.py::test_deep_output_includes_citation_metrics`） |

## 结论

规范授权与既有实现一致，七条护栏全部成立；无代码变更。**后续任何护栏的放宽（阈值/口径/跳过重校验/允许新数字）须先开 delta 修改该要求**，不得作为实现细节调整。
