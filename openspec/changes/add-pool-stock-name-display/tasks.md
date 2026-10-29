# Tasks: add-pool-stock-name-display

- [x] store 层：`selection_pool` 加列（DDL + 幂等 ALTER 迁移 + 重建序列同步），
      池行写入/读取往返含 `stock_name`，旧 schema 库 init 后历史行保留且读取返回 null
      （b8e89ce4；tests/screening 51 绿 + 真实生产库副本迁移演练 142 行零丢失）
- [x] pipeline 层：池行透传同日快照 name；缺名落空串 + warning 且批次不失败；
      主档与小票通道同契约验证（4c1f3e34；test_pipeline 27 绿）
- [x] API 层：`/api/screening/pool` 池行含 `stock_name`（历史行返回 null）
      （758a0888；test_api_screening 36 绿）
- [x] frontend：池行类型加字段，精选池表格新增「股票名称」列（缺名渲染 `—`），
      组件测试覆盖名称渲染与缺名退化（9ee2c95a；前端全量 970 绿 + tsc 零错）
- [x] E2E spec 已覆盖核心交互场景（选股页名称列断言 + stub seed 补名；隔离对
      screening-page 7/7 三跑全绿；全量两跑 0 failed；附 smoke.spec 端口参数化存量修复）
- [x] 人工验证报告已落 `tests/validation/2026-10-28-add-pool-stock-name-display-validation.md`
      （存量缺陷挂 issue #519，不阻塞本 delta）
