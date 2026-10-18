# 人工验证报告: fix-ops-sweep-active-scope（issue #334）

**日期**: 2026-10-17
**验证人**: ZCode 验证 runner（单测 + 机制推演；CI 复跑观察待随合并后流水线）
**关联 issue**: #334（CI flake: test_stale_running_swept_on_read 偶发 'running' == 'failed'）
**E2E 门禁**: 不适用（后端归属登记与状态接口读路径，无前端 UI / SSE / 会话切换 / 状态流转）
**执行环境**: worktree `.worktrees/fix-ops-sweep-scope` @ 基于 main 4e95ebfc

## 机制定位（issue 未证实假设 → 代码级推定）

失败签名（同 tree 两绿一红、无任何清扫日志 → 静默跳过）与代码路径逐条核对：

`_sweep_stale_running` 有两条跳过条件：① `run_id in active_run_ids()`（进程级所有权）；
② `started_at >= _PROCESS_STARTED_AT`。issue 已排除②（同进程同时钟、+5min 恒成立）
与异常路径（两条日志路径均无输出）→ 只剩①。

撞号条件成立：**`_ACTIVE_RUN_IDS` 是进程级 `set[int]`，而 job_runs.run_id 在每个库内
独立自增**；测试环境 `SESSIONS_DB_PATH` 逐用例新建（conftest tmp_path），run_id 从小值
重新开始。任一后台任务线程（`run_job` / `ops_api._spawn_task`，daemon，跨用例存活）在他
库登记了同号所有权且尚未注销时，本用例的新库同号悬挂行即被误保护——CI 慢机线程调度
放大窗口，恰好解释了「同 tree 两绿一红」「本地零复现」「无日志」。

这是**跨库误保护**的机制性缺陷（不只测试面：同进程多库场景同样存在）。

## 修复

- `mark_run_active/mark_run_finished/active_run_ids` 全部按 **`(库绝对路径, run_id)`** 作用域登记与查询（`jobs.py`，含 `_active_key` 解析与 `insert_job_run` 同源）；
- `run_job` 登记/注销携带自己的 `db_path`；`ops_api._spawn_task/_run_task` 与 `_sweep_stale_running` 均走默认库键（单库生产语义不变）；
- 清扫/所有权文档串同步（按库作用域表述）。

## 验证结果

| # | 场景 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| 1 | 他库同号 active 不挡本库清扫（flake 回归） | 本库悬挂行仍收尾 failed | `test_foreign_db_leaked_active_does_not_block_sweep`：mark(stale, db_path=others) 后 GET /jobs → failed（修复前红） | ✅ |
| 2 | 同库在跑行仍被保护（防误杀不回退） | 不扫 | 既有 `test_live_running_row_not_swept` / `test_backdated_inflight_run_not_swept_but_foreign_stale_row_is` 全绿 | ✅ |
| 3 | 作用域语义 | 按库存取互不可见 | `TestActiveOwnershipScope` 2 例（跨库不可见 + run_job 自库可见收尾即净） | ✅ |
| 4 | 失败现场保留（issue 处置建议） | 断言消息带现场 | flaky 用例断言消息含 `active_run_ids()` 快照 + 行 `started_at` + `_PROCESS_STARTED_AT` 原值；他库回归用例同类 | ✅ |
| 5 | 回归面 | 无破 | `tests/outcome/` + `tests/test_ops_api.py` 649 项全绿；ruff/mypy 零告警 | ✅ |

## 未达成项 / 披露

1. **CI 复跑验证待合并后观察**：本地不可复现（原 issue 即如此），修复依据是机制推定 + 定向回归用例（构造他库遗留场景，修复前红后绿）；合并后观察 flaky 统计（`scripts/ci/collect_flaky.py` 未覆盖后端 pytest，见 issue #334 关联缺口）。
2. 未启用 `--reruns`（issue 列为 owner 决策项）：机制修复后如需吸收残余抖动再议，本 PR 不加掩盖层。
3. 若合并后仍偶发红，失败现场（现场快照已入断言消息）将直接指向机制，无需再推测。
