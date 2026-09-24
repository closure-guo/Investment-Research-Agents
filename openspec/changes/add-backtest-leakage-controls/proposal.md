# Proposal: add-backtest-leakage-controls

## Why

回测是 outcome 读数腿中快的一条（样本可批量构造，不必等日历），但它有一个 forward 腿没有的威胁：**LLM 参数化记忆泄漏**——as-of 快照能截断数据，截不断模型对历史走势的记忆。唯一的试跑 pilot-2023-shock 决策日 2023-01-05，几乎必然落在模型训练语料内，其读数只能作通路验证。若未来批次拿深历史回测下「赚钱能力」结论，等于把「记忆」当「skill」。同时存在两处规范级债务：① `decision-backtest`「绩效指标与基线对比」写**前复权**日 K，而 `decision-outcome`「A 股异常结算规则」写**后复权**——两个主规范互相矛盾（前复权追溯重基还会破坏 as-of 保真：历史价格被今天的复权因子改写）；② 回测批次无预登记绑定、无报告生命周期 status 头，与因果消融 v2 已验证的纪律不对齐。

## What Changes

- **干净窗口判定**（MODIFIED「历史离线回放」）：产出 skill 结论的批次，决策日 SHALL 同时满足——距跑批日已完成主评估窗口结算（T+20 可结算）且通过泄漏探针披露；不满足的批次报告 SHALL 标「通路验证」定位，SHALL NOT 产出 skill 结论句。回放结算改为与生产 track-record 判定规则**同源实现**（Δ2 共享判定函数），不再保留第二套引擎
- **知识泄漏探针**（ADDED）：正式批前对候选窗口抽样标的执行记忆探测（问模型决策日后 N 日的实际涨跌方向/幅度桶/重大事件，对照 akshare 真值），计算**记忆命中率**随报告披露；命中率超预登记阈值（建议 60%，owner 终裁）→ 该批结论 SHALL 降级为「泄漏污染下的上界证据」，SHALL NOT 单独作为赚钱能力主张；探针失败（拒答/不可解析）计未知占比并披露
- **复权口径统一**（MODIFIED「绩效指标与基线对比」）：结算与回测的区间收益计算 SHALL 用**后复权**日 K（对齐 `decision-outcome`，消除两 spec 冲突，恢复 as-of 保真）；分析输入路径的复权口径不在本 delta 内（另行核对披露）
- **regime 分层与干净窗口的冲突条款**（MODIFIED「分层市场状态抽样」）：干净窗口优先；窗口内无法满足三 regime 覆盖时，报告 SHALL 披露 regime 覆盖范围，结论 SHALL 限定于已覆盖 regime、SHALL NOT 外推
- **批次预登记与报告生命周期**（ADDED）：正式回测批 SHALL 持有效预登记（Δ1 机制 `OUTCOME_REQUIRED_FIELDS`：主指标/MDE/决策阈值/样本量依据/停止规则/成本分型/泄漏控制）；报告落 `evals/backtest/results/` 并带 status 头（active/superseded-by，复用 `report_status.py` 校验）；存量 pilot-2023-shock.md 补 status 头与「深历史 = 泄漏风险」标注

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `decision-backtest`: 「历史离线回放」（干净窗口判定 + 结算同源）、「绩效指标与基线对比」（后复权统一 + 同源判定实现）、「分层市场状态抽样」（干净窗口冲突条款）；新增「知识泄漏探针」「回测批次预登记与报告生命周期」两条需求

## Impact

- 代码：`evals/backtest/`（run_backtest 干净窗口校验、replay 结算调用核对 Δ2 同源切换、探针模块新增、报告渲染加探针披露段与 status 头）；`data_snapshot.py`（结算用后复权序列的获取路径）；akshare_client 复权参数（结算/回测取数路径；**生产结算链路现用 qfq 与否需核对**——decision-outcome spec 已要求后复权，若实现不符属存量漂移，本 delta 一并修正结算取数路径）
- 存量产物：`evals/backtest/results/pilot-2023-shock.md` 补 status 头 + 泄漏风险标注（原文保留，就地标注，不改数字）
- 成本：探针为小头（每批 ≈ 标的数 × 3–5 问，几十次调用）；正式批主体成本 = 回放（≈166k tokens/次 × 样本量 × 3 重复），**样本量与预算为 owner 决策点**（由 Δ1 预登记的 MDE 反算给下限）
- 依赖：实现顺序在 `update-decision-settlement-contract`（同源判定函数）与 `add-outcome-profitability-protocol`（预登记/两句式/status 头机制）之后；与 `add-forward-paper-trading-cohort` 可并行
