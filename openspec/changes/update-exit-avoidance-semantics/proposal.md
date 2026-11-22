# Proposal: update-exit-avoidance-semantics

## Why

issue #606 影响分析第 3 条：`sell_type=exit`（清仓离场）观点现按 `direction=short` 落库并按 T+20 区间超额做空符号结算——**执行叙事成功与记分卡得分系统性反向**（深南电路实证：反弹到 240 完成减仓 = 退出执行最优，记分卡记 short −13.6%）。exit 叙事的价格语义（反弹减仓带在上方）与做空（看跌获益）的收益方向相反，horizon 做空结算对 exit 观点是系统性误判，且每夜 ~30 条持续污染 outcome 评估。

回避判定机制（neutral 观点：标的跑输 = 回避正确）与离场语义**数学同构**：离场后标的跑输 = 离场正确。复用全套回避统计（avoidance_win/loss/neutral 三态、±2% 中性带、superseded/unresolvable 处置），零新统计代码。

**拍板记录**：维护者授权 agent 代决（2026-11-22「你来」）。选项对比：B（新增 `direction="exit"` 独立枚举）语义显式但枚举贯穿全部统计/展示消费方，纯成本；A（exit→neutral + 回避判定）零新机制，已知代价 = 观点日志方向列对 exit 决策显示「中性」（原始 action 经 rationale_snapshot 可辨析，详情页可见）——选 A。

## What Changes

- `direction_for_action(action, sell_type=None)`：sell + `sell_type=exit` → **neutral**（回避判定语义）；sell + `sell_type=short` → short（不变）；sell 未申报 → 维持 short（历史形态/回测腿语义不变）；buy/hold/watch 不变
- ingest 落库传 `sell_type`；evals.backtest.replay 不传（回测腿语义独立，回测 sell 即做空观点）
- 统计消费方零改动：segments/significance/scorecards 的 avoidance 分支按 `direction=="neutral"` 既有条件自动纳入 exit 观点
- **历史读数不追溯**：存量 exit 观点已按 short 结算的读数保持原样（§1.9 预登记纪律——口径变更以本 delta 版本登记，历史读数按产出时版本解释）；切换时点后的 exit 观点走回避判定
- 已知披露：观点日志方向列 exit 决策显示「中性」+ 回避状态徽标（与 watch/hold 同渲染），原始 sell 语义经详情页 rationale_snapshot 辨析

**非目标**：settle.py/旧结算引擎（已删）；回测腿方向语义；exit 叙事中「反弹减仓带执行质量」的日内定价（horizon 判定的既有局限，对 long 同样存在）。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: track-record（观点全量记录 direction 映射 + 判定规则回避语义覆盖 exit）、decision-outcome（决策落库 direction 映射）

## Impact

- `src/finance_agent/outcome/track_record/judgment.py`：direction_for_action 加 sell_type 参数
- `src/finance_agent/outcome/track_record/ingest.py`：落库传 sell_type
- `evals/backtest/replay.py`：调用点核对（不传 sell_type，行为不变）
- 前端零改动（neutral/avoidance 渲染既有）
- 统计影响：回避桶样本扩大（exit 观点并入），胜率/IC 分母口径不变（日主规则）
