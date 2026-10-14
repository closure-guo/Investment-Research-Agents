# 变更包：体验页补齐 + 休眠链路接线（6 delta）

> 生成日期：2026-10-14。适用仓库：finance_analysis_agent。
> 来源两条线：
> (a) `unify-ui-design-system` proposal 预告的 **7 页面愿景**（选股/因子库/broker/记忆确认/门禁日志/日批运维/组合战绩）——选股页已交付、组合战绩以「虚拟 vs 仿真双曲线」retrofit 进战绩页交付，其余待补；
> (b) 2026-10-14 实施期排查发现的**三条休眠链路**：选股日批无触发器（`run_screening_batch` 零调用方）、执行链无驱动器（`generate_signal`/`aggregate_targets`/`check_order`/`PaperBroker.place_order` 零调用方）、记忆确认无入口（`confirm_clause` 仅 Python 函数）。

## 变更清单与依赖

| # | change id | 内容 | 依赖 |
|---|---|---|---|
| 1 | add-screening-batch-scheduling | 选股日批接线：ops 注册表新增 `screening_daily`（工作日 15:40，晚于快照 15:30）+ 统一运行历史与手动补跑；顺带修 eval-ops 规范「5 任务」漂移 | 无 |
| 2 | add-execution-chain-driver | 执行链驱动：决策 → Signal → 组合目标 → 风控门禁 → paper 下单（决策日次一交易日开盘口径）；门禁拒绝落 orders（rejected + 原因码）可回查；幂等 | 1（快照/交易日历） |
| 3 | add-memory-confirmation | 记忆确认入口：条款查询/确认/驳回 API + `/memory` 审批页（PageShellC） | 无 |
| 4 | add-factor-library-page | 因子库页：`/api/factors` 只读查询 + `/factors` 列表页（状态徽章/ICIR/闸门/衰减） | 无 |
| 5 | add-broker-console | 执行台账页：`/api/broker/*` 查询 + `/broker` 页（资金/持仓/订单/门禁拒绝） | 2（有数据才有内容） |
| 6 | add-daily-ops-page | 日批运维页：`/ops` 当日各批总览 + 失败原因 + 一键补跑 + 选股批次历史 | 1 |

## 建议实施顺序

```
阶段一（接线，先解锁数据）: 1 → 2
阶段二（入口与页）:        3 → 4 → 5 → 6
```

## 贯穿性纪律

- 页面一律复用既有设计系统（`PageShellA~D` / `StatusBadge` / `AiBadge` / 令牌），不新造样式；入口同源（侧边栏折叠图标 + 命令面板）。
- 新页面均需：前端 Vitest + Playwright E2E（stub）+ 真实栈人工验证（交互类变更红线）。
- 链路接线不改既有契约语义；既有测试只允许「契约合法扩容」型最小更新（计数类），逐条记录在验证报告。
- 每个 delta 完成后 `openspec archive` 再进入下一个；验证报告落 `tests/validation/`。
- 东财 IP 封禁（issue #325）未解除前，涉及实时数据的链路以「快照缺失 fail loud」为预期行为，不得伪造数据。
