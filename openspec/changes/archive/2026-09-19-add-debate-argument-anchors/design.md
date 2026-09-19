# Design: add-debate-argument-anchors

## Approach

把 Trader `evidence_refs` 的「论据必带出处」模式平移到辩论层，但校验只做**存在性**（锚点解析得到 / 回声命中），不做**支持性**（锚点是否真的支撑结论）——后者留给 judge。三段式：

1. **生成侧（LLM）**：辩手在 `key_arguments` 里为每条论点申报 `kind` 与 `anchors`。data 型的锚点就是分析师 claim 用的同一套 state 英文键路径（context 里数据段标题已内联标注），LLM 不需要学第二套词表。
2. **节点侧（代码，零 LLM）**：`bull_debater` / `bear_debater` / 三方 `risk_debater` 解析出 `DebateMessage` 后，立即调用纯函数 `check_argument_anchors(message, state) -> list[AnchorCheck]`，把结果追加到 state channel `debate_anchor_checks`，并 `update_current_span(metadata=...)`。不改路由、不阻断。
3. **评估侧（代码 + judge）**：`evals/evaluators.py` 新增确定性评估器 `argument_anchor_coverage`；`evals/extract.py` 在 debate_quality 材料的骨架行加锚点覆盖统计并在每条论点前标注 `[kind ✓/✗]`；消融 run 记录附该指标。

## 数据模型

```python
class DebateArgument(BaseModel):
    text: str                                  # 论点标头原文，非空
    kind: Literal["data", "event", "inference", "unspecified"]
    anchors: list[str] = Field(default_factory=list)
```

- `kind` 对 LLM 只暴露三值（data / event / inference）；`unspecified` 由校验器在**旧格式裸字符串**或 `kind` 缺失/非法时赋予——显式降级，不猜。
- `DebateMessage.key_arguments: list[DebateArgument]`；`model_validator(mode="before")` 把 `list[str]` 逐项包成 `{text, kind: "unspecified"}`，使既有 fixture、TESTING stub、历史会话数据、Langfuse 反解材料全部继续可解析。
- `rebuttal_to` 仍是 1-based 序号指向对方 `key_arguments` 的**位置**，结构化不改变位置语义。

## 锚点校验规则（`check_argument_anchors`）

| kind | anchors 语义 | 判定 | 零锚点 |
|---|---|---|---|
| data | state field_ref 路径 | 复用 `finance_agent.citation._resolve_field_ref(anchor, state)`，解析出非 None 值 → `resolved`，否则 `unresolved` | `missing`（申报纪律违规，计数） |
| event | 事件标题要点 | 复用 `_verify_textual` 的回声源集合与 `_norm_text` 归一子串匹配（news_list / key_events / announcements / research_reports），命中 → `resolved`，否则 `unresolved` | `missing` |
| inference | 可选 | 若给了锚点：先按 field_ref 解析，失败再按回声匹配；记录状态 | `none`（合法，但计入「推断无锚」） |
| unspecified | — | 不校验 | 计入 `unspecified`（降级计数） |

`AnchorCheck = {role, round, index, kind, anchors, statuses, anchored: bool}`；`anchored = 任一 anchor 为 resolved`。

**为什么复用 citation 解析器而不是新写**：field_ref 词表、负索引、DataFrame 行键.列名、日期形态归一、季度标签换位这些语义已经在 `_resolve_field_ref` 里钉死并有 fixture；辩手引用同一份 context 数据段，天然同源。新写一份就是第二套解析语义 = 新的「校验器债」。

## state channel 与观测

- `AnalysisState` 新增 `debate_anchor_checks: Annotated[list[dict], add]`（append reducer——两层辩论各轮并行写入）。**必须**同步加入 `tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared` 的断言集合（incident 027：未声明键被图合并静默丢弃，本项目已踩两次）。
- 每个辩手节点在 `update_current_span` metadata 里落本轮 `{anchored, total, unanchored_inference, unresolved, unspecified}`，使 Langfuse 里逐轮可见。

## 指标定义（`argument_anchor_coverage`）

- `value = anchored_args / total_args`（全场辩论，Layer II + Layer IV 合并；分层拆项进 comment）。
- 拆项：`unanchored_inference`（inference 且零锚）、`unresolved`（申报了锚但全部不可解析/未命中）、`unspecified`（旧格式或 kind 非法）、`missing_required`（data/event 型零锚）。
- `total_args = 0` 时返回 null（沿确定性评估器「expected 缺省时跳过」口径）。
- 作为 NUMERIC Score 上报 Langfuse，与 `citation_coverage` 并列；`evals/run.py` 报告与消融 run 记录携带 value + 拆项。

## judge 材料形态

`evals/extract.py` debate_quality 材料：

```
【锚点覆盖】bull 5/6（推断无锚 1）｜bear 3/6（未解析 2，推断无锚 1）｜风控 7/9
R1 bull 论点: ①[data ✓] MA5 上穿 MA20 …  ②[inference ○] 龙头份额集中利好 …  ③[data ✗ 锚不可解析] …
```

只是**呈现**锚点状态；rubric v6 与 `points` 契约不变。judge 因此获得一个确定性的对照锚，与既有「交锋覆盖率骨架行」同款定位。

## 对手可见的辩论历史

`nodes/debate.py` 与 `nodes/risk.py` 渲染「R{n} 论点: ①…②…」时**只取 `text`**。理由：把锚点暴露给对手会让反方去攻击数据本身，这是辩论动力学层面的另一个变量；本轮只引入「申报 + 观测」，动力学变更留待锚点覆盖率有基线后单独立项。

## Alternatives Considered

- **只在 judge 侧做**（让 judge 判定每条论点是否有据）：这就是现状——round9–11 已证明 LLM 判定在边界随机，且 v6 封顶把取值域压死。锚点必须在生成侧结构化产出，才能由代码判存在性。
- **锚点校验直接上门禁**（无锚 data 论点打回辩手）：incident 026 明确了「自动化处置须先分桶归因、逐条终裁」；一个刚出生、遵从率未知的校验器上门禁就是重蹈覆辙。先观测、拿到首批遵从率分布再谈处置。
- **`llm_inference` claim 免检卡收窄 / 解读词一致性校验**（原方案 B）：管的是分析师报告层，与本 delta 互补但是另一条链路，另开 delta。
- **把 anchors 直接塞进 `Claim` 体系**（让辩手也产 claim）：辩手论点是「论证」不是「数据点」，强行套 claim 的 stated_value/容差语义会产生大量结构性 UNVERIFIABLE；独立轻量模型更贴切。

## Risks

- **遵从率**：LLM 可能大量输出 `inference` 逃避申报锚点，或给 data 型论点填不可解析的路径。对策：验证任务硬性统计首轮真实 deep 的分布并写入报告；处置方向限定为 prompt 迭代（申报纪律措辞、few-shot），**不得**放松校验或把 unresolved 改判 anchored。
- **上下文膨胀**：三份 prompt 增加契约说明，risk 辩论 context 三方并行——预算上限受 `analyst-context-budget` 既有护栏约束，实测 token 增量入验证报告。
- **解析降级面扩大**：`kind` 非法值走 `unspecified` 而非 ValidationError——与 `rebuttal_to` 为空不视为失败的先例一致；但 `text` 为空仍按既有 `plain_conclusion` 非空同款硬校验。
