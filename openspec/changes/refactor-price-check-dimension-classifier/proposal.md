# Proposal: refactor-price-check-dimension-classifier

## Why

价位校验器是本仓库的慢性 bug 热点：7 周 16 个 commits 里 12 个是误报修复（#254→#337→#566→#579→#582→#599），每条豁免要在 **3 处同步**（openspec Requirement 逐 token 入册 + 模块正则/字符表 + 事故命名测试类）——「什么不是价」的知识以 10 种互不统一的机制散布在 `metrics/decision_price_check.py`（806 行）头部，无 locality，每个新语境都要新开一张表。spec 的豁免段落以 token 粒度逐条入册（编号列表/×/PEG 各一段），随误报无限增长——spec 粒度=实现粒度，契约本身也 shallow。

## What Changes

- **spec 粒度重写**：price-level-tooling 的三个 token 豁免段落（编号列表记号/×量纲乘号/PEG 估值倍数）收编为**量纲归类契约**单一段落（结构记号/量纲语境/自引用三类规则）；全部护栏 Scenario 原样保留（行为不变）
- **实现归类化**：新增 `metrics/price_text.py`——量纲归类器 + 价格承诺账本（PriceLedger）唯一实现：
  - `classify(text, span, ...) → NumberDimension`：结构记号/非价格语境/自引用/量纲外/子单元比值/价位候选
  - `extract_commitments(text, ...) → list[PriceCommitment]`：文本→价格承诺账本（值/span/摘录/方向集），校验消费账本而非直接扫文本——「文本承诺了哪些价」成为可测试的中间产物
  - 点名指标别名表、方向词绑定（窗口+分句阻断）随迁内聚
- `decision_price_check.py` 收编为消费方：交叉核对（归属/参考带/偏差/空洞）+ 结构化触发位校验留在原模块；10 张表移入归类器
- 测试：既有 91 个行为测试**原样保留**（全过=行为不变证明）；新增归类器直测用例

**非目标**：PriceLedger 落 trace 的 state 通道接线（涉及 agent-node-contracts 状态键契约，另立）；分句器（Clause Binder）结构化重写——绑定语义（窗口+阻断字符集）原样随迁。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: price-level-tooling（MODIFIED 决策文本价位交叉校验——豁免段落归类化）

## Impact

- `src/finance_agent/metrics/price_text.py`（新增，归类器+账本）
- `src/finance_agent/metrics/decision_price_check.py`（消费方收编，体量下降）
- `tests/metrics/test_decision_price_check.py`（原样全过）；`tests/metrics/test_price_text.py`（新增）
- 行为零变化：91 个行为测试原样通过是验收标准
