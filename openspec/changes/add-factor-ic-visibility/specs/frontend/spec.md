## MODIFIED Requirements

### Requirement: 因子库页

系统 SHALL 提供 `/factors` 路由页，采用 PageShellB（筛选栏 + 数据表 + 行展开详情）形态：筛选栏 SHALL 支持状态（全部 / active / retired）与来源（全部 / builtin / llm）过滤；表格列 SHALL 含 名称、表达式（截断）、状态、来源、ICIR（旁注 IC 样本数 `n`；`n` 为 0 时 ICIR 显示「—」）、衰减标记；状态 SHALL 使用 StatusBadge，`llm` 来源 SHALL 携带 AiBadge；行展开 SHALL 展示完整表达式、**IC 序列折线图**（横轴交易日、纵轴 RankIC，最多近 60 点）、IC 就绪度（`样本 n/60` 与 `打分门槛 n/10`）、预计恢复因子打分日期、风格暴露与事件历史；样本数低于打分门槛时 SHALL 明确展示「样本不足（n/10）」而非只给一个 ICIR 数字；`expected_factor_mode_date` 为空时 SHALL 展示「已达标 / 暂无 IC 记录」两态之一而不得臆造日期；空库时 SHALL 如实展示空态文案。页面入口 SHALL 同源提供于侧边栏与命令面板。

(Previously: 表格 ICIR 列无样本数旁注；行展开只以一行文本罗列 IC 序列，不展示折线图、样本进度与预计恢复日期。)

#### Scenario: 筛选与徽章

- **GIVEN** 库中含 active/llm 与 retired/builtin 两条因子
- **WHEN** 选择来源筛选 `llm`
- **THEN** 仅显示 active/llm 因子，其来源带 AiBadge、状态为 StatusBadge active
- **AND** retired 因子被过滤

#### Scenario: 行展开详情

- **GIVEN** 列表中的一条因子
- **WHEN** 展开该行
- **THEN** 展示完整 DSL 表达式、IC 序列折线图、样本与门槛进度、预计恢复日期与事件历史（含时间与事件类型）

#### Scenario: IC 折线图与就绪度

- **GIVEN** 某因子有 4 条 IC 记录，最新日期 2026-10-21，门槛 10
- **WHEN** 展开该行
- **THEN** 折线图渲染 4 个点（无数据点时不渲染伪点，展示空态说明）
- **AND** 展示「样本 4/60」「打分门槛 4/10」「预计 2026-11-02 恢复因子打分（按工作日推算）」

#### Scenario: 样本不足明确披露

- **GIVEN** 某因子有 3 条 IC 记录，门槛 10
- **WHEN** 展开该行
- **THEN** 展示「样本不足（3/10）」标注
- **AND** MUST NOT 只展示一个无上下文的 ICIR 数字

### Requirement: 选股页

系统 SHALL 提供 `/screening` 路由页，展示最近成功批次的候选池表格，列含：标的、综合分、连续在榜天数、L3 verdict 与 reason、批次日期；综合分列 SHALL 支持 hover 展示因子贡献分解（factor_id 与 contribution）；页面 SHALL 提供「对精选池跑深度分析」按钮，触发后复用现有 SSE 管线时间轴展示各标的分析进度；选择模式为 `fallback` 的批次 SHALL 在页面展示降级披露横幅（明示「因子库无可打分因子、按近 20 日日均成交额排序、非因子信号」），且综合分列 SHALL 切换为「近 20 日日均成交额」语义呈现（标题与数值口径同步切换）。页面 SHALL 展示「因子信号状态」卡片：逐 active 因子给出 IC 样本进度（`n/10`）、是否达标与预计达标日，并给出批次级 `selection_mode`、降级原因与预计恢复因子打分日期；`factor` 模式且全部因子达标时 SHALL 展示达标态；无 active 因子时 SHALL 如实展示「无 active 因子」空态（MUST NOT 显示伪进度）。卡片数据 SHALL 来自池接口的 `factor_signal` 块，MUST NOT 前端自行估算日期。

(Previously: 选股页只展示降级横幅与池表格，不披露逐因子 IC 样本进度与预计恢复日期。)

#### Scenario: 贡献分解 hover

- **GIVEN** 候选池表格中标的 C 综合分 1.823
- **WHEN** 用户 hover 综合分单元格
- **THEN** 浮层展示 C 的 factor_contribs 列表（factor_id + contribution），按 contribution 绝对值降序

#### Scenario: 降级披露横幅

- **GIVEN** 最近成功批次选择模式为 fallback
- **WHEN** 用户打开选股页
- **THEN** 页面展示降级披露横幅，文案含「非因子信号」
- **AND** 综合分列标题切换为「近 20 日日均成交额（亿元）」且数值按成交额口径呈现

#### Scenario: 因子信号状态卡片

- **GIVEN** 池接口返回 `factor_signal`：3 个因子分别 4/3/1 条样本、门槛 10、预计达标日 2026-11-02
- **WHEN** 用户打开选股页
- **THEN** 卡片逐因子展示「4/10」「3/10」「1/10」与「未达标」，并展示「预计 2026-11-02 恢复因子打分」
- **AND** 降级原因逐字展示（含「IC 样本不足」）

#### Scenario: 达标态

- **GIVEN** 池接口返回的因子全部 `eligible=true` 且批次 `selection_mode=factor`
- **WHEN** 用户打开选股页
- **THEN** 卡片展示达标态（如「因子信号已恢复」），MUST NOT 展示恢复日期
