## Why

定性/文本层面的幻觉在生产链路里**没有程序防线**。反幻觉盘点（2026-09-15）确认三个逃逸口，其中辩论层是最大的一个：

1. `source_type=llm_inference` 的 claim 在校验分派处直接判 UNVERIFIABLE、不做任何检查（`citation.py:1002`）；
2. 文本 claim（entity/regulatory/event）只做回声匹配——只能证明「这件事在输入里出现过」，证明不了「由它推出的结论成立」；
3. **辩论层（Layer II Bull/Bear、Layer IV 三方风控）产出的全部论点 `DebateMessage.key_arguments: list[str]` 是裸字符串**，没有任何锚点字段，辩手 prompt 只要求 `rebuttal_to` 编号回应对方、不要求论点带数据支撑——整层内容在 citation 体系之外。

后果链已在评估侧完整显形：judge 成为定性内容唯一的防线，而 judge 在 4/5 边界随机翻转（round11：同材料 n=13 次调用 4 分 7 次 / 5 分 6 次，σ≈0.5）；round9→10→11 三轮才让 judge 稳定识别「纯定性论点」，代价是 debate_quality 取值域压缩到 4.0–4.333、消融实验对辩论层的分辨力 ≈0（`docs/evals/metrics.md` §2.5 副作用、§3 遗留候选 (c)）。**两个问题是同一个根：定性论点没有结构化锚点，程序判不了「有没有锚」，judge 只能靶向「是不是纯定性」这个粗粒度信号。**

解法不是让程序判断定性论断「对不对」（那是事实核查，不可靠且昂贵，而且把第二个噪声评分者放上门禁正是 incident 026 的反面教训），而是把「忠实性」拆成**可追溯性（程序）+ 支持性（judge）**：程序判断「有没有锚、锚在哪、锚存不存在」，judge 只判断「锚是否支持结论」。这套模式在项目里已被证明有效一次——Trader 的 `evidence_refs`（decision_grounding r1 2.75 → r3 4.22）。

## What Changes

- **论点结构化**：`DebateMessage.key_arguments` 从 `list[str]` 升级为 `list[DebateArgument]`，每项 `{text, kind, anchors}`，`kind ∈ {data, event, inference}`，`anchors` 为锚点列表（data 型 = state 英文键路径 field_ref，与分析师 claim 同一词表；event 型 = 来源事件标题要点；inference 型可为空）。旧格式裸字符串 SHALL 解析为 `kind="unspecified"`（显式降级，不猜 kind——与 `direction=None` 计覆盖缺口的先例一致）。
- **节点侧确定性锚点校验（零 LLM）**：辩论/风控节点解析后即校验——data 型锚点复用 citation 的 `field_ref` 解析器判可解析；event 型按既有回声源集合（news/key_events/公告/研报）子串匹配；inference 型无锚合法但计数。结果写入**声明的** state channel `debate_anchor_checks`（incident 027：未声明键被图合并静默丢弃）并落 span metadata。**fail-open 不阻断**——新校验器不直接上门禁，先观测（incident 026 纪律）。
- **确定性指标 `argument_anchor_coverage`**（≥1 有效锚的论点数 / 论点总数）及拆项（推断无锚数 / 未解析锚数 / unspecified 数）：进确定性评估器、Langfuse Score、debate_quality judge 材料骨架行、消融 run 记录——debate 层首次拥有一个**不受 judge 取值域压缩影响**的层间比较信号。
- **辩手 prompt 锚点申报纪律**：bull/bear/risk_debater 三份 prompt 更新 `key_arguments` 输出契约并加纪律——data 型论点必须附 ≥1 个 field_ref 且只能引用输入数据段标注的英文键；event 型附来源事件标题要点；inference 型明示为推断、**禁止为推断伪造 field_ref**。经 `deploy_prompts.py` 发布；契约测试锁定。
- **对手可见的辩论历史首版只渲染 `text`**（编号行「R{n} 论点: ①…」形态与 `rebuttal_to` 编号语义完全不变）——锚点暴露给对手会改变辩论动力学，属另一个变量，本 delta 不动（评估 SOP：一次只动一个变量）。judge 材料渲染锚点状态。
- **rubric 不变**（debate_quality 仍 v6）。judge 输出 `points.type` 与辩手申报 `kind` 的交叉核对、以及把锚点覆盖率纳入 rubric，留待后续校准轮——本 delta 只把确定性信号生产出来并落盘。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-node-contracts`：新增「辩论论点结构化锚点」——模型契约、旧格式显式降级、节点侧确定性校验、state channel 声明、fail-open、与 `rebuttal_to` 编号语义兼容。
- `agent-prompt-contracts`：「辩论者对抗性指令」增加锚点申报纪律与禁伪造条款（含可测试性）。
- `evaluation`：新增「辩论论点锚点覆盖率」——确定性评估器口径、拆项、judge 材料骨架行、消融 run 记录、Score 上报、跨切点口径登记。

## Impact

- **业务链路**：辩手输出契约变化（LLM 需多产出 `kind`/`anchors`）；辩论正文、`rebuttal_to`、交锋覆盖率语义不变；报告与前端不消费 `key_arguments`（已核：仅 `models.py` / `nodes/debate.py` / `nodes/_llm_utils.py` / `evals/extract.py` 四处消费），**无 UI 变化，非交互类变更，不触发 E2E 门禁**。
- **观测**：新增 channel `debate_anchor_checks`（须补进 `TestStateChannelsDeclared`）、新 Langfuse Score `argument_anchor_coverage`。
- **评估**：judge 材料多骨架行（形态变化，`metrics.md` 登记切点）；消融 run 记录多一个确定性维度；rubric 版本不变。
- **风险（如实登记）**：LLM 对锚点申报的遵从率未知——delta 内含真实链路实测（一次 deep 全流程 + Langfuse trace 核对，统计 unspecified / data 型无锚 / 锚不可解析的比例入验证报告）。若 data 型论点大量无锚或锚不可解析，处置对象是 prompt（迭代申报纪律），**不得因此放松校验或改判 kind**。
- **回归**：`tests/nodes/test_debate*.py`（新增结构化解析/降级/校验用例）、`tests/evals/test_extract.py`（判材料骨架行）、`tests/evals/test_evaluators.py`（新评估器）、`tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared`（新 channel）、TESTING stub（`_llm_utils.py`）同步。
