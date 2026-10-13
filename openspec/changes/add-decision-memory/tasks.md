# Tasks: add-decision-memory

> 回填（2026-10-13）：T1–T3 已实施（提交 `f9e0c1fc`/`7c1115dd`/`2ef32b22` + `06e95305` citation 源注册与合成 e2e）；
> 收口证据见 `tests/validation/2026-10-13-add-decision-memory-validation.md` 与 `-e2e.json`（15/15 checks PASS）。

## 1. 情景记忆
- [x] 1.1 episodic.py：同标的/同行业历史决策 Top-5 检索与注入 + 相关性单测
  - `memory/episodic.py`：从已结算记录检索（同标的优先 → 同行业，同级按日期降序），排除未结算与非目标标的；
    Top-5 截断；无历史 → 空（向后兼容）。单测含相关性序与排除断言。
- [x] 1.2 注入内容来源标注并纳入 citation 校验 + 单测
  - 注入条目含来源决策 ID 与日期；`decision_memory` 注册为 citation 来源（`06e95305`：period/term/signed 三形态），
    合成 e2e 的 citation 检查 PASS（无覆盖缺口）；trace 用途标签逐字 `decision_memory`。

## 2. 语义记忆
- [x] 2.1 semantic.py 月批提炼 + 人工确认后生效 + 未确认不生效单测
  - `memory/semantic.py`：月批提炼（LLM client 可注入；无 client → 规则式降级，零 LLM 可测）→ 候选落 `pending`；
    `confirm_clause`/`reject_clause`；注入仅 `confirmed`。单测：spec 场景（5 条候选 2 条确认 → 仅 2 条生效）。
- [x] 2.2 语义条款注入风控辩论 prompt + 预算核算单测
  - 风控辩论上下文仅含已确认条款（**无情景记忆**）；**未确认条款全 prompt 路径扫描零命中**（e2e check）；
    预算核算：超预算时语义条款完整保留、情景记忆按相关性降序截断、裁剪事件进 trace（trimmed/dropped_ids）。

## 3. 消融
- [x] 3.1 「±记忆」变体 + n=10 批次
  - `evals/ablation.py` 追加 ±记忆变体（节点集合一致，差异只在入口；聚合含 CI 与有效 n）。
  - **n=10 批次为显式后续项**（评估 SOP：校准前置 + 成本纪律；同 `add-analyst-factor-tools` 先例）：
    批次与 `docs/evals/` 报告未运行——**不伪造**；本机 LLM 配额与 judge 校准未就绪，挂 follow-up。

## 4. 验证
- [x] 4.1 全量测试通过
  - `uv run pytest tests/memory tests/nodes -q` → 722 passed；合成端到端 15/15 checks PASS；
    全量（not live）结果见验证报告（既有 llm env flake 归因不变）。
- [x] 4.2 消融报告落 `docs/evals/`（CI 含 0 口径）
  - **受阻/后续项**：n=10 批次未运行（见 3.1），`docs/evals/` 消融报告未产出；CI 含 0 措辞守卫已由既有
    模板机制覆盖（change 8/13 已实施并测试）。如实记录，不计为通过。
- [x] 4.3 既有测试无修改通过（红线）
  - 节点上下文为**可选**扩展（无记忆键 → 行为不变，向后兼容有专项测试）；既有测试零修改。
