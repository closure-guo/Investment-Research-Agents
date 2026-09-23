# Tasks: update-decision-settlement-contract

## 1. Schema 与配置

- [ ] 1.1 配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`（默认 20，env 可覆盖）+ 单测
- [ ] 1.2 predictions 迁移：加 `avoidance_status` 列（可空、幂等迁移）+ 迁移测试；存量行不回填
- [ ] 1.3 `track-record-versioning` 口径切点登记（默认窗口 252→20 分段，存量不重算）

## 2. ingest 改造（决策落库契约）

- [ ] 2.1 失败测试先行：entry_price = 决策日收盘（收盘后决策 → 次一交易日收盘）；horizon_days 显式写入配置默认值
- [ ] 2.2 rationale_snapshot 富化：冻结 TradeDecision 申报 entry/stop/target（与 action/FM decision/reasoning 并列）；快照哈希语义不变
- [ ] 2.3 「无可靠入场价不计分」路径适配（收盘口径下 quote 不再参与，K 线不可得 → 存档不计分 + WARN）

## 3. 判定与统计

- [ ] 3.1 `judgment.py` 抽取共享纯函数（观点行 + 日 K → 判定结果），生产 job 与回测可共同调用 + 单测
- [ ] 3.2 neutral 回避判定分支：±2% 带（沿用全局中性带配置）→ avoidance_win/loss/neutral 写 `avoidance_status`，不写 resolved_* + 单测
- [ ] 3.3 统计函数：胜率仅计 long/short；回避正确率独立返回（分母 = avoidance_win+loss，展示门槛 <10 同胜率）+ 单测
- [ ] 3.4 Score 上报收窄：中性观点不上报 decision_hit/return/excess；long/short 语义不变 + 单测
- [ ] 3.5 停牌顺延/unresolvable/superseded 既有语义在共享函数中保持不回归（对照既有测试）

## 4. 结算引擎同源

- [ ] 4.1 `evals/backtest/replay.py` 结算调用切换到 3.1 共享函数（决策日收盘入场语义不变）+ 回测冒烟（pilot 材料复跑通路验证）
- [ ] 4.2 `outcome/settle.py` 标 deprecated（模块 docstring + 调用时 WARN 日志）；确认 `GET /api/decisions*` 只读 API 与存量 decision_log 数据不受影响
- [ ] 4.3 全仓 grep 确认 settle.py 残余调用方并逐一处置（切换或保留只读理由记录）

## 5. 验证与收口

- [ ] 5.1 全量测试绿 + ruff + mypy（触碰文件零新增）
- [ ] 5.2 真实链路验证：一次 deep 分析 → predictions 行核对（horizon=20、entry=决策日收盘、申报价在快照内）；一次 neutral 观点的回避判定路径核对（可用历史 K 线离线驱动）
- [ ] 5.3 人工验证报告落 `tests/validation/`：含战绩页展示核对（胜率语义变化不失真、样本积累中状态正常）
- [ ] 5.4 `metrics.md` §2 时间线追加切点行（引用 Δ1 §1.8 口径）
- [ ] 5.5 `openspec validate update-decision-settlement-contract --strict` 通过；sync + archive 前置核对（tasks 全勾 + verification + 人工验证报告）
