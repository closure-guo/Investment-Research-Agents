# Design: add-feedback-quota-fairness

## Approach

分配逻辑全部收敛在 `factors/feedback.py` 的 `build_payload` 内，替换现行的「档内单一键排序 + 总量截断」为**两段式分配**：

1. **rejected 档 per-gate 分配**：`floor = PER_GATE_FLOOR`（默认 5，`gate_floor` 参数可配，负数钳制为 0）。保底按**闸间轮转**分配（每轮每闸 1 条，至多 floor 条）——预算不足以给满所有闸保底时公平分摊，**单闸不得独占**（spec MUST NOT 的绝对句）；余量 `slots = rejected_budget - sum(floors_actual)` 按 gate 剩余池占比分配（最大余数法取整），池耗尽缺额按闸轮转回填。`rejected_budget` 不是新常数——沿用现行行为隐含的预算（`limit - len(admitted) - untranslatable_quota`），不引入第二套预算。
2. **untranslatable 档 stage 代表性**：`_select_untranslatable` 的按日轮转外层之内、单日桶选取时，按 `untranslatable:{stage}` 前缀分组轮转（同一轮转指针先取每 stage 一条再循环），保证单一 stage 无法独占；stage 取自现有 `_reason` 产出的 reason 前缀，无新解析。

配额读数：payload 新增顶层字段 `quota_allocation`（`{"rejected_by_gate": {"2": n, "4": n}, "untranslatable_by_stage": {"translator": n, "LLM": n}}` 形态，非 int gate 计入 `"unknown"` 桶保证全量对账），由**实际出场条目**统计，不做事后重算——读数与清单天然一致（对应 Scenario 4）。

**输出顺序为闸间轮转**（而非闸号分组）：消费端 `explored_feedback.py` 的 9000 字符行级预算构成**第二层截断**——真实数据下渲染在第 ~42 条截断，若按闸号分组输出，闸④/⑤（闸号大）条目落在渲染窗之外，payload 有而 prompt 里看不到（审查 C1）。轮转输出使每个闸的条目前置铺开，配合新增的**渲染层回归测试**（真实规模合成数据 + 消费端渲染，断言 `gate4` 行在渲染块内）钉住端到端可见性。消费端本身零改动。

## Alternatives Considered

- **只调大 `limit`**：不解决单调性——闸② 增长快于 limit 任何合理值，闸④ 永远在截断线之后；且 prompt 预算有限，放大载荷伤生成侧上下文。否。
- **gate_rejections 计数扩成逐 gate 示例**（契约块内嵌示例）：把示例塞进 contract 段会与已探索清单重复表达，且 #482 刚部署、动契约段形状会污染其判效读数。否。
- **按机制族（残差×反转等）聚类配额**：库内无族分类法，需先建 taxonomy（另一 delta 的量级）；stage 是现成、确定性的代理维度。留 BACKLOG（族级聚类待 stage 级验证传导改善后再议）。

## Risks

- 与 #482 判效窗口的变量混淆 → 排期上**实施在 10-31 读数归档之后**（见 tasks 前置项）；两 delta 触碰的载荷字段不相交（本 delta 只动 `explored` 分配与新增 `quota_allocation`，不动 `contract`/`gate_rejections`）。
- 保底把「最有拒收信息量」的近期闸②条目挤出 → 保底之外的余量仍按占比（闸② 占绝对多数，余量大头仍归闸②）；单测覆盖「保底后闸②出场数 ≥ 占比余量」防矫枉过正。
- `quota_allocation` 字段被消费端当未知字段处理的兼容性 → 消费端按 key 显式读取（contract/explored），未知顶层字段忽略，已由 fail-safe 设计保证；回归测试钉住。
