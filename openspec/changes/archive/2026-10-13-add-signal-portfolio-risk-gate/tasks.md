# Tasks: add-signal-portfolio-risk-gate

> 回填（2026-10-13，T5）：全部完成；TDD 红→绿逐任务执行，实施证据见
> `tests/validation/2026-10-13-add-signal-portfolio-risk-gate-validation.md`。

## 1. Signal 契约
- [x] 1.1 signals.py：TradeDecision→Signal 转换 + 落库 + 合同单测（缺字段拒收）
  - 实现 `execution/signals.py`（G1 映射/审批门禁/strength/horizon 同源）+ `execution/store.py`
    （G2 幂等 upsert + hysteresis_events 表）；T1 提交 `bab99ad5`；单测 31 例（含 spec 场景
    「同日 approve+reject → 仅 approve 落库且 decision_id 指向它」）。
  - direction 枚举拒收消息逐字「direction 非法值: short」（sell+short 与显式 direction=short 两入口）。

## 2. 组合聚合
- [x] 2.1 portfolio.py：等权/强度加权两档 + 权重合计与上限勾稽单测
  - 实现 `execution/portfolio.py`（先单票 clip → 再总仓等比缩减，权重降序/symbol 升序，
    Σ ≤ total_cap + 1e-9）；T2 提交 `8353d0bf`；单测 17 例（spec 场景 96% → 80% 逐数手算）。
  - 约束常量 `MAX_SINGLE_POSITION=0.2` / `MAX_TOTAL_POSITION=0.8` 随 T2 落
    `screening/constraints.py`（LOT_SIZE 随 T3 落，见 3.1——两批提交均为单一来源追加）。

## 3. 风控门禁
- [x] 3.1 risk_gate.py 逐规则实现（复用 screening/constraints）+ 正反单测
  - 实现 `execution/risk_gate.py`（检查顺序 ①ST→②停牌→③涨跌停→④单票→⑤总仓→⑥T+1→⑦整手，
    首个命中即拒 + 检查轨迹）；`constraints.py` 追加 `LOT_SIZE=100`；`matching.py` 整手判定
    改为 import `LOT_SIZE`（单一来源，既有 matching 测试零修改全绿）；T3 提交 `be2c82e7`。
  - 静态扫描（AST）：无 llm/gateway/harness import、无动态导入后门；常量改动单点生效有行为测试。
- [x] 3.2 订单挂 decision_id/signal_id 证据链 + 单测
  - `OrderIntent` 必填 decision_id/signal_id；`GateDecision.detail` 回写证据链 + 检查轨迹
    （T3 单测 `test_order_requires_evidence_chain_fields` / `test_approved_detail_records_full_check_trace_in_order`）。
- [x] 3.3 滞回机制复用到信号生成前 + 抖动吸收单测
  - 实现 `execution/hysteresis.py`（共享纯函数；`nodes/risk.py::_evaluate_hysteresis` 改为
    适配层调用之，行为保持）+ `signals.generate_signal` 挂点（转换 → 判定 → 落库；
    「生成前」= 判定先于信号落库，与计划 G5「signal_from_decision 之后调用」同一挂点）；
    判定被应用时写 `hysteresis_events`；T4 提交 `51e0dff6`；单测 29 例。

## 4. 验证
- [x] 4.1 全量测试通过
  - `uv run pytest tests/execution tests/nodes -q` 全绿；`uv run pytest tests/ -q -m "not live"`
    全量回归见验证报告 §0（唯一失败为既有 llm env flake，归因见报告）。
- [x] 4.2 20 个边界场景人工核对，报告落 `tests/validation/`
  - 脚本 `tests/validation/2026-10-13-add-signal-portfolio-risk-gate-acceptance.py`
    （20/20 手算一致，独立判据链 + 逐例算术注记，JSON artifact 同目录）；
    报告 `tests/validation/2026-10-13-add-signal-portfolio-risk-gate-validation.md`。
- [x] 4.3 既有测试无修改通过（红线）
  - `git status`/`git diff --stat` 可证：既有测试文件零修改（本 change 仅新增 tests/execution/
    四文件 + tests/validation 两文件；`tests/conftest.py` 仅追加 EXECUTION_DB_PATH 运行时库隔离）。
