# Design: add-factor-library-page

## Context

`factors/library.py` 已有完整只读面：`list_factors`（含状态/来源/IC 汇总）、`list_active_factors`、`list_events`（闸门/状态变更事件）、`list_ic_series`（IC 时间序列）。本 delta 只做「暴露 + 呈现」，不改因子域逻辑。

页面形态：PageShellB（筛选栏 + 数据表 + 行展开详情）——设计系统四壳之一，正是列表类页面的目标场景。

## Decisions

- **API 只读、零复制**：两个端点只做查询参数校验 + 调既有函数 + 空库返回空数组（不 500）。
- **列表列**：名称 / 表达式（截断展示，展开看全）/ 状态（StatusBadge：active→`active`、retired→`retired`）/ 来源（builtin 或 llm→AiBadge）/ ICIR / 最近闸门结论 / 衰减标记（degraded→StatusBadge `degraded`）。
- **行展开详情**：完整 DSL 表达式、IC 序列（近期若干点，纯数字列表或小图）、事件历史（准入/降级/查重事件与时间）。
- **筛选栏**：状态（全部/active/retired）+ 来源（全部/builtin/llm）。
- **空态**：库为空时如实展示「因子库为空——等待 builtin 注册或提案通过五道闸」，MUST NOT 冒充加载中。
- **入口同源**：侧边栏折叠图标 + 命令面板（与选股池/记忆页同模式）。

## Alternatives Considered

- **并入评估运维页**：放弃——因子库是「资产目录」型浏览（长期看库），与运维控制台（跑批操作）语义不同；7 页面愿景也将其独立。
- **列表带排序/搜索**：暂缓——当前因子量级（十位级）不需要；先按 ICIR 降序固定排序。
- **展示因子 IC 曲线大图**：暂缓——行展开先给数值序列；图表化留给后续迭代（避免首版引入图表口径问题）。

## Risks

- 生产库可能长期为空（builtin 注册与提案管线尚未在真实数据上跑通）：空态如实即可，非缺陷。
- 因子名/表达式可能含长文本：表格截断 + 展开全量，防溢出（复用设计系统数字规范与截断惯例）。
