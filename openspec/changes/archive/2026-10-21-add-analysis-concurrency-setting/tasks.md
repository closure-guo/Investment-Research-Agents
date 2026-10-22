# Tasks: add-analysis-concurrency-setting

- [x] 并发设置模块（读写/校验/来源披露）+ 队列运行期调整上限（提高补位、降低不杀运行中）
- [x] `GET/PUT /api/settings/analysis-concurrency` 端点 + 保存后即时作用于队列
- [x] 默认并发上限由 2 调整为 10（主规范 delta 已记录）
- [x] 设置中心「会话管理」分区新增设置项（输入/范围校验/来源/已保存反馈）
- [x] 后端单测（默认值/优先级/损坏降级/非法拒绝/队列补位与降限语义）全绿
- [x] 前端 Vitest（展示/保存/非法输入）全绿
- [x] E2E `settings-concurrency.spec.ts` 覆盖读取-保存-刷新持久化，门禁全绿
- [x] 人工验证报告落 `tests/validation/`
- [x] OpenSpec sync + archive
