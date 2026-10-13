# 人工验证报告: add-decision-memory

**日期**: 2026-10-13
**验证人**: ZCode 实施 runner（自动化执行；人工终审签字待补）
**关联 delta**: `openspec/changes/add-decision-memory/`
**E2E 门禁**: 不适用（记忆注入为后端上下文层，无前端/SSE/会话交互面；合成端到端见下）

## 验证结果

| # | Scenario | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 同标的历史注入（spec：300750 三条已结算 → 注入 3 条含来源 ID 与日期） | 分析师与 Trader 上下文含记忆条目（来源 ID + 日期）+ trace 标签 `decision_memory` | 合成 e2e：分析师×4 + Trader 上下文含条目；来源标注与标签逐字检查 PASS | ✅ |
| 2 | 检索相关性 | 同标的优先 → 同行业；排除未结算/非目标 | e2e check「相关性：同标的优先 → 同行业，均按日期降序，且不含非目标标的/未结算行」= true | ✅ |
| 3 | 未确认条款不生效（spec：5 条候选 2 条确认 → prompt 仅含 2 条） | 未确认文本在全 prompt 路径零命中 | e2e check「风控辩论上下文仅含已确认语义条款（无情景记忆）」+「未确认条款全 prompt 路径零命中」= true | ✅ |
| 4 | 预算不足裁剪 | 语义条款完整保留；情景按相关性降序截断；裁剪事件记 trace | e2e check：情景被截断 + 裁剪事件计数一致 + 语义完整保留 + trace（trimmed/dropped_ids） | ✅ |
| 5 | 无记忆时行为不变（向后兼容） | 无记忆键 → 各上下文无记忆块，产出同结构 | e2e check「向后兼容：无记忆键 → 各上下文无记忆块」= true | ✅ |
| 6 | citation 覆盖 | 注入条目纳入既有 citation 校验 | e2e check「记忆条目纳入 citation 校验覆盖（PASS，无覆盖缺口）」= true | ✅ |
| 7 | 记忆消融变体 | ±记忆变体可构建、配对 bootstrap、CI 含 0 口径 | e2e check：变体可构建且节点集合一致（差异只在入口）、聚合含 CI 与有效 n | ✅ |

命令证据：

```
$ uv run pytest tests/memory tests/nodes -q
722 passed in 415.73s (0:06:55)

$ uv run python tests/validation/2026-10-13-add-decision-memory-e2e.py
verdict = PASS   （checks 15/15）
```

## 异常记录

1. **n=10 消融批次未运行（显式后续项，不伪造）**：需要真实 LLM 批次 + judge 校准（评估 SOP 一次只动一个变量、
   校准先行）与配额，本机未就绪；同 `add-analyst-factor-tools`（issue #326）先例挂 follow-up。
   `docs/evals/` 消融报告因此未产出——**不计为通过**。
2. **合成端到端口径**：e2e 使用合成已结算历史与候选条款（固定数据），验证的是检索/门禁/预算/注入/citation 链路，
   **不构成真实记忆增量的证据**（增量由 n=10 批次裁决，属后续项）。
3. **语义条款提炼的降级路径**：无 LLM client 时走规则式提炼（模板条款），保证零网络可测；生产月批走 LLM 路径
   （client 注入），行为差异已在模块 docstring 与测试中标注。
4. **既有测试**：节点上下文为可选扩展，既有测试零修改（红线满足）；向后兼容有专项断言。

## 结论

- [x] 全部通过（机器核对层面），可进入 archive 流程
- [ ] 存在失败项（无）
- 说明：n=10 消融批次与 `docs/evals/` 报告为显式后续项；人工终审签字待补。
