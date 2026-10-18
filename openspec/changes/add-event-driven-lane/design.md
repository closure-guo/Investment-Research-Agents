# Design: add-event-driven-lane

## 数据流

```
日批 A：预告抓取（公开数据源，幂等 upsert，公告日=可得时点）
  → 日批 B：扫描未分诊记录 → LLM 分诊（event_triage.md prompt）
    → verdict=outperform → 事件观察清单（落库 + 端点 + 选股页区块）
      → 发起深度分析（复用 screening/queue.py，source=event）→ 关联 session
    → outperform 候选 → event_journal 记账（T+1 开盘口径，horizon 默认 5）
      → 每日结算 job → 事件族读数 → strategy-scorecards（family=event）
```

## 数据源与字段（实现期以 akshare 当前可用接口为准，字段映射表落实现）

预告字段：`代码 / 名称 / 报告期 / 预告类型 / 净利润变动幅度下限 / 上限 / 公告日期 / 来源`。
预告类型枚举以数据源取值原样落库（不做二次归类）；来源标识进唯一键。

## 存储（`data/events.db`，`EVENTS_DB_PATH` 可覆盖）

- `earnings_forecasts`：预告记录（唯一键：代码+报告期+来源记录号；含 `available_at`=公告日期）
- `event_triage`：分诊结果（预告外键、verdict、reason、evidence JSON、模型、prompt 指纹、分诊时间）
- `event_watchlist`：观察清单条目（分诊外键、状态、关联 session_id、发起时间）
- `event_journal`：记账（起算日/起算价/顺延原因、horizon、结算日/结算价/持有期收益、状态）

## 分诊输入预算

预告要素全量 + 标的快照要素（价量类：close / pct_chg / amount）≤ 12 字段；预算常量集中定义，禁止散落。

## 分诊合同与降级

`{verdict: outperform|in_line|risk, reason: ≤50字, evidence: [{field, value}]}`；解析失败按非法输出拒收 → 降级 `in_line` + trace warning。prompt 权威源 `src/finance_agent/prompts/event_triage.md`（git 跟踪），发布 `uv run python scripts/deploy_prompts.py`，门禁拒跑时按既有纪律处理。

## 记账口径

- 起算：次一交易日开盘价（与执行链同口径）；停牌顺延至下一可成交日并留痕，涨停板挡买（跌停可买，买入侧门禁语义）。
- horizon：默认 5 个交易日（可配置）；到期结算 = 结算日收盘价 / 起算价 − 1。
- 参考价取数复用既有读价通道；失败保持未结算 + 留痕重试。

## 调度与运维

- 三个新 job 进 `outcome/scheduler.py`：`earnings_fetch_daily`（抓取）、`earnings_triage_daily`（分诊+清单）、`event_settle_daily`（记账+结算），排在快照 job 之后。
- 幂等：重跑零重复；失败留痕进 ops 运行历史。

## 与变更 1/2 的接口

- scorecards 注册 `family=event`（注册表扩展即验证「注册表驱动」契约；不触碰计算核心）。
- 事件线 v1 **不接** regime 系数、**不进**执行链目标（纯观察/研究通道，避免与 paper 台账双重记账）。

## 已知边界 / 非目标

- v1 不做事件专用基准（只记收益）、不做转债条款事件（独立变更）、不做实时盘中扫描（日频）。
- 预告抓取首跑为全量回填，需遵守时点截断（`available_at ≤ 判断日`）。
