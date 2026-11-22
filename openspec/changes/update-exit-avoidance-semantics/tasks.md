# Tasks: update-exit-avoidance-semantics

- [ ] judgment.direction_for_action 加 sell_type 参数（sell+exit→neutral、sell+short→short、sell 未申报→short、其余不变），单测先红后绿
- [ ] ingest 落库传 sell_type；evals.backtest.replay 调用点核对（不传，行为不变）
- [ ] 回避判定链路回归：exit 观点落 neutral 后 avoidance_status/终态 avoidance/统计纳桶全链通
- [ ] spec MODIFIED（track-record 观点全量记录 + decision-outcome 决策落库）validate --strict 通过
- [ ] metrics.md §1.9 口径变更版本登记（历史读数按产出时版本解释）
- [ ] 全量回归 + ruff/mypy/format 三连
