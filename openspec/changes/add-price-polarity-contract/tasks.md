# Tasks: add-price-polarity-contract

- [ ] TradeDecision 极性声明：sell_type→polarity 派生（exit→exit、short→short、buy/hold/watch→long/无），单测先红后绿
- [ ] validate.py 价格关系校验按分型极性（exit 不再被 short 规则拦截/镜像），含 600771 A 型 / 600029 B 型回归用例
- [ ] ingest.py 冻结 declared_prices 带 polarity 字段，存量 NULL 兼容（不追溯）
- [ ] FM 记账口径更正回写：按文字口径重设 declared_prices + 修正来源标注，含「自曝作废仍原样入库」复现用例
- [ ] 前端 track-record：stop/target 按声明极性解释展示 + 存量行「口径未声明」标注
- [ ] E2E spec 已覆盖核心交互场景（观点日志口径标注、详情页极性解释）
- [ ] 人工验证报告落 tests/validation/（含 #606 存量 30 条展示辨识抽查）
