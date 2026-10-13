# frontend delta: unify-ui-design-system

## ADDED Requirements

### Requirement: 涨跌语义令牌（A 股惯例）

设计令牌 SHALL 提供 `rise`（红系）/ `fall`（绿系）/ `flat`（灰）三个涨跌语义色，浅色与深色主题下语义一致；页面组件 SHALL 通过语义令牌引用涨跌色，SHALL NOT 直接书写 `text-red-*` / `text-green-*` 等裸色值表达涨跌。

#### Scenario: 涨跌色渲染

- **GIVEN** 一个数值 +2.31% 与一个数值 -1.05%
- **WHEN** 以数字规范组件渲染
- **THEN** 前者显示为「+2.31%」且使用 rise 令牌色（红），后者显示为「-1.05%」且使用 fall 令牌色（绿）
- **AND** 深色主题下语义不变

### Requirement: 状态徽章五态

系统 SHALL 提供统一的 StatusBadge 组件，状态枚举限五种：`active`（实心绿底）、`degraded`（琥珀描边）、`retired`（灰底）、`running`（蓝底 + 呼吸动画）、`failed`（红底）；任何页面的状态展示 SHALL 复用该组件，禁止自绘状态样式。

#### Scenario: 五态渲染

- **GIVEN** 分别以五种状态渲染 StatusBadge
- **WHEN** 截图对比
- **THEN** 五态配色与规范一致；running 态含呼吸动画，prefers-reduced-motion 时动画禁用

### Requirement: AI 生成内容标识

凡 LLM 生成的内容（L3 粗筛理由、语义记忆条款、分析结论等）SHALL 携带 AiBadge 紫系角标，hover 时展示提示「由模型生成，仅供参考」；确定性数据（数字、状态、时间）SHALL NOT 携带该角标。

#### Scenario: 角标展示

- **GIVEN** 选股页一行候选，含 L3 理由文本与综合分数字
- **WHEN** 渲染该行
- **THEN** L3 理由带 AiBadge 角标且 hover 出现规定提示文案；综合分数字无角标

### Requirement: 四种页面壳

系统 SHALL 提供 PageShellA（指标条+主图表+底部 Tab 表）、PageShellB（筛选栏+数据表+行展开详情）、PageShellC（左列表+右详情+确认/驳回）、PageShellD（批次列表+步骤级状态图标）四种布局壳组件；后续新增功能页面 SHALL 从四种壳中选择组装，禁止自由布局。

#### Scenario: 壳组件空态

- **GIVEN** PageShellB 的数据为空
- **WHEN** 渲染
- **THEN** 展示统一空态：图标 + 一句说明 + 主操作按钮
- **AND** 加载中展示 5 行骨架屏

### Requirement: 动效规范

表格行入场 SHALL 为 fade-in + translateY(8px)→0，stagger 间隔 30ms；Tab 切换内容区 SHALL 150ms 淡入；徽章状态变化 SHALL 200ms ease-out 颜色过渡；`prefers-reduced-motion` 时上述动画 SHALL 全部禁用。

#### Scenario: 动效降级

- **GIVEN** 系统偏好设置为 reduce motion
- **WHEN** 列表页渲染
- **THEN** 无任何入场/过渡动画，内容直接呈现

### Requirement: 本 change 零视觉变更红线

本 change SHALL NOT 改变任何现有页面的视觉与行为；打底前后对现有全部页面（浅色+深色）截图对比 SHALL 逐页零差异。

#### Scenario: 视觉基线对比

- **GIVEN** 打底前的全页面截图已存档于 `tests/validation/ui-baseline/`
- **WHEN** 本 change 完成后重新截图对比
- **THEN** 全部页面像素级无差异（允许的抗锯齿噪声容差 ≤ 0.1% 像素）
