# Tasks: add-backtest-leakage-controls

## 1. 复权口径统一

- [x] 1.1 核对生产结算链路与回测取数的实际复权口径（fetch_kline qfq 与 decision-outcome「后复权」spec 的漂移面）→ 核对结论记录
- [x] 1.2 结算/回测取数路径切换后复权序列 + 单测（分红除权样本的区间收益对照）；分析输入路径不动，核对结果如实披露
- [x] 1.3 切换切点登记（存量已结算行不重算，metrics.md §2 时间线行）

## 2. 结算同源核对

- [x] 2.1 确认 replay.py 已走 Δ2 共享判定函数（horizon/超额/±2% 带），无第二引擎残余 + 回归测试
- [x] 2.2 run_backtest 报告渲染同步判定语义描述

## 3. 泄漏探针

- [x] 3.1 探针模块：抽样标的 × 三层题目（方向/幅度桶/事件）构造、裸问条件调用、akshare 后复权真值 + 新闻真值离线构造
- [x] 3.2 记忆命中率计算（方向题主指标）+ 未知占比（拒答/不可解析/真值不可得）+ 单测
- [x] 3.3 报告探针披露段 + 超阈降级句式接线（阈值走预登记，默认 60%）

## 4. 干净窗口与批次准入

- [x] 4.1 run_backtest 干净窗口校验（T+20 可结算硬条件 + 探针披露软条件）；未过 → 报告自动标「通路验证」、剥离 skill 结论句 + 单测
- [x] 4.2 正式批预登记绑定校验（无预登记拒绝正式批身份，可降级通路验证）
- [x] 4.3 regime 覆盖披露与结论限定句式（干净窗口冲突条款）+ 报告模板固定段落（探针/窗口判定/regime/预登记指针）
- [x] 4.4 报告 status 头接线（report_status.py 校验 + docs/evals/README.md 索引渲染）+ 单测
- [x] 4.5 pilot-2023-shock.md 就地补 status 头 + 「通路验证 + 泄漏风险」标注（原文与数字保留）

## 5. 验证与收口

- [x] 5.1 全量测试绿 + ruff + mypy（触碰文件零新增）
- [x] 5.2 通路验证批实跑（小样本，含探针全流程）：窗口校验、探针披露、status 头、索引渲染端到端核对
- [x] 5.3 人工验证报告落 `tests/validation/`（探针题目与真值构造抽样人工复核）
- [x] 5.4 `openspec validate add-backtest-leakage-controls --strict` 通过；sync + archive 前置核对

## 依赖与顺序说明

- 前置：`update-decision-settlement-contract`（共享判定函数）、`add-outcome-profitability-protocol`（预登记/两句式/status 头机制）
- Owner 决策点：探针降级阈值（建议 60%）；首个正式批的窗口与样本量/预算（预登记时定，不在本 delta 实施内强跑）
