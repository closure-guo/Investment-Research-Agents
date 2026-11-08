# Proposal: add-cross-section-translation-path

> **状态：立项提案，待 owner 评审。** 出处：A2 判效终裁（2026-11-09，owner 授权 agent 终裁，选项 1——见 `docs/evals/2026-10-31-因子供给判效读数.md` §9.1）；承载 umbrella `optimize-factor-supply-funnel` 的 B1 项（其 proposal.md 明言 B1 另立 change-id 承载）。

## Why

横截面型因子想法在中间语言处被锁死：

- A2 判效（两批 n=329）：翻译失败主桶已从「llm-横截面」塌缩（61%→12%）转为「translator-复杂度」（32%→82%）——生成器「听指令」（否定式禁令合规）但「写不出」（计数式约束做不到）。**横截面桶的塌缩不是解锁，是被禁令锁死后的清零**——769 条量级的横截面想法空间仍不可达（10-29 冻结基线：横截面 769 条 = untranslatable 的 50.1%）。
- 双受益杠杆（umbrella design §交集杠杆）：既压转译率（769 条横截面桶），又提闸④通过率——横截面因子与全量价池 IC 天然低相关（现役池恰好缺横截面/动量/波动率风格）。
- A2 终裁（选项 1）已降级 #510（prompt 侧契约约束不追投）——「写不出」的解法只能换层：**改中间表示，不改 prompt**。

## What Changes

在「外部研发供给接入」的转译链路上**解锁横截面构念**，两条候选路径（择一，决策留实施期 spike 后拍板）：

- **案 A·Qlib 映射桥**：为 Qlib→本系统 DSL 映射层补横截面算子桥——Qlib `Rank(x,N)` 是滚动秩 → 映射 `ts_rank`（umbrella 既有考证）；逐算子语义核对，不引入近似转译。
- **案 B·直出本系统 DSL**：RD-Agent 侧改产出本系统 DSL（绕过 Qlib 中间语言）——失去 Qlib 生态既有校验，需评估回归面。

无论何案：**`qlib_translate` 既有映射回归全绿 = 合入硬门禁**；**先桥后切、分期落地**；解锁后生成侧契约的「禁横截面运算」条款 SHALL 随真相源自动解除（MUST NOT 留手工第二真相源）。

## Capabilities

- **Modified Capabilities**: `factor-proposal`（ADDED requirement：横截面转译路径）

## Impact

- 代码：转译层（`qlib_translate` 映射或 RD-Agent 产出侧，随案而定）；生成侧契约真相源（`factors.dsl` / `qlib_translate` 导出）无需手改——契约段按既有真相源跟随语义自动更新。
- 读数：验收 = `stage=llm` 且 reason 含横截面的拒译占比显著下降（对照基线：10-29 冻结横截面 769 条 / 占 untranslatable 50.1%；A2 暴露窗 12%）；闸④通过率随横截面因子入闸观察。
- 前置：**供给恢复**（provider 续费 + 重开研究开关）——设计/评审不需要，判效读数需要。
- 风险：架构级改动（umbrella design §28）——直出 DSL 失去 Qlib 生态校验、映射回归可能漏边角；对策 = 映射回归全绿硬门禁 + 分期。
