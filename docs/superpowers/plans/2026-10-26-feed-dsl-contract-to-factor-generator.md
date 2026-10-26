# feed-dsl-contract-to-factor-generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 跨会话记忆桥载荷携带机器可读 DSL 契约块，生成侧消费端渲染进首轮 prompt——让 RD-Agent 在 DSL 表达边界内提候选。

**Architecture:** 宿主侧 `feedback.py` 在 payload 增加 `contract`（import 单一真相源取值）与 `gate_rejections` 聚合；distro 消费端 `tests/scripts/spike/explored_feedback.py` 数据驱动渲染契约段（先于已探索清单，共享字符预算，缺契约回落旧行为）。五道闸与转译等价性契约零改动。

**Tech Stack:** Python 3.14（后端仓）、stdlib json/importlib、pytest；部署经 wsl 覆盖 distro site-packages。

## Global Constraints

- 契约复杂度数值 MUST import 自 `finance_agent.factors.dsl`（`MAX_AST_DEPTH`/`MAX_FREE_PARAMS`/`MAX_FIELDS`），MUST NOT 复制字面量
- 算子/字段白名单 MUST 取自 `finance_agent.factors.qlib_translate.OPERATOR_MAPPINGS`/`FIELD_MAPPINGS` 中**有 DSL 对应**（`dsl_name`/`dsl_field` 非 None）的条目
- 消费端契约缺失/字段异常 MUST fail-safe（仅渲染已探索清单或空串），MUST NOT 阻断生成、MUST NOT 虚构契约
- 渲染总量 ≤ `MAX_CHARS`（9000）且超限截断标注（既有语义）
- 转译契约「禁止猜测」语义（`prompts/factor_transcription.md`）零改动
- 规则文本三条：数值常量必须具体（禁 ε/epsilon 符号）；禁横截面（当日跨标的）运算；禁矩阵/回归/ML 流水线

---

### Task 1: 宿主侧 contract 块与 gate_rejections 聚合

**Files:**
- Modify: `src/finance_agent/factors/feedback.py`
- Modify: `scripts/export_factor_feedback.py`
- Test: `tests/factors/test_feedback.py`

**Interfaces:**
- Consumes: `finance_agent.factors.dsl.{MAX_AST_DEPTH, MAX_FREE_PARAMS, MAX_FIELDS}`；`finance_agent.factors.qlib_translate.{OPERATOR_MAPPINGS, FIELD_MAPPINGS}`
- Produces: `build_payload(...)` 返回 dict 新增键 `contract: {"complexity": {"max_ast_depth": int, "max_free_params": int, "max_fields": int}, "operators": list[str], "fields": list[str], "rules": list[str]}` 与 `gate_rejections: dict[str, int]`（键为 str(gate)）；模块常量 `CONTRACT_RULES: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

在 `tests/factors/test_feedback.py` 末尾追加：

```python
def test_build_payload_carries_dsl_contract_single_sourced() -> None:
    from finance_agent.factors.dsl import MAX_AST_DEPTH, MAX_FIELDS, MAX_FREE_PARAMS
    from finance_agent.factors.feedback import CONTRACT_RULES
    from finance_agent.factors.qlib_translate import FIELD_MAPPINGS, OPERATOR_MAPPINGS

    payload = build_payload({}, {})
    contract = payload["contract"]
    assert contract["complexity"] == {
        "max_ast_depth": MAX_AST_DEPTH,
        "max_free_params": MAX_FREE_PARAMS,
        "max_fields": MAX_FIELDS,
    }
    expected_ops = sorted(k for k, m in OPERATOR_MAPPINGS.items() if m.dsl_name)
    expected_fields = sorted(k for k, f in FIELD_MAPPINGS.items() if f.dsl_field)
    assert sorted(contract["operators"]) == expected_ops
    assert sorted(contract["fields"]) == expected_fields
    assert contract["rules"] == list(CONTRACT_RULES)
    joined = " ".join(CONTRACT_RULES).lower()
    assert "epsilon" in joined and "cross-sectional" in joined and "machine" in joined


def test_build_payload_gate_rejections_aggregated() -> None:
    processed = {
        "a": {"status": "rejected", "expression": "sub(close,ref(close,1))", "gate": 2, "reason": "WEAK_ICIR", "at": "2026-10-21T16:00:00+08:00"},
        "b": {"status": "rejected", "expression": "sub(close,ref(close,2))", "gate": 4, "reason": "NO_POOL_INCREMENT", "at": "2026-10-21T16:00:01+08:00"},
        "c": {"status": "rejected", "expression": "sub(close,ref(close,3))", "gate": 2, "reason": "WEAK_ICIR", "at": "2026-10-21T16:00:02+08:00"},
        "d": {"status": "admitted", "expression": "div(volume,ts_mean(volume,20))", "at": "2026-10-21T16:00:03+08:00"},
    }
    payload = build_payload(processed, {})
    assert payload["gate_rejections"] == {"2": 2, "4": 1}


def test_cli_prints_contract_presence(tmp_path, capsys) -> None:
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    out = tmp_path / "explored.json"
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / "export_factor_feedback.py"), "--out", str(out)],
        capture_output=True, text=True, check=True, cwd=repo,
    )
    assert "contract=yes" in proc.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_feedback.py -k "contract or gate_rejections" -v`
Expected: FAIL（KeyError: 'contract' / 'gate_rejections'；CLI 断言 contract=yes 失败）

- [ ] **Step 3: Write minimal implementation**

`src/finance_agent/factors/feedback.py`：

```python
# 模块 docstring 口径清单追加一行：
# - ``contract`` 块（复杂度上限/算子与字段白名单/表达规则）+ ``gate_rejections`` 聚合——
#   生成侧表达契约回喂（feed-dsl-contract-to-factor-generator），数值 import 单一真相源；

# 既有导入行扩展：
from finance_agent.factors.dsl import (
    MAX_AST_DEPTH,
    MAX_FIELDS,
    MAX_FREE_PARAMS,
    Call,
    Number,
    parse,
    serialize,
)
# 新增导入：
from finance_agent.factors.qlib_translate import FIELD_MAPPINGS, OPERATOR_MAPPINGS

CONTRACT_RULES: tuple[str, ...] = (
    "Numeric constants must be concrete numbers (e.g. write 1e-12); symbolic constants "
    "such as epsilon are inexpressible and the proposal is rejected.",
    "Per-instrument time-series expressions only: cross-sectional (same-day cross-stock) "
    "operations such as cross-sectional percentile rank or market-wide averages are inexpressible.",
    "Matrix operations, rolling regressions, and machine-learning pipelines are outside "
    "the expressible DSL.",
)


def _contract_block() -> dict[str, Any]:
    return {
        "complexity": {
            "max_ast_depth": MAX_AST_DEPTH,
            "max_free_params": MAX_FREE_PARAMS,
            "max_fields": MAX_FIELDS,
        },
        "operators": sorted(k for k, m in OPERATOR_MAPPINGS.items() if m.dsl_name),
        "fields": sorted(k for k, f in FIELD_MAPPINGS.items() if f.dsl_field),
        "rules": list(CONTRACT_RULES),
    }
```

`build_payload` 内：entries 聚合循环中追加 `gate_rejections[str(gate)] += 1`（`_reason` 已解析 gate；仅 status=="rejected" 且 gate 为 int 计数）；返回 dict 增加：

```python
    gate_rejections: dict[str, int] = {}
    # …循环内：if status == "rejected" and isinstance(gate, int): gate_rejections[str(gate)] = gate_rejections.get(str(gate), 0) + 1
    return {
        "generated_at": ...,
        "total_explored": len(entries),
        "returned": len(trimmed),
        "counts": {...},
        "gate_rejections": dict(sorted(gate_rejections.items())),
        "contract": _contract_block(),
        "explored": trimmed,
    }
```

`scripts/export_factor_feedback.py` 摘要 print 追加 `contract={'yes' if 'contract' in payload else 'no'}`：

```python
    print(
        f"wrote {args.out}: explored={payload['total_explored']} returned={payload['returned']} "
        f"admitted={counts.get('admitted', 0)} rejected={counts.get('rejected', 0)} "
        f"untranslatable={counts.get('untranslatable', 0)} contract={'yes' if payload.get('contract') else 'no'}"
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/factors/test_feedback.py -v`
Expected: 全 PASS（含既有 6 条）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/feedback.py scripts/export_factor_feedback.py tests/factors/test_feedback.py
git commit -m "feat(factors): 记忆桥载荷携带 DSL 契约块与闸口拒收聚合（feed-dsl-contract）"
```

---

### Task 2: distro 消费端渲染契约块

**Files:**
- Modify: `tests/scripts/spike/explored_feedback.py`
- Test: `tests/factors/test_explored_feedback_consumer.py`（新建）

**Interfaces:**
- Consumes: Task 1 的载荷键 `contract`（complexity/operators/fields/rules）、`gate_rejections`
- Produces: `render_explored_block(path=None) -> str`（既有签名不变）；新私有函数 `_render_contract(contract: dict, gate_rejections: dict | None) -> list[str]`

- [ ] **Step 1: Write the failing test**

新建 `tests/factors/test_explored_feedback_consumer.py`：

```python
"""distro 消费端渲染块单测（源码按文件加载，与部署件同源）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_CONSUMER_SRC = (
    Path(__file__).resolve().parents[2] / "tests" / "scripts" / "spike" / "explored_feedback.py"
)


def _load_consumer():
    spec = importlib.util.spec_from_file_location("explored_feedback_under_test", _CONSUMER_SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_payload(tmp_path, payload) -> str:
    p = tmp_path / "explored.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


_CONTRACT = {
    "complexity": {"max_ast_depth": 8, "max_free_params": 3, "max_fields": 3},
    "operators": ["Abs", "Mean", "Ref"],
    "fields": ["$close", "$volume"],
    "rules": [
        "Numeric constants must be concrete numbers (e.g. write 1e-12); symbolic epsilon is rejected.",
        "No cross-sectional operations.",
        "No machine-learning pipelines.",
    ],
}


def test_contract_rendered_before_explored(tmp_path) -> None:
    mod = _load_consumer()
    path = _write_payload(
        tmp_path,
        {
            "contract": _CONTRACT,
            "gate_rejections": {"2": 322, "4": 84},
            "explored": [{"name": "mom20", "status": "rejected", "gate": 2, "reason": "WEAK_ICIR"}],
        },
    )
    block = mod.render_explored_block(path)
    assert "Hard contract" in block
    assert block.index("Hard contract") < block.index("mom20")
    for token in ("epsilon", "cross-sectional", "machine-learning", "max_ast_depth <= 8",
                  "Mean", "$close", "gate2=322"):
        assert token in block


def test_missing_contract_falls_back_to_explored_only(tmp_path) -> None:
    mod = _load_consumer()
    path = _write_payload(
        tmp_path, {"explored": [{"name": "mom20", "status": "rejected", "gate": 2}]}
    )
    block = mod.render_explored_block(path)
    assert "Hard contract" not in block
    assert "mom20" in block


def test_contract_only_payload_renders_contract(tmp_path) -> None:
    mod = _load_consumer()
    path = _write_payload(tmp_path, {"contract": _CONTRACT, "explored": []})
    block = mod.render_explored_block(path)
    assert "Hard contract" in block


def test_corrupted_file_renders_empty(tmp_path) -> None:
    mod = _load_consumer()
    p = tmp_path / "explored.json"
    p.write_text("{not json", encoding="utf-8")
    assert mod.render_explored_block(str(p)) == ""


def test_total_length_bounded_with_truncation_marker(tmp_path) -> None:
    mod = _load_consumer()
    items = [{"name": f"f{i}", "status": "rejected", "gate": 2, "desc": "x" * 200} for i in range(500)]
    path = _write_payload(tmp_path, {"contract": _CONTRACT, "explored": items})
    block = mod.render_explored_block(path)
    assert len(block) <= mod.MAX_CHARS + 200
    assert "(truncated)" in block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_explored_feedback_consumer.py -v`
Expected: FAIL（"Hard contract" 不在渲染产物中）

- [ ] **Step 3: Write minimal implementation**

`tests/scripts/spike/explored_feedback.py`：保持既有契约（文件缺失/损坏 → 空串；MAX_CHARS 预算）。改造：

```python
def _render_contract(contract: dict, gate_rejections: dict | None) -> list[str]:
    """契约段（数据驱动：全部取自载荷，不硬编码约束）。字段异常 → 跳过该行（fail-safe）。"""
    lines = [
        "# Hard contract for factor expressions (downstream DSL)",
        "Proposals violating any rule below are rejected BEFORE evaluation:",
    ]
    rules = contract.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, str) and rule.strip():
                lines.append(f"- {rule.strip()}")
    complexity = contract.get("complexity")
    if isinstance(complexity, dict) and complexity:
        parts = [f"{k} <= {v}" for k, v in sorted(complexity.items())]
        lines.append(f"- Complexity limits: {', '.join(parts)}.")
    operators = contract.get("operators")
    if isinstance(operators, list) and operators:
        lines.append(f"- Expressible operators (Qlib form): {', '.join(str(o) for o in operators)}.")
    fields = contract.get("fields")
    if isinstance(fields, list) and fields:
        lines.append(f"- Expressible fields: {', '.join(str(f) for f in fields)}.")
    if isinstance(gate_rejections, dict) and gate_rejections:
        pairs = ", ".join(f"gate{k}={v}" for k, v in sorted(gate_rejections.items()))
        lines.append(f"- Historical gate rejections to date: {pairs} (advisory).")
    return lines
```

`render_explored_block` 主体重排（单一 `used` 预算贯穿契约段与清单段；契约在前；两者皆缺 → ""）：

```python
def render_explored_block(path: str | None = None) -> str:
    resolved = Path(path or os.environ.get("FACTOR_FEEDBACK_PATH", DEFAULT_PATH))
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    items = payload.get("explored")
    contract = payload.get("contract")
    gate_rejections = payload.get("gate_rejections")
    has_contract = isinstance(contract, dict)
    has_items = isinstance(items, list) and bool(items)
    if not has_contract and not has_items:
        return ""

    lines: list[str] = []
    used = 0
    if has_contract:
        for line in _render_contract(contract, gate_rejections if isinstance(gate_rejections, dict) else None):
            lines.append(line)
            used += len(line) + 1
        lines.append("")
        used += 1
    if has_items:
        lines += [
            "=========================================================",
            "# Cross-session memory: already explored factors",
            "The factors below were already proposed and evaluated by the downstream 5-gate library.",
            "Do NOT propose the same hypotheses or trivial variants (renamed windows, epsilon guards, "
            "rescalings). Prefer genuinely different mechanisms and data transformations.",
            "",
        ]
        used += sum(len(line) + 1 for line in lines[-5:])
        for item in items:
            if not isinstance(item, dict):
                continue
            line = _format_item(item)
            if used + len(line) + 1 > MAX_CHARS:
                lines.append("- ... (truncated)")
                break
            lines.append(line)
            used += len(line) + 1
    lines.append("=========================================================")
    return "\n".join(lines)
```

（模块 docstring 同步补一句：契约段先于清单渲染、缺契约回落旧行为。）

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/factors/test_explored_feedback_consumer.py tests/factors/test_feedback.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/scripts/spike/explored_feedback.py tests/factors/test_explored_feedback_consumer.py
git commit -m "feat(factors): 生成侧消费端渲染 DSL 契约段（先契约后清单，fail-safe）"
```

---

### Task 3: 部署与实跑验证（操作性任务）

**Files:**
- 无仓内代码变更；产物落 `tests/validation/2026-10-26-feed-dsl-contract-to-factor-generator-validation.md`

**Interfaces:**
- Consumes: Task 1/2 产物
- Produces: distro site-packages 新消费端、宿主手动导出的带契约 `explored_factors.json`、实跑渲染证据

- [ ] **Step 1: 部署消费端到 distro**（运行中进程不受影响，下一 rdagent 进程生效）

```bash
wsl.exe -d rdagent -u root -e bash -c "tr -d '\r' > /opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/explored_feedback.py" < tests/scripts/spike/explored_feedback.py
```

- [ ] **Step 2: 宿主手动导出（当日研究循环即可见契约）**

```bash
uv run python scripts/export_factor_feedback.py
```

Expected stdout 含 `contract=yes`

- [ ] **Step 3: distro 内实跑渲染验证**

```bash
wsl.exe -d rdagent -u root -e python3 /opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/explored_feedback.py
```

Expected 输出含 `Hard contract`、`max_ast_depth <= 8`、`epsilon`、算子白名单

- [ ] **Step 4: 全量回归 + ruff + mypy**

```bash
uv run pytest tests/factors/ -q && uv run ruff check && uv run mypy src/finance_agent/factors/feedback.py
```

- [ ] **Step 5: 人工验证报告落 tests/validation/**（含 Step 2/3 实跑输出摘录）

- [ ] **Step 6: 后端容器重建**（前置：`GET /api/sessions` 无 running + `GET /api/v1/ops/jobs` 无 running + 避开 15:30–17:10 / 17:55–20:45 红线时窗）——使日批 15:55 自动导出带契约块
