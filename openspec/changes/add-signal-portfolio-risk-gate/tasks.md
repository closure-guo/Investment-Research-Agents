# Tasks: add-signal-portfolio-risk-gate

## 1. Signal 契约
- [ ] 1.1 signals.py：TradeDecision→Signal 转换 + 落库 + 合同单测（缺字段拒收）

## 2. 组合聚合
- [ ] 2.1 portfolio.py：等权/强度加权两档 + 权重合计与上限勾稽单测

## 3. 风控门禁
- [ ] 3.1 risk_gate.py 逐规则实现（复用 screening/constraints）+ 正反单测
- [ ] 3.2 订单挂 decision_id/signal_id 证据链 + 单测
- [ ] 3.3 滞回机制复用到信号生成前 + 抖动吸收单测

## 4. 验证
- [ ] 4.1 全量测试通过
- [ ] 4.2 20 个边界场景人工核对，报告落 `tests/validation/`
- [ ] 4.3 既有测试无修改通过（红线）
