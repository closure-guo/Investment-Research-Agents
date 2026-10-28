# Factor Feedback Supply Optimizations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地两条 delta——① 回喂清单配额公平性（rejected per-gate 保底 + untranslatable stage 代表性 + 配额读数）；② 生成 prompt 任务指令层内嵌 DSL 契约段（同源派生、fail-safe）。

**Architecture:** delta ① 全部收敛在 `factors/feedback.py` 的 `build_payload` 分配逻辑（两段式：per-gate floor + 最大余数余量；untranslatable 在按日轮转内按 stage 交织）；delta ② 复用 #482 形态——渲染函数加进已部署的 distro 消费模块 `explored_feedback.py`，新补丁脚本 `patch-prompt-contract.py` 把渲染结果注入 `factor_proposal.py::prepare_context` 的 `hypothesis_specification`（每轮生效、fail-safe 空串跳过）。

**Tech Stack:** Python 3.12 / pytest / ruff；无新依赖。

## Global Constraints

- 既有语义零回退：admitted 全量在前、canonical 去重、`limit` 总量上界、`contract` 块与 `gate_rejections` 照常（spec Scenario「既有语义零回退」）。
- 消费端 `explored_feedback.py` 的 `render_explored_block` 行为零改动（只增函数）。
- 契约数值单一真相源：一切 import 自 `factors.dsl` / `factors.qlib_translate` / 载荷 `contract` 块，零字面量复制。
- 实施分支放 `.worktrees/`（主检出被并发会话占用）；部署步骤（后端 `--build` / distro site-packages）**不在本计划内**——两条 delta 的 tasks.md 第 1 项把部署门到 10-31 判效读数归档之后。
- pre-commit 缓存锁可能坏（已知坑）：跑 `ruff check` + `ruff format --check` 等价检查后按需 `--no-verify`。

---

### Task 1: rejected per-gate 保底分配器（delta ①）

**Files:**
- Modify: `src/finance_agent/factors/feedback.py`（新增 `PER_GATE_FLOOR`、`_allocate_rejected`；`build_payload` 调用）
- Test: `tests/factors/test_feedback.py`（追加 2 个失败测试）

**Interfaces:**
- Produces: `_allocate_rejected(entries: list[dict], budget: int, floor: int = 5) -> tuple[list[dict], dict[str, int]]`（返回选中条目闸号升序拼接 + `{"2": n, ...}` 分配读数）

- [ ] **Step 1: 失败测试**（真实比例合成数据）

```python
def test_build_payload_gate_floor_breaks_gate2_monopoly() -> None:
    processed: dict[str, dict] = {}
    for i in range(395):
        processed[f"g2_{i}"] = {"status": "rejected", "expression": f"sub(div(close,ref(close,{i + 2})),1)", "gate": 2, "reason": "WEAK_ICIR", "at": "2026-10-27T16:00:00+08:00"}
    for i in range(84):
        processed[f"g4_{i}"] = {"status": "rejected", "expression": f"mul(sub(div(volume,ts_mean(volume,20)),1),{i + 1})", "gate": 4, "reason": "REDUNDANT_WITH_POOL", "at": "2026-10-26T16:00:00+08:00"}
    for i in range(12):
        processed[f"adm_{i}"] = {"status": "admitted", "expression": f"div(volume,ts_mean(volume,{i + 5}))", "at": "2026-10-20T16:00:00+08:00"}
    for i in range(30):
        processed[f"unt_{i}"] = {"status": "untranslatable", "stage": "translator", "detail": "复杂度超限", "at": "2026-10-27T09:00:00+08:00"}
    payload = build_payload(processed, {}, limit=80)
    gates = [e["gate"] for e in payload["explored"] if e["status"] == "rejected"]
    assert sum(1 for g in gates if g == 4) >= 5          # 闸④ 保底命中（现状恒 0 → 红）
    assert sum(1 for g in gates if g == 2) < 48          # 闸② 不独占 rejected 档
    assert payload["returned"] == 80                     # 总量不被保底机制缩水

def test_build_payload_gate_floor_underfull_gate_fully_shown() -> None:
    processed = {f"g2_{i}": {"status": "rejected", "expression": f"sub(div(close,ref(close,{i + 2})),1)", "gate": 2, "reason": "WEAK_ICIR", "at": "2026-10-27T16:00:00+08:00"} for i in range(100)}
    processed["g6_a"] = {"status": "rejected", "expression": "sub(close,ref(close,1))", "gate": 6, "reason": "X", "at": "2026-10-27T16:00:00+08:00"}
    processed["g6_b"] = {"status": "rejected", "expression": "sub(close,ref(close,3))", "gate": 6, "reason": "X", "at": "2026-10-27T15:00:00+08:00"}
    payload = build_payload(processed, {}, limit=20)
    g6 = [e for e in payload["explored"] if e["gate"] == 6]
    assert len(g6) == 2                                  # 不足保底 → 全量出场（现状 0 → 红）
    assert payload["returned"] == 20
```

- [ ] **Step 2: 跑红** `uv run pytest tests/factors/test_feedback.py -k "gate_floor" -v` → 2 FAIL
- [ ] **Step 3: 实现** `_allocate_rejected`（保底闸号升序逐闸取满 → 余量按剩余池占比最大余数法 → 池耗尽缺额按闸号升序回填；预算 `min(budget, len(entries))` 截顶）
- [ ] **Step 4: 跑绿** 同命令 PASS + 全文件 PASS（既有 13 测试零回归）
- [ ] **Step 5: commit** `test+feat: 回喂 rejected 档 per-gate 保底分配（add-feedback-quota-fairness）`

### Task 2: untranslatable stage 代表性 + 配额读数（delta ①）

**Files:** 同 Task 1 两文件。

- [ ] **Step 1: 失败测试**

```python
def test_build_payload_untranslatable_stage_representativeness() -> None:
    processed: dict[str, dict] = {}
    for i in range(300):
        processed[f"tr_{i}"] = {"status": "untranslatable", "stage": "translator", "detail": "复杂度超限", "at": f"2026-10-25T16:{i % 60:02d}:00+08:00"}
    for i in range(60):
        processed[f"ll_{i}"] = {"status": "untranslatable", "stage": "llm", "detail": "缺少横截面算子", "at": f"2026-10-25T17:{i % 60:02d}:00+08:00"}
    payload = build_payload(processed, {}, limit=80)
    stages = [e["reason"].split("（")[0].removeprefix("untranslatable:") for e in payload["explored"] if e["status"] == "untranslatable"]
    assert set(stages) == {"translator", "llm"}          # 单一 stage 不得独占（现状 llm=0 → 红）
    assert 0 < stages.count("llm") < 20

def test_build_payload_quota_allocation_readout_consistent() -> None:
    # 沿用上一测试的 processed 构造（独立复制一份避免测试间耦合）
    payload = build_payload(processed, {}, limit=80)
    alloc = payload["quota_allocation"]                  # 现状 KeyError → 红
    rej_gates = [str(e["gate"]) for e in payload["explored"] if e["status"] == "rejected"]
    assert alloc["rejected_by_gate"] == {g: rej_gates.count(g) for g in set(rej_gates)}
    unt_stages = [e["reason"].split("（")[0].removeprefix("untranslatable:") for e in payload["explored"] if e["status"] == "untranslatable"]
    assert alloc["untranslatable_by_stage"] == {s: unt_stages.count(s) for s in set(unt_stages)}
```

- [ ] **Step 2: 跑红** → 2 FAIL
- [ ] **Step 3: 实现** `_select_untranslatable` 内层按 stage 交织（day 外层不变）；`_entry_stage` 解析 helper；payload 增 `quota_allocation`（从 trim 后 `explored` 实际统计，保证与清单一致）
- [ ] **Step 4: 跑绿** 全文件 PASS
- [ ] **Step 5: commit** `feat: untranslatable stage 交织 + quota_allocation 读数`

### Task 3: 真实数据回放 + lint + delta ① 收口

- [ ] 主检出土产数据回放（不提交）：`cd .worktrees/add-feedback-quota-fairness && uv run python -c "…export_feedback(state=主检出 harvest_state.json, candidates=…, out=tmp)"` 断言闸④出场 ≥5、闸② < rejected 档全位、`quota_allocation` 在
- [ ] `uv run ruff check src/finance_agent/factors/feedback.py tests/factors/test_feedback.py` + `ruff format --check` 同文件
- [ ] `uv run pytest tests/factors/test_feedback.py tests/factors/test_explored_feedback_consumer.py -q` 全绿
- [ ] 更新 `openspec/changes/add-feedback-quota-fairness/tasks.md` 勾选；commit

### Task 4: 模板契约段渲染函数（delta ②）

**Files:**
- Modify: `tests/scripts/spike/explored_feedback.py`（新增 `TEMPLATE_CONTRACT_MAX_CHARS = 1600`、`render_contract_block`）
- Test: `tests/factors/test_explored_feedback_consumer.py`（追加失败测试）

**Interfaces:**
- Produces: `render_contract_block(path: str | None = None) -> str`——同源读载荷 `contract` 块；规则全量、复杂度全量、算子摘要（总数 + 前 8 例）、字段全量；超 1600 截断标注；源缺失/损坏/无契约 → `""`

- [ ] **Step 1: 失败测试**（复用 consumer 测试的 `_load_consumer`/`_write_payload`/`_CONTRACT`）

```python
def test_template_contract_block_renders_summary(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(tmp_path, {"contract": _CONTRACT, "explored": []})
    block = mod.render_contract_block(path)
    assert "binding" in block
    for token in ("epsilon", "Complexity limits: max_ast_depth <= 8", "$close"):
        assert token in block
    assert "3 kinds in total" in block and "Mean" in block   # 算子摘要=总数+样例

def test_template_contract_block_fails_safe(tmp_path: Path) -> None:
    mod = _load_consumer()
    p = tmp_path / "explored.json"; p.write_text("{bad", encoding="utf-8")
    assert mod.render_contract_block(str(p)) == ""
    assert mod.render_contract_block(_write_payload(tmp_path, {"explored": []})) == ""

def test_template_contract_block_budget_bounded(tmp_path: Path) -> None:
    mod = _load_consumer()
    huge = dict(_CONTRACT, rules=["x" * 5000])
    path = _write_payload(tmp_path, {"contract": huge, "explored": []})
    block = mod.render_contract_block(path)
    assert len(block) <= mod.TEMPLATE_CONTRACT_MAX_CHARS + 15 and "(truncated)" in block

def test_template_contract_consistent_with_context_contract(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(tmp_path, {"contract": _CONTRACT, "gate_rejections": {"2": 322}, "explored": []})
    tpl, ctx = mod.render_contract_block(path), mod.render_explored_block(path)
    assert "max_ast_depth <= 8" in tpl and "max_ast_depth <= 8" in ctx   # 同源数值一致
```

- [ ] **Step 2: 跑红** `uv run pytest tests/factors/test_explored_feedback_consumer.py -k template_contract -v` → 4 FAIL（`render_contract_block` 不存在）
- [ ] **Step 3: 实现** `render_contract_block`（fail-safe 复刻 `render_explored_block` 的加载语义）
- [ ] **Step 4: 跑绿** 全文件 PASS（既有 5 测试零回归）
- [ ] **Step 5: commit** `feat: distro 消费模块新增模板契约段渲染函数`

### Task 5: distro 补丁脚本 + 补丁单测（delta ②）

**Files:**
- Create: `tests/scripts/spike/patch-prompt-contract.py`
- Test: `tests/factors/test_patch_prompt_contract.py`（importlib 按文件加载，覆写 `module.TARGET` 指向 tmp 桩文件）

**Interfaces:**
- Consumes: distro `factor_proposal.py` 精确 OLD 块（`context_dict = {` … `return context_dict, True`，含 explored 补丁后现状）；import 行 `from rdagent.scenarios.qlib.proposal.explored_feedback import render_explored_block`
- Produces: `main() -> int`（0=已打/新打，1=abort），幂等（MARKER=`render_contract_block()`）、备份 `.bak-20261028-contract`、锚点缺失 abort、`py_compile` 收尾

- [ ] **Step 1: 失败测试**——tmp 桩文件（含 OLD 块全文）：fresh patch → 桩文件含 `render_contract_block`、`_spec_text`、import 合并；rerun → `already patched` rc 0；锚点破坏 → rc 1
- [ ] **Step 2: 跑红**（模块缺 `patch-prompt-contract.py` → 加载失败即红；最小先建空壳再补实现亦可按 TDD 三步走）
- [ ] **Step 3: 实现** 补丁脚本（OLD/NEW 逐字取自 distro 现状，见 design；import 三分支：已有 contract → 跳过 / 旧 import 行 → 替换合并 / 无 → 锚点后插入）
- [ ] **Step 4: 跑绿** + `tests/factors/` 全量 PASS
- [ ] **Step 5: commit** `feat: prompt 本体契约注入补丁脚本（幂等+探针+备份）`

### Task 6: delta ② 收口

- [ ] `ruff check` + `ruff format --check` 三个触碰文件
- [ ] 更新 delta ② `tasks.md` 勾选（部署/判效两项保持未勾——门到 10-31）；commit
- [ ] 两分支各派 general-purpose 审查 agent（全分支 diff）→ 修复 → 推送 → `gh pr create`
