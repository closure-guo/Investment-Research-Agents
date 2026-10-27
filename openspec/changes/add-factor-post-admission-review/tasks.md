# Tasks: add-factor-post-admission-review

- [ ] 复核判据实现：入库后全样本 ICIR + 账本全局反事实 p_adj（复用账本只读读数函数，零新增校正实现），失败测试先行
- [ ] 月批尾部挂载复核步骤 + 摘要 `{reviewed, degraded, skipped}`，异常上抛 fail loud
- [ ] 处置走既有 `active→degraded` 事件路径（`reason=POST_ADMISSION_REVIEW`，detail 审计块）
- [ ] 单测覆盖六个 spec 场景（水位降级/反事实降级/样本不足跳过/通过无动作/摘要/账本边界不动）
- [ ] 生产首月实跑验证：月批 job 摘要读数 + 降级名单逐条人工归因（incident 026 纪律）+ 降级因子次日退出打分核验
