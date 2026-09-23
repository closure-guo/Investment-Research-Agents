# Tasks: update-decision-settlement-contract

## 1. Schema 与配置

- [x] 1.1 配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`（默认 20，env 可覆盖）+ 单测
- [x] 1.2 predictions 迁移：加 `avoidance_status` 与 `settle_entry_price` 列（可空、幂等迁移）+ 迁移测试；存量行不回填
- [x] 1.3 `track-record-versioning` 口径切点登记（默认窗口 252→20 分段，存量不重算）

## 2. ingest 改造（决策落库契约）

- [x] 2.1 失败测试先行：horizon_days 显式写入配置默认值；参考价语义保持现行为（quote 优先 / K 线兜底），参考价不可得 → 存档 + WARN + 状态 open（判定不依赖参考价）
- [x] 2.2 rationale_snapshot 富化：冻结 TradeDecision 申报 entry/stop/target（与 action/FM decision/reasoning 并列）；快照哈希语义不变

## 3. 判定与统计

- [x] 3.1 `judgment.py` 抽取共享纯函数（观点行 + 日 K → 判定结果），**含派生入场价**（归属日收盘 / 收盘后决策取次一交易日 / 非交易日顺延；收盘切分常量 15:00），生产 job 与回测共同调用 + 单测
- [x] 3.2 neutral 回避判定分支：±2% 带（沿用全局中性带配置）→ avoidance_win/loss/neutral 写 `avoidance_status`，不写 resolved_* + 单测
- [x] 3.3 统计函数：胜率仅计 long/short；回避正确率独立返回（分母 = avoidance_win+loss，展示门槛 <10 同胜率）+ 单测
- [x] 3.4 Score 上报：track-record 判定链路补齐 long/short 的 decision_hit/return/excess 上报（Langfuse 缺失/离线仅 WARN）；neutral 与旧链路 hold/watch 排除 + 单测
- [x] 3.5 停牌顺延/unresolvable/superseded 既有语义在共享函数中保持不回归（对照既有测试）
- [x] 3.6 前端 PredictionStatus 联合类型与状态标签/颜色映射补 `avoidance`（防空白标签）+ 前端测试绿

## 4. 结算引擎同源

- [x] 4.1 `evals/backtest/replay.py` 结算调用切换到 3.1 共享函数（派生入场价与回测现有 `_close_on_or_before` 语义对齐：决策归属日收盘）+ 回测冒烟（pilot 材料复跑通路验证）
- [x] 4.2 `outcome/settle.py` 标 deprecated（模块 docstring + 调用时 WARN 日志）；确认 `GET /api/decisions*` 只读 API 与存量 decision_log 数据不受影响；`evals/golden/gates.py` 保留自有语义不动（独立登记）
- [x] 4.3 全仓 grep 确认 settle.py 残余调用方并逐一处置（切换或保留只读理由记录）

## 5. 验证与收口

- [x] 5.1 全量测试绿 + ruff + mypy（触碰文件零新增）
- [x] 5.2 真实链路验证：一次 deep 分析 → predictions 行核对（horizon=20、参考价口径、申报价在快照内）；一次判定任务离线驱动核对（`settle_entry_price` 派生正确 + neutral 回避判定路径 + long/short Score 上报调用）
- [x] 5.3 人工验证报告落 `tests/validation/`：含战绩页展示核对（胜率语义变化不失真、样本积累中状态正常、**avoidance 行标签渲染正常**）
- [x] 5.4 `metrics.md` §2 时间线追加切点行（引用 Δ1 §1.8 口径）
- [ ] 5.5 `openspec validate update-decision-settlement-contract --strict` 通过；sync + archive 前置核对（tasks 全勾 + verification + 人工验证报告）
  - 注：`openspec validate ... --strict` 已通过（Δ2T7 收口实测）；**sync/archive 待读数腿（Δ3 `add-forward-paper-trading-cohort` / Δ4 `add-backtest-leakage-controls`）落地后统一执行**。
