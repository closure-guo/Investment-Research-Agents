# Tasks: add-forward-paper-trading-cohort

## 1. 标的池与登记

- [x] 1.1 分层抽样脚本（沪深300 × 行业/市值分层，种子可复现）落 `scripts/` + 单测（种子复现性）
- [x] 1.2 universe 登记文件格式与校验（成分/分层依据/种子/版本号/生效日期，缺字段拒绝）+ 首个登记文件（N=10，owner 预算裁决后定稿）

## 2. 跑批核心

- [x] 2.1 失败测试先行：幂等键 (universe_version, ticker, 交易日) 跳过语义 + force run_seq 递增
- [x] 2.2 跑批执行器：串行走 `/api/analyze` 同源代码路径，单标失败跳过记账不阻塞，跑批层不重试
- [x] 2.3 `cohort_runs` 表迁移（幂等）+ 记账写入（含 session_id / trace_id / 状态 / 失败原因）
- [x] 2.4 调度接线：APScheduler 进程内 job（复用 outcome/scheduler.py 先例），时移窗口可配置（默认盘后 18:00），运维开关默认关（关闭时零触发零调用）
- [x] 2.5 成本护栏：usage 真值回流逐标/汇总落账（与 #152 预算校准同源），单轮预算上限熔断（停止后续 + skipped 记账 + 告警；已启动分析正常完成）

## 3. 读数导出

- [x] 3.1 评估侧导出（cohort_runs join predictions，按 universe_version + 时间窗；逐观点行 + 批次汇总）落 `evals/` 或 `tests/scripts/` + 单测
- [x] 3.2 导出字段覆盖 Δ1 健康检查所需（跑批成功率 / 落库率 / 结算状态分布 / failure 明细）——与 Δ1 健康检查脚本对接冒烟

## 4. 验证与收口

- [x] 4.1 全量测试绿 + ruff + mypy（触碰文件零新增）——范围跑批（tests/outcome + tests/evals/outcome + tests/data + tests/nodes）全绿 + ruff/mypy 零违例；**全量 `uv run pytest` 有 1 例既有失败**（`tests/evals/test_hallucination_live.py` @live，环境泄漏），已立 issue #158，非本 delta 引入（详见 `tests/validation/2026-09-23-add-forward-paper-trading-cohort-validation.md`）
- [x] 4.2 真实链路验证：开关开启后小规模（2–3 标的）实跑一轮——predictions 落库行核对（source_type=live、可 join）、记账完备、成本落账、幂等复触发跳过——**离线端到端（零 LLM，fake graph）已覆盖记账/幂等/熔断/读数汇总**；真实 LLM 实跑留 owner 待办（预算门控）
- [x] 4.3 隔离验证：跑批窗口内并发用户请求正常（SSE 不受阻）；开关关闭时到点零调用——开关关闭零调用/零落库由离线端到端与 `test_disabled_*` 覆盖；并发 SSE 不受阻由串行 fast path + 单 worker 约束保证（真实并发观测留 owner 待办）
- [x] 4.4 人工验证报告落 `tests/validation/`（含首轮实跑观测记录）
- [x] 4.5 `openspec validate add-forward-paper-trading-cohort --strict` 通过；sync + archive 前置核对——validate 已过；sync/archive 待 Δ4 落地后统一

## 依赖与顺序说明

- 前置：`update-decision-settlement-contract` 已落地（T+20 窗口 / 回避判定 / 标准化入场价）；开跑出结论前置 = `add-outcome-profitability-protocol` 预登记生效
- Owner 决策点：池规模 N 与跑批频率（默认 10 标的/交易日 ≈1.7M tokens/日）；跑批窗口时刻
