# Tasks: enforce-decision-contract-at-execution

- [ ] T1 信号契约扩展：`Signal` 增加 trigger_high/position_tier/exit_first_batch_ratio；`_decision_payload` 从 rationale_snapshot 解析；缺失退化 + 脏数据跳过留痕（单测：完整申报/无字段/档位名非百分比/snapshot 不可解析 四形态）
- [ ] T2 条件入场触发门：bar.close vs trigger_high/trigger_low，未触发零订单 + `TRIGGER_NOT_MET` 结构化留痕（单测：未触发/恰好触发/跌破型 trigger_low 三场景）
- [ ] T3 档位→权重映射：下沿锚 10/30/50%，单票/总仓 clip 不回归，缺档退化等权 + `no_position_tier` 留痕（单测：单 light/多档位并存/缺档/超限 clip 四场景）
- [ ] T4 exit 首批比例：首日卖 min(比例,持仓) 整手，首批状态持久化防重复，schedule 不可解析退化全额 + `schedule_unparsed` 留痕（单测：首批 70%/次日无触发不重复/整手边界 120 股/不可解析 四场景）
- [ ] T5 对账留痕：执行摘要 `plan_deviation` 段（entry 偏差/权重 vs 档位/分批进度）+ 台账只读回查（单测：偏差记录/零偏差/台账查询）
- [ ] T6 回归：执行链全量 pytest + ruff check + ruff format --check + mypy（基线 98 不新增）
- [ ] T7 生产实跑验证：下一执行日批 paper broker 核对触发门/档位权重/分批三行为，报告落 `tests/validation/`，#593 附记录核关
