# Design: unify-ui-design-system

## 决策

1. **扩展现有设计系统而非重建**：Kimi 式浅色/深色体系已经过 `refactor-ui-design-system` 验证，本 change 只增补语义令牌（涨跌/状态/AI 标识），被否掉的方案是引入第三方 dashboard 模板——会撞脸且破坏设计令牌一致性。
2. **页面模式收敛为四种壳**：新页面只允许从 PageShellA~D 组装，禁止自由布局；被否掉的方案是每页独立设计——7 个新页面必然发散。
3. **红涨绿跌写死为令牌名而非裸色值**：用 `rise/fall/flat` 语义名，禁止页面直接写 `text-red-*`/`text-green-*`，从机制上防国际惯例跑偏。
4. **打底与迁移分离**：本 change 只交付令牌+壳+组件且验证现有页面零变化；各功能页在各自 delta 中引用壳组件——避免一个 delta 既改地基又改脸，无法验收。

## 风险

- **「打底后 UI 没变化」的预期落差**：任务书与验收标准中已写明零视觉变更是本 change 的正确结果 → 报告口径以截图对比为准。
- **新页面 delta 不遵守壳规范**：在功能 delta 的验收中检查「页面是否使用 PageShellX 组装」→ 本 change 的 spec 中已写为 SHALL。
- **动效被 agent 省略**：动效参数已写入 spec 的 Scenario（具体数值），验收时逐条核对。
