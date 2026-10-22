# 分析主链失败归因与处置对照（2026-10-15 至 2026-10-22，24 次 failed）

**日期**: 2026-10-22
**数据源**: `data/sessions.db`（`status='failed'` 近 7 天）+ 容器日志 + Langfuse trace
**纪律**: incident 026——先分桶，再逐桶定处置；指标异常不得直接改 prompt。

## 分桶

| 桶 | 次数 | 典型 error | 归因 | 处置 |
|---|---|---|---|---|
| LLM provider 5xx/连接 | 9 | `ProviderInternalError: litellm.InternalServerError ... Connection error` | 主端点（opencode zen）断供，同端点重试 3 次（1/2/4s）无法覆盖分钟级中断 | **已修**：新增 env 注入 fallback 成员（`add-env-fallback-profile`，Kimi k3-256k），容器内链装配已验证；断供时自动切端点 |
| 空正文（思考后即止） | 4 | `EmptyLLMOutputError: 无正文 delta 且 finish_reason='stop'` | 模型侧非确定性行为；节点内已有强化指令 repair（最多 3 次），仍失败才上抛 | **不新增改动**：现有 repair 已覆盖，样本全集中在 10-19 一晚，无法稳定复现；再加重试只会翻倍等待。保留观测 |
| 限流（429） | 3 | `RateLimitError` | 与 provider 桶同源（配额/拥塞） | 由 fallback 链覆盖；fallback 成员能力档经 PolicyRouter 偏序校验 |
| 数据源（新浪） | 3 | `新浪财经接口连续 2 次无响应` | 行情源瞬时不可用；快照链路本身有腾讯/东财回退，但个别标的全链失败 | 纳入数据源健康观测；不改分析链 |
| 价位校验打回后仍异常 | 5 | `决策价位交叉校验未通过（打回重试后仍异常），报告阻断交付` | **规格行为**：`pipeline-events` 要求「未产出报告的管线结束 → status=failed + failure_reason」；`price-level-tooling` 要求「恶化则阻断」 | **不改**：会话失败是规范的 fail-closed 语义，不是缺陷。可观测改进（区分「阻断交付」与「链路故障」）另立需求 |

## 结论

- 24 次中 12 次（provider + 限流）由本轮 fallback 变更覆盖；5 次价位阻断是规格要求；4 次空正文属模型随机行为且已有 repair；3 次数据源属外部可用性。
- 「阻断交付」与「链路故障」共用 `status=failed`，运维看板无法区分——登记为后续可观测需求（不属本轮修复范围）。

## 关联

- `openspec/changes/archive/2026-10-21-add-env-fallback-profile/`
- `openspec/specs/pipeline-events/spec.md`（未产出报告 → failed）
- `openspec/specs/price-level-tooling/spec.md`（恶化阻断）
