# Tasks: add-price-polarity-contract

- [x] TradeDecision 极性声明：sell_type→polarity 派生（exit→exit、short→short、buy/hold/watch→long/无），单测先红后绿（models.price_polarity，7 例）
- [x] validate.py 价格关系校验按分型极性（exit 不再被 short 规则拦截/镜像），含 600771 A 型 / 600029 B 型回归用例（test_sell_polarity_matrix；_plan_direction 删除，correct_prices 分派修正）
- [x] ingest.py 冻结 declared_prices 带 polarity 字段，存量 NULL 兼容（不追溯）
- [x] FM 记账口径更正回写：price_correction 结构化申报 → 终稿重写 + 修正来源标注（复用 price_level_corrected 语义），prompt 已 deploy（导入 1）
- [x] 前端 track-record：详情页止损/目标按声明极性解释展示（清仓位/失效位/兑现位）+ 存量行「口径未声明」标注；读侧 list/get_prediction 装饰 declared_polarity
- [x] E2E spec 已覆盖核心交互场景（track-record-polarity.spec.ts：exit 极性标注 + 存量未声明两态，seed 快照透传打通；详情页直达路由避开当前观点区同名行歧义）
- [ ] 人工验证报告落 tests/validation/（含 #606 存量 30 条展示辨识抽查；部署后执行）
