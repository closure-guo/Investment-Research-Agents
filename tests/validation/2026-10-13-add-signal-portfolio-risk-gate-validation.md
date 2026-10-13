# 人工验证报告: add-signal-portfolio-risk-gate

**日期**: 2026-10-13
**验证人**: ZCode 实施 runner（自动化执行；人工终审签字待补）
**关联 delta**: `openspec/changes/add-signal-portfolio-risk-gate/`
**E2E 门禁**: 不适用（本 change 为纯后端执行层，无前端/SSE/会话交互面）

## 验证结果

| # | Scenario | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | Signal 契约（direction 枚举/缺字段/审批门禁） | `sell+short` → 合同错误消息含「direction 非法值: short」；未 approve 不生成 | 单测 31 例全绿（含两入口 + spec 场景「同日 approve+reject → 仅 approve 落库且 decision_id 指向它」） | ✅ |
| 2 | 组合聚合（等权/强度加权 + 上限） | 96% → 80% 等比缩 + `TOTAL_CAP_SCALE_DOWN`；单票 clip + `SINGLE_CAP_CLIP`；Σ ≤ 80%+1e-9 | 单测 17 例全绿；spec 场景逐数手算核对 | ✅ |
| 3 | 确定性风控门禁（8 原因码） | 逐规则正反；首个命中即拒 + 检查轨迹；ST 与 L1 同常量 | 单测全绿；**静态扫描（AST）无 llm/gateway/harness import**（spec 场景） | ✅ |
| 4 | 证据链（decision_id/signal_id） | 订单必填两 id，detail 回写 | 单测 `test_order_requires_evidence_chain_fields` 等 | ✅ |
| 5 | 滞回复用到信号路径 | 前日 long + 今日裸 flat 且均衡带内 → 维持 long + 判定落库 | 共享纯函数 + 适配层；`nodes/risk.py` 既有滞回测试**零修改**全绿；抖动吸收单测 29 例 | ✅ |
| 6 | 20 边界场景人工核对 | 每例手算期望 vs 门禁判定 | 脚本 `tests/validation/2026-10-13-add-signal-portfolio-risk-gate-acceptance.py`（期望值为独立手算硬编码）→ **20/20 matched**，`forbidden_imports: []`，`constants_match: true` | ✅ |

命令证据：

```
$ uv run pytest tests/execution tests/nodes -q
843 passed in 430.20s (0:07:10)

$ uv run python tests/validation/2026-10-13-add-signal-portfolio-risk-gate-acceptance.py
verdict: {"total": 20, "matched": 20, "independent_self_consistent": true,
          "constants_match": true, "forbidden_imports": [], "passed": true}
```

## 异常记录

1. **常量单点化的连带改动**：`execution/matching.py` 的整手判定改为 import `screening/constraints.py::LOT_SIZE`（spec 的单一来源要求）。既有 matching 测试零修改通过；该改动已在 T3 提交说明中披露。
2. **滞回抽取的行为保持**：`nodes/risk.py::_evaluate_hysteresis` 改为适配层调用 `execution/hysteresis.py` 的共享纯函数；既有滞回测试全部零修改通过（行为保持的直接证据）。
3. **live 依赖**：本 change 无 live 数据依赖（纯函数 + 本地库），不受东财 IP 封禁（issue #325）影响。
4. **人工终审**：20 例对照表为机器执行 + 独立手算期望；语义合理性的**人工**终审待签字（材料：acceptance JSON 全量逐例）。

## 结论

- [x] 全部通过（机器核对层面），可进入 archive 流程
- [ ] 存在失败项（无）
- 说明：人工终审签字待补（项目惯例：验证报告随档，签字栏留待维护者）。
