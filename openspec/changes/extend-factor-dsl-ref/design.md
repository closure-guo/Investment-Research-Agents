# Design: extend-factor-dsl-ref

## D1 语义：沿日期轴按标的后移 N 期，正整数窗口，零前瞻

- `ref(x, N)` 复用 `delta` 同源的 shift 机制（`frame.shift(N)`：DataFrame 索引为日期、列为
  标的 → 逐标的后移），前 N 行为 NaN；N 须正整数数字字面量（复用 D2 窗口形态约束，单一来源）。
- 备选（允许负窗口 = 前瞻引用）否决：未来函数是回测红线，不可作为语言能力开放；
  `ref(x, 0)` 恒等引用无信息量，一并拒收（与其余窗口算子形态约束一致）。
- 与 pyqlib `Ref` 的等价性：`series.shift(N)`（qlib/data/ops.py L793）与本实现同语义、
  **无热身期口径差**（shift 无 min_periods 概念）——翻译器映射状态 `missing` → `equivalent`
  即如实反映；负/零窗口按「窗口须正整数」拒收（pyqlib 负 N 为前瞻，超出支持面）。

## D2 覆盖率取向：一个算子解锁一个因子大类，其余缺口以读数驱动

- 复跑条件逐字为「扩 DSL ref」：6/10 不可翻译全因 Ref（动量/反转/收益率族）。先补这一个。
- 滚动 Sum/Var/Skew/Max-rolling/Quantile 等其余缺口**不在本 delta**——复跑读数出来后逐个评估
  （一次只动一个变量，覆盖率提升幅度可归因；避免「扩了一批算子、说不清哪一条带来改善」）。

## D3 边界：LLM 提案 prompt 的算子清单不动

- `factor_proposal.md` 的「可用算子」清单本次不加入 ref：prompt 变更是独立版本事件
  （Langfuse 部署 + eval 基线可比性），与 DSL 扩展分开推进；外部翻译路径不受影响
  （翻译器直接以 DSL OPS 为准）。跟进项另立。

## Risks / Trade-offs

- `ref` 进入外部翻译后，动量/反转族候选将开始到达五道闸——闸②/闸④拒收读数会上升。这是设计
  意图（闸门是裁判，拒收是真实读数而非缺陷），复跑报告需按桶归因呈现。
- 复杂度约束（深度 4 / 自由参数 3 / 字段 3）对 ref 族天然友好（如
  `sub(div(close,ref(close,20)),1)`：深度 3、参数 1、字段 1），不引入新超限面。
- `ref` 不参与规范化/交换律（非 _CANONICAL_OPS），子树同构与查重语义不变。
