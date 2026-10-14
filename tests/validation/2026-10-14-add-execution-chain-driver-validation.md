# add-execution-chain-driver 实施验证报告

**日期**: 2026-10-14
**范围**: 执行链驱动器（决策 → Signal → 组合目标 → 风控门禁 → paper 下单）+ 门禁拒绝留痕 + 注册表 9→10
**交付**: `execution/driver.py`（新增）、`execution/store.py`（+`signal_ids_with_orders` / `record_order_rejection`）、`outcome/ops/jobs.py` + `outcome/scheduler.py`（`paper_execute_daily` 16:10）

## 一、实施摘要

接线前：`generate_signal` / `aggregate_targets` / `check_order` / `PaperBroker.place_order` 全仓零调用方——规范两条主要求（「审批通过的 TradeDecision 转换为 Signal 落库」「所有订单 SHALL 通过 risk_gate」）在运行态均不成立。接线后 `run_execution_daily()` 五段编排：

1. **决策→信号（幂等）**：`decision_log` 中 `timestamp[:10] < as_of` 的 open 决策 → `signal_from_decision` → `record_signal`（已存在 decision_id 集合先行排除）；
2. **信号→目标**：尚无订单的信号（`signal_ids_with_orders`）→ `aggregate_targets`（等权）得目标权重；
3. **目标→订单**：持仓按快照 `close_raw` 估值（缺价标的入 `skipped_symbols`，不编造）→ 目标股数（整手）→ **只补不缩**差分；`flat` 信号先平仓（释放敞口/资金）；
4. **门禁逐单**：`check_order`（ST/停牌/一字板/单票上限/总仓上限/T+1/整手）；被拒 → `record_order_rejection`（orders 行 status=rejected + 原因码 + decision_id/signal_id；order_id 与 `PaperBroker.order_id_for` 同源派生保证重跑幂等）；
5. **下单**：`PaperBroker.place_order`（执行日开盘口径；pending 次日重试沿用既有机制）；摘要 `{trade_date, signals_created, orders_placed, rejected, pending, skipped_reason, skipped_symbols}`。

快照不可用 → `skipped_reason="no_snapshot_data"` 零摘要（跳过不抛错，信号顺延——同 `run_paper_settle_daily` 先例）。

## 二、TDD 与验证证据

- **新测试** `tests/execution/test_driver.py` 7 例（合成决策 + 合成快照，独立手算期望）：闭环（信号含 decision_id + 成交 + 持仓/资金三处）/ 幂等重跑零新增 / flat 平仓 / 快照缺失零副作用 / 门禁拒绝留痕且其余订单不受阻 / 同日决策延后次日 / 空库零摘要。
- **非空转实证**：移走 `driver.py` → 收集失败（红）；还原 → **7 passed**（测试确实驱动实现）。
- **靶向**：`pytest tests/execution/test_driver.py tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py -q` → **69 passed**
- **域回归**：`pytest tests/execution tests/outcome tests/screening -q` → **932 passed**
- **lint**：ruff check / format 全过
- **真实 API（本地隔离实例 :8090，真调度器）**：
  - `GET /api/v1/ops/jobs` → 任务数 **10**；顺序 `[… decision_settle_daily, paper_execute_daily, paper_settle_daily …]`；`paper_execute_daily`=仿真执行 mon-fri **16:10** Asia/Shanghai，下次触发 `2026-10-15T16:10:00+08:00`；
  - `POST /api/v1/ops/jobs/paper_execute_daily/run` → 受理（run_id=2）；运行历史终态 **ok** + 摘要 `{"trade_date": null, "skipped_reason": "no_snapshot_data", "signals_created": 0, "orders_placed": 0, "rejected": 0, "pending": 0}`（快照封禁期跳过语义如实，零伪造）。

## 三、实施口径说明（设计细化）

1. **只补不缩**：目标 ≤ 当前持仓时不生成卖出订单（避免把「再平衡」语义偷渡进本 delta）；`flat` 信号是唯一卖出来源（与非目标声明一致）。
2. **聚合口径** = 当次运行的全部未执行信号（非逐日分组）——「每条信号恰好执行一次」由 `signal_ids_with_orders` 差分保证；逐日分组/持续再平衡留待后续 delta。
3. **退出先行**：flat 平仓意图先于 long 入场规划，使总仓门禁输入（`projected_value`）反映真实释放后的敞口。
4. **估值保守**：持仓缺快照价的标的不计入市值（进 `skipped_symbols`），不填 0/不编造。

## 四、既有测试最小更新清单（计数类）

1. `tests/outcome/test_ops_jobs.py`：`TestRegistry` len 9→10；`TestScreeningDailyRegistration::test_registered_aligned_and_ordered` len 9→10
2. `tests/outcome/test_scheduler.py`：注册集合两处 len 9→10 + id 集合 +`paper_execute_daily`
3. `tests/e2e/playwright/tests/eval-ops-console.spec.ts`：JOB_IDS +`paper_execute_daily`（位置=后端顺序）

## 五、E2E 门禁

eval-ops 套件在 D1 已跑通（5 passed）；本 delta 的 JOB_IDS 更新后**待随 D 批次收尾统一复跑**（挂合并前门禁）。

## 六、遗留

1. 部署复核：合并后容器 `--reload` 生效，`GET /api/ops/jobs` 应现 10 任务（挂合并后部署核验）。
2. **真实链路补验**（决策→成交的真实数据）待快照解封（issue #325）：封禁期 `no_snapshot_data` 为预期态；解封后首个执行日核对真实信号/订单/持仓三处。
3. 持仓退出（horizon 届满）与再平衡为非目标（后续 delta）。

## 七、结论

执行链接线完成：五段编排在合成数据上闭环（含幂等/门禁留痕/跳过语义），注册表 10 任务与手动补跑在真实 API 上验证通过；规范两条主要求（Signal 落库、订单过门禁）首次在运行态成立。
