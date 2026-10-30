# Tasks: remove-standalone-stop-button

- [ ] 单测先行（红）：运行态断言页面无「停止生成」独立按钮、composer-stop 为唯一停止入口；终态后停止入口恢复为发送键
- [ ] 删除 App.tsx 页面级独立「停止生成」按钮容器，单测转绿
- [ ] E2E 定位符迁移：message-actions.spec.ts / refresh-resume-accept.spec.ts 改用 composer-stop，断言语义不变
- [ ] E2E 门禁通过（隔离端口对 stub 套件 + message-actions 所属 timeline 套件）
- [ ] 人工验证报告落 tests/validation/
