# Tasks: add-execution-chain-driver

## 1. 驱动器（先写失败测试）
- [x] 1.1 `execution/driver.py`：`run_execution_daily()` 五段编排（决策→信号幂等 / 信号→目标聚合 / 目标→订单差分（100 股整手）/ 门禁逐单 / 下单（执行日开盘口径））+ 摘要（`signals_created/orders_placed/rejected/pending/skipped_reason`）
      —— 单测（合成决策 + 合成快照，手算期望）：闭环（持仓/资金/订单三处断言）、幂等重跑零新增、flat 平仓、快照缺失跳过、门禁拒绝留痕（ST_FLAG + 其余订单不受阻）
- [x] 1.2 `jobs.py` 注册 `paper_execute_daily`（9→10）+ 排程 mon-fri 16:10 Asia/Shanghai + `scheduler.py` `_with_retry` 包装；单测：注册表/排程/封装透传

## 2. 既有测试最小更新（计数类，逐条记录）
- [x] 2.1 后端注册表与状态接口计数断言 9→10
- [x] 2.2 E2E `eval-ops-console.spec.ts` 计数 9→10

## 3. 验证
- [x] 3.1 全量测试通过（后端 + 前端）
- [x] 3.2 真实栈人工验证：手动补跑 `paper_execute_daily`（当前无快照 → 跳过并如实落历史；如有 open 决策则核对信号生成）；战绩页双曲线在 paper 有数据后按既有契约切换（当前无数据 → 单曲线不伪第二条）
- [x] 3.3 既有测试零修改（除 §2 计数类最小更新）
- [x] 3.4 验证报告落 `tests/validation/2026-10-14-add-execution-chain-driver-validation.md`（含合成数据的独立手算期望、快照恢复后真实链路补验挂 issue #325）
