# Tasks: fix-track-record-holdings-semantics

> 提案阶段 tasks 不勾选——owner 拍板后才进入实施。

- [ ] `predictions` 增加 `holdings_state` 字段（held/not_held/unreported，默认 unreported）
- [ ] `direction_for_action` 按 holdings_state 分流；`avoidance` 结算语义（收益 0，机会成本单列）
- [ ] `unreported` 退出主账本，单独打标（存量 56 条标 `legacy_short_mapping`，不回填）
- [ ] 战绩页 / track-record API 对 avoidance 与 short 分流展示
- [ ] 管线采集受众持仓申报写入 holdings_state
- [ ] 人工验证报告落 tests/validation/（主账本样本量变化与净值分段展示）
