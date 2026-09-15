## ADDED Requirements

### Requirement: 辩论论点锚点覆盖率

系统 SHALL 提供确定性评估器 `argument_anchor_coverage`（零 LLM 调用）：从 state channel `debate_anchor_checks` 计算 `value = 有效锚定论点数 / 论点总数`（有效锚定 = 任一锚点 `resolved`；Layer II 与 Layer IV 合并计），并携带拆项 `unanchored_inference`（推断型零锚）、`unresolved`（申报了锚但无一解析/命中）、`missing_required`（data / event 型零锚）、`unspecified`（旧格式或 kind 非法）。论点总数为 0 时 SHALL 返回 null（不计入该维度）。该指标 SHALL 作为 NUMERIC Score 上报 Langfuse（与 `citation_coverage` 并列），SHALL 进入实验报告与消融 run 记录（`evals/run.py` / 消融驱动），使辩论层拥有不依赖 judge 取值域的层间比较信号。

debate_quality 的 judge 材料 SHALL 在既有骨架行（交锋覆盖统计、收敛信号）之后追加**锚点覆盖骨架行**（各方 `anchored/total` 与拆项），并在每条论点编号前标注 `[kind 状态]`（如 `[data ✓]` / `[inference ○]` / `[data ✗ 锚不可解析]`）。本要求 SHALL NOT 改变 debate_quality 的 rubric 版本与 `points` 输出契约——锚点信息仅作确定性对照锚呈现；judge `points.type` 与辩手申报 `kind` 的交叉核对留待后续校准轮。

指标口径 SHALL 登记至 `docs/evals/metrics.md` §1.2（定义 / 代码位置 / 拆项），judge 材料形态变化 SHALL 在时间线标注切点（跨切点 debate 分不可直接比较）。

#### Scenario: 覆盖率确定性计算

- **GIVEN** 一次 deep 全流程 `debate_anchor_checks` 含 12 条论点校验，其中 8 条 `anchored=true`、2 条 inference 零锚、1 条 data 型锚全部 unresolved、1 条 unspecified
- **WHEN** 运行 `argument_anchor_coverage` 评估器
- **THEN** SHALL 返回 `{name: "argument_anchor_coverage", value: 0.6667, comment: 含 unanchored_inference=2 / unresolved=1 / missing_required=0 / unspecified=1}`
- **AND** SHALL NOT 发起任何 LLM 调用

#### Scenario: 无论点时跳过

- **WHEN** `debate_anchor_checks` 为空（quick 模式或辩论层未执行）
- **THEN** 评估器 SHALL 返回 null，不报错、不计入该维度

#### Scenario: judge 材料附锚点骨架行与状态标注

- **WHEN** 提取 debate_quality 的 `debate_history` 材料
- **THEN** 材料 SHALL 含「【锚点覆盖】bull a/b｜bear c/d｜风控 e/f（含拆项）」骨架行，位于交锋覆盖与收敛信号骨架行之后、原始发言之前
- **AND** 每条论点行 SHALL 形如「①[data ✓] 论点原文」，`kind` 与状态取自 `debate_anchor_checks`，SHALL NOT 由材料提取阶段重新推断
- **AND** rubric 版本与 `points` 契约 SHALL 保持 v6 不变

#### Scenario: 消融与实验报告携带

- **WHEN** 消融一条 run 完成或 `python -m evals.run` 一条 item 完成
- **THEN** run / item 记录 SHALL 含 `argument_anchor_coverage` 的 value 与拆项
- **AND** 消融聚合报告 SHALL 对该指标按既有配对 bootstrap 口径给出层间增量与 95% CI（与 judge 维度同法）

#### Scenario: 口径登记与切点

- **WHEN** 本变更合入并首轮实验收口
- **THEN** `docs/evals/metrics.md` §1.2 SHALL 新增该指标行（定义 / 代码位置 / 拆项）
- **AND** 时间线 SHALL 标注「judge 材料加锚点骨架行」切点，跨切点 debate_quality 分数 SHALL 标注不可直接比较
