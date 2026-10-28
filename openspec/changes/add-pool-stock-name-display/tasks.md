# Tasks: add-pool-stock-name-display

- [ ] store 层：`selection_pool` 加列（DDL + 幂等 ALTER 迁移 + 重建序列同步），
      池行写入/读取往返含 `stock_name`，旧 schema 库 init 后历史行保留且读取返回 null
- [ ] pipeline 层：池行透传同日快照 name；缺名落空串 + warning 且批次不失败；
      主档与小票通道同契约验证
- [ ] API 层：`/api/screening/pool` 池行含 `stock_name`（历史行返回 null）
- [ ] frontend：池行类型加字段，精选池表格新增「股票名称」列（缺名渲染 `—`），
      组件测试覆盖名称渲染与缺名退化
- [ ] E2E spec 已覆盖核心交互场景（选股页名称列断言 + stub seed 补名）
- [ ] 人工验证报告已落 `tests/validation/`
