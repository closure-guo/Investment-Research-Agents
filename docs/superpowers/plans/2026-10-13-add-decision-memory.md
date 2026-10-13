# add-decision-memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 情景记忆（同标的/同行业已结算历史决策 Top-5 注入分析师与 Trader 上下文，带来源标注与 citation 覆盖）+ 语义记忆（月批提炼候选条款、人工确认后才注入风控辩论、未确认绝不进 prompt）+ 记忆块预算核算 + ±记忆消融变体。

**Architecture:** `memory/episodic.py`（检索/排序/注入块组装，消费 outcome track-record 的 predictions/decision_log 已结算记录；只读）；`memory/semantic.py`（候选条款存储 pending/confirmed + 月批提炼入口 + 注入过滤）；注入走既有 state 键/上下文组装机制（沿用 `derived_series` 的节点注入先例，向后兼容：无记忆 → 行为与现状一致）；trace 用途标签 `decision_memory`；预算核算挂 `analyst-context-budget` 既有裁剪机制。

**Tech Stack:** Python 3.14 / sqlite3 / pytest；复用 `outcome/track_record` 库、`nodes/*` 上下文组装、`evals/ablation.py`。

## Global Constraints

- 测试：`uv run pytest tests/memory tests/nodes -q`；ruff/mypy 同仓库纪律；TDD 红→绿；既有测试零修改（节点上下文为**可选**扩展，缺省行为不变——向后兼容断言）。
- 注入条目逐字要求：含来源决策 ID 与日期；纳入既有 citation 校验覆盖（cite 规则按既有实现，不得只写进 prompt 而不注册来源）。
- 语义记忆：状态 `pending`/`confirmed`；**未确认条款 SHALL NOT 出现在任何 prompt**（测试：遍历所有 prompt 组装路径检索未确认文本，零命中）。
- 预算裁剪顺序（spec 逐字）：语义条款优先于情景记忆；情景记忆按结算结果相关性降序截断；裁剪事件记 trace。
- trace 用途标签逐字：`decision_memory`。
- 消融：±记忆变体接入既有配对框架；n=10 批次按评估 SOP（校准前置 + 成本）为**显式后续项**（挂 issue，不伪造批次结果）。
- 库：记忆候选条款表落 `MEMORY_DB_PATH`（默认 `data/decision_memory.db`，连接纪律同 snapshot）或复用 track-record 库——实施者择一并文档化（**优先复用 track-record 库**，因为记忆源自该库）。

## 关键设计决策

**M1 — 情景检索**：`retrieve_episodic(symbol, *, industry_codes: Sequence[str], as_of: str, top_k: int = 5, db_path=None) -> list[dict]`：从已结算记录（status 终态）取 `as_of` 之前、同标的或同申万三级行业标的的历史决策；排序：同标的优先 → 结算结果相关性（|raw_return| 降序？**钉：相关性 = 同标的 > 同行业，同级内按日期降序**，文档写明「相关性=标的/行业归属 + 时效」）；返回条目 `{decision_id, date, symbol, direction, settlement_status, raw_return, rationale_summary}`（rationale_summary 从决策论据字段截断 ≤200 字，缺字段 → 空串不编造）；无历史 → `[]`。

**M2 — 注入块**：`build_memory_block(episodic: list[dict], semantic_clauses: list[dict]) -> str`：markdown 块，逐条 `[记忆] 来源: {decision_id} ({date}) …`；`render_memory_state(state, ...)` 挂 state 键 `decision_memory`（与滞回键同风格）；analyst/Trader 上下文组装在**记忆为空时不产生任何块**（向后兼容，spec 场景「无记忆时行为不变」）；cite 覆盖：把记忆条目注册进既有 citation 来源集合（按既有 citation 模块的注册接口）。

**M3 — 语义条款**：`semantic.py`：`extract_candidate_clauses(settled: list[dict], *, client=None) -> list[dict]`（月批入口；LLM 提炼可注入 stub；无 client → 规则式降级提炼：按「方向×结果」分组统计生成模板条款，保证零 LLM 可测）；`store_candidate_clauses`（pending）、`confirm_clause(id)`/`reject_clause(id)`、`list_confirmed_clauses()`；`build_semantic_block()` 仅 confirmed；测试：注入前把所有 prompt 组装路径搜一遍未确认文本 → 零命中（spec 场景：5 条候选 2 条确认 → prompt 仅含 2 条）。

**M4 — 预算核算**：记忆块文本长度计入分析师上下文预算（按既有 budget 机制的计量方式）；超预算 → 先裁情景记忆（相关性升序=先丢最低相关），语义条款保留；裁剪事件记 trace（`decision_memory` 标签 + `trimmed` 计数）。

**M5 — 消融**：`evals/ablation.py` 追加 `tech_memory` / `tech_no_memory`（或复用 change 8 的 `_TECH_VARIANTS` 机制，命名遵循既有惯例）；n=10 批次后续项（issue）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/memory/__init__.py`、`episodic.py`、`semantic.py`（新） | 记忆层 |
| `src/finance_agent/nodes/{analysts,trader,risk}.py`（改，可选注入） | 上下文扩展（向后兼容） |
| `src/finance_agent/state.py`（改） | `decision_memory` state 键声明 |
| `evals/ablation.py`（改） | ±记忆变体 |
| `tests/memory/test_episodic.py`、`test_semantic.py`（新）；`tests/nodes/*`（追加） | 单测 |
| `tests/validation/2026-10-13-add-decision-memory-validation.md`（新） | 报告 |

---

### Task 1: 情景记忆检索与注入
- [ ] 测试：spec 场景（300750 三条已结算 → 注入 3 条含来源 ID 与日期）；同标的优先于同行业；无历史 → 空块且节点行为不变（向后兼容）；rationale 截断；trace 标签；cite 覆盖注册。
- [ ] Commit `feat(memory): episodic retrieval + context injection with citations (T1)`。

### Task 2: 语义记忆（月批 + 人工确认 + 未确认不生效）
- [ ] 测试：提炼（规则式降级路径）+ pending 落库；确认/驳回；注入仅 confirmed（spec 场景 2/5）；**全 prompt 路径扫描未确认文本零命中**；风控辩论 prompt 含已确认条款。
- [ ] Commit `feat(memory): semantic clauses with human confirmation gate (T2)`。

### Task 3: 预算核算 + 消融变体
- [ ] 测试：超预算裁剪顺序（语义优先保留、情景按相关性降序丢）；裁剪事件记 trace；±记忆变体构建与工具集/上下文差异；CI 含 0 措辞守卫（复用既有）。
- [ ] Commit `feat(memory): budget accounting + memory ablation variant (T3)`。

### Task 4: 验证收口
- [ ] 合成端到端：造已结算历史 + 候选条款 → 检索/注入/确认/预算裁剪/消融变体构建全链路 JSON；n=10 批次后续项（issue）。
- [ ] 报告 + `tasks.md` 全勾（4.2 如实标注）→（控制器 archive）。
