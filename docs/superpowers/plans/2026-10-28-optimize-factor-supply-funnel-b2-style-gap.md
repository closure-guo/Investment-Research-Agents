# B2 风格缺口定向回喂 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回喂载荷新增「风格缺口定向块」（active 池画像 + per-family 拒收聚合），消费端渲染为披露段，让 RD-Agent 提案避开量价红海。

**Architecture:** 生产侧在 `factors/feedback.py` 增 `_style_gap_block(db_path)`（读因子库 `factors` 表 + `created` 事件 detail.gate，任一失败整块省略 fail-safe），经 `export_feedback` 注入载荷 `style_gap` 键；消费侧 `tests/scripts/spike/explored_feedback.py`（distro 部署件同源）的 `render_explored_block` 在契约段之后、已探索清单之前渲染披露段（只增不改既有行）。distro 补丁重部署随 A1（10-31）窗口，不在本计划内。

**Tech Stack:** Python 3.12 / pytest / ruff / mypy；零新依赖。

## Global Constraints

- 契约逐字（specs/factor-proposal/spec.md）：数据驱动派生（单一真相源 = 因子库 `factors` 表 + `created` 事件 detail）；不可得整块省略，MUST NOT 空清单/0/占位冒充；只披露，MUST NOT 携带改变既有任务指令语义的硬性指令；既有语义零回退（admitted 全量在前 / 规范化去重 / `limit` 上界 / `contract` / `gate_rejections` / 配额公平性分配照常）。
- 分支放 `.worktrees/optimize-factor-supply-funnel`（主检出被并发会话占用，memory: concurrent-sessions-shared-checkout）。
- pre-commit 缓存锁可能坏（已知坑）：`ruff check` + `ruff format --check` 等价检查后按需 `--no-verify`。
- mypy `files=["src"]`：新增 src 代码必须全类型注解。
- distro 消费端部署件 = `tests/scripts/spike/explored_feedback.py` 整文件复制（`tr -d '\r'`），本计划只改仓库内同源文件。

---

### Task 1: 生产侧——`_style_gap_block` + 载荷注入

**Files:**
- Modify: `src/finance_agent/factors/feedback.py`
- Test: `tests/factors/test_feedback.py`

**Interfaces:**
- Consumes: `library.list_factors(*, status, db_path)` → `list[dict]`（行含 `category`）；`library.list_events(db_path)` → `list[dict]`（行含 `factor_id`/`event`/`detail`，rejected 行 `created` 事件 `detail["gate"]: int`）
- Produces: `_style_gap_block(db_path: str | Path | None) -> dict[str, Any] | None`；`build_payload(..., style_gap: Mapping[str, Any] | None = None)`（新 keyword-only 尾参）；`export_feedback(..., library_db_path: str | Path | None = None)`

- [ ] **Step 1: 失败测试**（追加到 `tests/factors/test_feedback.py`）

```python
def test_build_payload_includes_style_gap_block() -> None:
    processed = {
        "mom20": {
            "status": "admitted",
            "expression": "sub(div(volume,ts_mean(volume,20)),1)",
            "at": "2026-10-20T16:00:00+08:00",
        }
    }
    style_gap = {
        "pool_by_category": {"量与流动性": 8},
        "rejections_by_category": {"动量反转": 36},
        "rejections_by_category_gate": {"动量反转": {"2": 30, "4": 6}},
    }
    payload = build_payload(processed, {}, style_gap=style_gap)
    assert payload["style_gap"] == style_gap


def test_build_payload_omits_style_gap_by_default_and_when_empty() -> None:
    processed = {
        "mom20": {
            "status": "admitted",
            "expression": "sub(div(volume,ts_mean(volume,20)),1)",
            "at": "2026-10-20T16:00:00+08:00",
        }
    }
    assert "style_gap" not in build_payload(processed, {})
    assert "style_gap" not in build_payload(processed, {}, style_gap={})


def test_style_gap_block_reads_factor_library(tmp_path) -> None:
    from finance_agent.factors import library
    from finance_agent.factors.feedback import _style_gap_block

    db = tmp_path / "factor_library.db"
    library.add_factor(
        expression="div(volume,ts_mean(volume,20))",
        hypothesis="量比",
        category="量与流动性",
        status="active",
        db_path=db,
    )
    for gate in (2, 4):
        library.record_rejected_factor(
            expression=f"sub(close,ref(close,{gate}))",
            hypothesis="反转",
            category="动量反转",
            source="proposal",
            gate=gate,
            reason="WEAK_ICIR" if gate == 2 else "REDUNDANT_WITH_POOL",
            detail=None,
            db_path=db,
        )
    block = _style_gap_block(str(db))
    assert block is not None
    assert block["pool_by_category"] == {"量与流动性": 1}
    assert block["rejections_by_category"] == {"动量反转": 2}
    assert block["rejections_by_category_gate"] == {"动量反转": {"2": 1, "4": 1}}


def test_style_gap_block_fail_safe_on_unreadable_db(tmp_path) -> None:
    from finance_agent.factors.feedback import _style_gap_block

    bad = tmp_path / "not-a-db"
    bad.write_text("this is not sqlite", encoding="utf-8")
    assert _style_gap_block(str(bad)) is None
    assert _style_gap_block(str(tmp_path / "missing.db")) is None


def test_style_gap_block_empty_library_returns_none(tmp_path) -> None:
    from finance_agent.factors.feedback import _style_gap_block

    assert _style_gap_block(str(tmp_path / "fresh.db")) is None


def test_export_feedback_wires_style_gap(tmp_path) -> None:
    from finance_agent.factors import library

    db = tmp_path / "factor_library.db"
    library.add_factor(
        expression="div(volume,ts_mean(volume,20))",
        hypothesis="量比",
        category="量与流动性",
        status="active",
        db_path=db,
    )
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "processed": {
                    "mom20": {
                        "status": "admitted",
                        "expression": "div(volume,ts_mean(volume,20))",
                        "at": "2026-10-20T16:00:00+08:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    out = tmp_path / "explored.json"
    payload = export_feedback(
        state_path=state, candidates_path=candidates, output_path=out, library_db_path=db
    )
    assert payload["style_gap"]["pool_by_category"] == {"量与流动性": 1}
```

- [ ] **Step 2: 跑红** `uv run pytest tests/factors/test_feedback.py -k "style_gap" -v` → 6 FAIL（`style_gap` 未定义 / `_style_gap_block` 未导入）
- [ ] **Step 3: 实现**（`feedback.py` 三处）

① 顶部 import 区追加（`import json` 之后）：

```python
import logging
```

模块常量区（`_MAX_DETAIL_UNTRANSLATABLE = 40` 之后）追加：

```python
logger = logging.getLogger(__name__)
```

import 区（`from finance_agent.factors.qlib_translate import ...` 之后）追加：

```python
from finance_agent.factors.library import list_events, list_factors
```

② `_allocate_rejected` 与 `build_payload` 之间插入：

```python
def _style_gap_block(db_path: str | Path | None) -> dict[str, Any] | None:
    """风格缺口定向块（数据驱动：单一真相源为因子库 ``factors`` 表）。

    两族数据：``pool_by_category``（active 池按类别在册因子数）与
    ``rejections_by_category`` / ``rejections_by_category_gate``（拒绝池按类别、
    类别 × 闸口聚合的被拒结构数；闸口取该行 ``created`` 事件 ``detail["gate"]``，
    缺失记 ``"unknown"``）。库读失败（缺失/损坏/锁）或两族全空 → ``None``
    （fail-safe：整块省略，不冒充；spec「画像不可得时整块省略」）。只作披露。
    """
    try:
        pool_rows = list_factors(status="active", db_path=db_path)
        rejected_rows = list_factors(status="rejected", db_path=db_path)
        gate_by_factor: dict[int, int] = {}
        for event in list_events(db_path=db_path):
            if str(event.get("event")) != "created":
                continue
            detail = event.get("detail")
            gate = detail.get("gate") if isinstance(detail, dict) else None
            if isinstance(gate, int) and not isinstance(gate, bool):
                gate_by_factor[int(event["factor_id"])] = gate
    except Exception:  # noqa: BLE001 - fail-safe：库不可得 → 整块省略
        logger.warning("style gap block unavailable (factor library read failed)", exc_info=True)
        return None
    pool_by_category: dict[str, int] = {}
    for row in pool_rows:
        category = str(row.get("category") or "")
        if category:
            pool_by_category[category] = pool_by_category.get(category, 0) + 1
    rejections_by_category: dict[str, int] = {}
    rejections_by_category_gate: dict[str, dict[str, int]] = {}
    for row in rejected_rows:
        category = str(row.get("category") or "")
        if not category:
            continue
        rejections_by_category[category] = rejections_by_category.get(category, 0) + 1
        gate_key = str(gate_by_factor.get(int(row["id"]), "unknown"))
        by_gate = rejections_by_category_gate.setdefault(category, {})
        by_gate[gate_key] = by_gate.get(gate_key, 0) + 1
    if not pool_by_category and not rejections_by_category:
        return None
    return {
        "pool_by_category": dict(sorted(pool_by_category.items())),
        "rejections_by_category": dict(sorted(rejections_by_category.items())),
        "rejections_by_category_gate": {
            category: dict(sorted(by_gate.items()))
            for category, by_gate in sorted(rejections_by_category_gate.items())
        },
    }
```

③ `build_payload` 签名追加 keyword-only 尾参并改返回：

```python
def build_payload(
    processed: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = DEFAULT_LIMIT,
    untranslatable_quota: int = UNTRANSLATABLE_QUOTA,
    gate_floor: int = PER_GATE_FLOOR,
    style_gap: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
```

docstring「分档顺序」段末尾追加一行：`风格缺口定向块（``style_gap``，可省）按数据可用性附加。` 返回段改为：

```python
    payload: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "total_explored": len(entries),
        "returned": len(trimmed),
        "counts": {
            status: sum(1 for e in entries if e["status"] == status) for status in _STATUS_ORDER
        },
        "gate_rejections": dict(sorted(gate_rejections.items())),
        "quota_allocation": {
            "rejected_by_gate": dict(sorted(rejected_by_gate.items())),
            "untranslatable_by_stage": dict(sorted(untranslatable_by_stage.items())),
        },
        "contract": _contract_block(),
        "explored": trimmed,
    }
    if style_gap:
        payload["style_gap"] = dict(style_gap)
    return payload
```

`export_feedback` 签名追加 `library_db_path: str | Path | None = None`（`gate_floor` 之后），`build_payload` 调用前加一行 `style_gap = _style_gap_block(library_db_path)`，调用处传 `style_gap=style_gap`；docstring「一步导出」行改为「一步导出（CLI 与测试共用）：返回写出载荷（含风格缺口定向块，库不可得时省略）。」

- [ ] **Step 4: 跑绿** `uv run pytest tests/factors/test_feedback.py -v` → 全过（含 6 个新测试与既有零回退）
- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/factors/feedback.py tests/factors/test_feedback.py
git commit -m "feat(factors): 回喂载荷新增风格缺口定向块 (optimize-factor-supply-funnel B2)"
```

---

### Task 2: CLI 参数透传

**Files:**
- Modify: `scripts/export_factor_feedback.py`

**Interfaces:**
- Consumes: Task 1 的 `export_feedback(..., library_db_path: str | Path | None = None)`
- Produces: CLI `--library-db`（缺省走 `FACTOR_LIBRARY_DB_PATH`/`data/factor_library.db`）

- [ ] **Step 1: 实现**——`--gate-floor` 参数之后追加：

```python
    parser.add_argument(
        "--library-db",
        default=None,
        help="因子库路径（风格缺口定向块数据源；缺省走 FACTOR_LIBRARY_DB_PATH 环境变量）",
    )
```

`export_feedback(...)` 调用追加 `library_db_path=args.library_db,`。

- [ ] **Step 2: 验证** `uv run python scripts/export_factor_feedback.py --help` → 含 `--library-db`；`uv run python scripts/export_factor_feedback.py --library-db data/factor_library.db --out tmp/b2-explored.json` → 退出码 0，`tmp/b2-explored.json` 含 `style_gap` 块（真实库读数：`pool_by_category` 应为 `{"外部": 10}` 一类）
- [ ] **Step 3: 提交**

```bash
git add scripts/export_factor_feedback.py
git commit -m "feat(factors): 导出 CLI 透传 --library-db (optimize-factor-supply-funnel B2)"
```

---

### Task 3: 消费侧——披露段渲染

**Files:**
- Modify: `tests/scripts/spike/explored_feedback.py`
- Test: `tests/factors/test_explored_feedback_consumer.py`

**Interfaces:**
- Consumes: Task 1 载荷 `style_gap: {pool_by_category, rejections_by_category, rejections_by_category_gate}`
- Produces: `render_explored_block` 在契约段之后、`# Cross-session memory` 头之前渲染 `# Style gap (disclosure only — advisory, no hard rule):` 段；块缺失/损坏时既有输出逐字不变

- [ ] **Step 1: 失败测试**（追加到 `test_explored_feedback_consumer.py`）

```python
def _style_gap_payload() -> dict:
    return {
        "contract": _CONTRACT,
        "gate_rejections": {"2": 322},
        "style_gap": {
            "pool_by_category": {"量与流动性": 8, "外部": 2},
            "rejections_by_category": {"动量反转": 36, "量与流动性": 49},
            "rejections_by_category_gate": {
                "动量反转": {"2": 30, "4": 6},
                "量与流动性": {"2": 41, "4": 8},
            },
        },
        "explored": [
            {"name": "mom20", "status": "rejected", "gate": 2, "reason": "WEAK_ICIR"}
        ],
    }


def test_style_gap_rendered_between_contract_and_explored(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(tmp_path, _style_gap_payload())
    block = mod.render_explored_block(path)
    assert "Style gap" in block
    assert "量与流动性×8" in block
    assert "gate2×30" in block
    assert block.index("Hard contract") < block.index("Style gap") < block.index("mom20")


def test_style_gap_absent_keeps_legacy_output(tmp_path: Path) -> None:
    mod = _load_consumer()
    base_payload = {k: v for k, v in _style_gap_payload().items() if k != "style_gap"}
    base = mod.render_explored_block(_write_payload(tmp_path / "a", base_payload))
    styled = mod.render_explored_block(_write_payload(tmp_path / "b", _style_gap_payload()))
    marker = "# Cross-session memory"
    assert base[base.index(marker):] == styled[styled.index(marker):]
    assert base.splitlines()[0] == styled.splitlines()[0]
    assert "Style gap" not in base


def test_style_gap_malformed_fields_fail_safe(tmp_path: Path) -> None:
    mod = _load_consumer()
    payload = _style_gap_payload()
    payload["style_gap"] = {
        "pool_by_category": {"x": "not-int", 3: None},
        "rejections_by_category_gate": {"fam": "not-a-dict", "ok": {"2": "bad", "3": 1}},
    }
    path = _write_payload(tmp_path, payload)
    block = mod.render_explored_block(path)
    assert "mom20" in block  # 损坏字段不致命，清单照常
    assert "Style gap" in block


def test_style_gap_only_payload_renders_style_gap(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(
        tmp_path,
        {"style_gap": {"pool_by_category": {"量与流动性": 8}}},
    )
    block = mod.render_explored_block(path)
    assert "Style gap" in block
    assert "Hard contract" not in block
    assert "量与流动性×8" in block
```

- [ ] **Step 2: 跑红** `uv run pytest tests/factors/test_explored_feedback_consumer.py -k "style_gap" -v` → 4 FAIL（无 Style gap 段）
- [ ] **Step 3: 实现**——`_render_contract` 之后插入：

```python
def _render_style_gap(style_gap: dict) -> list[str]:
    """风格缺口段（数据驱动披露；字段异常逐项跳过，全不可用 → 空段）。"""
    lines = ["# Style gap (disclosure only — advisory, no hard rule):"]
    pool = style_gap.get("pool_by_category")
    if isinstance(pool, dict) and pool:
        parts = [f"{k}×{v}" for k, v in sorted(pool.items()) if isinstance(v, int)]
        if parts:
            lines.append(f"- Active pool composition by family: {', '.join(parts)}.")
    rejections = style_gap.get("rejections_by_category")
    by_gate = style_gap.get("rejections_by_category_gate")
    if isinstance(rejections, dict) and rejections:
        parts: list[str] = []
        for family in sorted(rejections):
            total = rejections[family]
            if not isinstance(total, int):
                continue
            piece = f"{family}×{total}"
            if isinstance(by_gate, dict) and isinstance(by_gate.get(family), dict):
                gate_parts = [
                    f"gate{g}×{n}"
                    for g, n in sorted(by_gate[family].items())
                    if isinstance(n, int)
                ]
                if gate_parts:
                    piece += f" ({', '.join(gate_parts)})"
            parts.append(piece)
        if parts:
            lines.append(f"- Rejected structures by family: {'; '.join(parts)}.")
    return lines if len(lines) > 1 else []
```

`render_explored_block` 三处改动：

① `has_items` 判定之后加一行：

```python
    style_gap = payload.get("style_gap")
    has_style_gap = isinstance(style_gap, dict) and bool(style_gap)
```

② 早退条件改为：

```python
    if not has_contract and not has_items and not has_style_gap:
        return ""
```

③ 契约段渲染块（`if has_contract:` 的循环与空行）之后、`if has_items:` 之前插入：

```python
    if has_style_gap:
        for line in _render_style_gap(style_gap):
            lines.append(line)
            used += len(line) + 1
        lines.append("")
        used += 1
```

模块 docstring 末段（契约描述之后）追加一行：`optimize-factor-supply-funnel B2：载荷含 ``style_gap`` 块时，在契约段与已探索清单之间渲染风格缺口披露段（只披露，无硬性指令；缺失/损坏 → 跳过该段，既有输出逐字不变）。`

- [ ] **Step 4: 跑绿** `uv run pytest tests/factors/test_explored_feedback_consumer.py tests/factors/test_feedback.py tests/factors/test_harvest.py -v` → 全过
- [ ] **Step 5: 提交**

```bash
git add tests/scripts/spike/explored_feedback.py tests/factors/test_explored_feedback_consumer.py
git commit -m "feat(factors): 消费端渲染风格缺口披露段 (optimize-factor-supply-funnel B2)"
```

---

### Task 4: 全量验证 + 真实数据核对 + 收尾

**Files:**
- Modify: `openspec/changes/optimize-factor-supply-funnel/tasks.md`（勾 B2）

- [ ] **Step 1: 门禁** `uv run ruff check && uv run ruff format --check` → 0 错；`uv run mypy src/finance_agent/factors/feedback.py` → 0 错；`uv run pytest tests/factors -q` → 全过
- [ ] **Step 2: 真实数据核对**（只读导出到 tmp，不触碰生产 `explored_factors.json`）

```bash
uv run python scripts/export_factor_feedback.py --library-db data/factor_library.db --out tmp/b2-verify-explored.json
python -c "import json; p=json.load(open('tmp/b2-verify-explored.json',encoding='utf-8')); print(json.dumps(p.get('style_gap'), ensure_ascii=False, indent=1))"
```

Expected: `pool_by_category == {"外部": 10}`（active 池 10 只全「外部」桶）；`rejections_by_category` 与 191 行拒绝池的类别分布一致（外部-其他 61 / 量与流动性 49 / 动量反转 36 / 波动率 36 / 量价交互 5 / builtin 4）。
- [ ] **Step 3: 渲染抽查**（模拟消费端，无 distro 依赖）

```bash
python -c "
import importlib.util, json
spec = importlib.util.spec_from_file_location('ef', 'tests/scripts/spike/explored_feedback.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.render_explored_block('tmp/b2-verify-explored.json')[:1200])"
```

Expected: 契约段之后出现 `# Style gap (disclosure only — advisory, no hard rule):` 与两行读数；字符预算内。
- [ ] **Step 4: 勾选 + 提交**——`openspec/changes/optimize-factor-supply-funnel/tasks.md` B2 行改 `- [x]`（括注「渲染部署随 A1 10-31 窗口重打 distro 补丁」），提交：

```bash
git add openspec/changes/optimize-factor-supply-funnel/tasks.md
git commit -m "docs(openspec): optimize-factor-supply-funnel B2 验收勾选"
```

## 自审记录

- Spec 覆盖：正常构造（Task 1 test_style_gap_block_reads_factor_library + Task 3 test_style_gap_rendered_...）；整块省略（Task 1 fail-safe/empty + Task 3 absent）；披露不越权（Task 3 legacy_output + 渲染文本无祈使句）；零回退（既有测试全量跑 + Task 3 marker 对比）。✓
- 占位符扫描：无 TBD/TODO；所有代码块完整。✓
- 类型一致性：`_style_gap_block` 返回 `dict[str, Any] | None`，`build_payload(style_gap: Mapping | None)`，`export_feedback(library_db_path)` 三处签名一致。✓
