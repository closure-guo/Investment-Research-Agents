# add-fm-grounding-surface 人工验证报告（issue #242 断点 3）

- 日期：2026-10-10
- Delta：`add-fm-grounding-surface`（openspec/changes/add-fm-grounding-surface/，堆叠于 add-anchor-value-grounding）
- 范围：FM 审批上下文三类 grounding 输入（估值完整性标注 / 辩论锚点告警 / 数据口径披露原文）+ fund_manager.md 审批前核查条款
- 验证性质：确定性上下文构建（零 LLM 路由变化）+ 提示词静态断言；FM 决策行为分布留待实跑观测

## 1. 光大 601818 场景复核

以光大审计缺口构造 state 直测 `_build_fund_manager_context`：

| 原缺口 | 上下文渲染结果 | 断言 |
|---|---|---|
| PE 缺失无原因可读 | 「估值完整性标注：market_cap 缺失；快照缺失（估值数据缺席，估值相关论断缺乏确定性数据支撑）」 | ✓ |
| -24.01% 锚点断链零告警 | 「辩论锚点告警：bull R1 #2：value_mismatch（锚点：fundamental.中报净利润同比）」 | ✓ |
| 披露节不在 FM 视野 | 「数据口径披露（确定性渲染，供交叉核对）」整段注入（含「归母净利同比 暂缺」「年化波动率 15.68%」） | ✓ |
| 既有段 | 「交易决策」「已退回次数」不受影响 | ✓ |

零回归实证：仅决策 + return_count 的基础 state 上下文无任何新增空段（三段均非空才出现）。

## 2. 告警面行为

- value_mismatch / unresolved / missing（全 kind）/ echo_only 四类信号进告警，逐条列示 role/round/index/锚点
- 上限 5 条：第 6 条起「另 N 条未列示」计数汇总（上下文体积纪律）
- 全零或无 debate_anchor_checks 键（旧会话/stub）→ 不出段
- fail-open：告警不改变路由、不自动退回——仲裁权保留（与「FM 审批对象完整性可见性」同原则）；prompt 条款要求 reasoning 显式回应（沉默审批在评估中按可疑形态标注），替代 issue 建议的自动退回（design D1：无阈值观测数据，先可见性后门禁）

## 3. 提示词静态断言

fund_manager.md 新增「审批前核查」段（条件式条款——上下文出现对应段才生效，prompt 先发 Langfuse 不产生悬空指令）：
- 三类标注/告警的显式回应义务（MUST）
- 数据口径披露段作为交叉核对基准（口径冲突须说明）
- 仲裁权保留表述在场（「不禁止你 approve（仲裁权在你）」）

## 4. 自动化验证汇总

- `pytest tests/nodes/test_fund_manager.py`：35 passed（新增 9 用例先红后绿）
- 全量 `pytest -m "not live"`：4537 passed, 0 failed
- `ruff check` / `ruff format --check` / `mypy`：零错误
- `openspec validate add-fm-grounding-surface --strict`：valid（agent-node-contracts + agent-prompt-contracts 两 MODIFIED）

## 5. 范围外

- 报告渲染不变（披露节已在报告内；新标注仅进 FM 上下文）
- FM prompt 部署：合并后执行 `uv run python scripts/deploy_prompts.py`（tasks 2.4）
- 标注出现时 FM 行为分布观测 → 决定是否升级硬门禁（incident 026 纪律）
