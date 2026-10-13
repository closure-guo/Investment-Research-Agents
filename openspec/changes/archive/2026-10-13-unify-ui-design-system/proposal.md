# Proposal: unify-ui-design-system

## Why

- 即将新增的 7 个页面（选股/因子库/broker/记忆确认/门禁日志/日批运维/组合战绩）若各自设计布局与配色，必然撞脸失控；需要一套统一令牌与页面模式先行收敛。
- 现有设计系统缺少三类关键语义：涨跌色彩约定（A 股红涨绿跌，coding agent 训练数据多为国际惯例绿涨红跌，不写死必跑偏）、全局状态徽章体系、AI 生成内容的视觉标识。
- 历史教训（refactor-ui-design-system 归档经验）：设计令牌打底 ≠ 界面 redesign——本 change 只建地基不改脸，预期必须在任务书写死。

## What Changes

- 新增设计令牌：涨跌语义色（rise 红 / fall 绿 / flat 灰，A 股惯例）、状态徽章五态（active/degraded/retired/running/failed）、AI 标识色（紫系）、数字规范（等宽 tabular-nums / 带符号涨跌 / 千分位 / 百分比两位小数）；浅色/深色双主题遵循同一语义规则。
- 新增四种页面壳组件 `PageShellA/B/C/D`：A 工作台页（指标条+主图表+底部 Tab 表）、B 列表页（筛选栏+数据表+行展开）、C 审批队列（左列表+右详情+确认/驳回）、D 运行记录页（批次列表+步骤级状态图标）。
- 新增统一组件约定：表格（TanStack Table + 数值右对齐等宽 + 统一空态）、AI 角标组件（紫系角标 + hover 提示「由模型生成，仅供参考」）、图表规范（ECharts：主色实线/基准灰虚线/损耗半透明填充/事件标记图标集）、骨架屏（列表页 5 行 / 工作台页指标方块+图表矩形）。
- 动效参数写死：行入场 fade-in + translateY(8px)→0、stagger 30ms；Tab 内容区 150ms 淡入；徽章变色 200ms ease-out；全部 `prefers-reduced-motion` 降级为无动画。
- 视觉基线：打底前对现有全部页面全局截图存档 `tests/validation/ui-baseline/`，本 change 完成后逐页对比零变化。

非目标（Out of scope）：

- **不改任何现有页面的视觉与行为**（含下载中心、战绩仪表盘、设置面板）——本 change 只新增令牌与壳组件。
- 不实现任何新页面（/screening 等在各自功能 delta 中按本规范实现）。
- 不引入新 UI 依赖（沿用 shadcn/ui + Tailwind + ECharts + TanStack Table）。

## Capabilities

### Modified Capabilities
- `frontend`: 新增设计令牌、四种页面壳组件、全局组件约定与动效规范的契约；明确本 change 对现有页面零视觉变更的约束。

## Impact

- **前端**：`frontend/src/design/tokens.ts`（新增）、`frontend/src/components/shells/PageShellA~D.tsx`（新增）、`components/AiBadge.tsx`、`components/StatusBadge.tsx`（新增）。
- **测试**：令牌快照单测；四壳组件渲染单测（含空态/加载态）；视觉基线对比验证。
- **验证**：打底前后全页面截图逐页对比，报告落 `tests/validation/`。
