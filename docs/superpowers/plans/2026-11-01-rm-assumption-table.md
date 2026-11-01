# RM Assumption Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Research Manager 在评级之外输出固定 4 行结构化假设表（H1-H4），证据按辩论论点编号转引，落 state 并渲染进报告「多空辩论结论」节。

**Architecture:** RM 上下文复用辩论层的「R{n} 论点: ①…」编号渲染（抽共用函数），RM 节点新增表校验（非法整表丢弃、不碰 `parse_degraded`）+ 人读表拼进 conclusion 文本（trader/risk_judge 零改动可见）；报告节从结构化键重渲染并剥离 conclusion 中的拼装后缀（避免重复）。报告是 markdown，状态区分用 ✅/⚠️/❌。

**Tech Stack:** Python 3 / LangGraph state dict / pytest + unittest.mock / markdown 报告。

## Global Constraints

- delta：`openspec/changes/add-rm-assumption-table/`（三 capability：agent-prompt-contracts MODIFIED、agent-node-contracts ADDED×2、report-decision-rendering ADDED）
- 假设表固定 4 行，`code` ∈ {H1 竞争格局, H2 成长驱动, H3 盈利质量, H4 估值与预期}；`status` ∈ {成立, 注意, 不成立}；`time_tag` ∈ {short, long, short+long}；`assumption`/`inflection_signal` 非空；`bull_evidence`/`bear_evidence` 至少一侧非空
- 表非法 → 整表丢弃 + `logger.warning` 含原因；MUST NOT 置 `parse_degraded`、MUST NOT 中断；整体 JSON 解析失败沿用既有降级且 `research_manager_assumptions=None`
- 报告：结构化键缺失/None → 现状渲染（分歧卡 + 结论文本），MUST NOT 渲染空表
- 改 prompt 后 MUST 执行 `uv run python scripts/deploy_prompts.py`
- 提交只加本变更文件；`docs/evals/metrics.md` 与 `docs/evals/2026-10-31-*.md` 是他人未提交工作，禁止纳入
- 跑 pytest 前确认 `GET /api/sessions` 无 running（已确认 0）
- 分支 `feat/rm-assumption-table`，origin/main 禁 push，走 PR

---

### Task 1: 辩论消息渲染抽共用函数（行为不变重构）

**Files:**
- Modify: `src/finance_agent/nodes/debate.py:89-112`
- Test: `tests/nodes/test_debate_arguments.py`（追加）

**Interfaces:**
- Produces: `debate_message_fields(msg) -> tuple[str, int, str, list] | None`（role, round, content, key_arguments；非法消息返回 None）
- Produces: `format_debate_message(role: str, rnd: int, content: str, args: list | None) -> str`（有 args → `R{n} 论点: ①a ②b\nrole(R{n}): content`；无 args → `role(R{n}): content`）

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/nodes/test_debate_arguments.py 末尾
from finance_agent.nodes.debate import debate_message_fields, format_debate_message


class TestSharedMessageFormatter:
    """add-rm-assumption-table：编号行渲染抽共用——辩论层与 RM 上下文同一格式。"""

    def test_numbered_line_precedes_content(self):
        out = format_debate_message("bull", 1, "正文", ["份额 35%", "回购 10 亿"])
        assert out == "R1 论点: ①.份额 35% ②.回购 10 亿\nbull(R1): 正文"

    def test_no_args_degrades_to_basic_format(self):
        assert format_debate_message("bear", 2, "正文", []) == "bear(R2): 正文"
        assert format_debate_message("bear", 2, "正文", None) == "bear(R2): 正文"

    def test_fields_from_dict_and_invalid(self):
        assert debate_message_fields({"role": "bull", "round": 1, "content": "c"}) == (
            "bull", 1, "c", []
        )
        assert debate_message_fields("garbage") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/nodes/test_debate_arguments.py::TestSharedMessageFormatter -v`
Expected: FAIL with `ImportError: cannot import name 'debate_message_fields'`

- [ ] **Step 3: Write minimal implementation**

在 `debate.py` 模块级（`_build_debate_context` 之前）新增：

```python
_ARG_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"


def debate_message_fields(msg: object) -> tuple[str, int, str, list] | None:
    """DebateMessage / dict → (role, round, content, key_arguments)；非法消息返回 None。"""
    if hasattr(msg, "role"):
        return msg.role, msg.round, msg.content, list(msg.key_arguments or [])  # type: ignore[union-attr]
    if isinstance(msg, dict):
        return (
            msg.get("role", "?"),
            msg.get("round", 1),
            msg.get("content", ""),
            msg.get("key_arguments") or [],
        )
    return None


def format_debate_message(role: str, rnd: int, content: str, args: list | None) -> str:
    """单条辩论消息标准渲染：「R{n} 论点: ①…②…」编号行 + 「role(R{n}): content」。

    交锋结构化引用（1.10）与 add-rm-assumption-table 共用——论点获得稳定身份，
    辩论者按编号回应（rebuttal_to）、RM 假设表证据按「角色+轮次+论点序号」转引。
    args 为空时退化为无编号行的基础格式。
    """
    arg_line = ""
    if args:
        numbered = " ".join(
            f"{_ARG_MARKS[i] if i < 10 else i + 1}.{a.text if hasattr(a, 'text') else a}"
            for i, a in enumerate(args)
        )
        arg_line = f"R{rnd} 论点: {numbered}\n"
    return f"{arg_line}{role}(R{rnd}): {content}"
```

把 `_build_debate_context` 中 L92-112 的循环替换为：

```python
    history = state.get("debate_history") or []
    if history:
        history_lines = []
        for msg in history:
            fields = debate_message_fields(msg)
            if fields is None:
                continue
            history_lines.append(format_debate_message(*fields))
        sections.append("辩论历史:\n" + "\n".join(history_lines))
```

- [ ] **Step 4: Run tests to verify pass（含既有辩论测试全绿）**

Run: `uv run pytest tests/nodes/test_debate.py tests/nodes/test_debate_arguments.py tests/nodes/test_debate_anchors.py tests/nodes/test_debate_anchor_wiring.py tests/nodes/test_debate_focus.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/debate.py tests/nodes/test_debate_arguments.py
git commit -m "refactor(debate): 辩论消息编号行渲染抽共用函数（add-rm-assumption-table 前置，行为不变）"
```

---

### Task 2: RM 上下文接线编号渲染

**Files:**
- Modify: `src/finance_agent/nodes/research_manager.py:86-95`
- Modify: `openspec/changes/add-rm-assumption-table/specs/agent-node-contracts/spec.md`（降级场景措辞对齐实现）
- Test: `tests/nodes/test_research_manager.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `debate_message_fields` / `format_debate_message`

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/nodes/test_research_manager.py
from finance_agent.models import DebateMessage
from finance_agent.nodes.research_manager import _build_research_context


class TestContextNumbering:
    """spec：RM 上下文复用「R{n} 论点: ①…」编号行；无 key_arguments 退化基础格式。"""

    def test_numbered_arguments_enter_context(self):
        msg = DebateMessage(
            role="bull", round=1, content="看多正文",
            key_arguments=[
                {"text": "份额 35% 创新高", "kind": "data", "anchors": ["x.y"]},
                {"text": "回购提振", "kind": "event", "anchors": ["回购公告"]},
            ],
        )
        ctx = _build_research_context({"analyst_reports": {}, "debate_history": [msg]})
        assert "R1 论点: ①.份额 35% 创新高 ②.回购提振" in ctx
        assert "bull(R1): 看多正文" in ctx

    def test_missing_key_arguments_degrades(self):
        ctx = _build_research_context(
            {"analyst_reports": {}, "debate_history": [{"role": "bear", "round": 1, "content": "空方正文"}]}
        )
        assert "bear(R1): 空方正文" in ctx
        assert "论点:" not in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/nodes/test_research_manager.py::TestContextNumbering -v`
Expected: FAIL（现状渲染为 `bull: 看多正文`，无编号行）

- [ ] **Step 3: Write minimal implementation**

`research_manager.py` 顶部 import 加 `from finance_agent.nodes.debate import debate_message_fields, format_debate_message`；`_build_research_context` 辩论历史段替换为：

```python
    # 辩论历史：编号行渲染与辩论层共用（add-rm-assumption-table）——
    # 假设表证据按「角色+轮次+论点序号」稳定引用；无 key_arguments 退化基础格式
    history = state.get("debate_history") or []
    if history:
        history_lines = []
        for msg in history:
            fields = debate_message_fields(msg)
            if fields is None:
                continue
            history_lines.append(format_debate_message(*fields))
        sections.append("辩论记录:\n" + "\n".join(history_lines))
```

delta spec `agent-node-contracts` 的「key_arguments 缺失降级」场景 THEN 改为：
`该消息退化为无编号行的基础格式（role(R{round}): content）渲染，MUST NOT 报错中断`。

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/nodes/test_research_manager.py -q && openspec validate add-rm-assumption-table --strict`
Expected: PASS / valid

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/research_manager.py tests/nodes/test_research_manager.py openspec/changes/add-rm-assumption-table/specs/agent-node-contracts/spec.md
git commit -m "feat(rm): 上下文复用辩论论点编号渲染——假设表证据可按论点序号对位"
```

---

### Task 3: 假设表校验、state 落键与结论拼装

**Files:**
- Modify: `src/finance_agent/nodes/research_manager.py`
- Modify: `src/finance_agent/state.py:130-133`
- Test: `tests/nodes/test_research_manager.py`（追加）

**Interfaces:**
- Produces: `validate_assumption_table(table: object) -> tuple[list[dict] | None, str]`（合法 → (table, "")；非法 → (None, 原因)）
- Produces: `format_assumptions_text(table: list[dict]) -> str`（markdown 表格，Task 5 复用）
- Produces: state 键 `research_manager_assumptions: list[dict] | None`

- [ ] **Step 1: Write the failing tests**

```python
# 追加到 tests/nodes/test_research_manager.py
import json
import logging


def _valid_table():
    rows = []
    for code, dim in (("H1", "竞争格局"), ("H2", "成长驱动"), ("H3", "盈利质量"), ("H4", "估值与预期")):
        rows.append({
            "code": code, "dimension": dim,
            "assumption": f"{dim}命题 ≥ 30%",
            "bull_evidence": [f"Bull R1 论点①: {dim}利多"],
            "bear_evidence": [],
            "inflection_signal": f"{dim}连续两季恶化",
            "status": "成立", "time_tag": "long",
        })
    return rows


def _rm_json(table):
    return json.dumps(
        {"reasoning": "多空交锋后偏多。", "rating": "看多", "confidence": 0.62, "assumption_table": table},
        ensure_ascii=False,
    )


class TestAssumptionTable:
    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_valid_table_lands_state_and_conclusion(self, mock_llm):
        mock_llm.return_value = _rm_json(_valid_table())
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] == _valid_table()
        conclusion = result["research_manager_conclusion"]
        assert conclusion.startswith("评级: 看多")
        assert "| H1 竞争格局 |" in conclusion
        assert "✅ 成立" in conclusion
        assert "Bull R1 论点①: 竞争格局利多" in conclusion
        assert result["research_manager_rating"] == "看多"

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_missing_row_drops_table_keeps_rating(self, mock_llm, caplog):
        table = [r for r in _valid_table() if r["code"] != "H3"]
        mock_llm.return_value = _rm_json(table)
        with caplog.at_level(logging.WARNING, logger="finance_agent.research_manager"):
            result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] is None
        assert "| H1" not in result["research_manager_conclusion"]
        assert result["research_manager_rating"] == "看多"
        assert result["research_manager_confidence"] == 0.62
        assert "parse_degraded" not in result
        assert any("H3" in r.message for r in caplog.records)

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_bad_enum_drops_table(self, mock_llm):
        table = _valid_table()
        table[1]["status"] = "大概成立"
        mock_llm.return_value = _rm_json(table)
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] is None
        assert result["research_manager_rating"] == "看多"

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_unsupported_row_no_evidence_drops(self, mock_llm):
        table = _valid_table()
        table[3]["bull_evidence"] = []
        table[3]["bear_evidence"] = []
        mock_llm.return_value = _rm_json(table)
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] is None

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_free_text_degradation_sets_assumptions_none(self, mock_llm):
        mock_llm.return_value = "自由文本结论。"
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result.get("parse_degraded") is True
        assert result["research_manager_assumptions"] is None

    def test_pipe_in_cell_is_escaped(self):
        from finance_agent.nodes.research_manager import format_assumptions_text
        table = _valid_table()
        table[0]["assumption"] = "A|B"
        text = format_assumptions_text(table)
        assert "A／B" in text and "| A|B |" not in text
```

- [ ] **Step 2: Run tests to verify fail**

Run: `uv run pytest tests/nodes/test_research_manager.py::TestAssumptionTable -v`
Expected: FAIL（KeyError `research_manager_assumptions` / ImportError）

- [ ] **Step 3: Write minimal implementation**

`state.py` L132 之后插入：

```python
    # add-rm-assumption-table：RM 假设表（固定 4 行 H1-H4，合法才落键；非法整表丢弃为 None）
    research_manager_assumptions: list[dict] | None
```

`research_manager.py` 模块级新增：

```python
_ASSUMPTION_DIMENSIONS = {"H1": "竞争格局", "H2": "成长驱动", "H3": "盈利质量", "H4": "估值与预期"}
_ASSUMPTION_STATUSES = ("成立", "注意", "不成立")
_ASSUMPTION_TIME_TAGS = ("short", "long", "short+long")
_STATUS_MARKS = {"成立": "✅", "注意": "⚠️", "不成立": "❌"}


def validate_assumption_table(table: object) -> tuple[list[dict] | None, str]:
    """RM 假设表合法性校验：合法 → (table, "")；非法 → (None, 丢弃原因)。

    表是增强不是门禁：任何非法都整表丢弃，由调用方告警，不触碰评级解析语义。
    """
    if not isinstance(table, list):
        return None, f"assumption_table 非列表: {type(table).__name__}"
    if len(table) != 4:
        return None, f"行数非法: {len(table)}（应为 4）"
    seen: list[str] = []
    for row in table:
        if not isinstance(row, dict):
            return None, "存在非对象行"
        code = str(row.get("code", "")).strip()
        if code not in _ASSUMPTION_DIMENSIONS:
            return None, f"code 非法: {code!r}"
        seen.append(code)
        if row.get("status") not in _ASSUMPTION_STATUSES:
            return None, f"行 {code} status 非法: {row.get('status')!r}"
        if row.get("time_tag") not in _ASSUMPTION_TIME_TAGS:
            return None, f"行 {code} time_tag 非法: {row.get('time_tag')!r}"
        if not str(row.get("assumption") or "").strip():
            return None, f"行 {code} assumption 为空"
        if not str(row.get("inflection_signal") or "").strip():
            return None, f"行 {code} inflection_signal 为空"
        bull, bear = row.get("bull_evidence"), row.get("bear_evidence")
        if bull is not None and not isinstance(bull, list):
            return None, f"行 {code} bull_evidence 非列表"
        if bear is not None and not isinstance(bear, list):
            return None, f"行 {code} bear_evidence 非列表"
        if not (bull or bear):
            return None, f"行 {code} 多空证据双侧皆空"
    missing = [c for c in _ASSUMPTION_DIMENSIONS if c not in seen]
    if missing:
        return None, f"缺 {'/'.join(missing)} 行"
    return table, ""


def _cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "／").replace("\n", " ").strip()


def format_assumptions_text(table: list[dict]) -> str:
    """假设表 → 人读 markdown 表格（RM conclusion 拼装与报告节渲染共用同一格式）。"""
    lines = [
        "| 维度 | 核心假设 | 多方证据 | 空方证据 | 拐点信号 | 状态 | 周期 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table:
        code = str(row.get("code", ""))
        dim = f"{code} {_ASSUMPTION_DIMENSIONS.get(code, '')}".strip()
        bull = "；".join(_cell(e) for e in (row.get("bull_evidence") or [])) or "—"
        bear = "；".join(_cell(e) for e in (row.get("bear_evidence") or [])) or "—"
        status = str(row.get("status", ""))
        mark = _STATUS_MARKS.get(status, "")
        lines.append(
            f"| {dim} | {_cell(row.get('assumption'))} | {bull} | {bear} | "
            f"{_cell(row.get('inflection_signal'))} | {f'{mark} {status}'.strip()} | {_cell(row.get('time_tag'))} |"
        )
    return "\n".join(lines)
```

`research_manager()` 改动：

```python
    result: dict = {
        "research_manager_conclusion": raw,
        "research_manager_rating": None,
        "research_manager_confidence": None,
        "research_manager_assumptions": None,
    }
    try:
        data = parse_json_response(raw)
        ...（既有 rating/confidence/reasoning 校验不变）...
        result["research_manager_conclusion"] = (
            f"评级: {rating}（置信度 {confidence:.2f}）\n{reasoning}"
        )
        result["research_manager_rating"] = rating
        result["research_manager_confidence"] = confidence
        # 假设表是增强不是门禁：非法整表丢弃+告警，不触碰 parse_degraded
        table, reason = validate_assumption_table(data.get("assumption_table"))
        if table is not None:
            result["research_manager_assumptions"] = table
            result["research_manager_conclusion"] += "\n\n" + format_assumptions_text(table)
        else:
            logger.warning("RM 假设表丢弃（评级照常生效）: %s", reason)
    except Exception as exc:  # noqa: BLE001
        ...（不变）...
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/nodes/test_research_manager.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/research_manager.py src/finance_agent/state.py tests/nodes/test_research_manager.py
git commit -m "feat(rm): 假设表校验、state 落键与结论拼装——非法整表丢弃不触碰评级降级语义"
```

---

### Task 4: 提示词契约测试 + research_manager.md 改写

**Files:**
- Modify: `src/finance_agent/prompts/research_manager.md`
- Test: `tests/test_prompt_contracts.py`（`TestResearchManagerStance` 之后追加）

- [ ] **Step 1: Write the failing test**

```python
class TestResearchManagerAssumptionTable:
    """add-rm-assumption-table：固定 4 行假设表契约。"""

    def test_fixed_four_dimensions_and_json_example(self):
        text = _load("research_manager.md")
        assert "assumption_table" in text
        for dim in ("H1 竞争格局", "H2 成长驱动", "H3 盈利质量", "H4 估值与预期"):
            assert dim in text

    def test_evidence_cites_debate_argument_numbers(self):
        text = _load("research_manager.md")
        assert "角色+轮次+论点序号" in text
        assert "禁止引入辩论记录中不存在的论据" in text

    def test_inflection_signal_status_coupling(self):
        text = _load("research_manager.md")
        assert "可观察" in text
        assert "MUST NOT 为「成立」" in text

    def test_insufficient_evidence_degrades_honestly(self):
        text = _load("research_manager.md")
        assert "如实降级该行 status" in text
        assert "禁止编造证据或空话填充" in text
```

- [ ] **Step 2: Run test to verify fail**

Run: `uv run pytest tests/test_prompt_contracts.py::TestResearchManagerAssumptionTable -v`
Expected: FAIL（assumption_table 不在模板中）

- [ ] **Step 3: Rewrite the prompt**（完整文件内容）

````markdown
你是 Research Manager（研究经理）。总结 Bull/Bear 辩论，给出均衡的研究结论，并产出结构化假设表。

## 输出格式

只输出 JSON（字段顺序固定：先 reasoning 后 rating——先推理后结论；assumption_table 置于最后）：

```json
{
  "reasoning": "先概括多空双方核心理由（引用辩论中的具体论据），再给出你的判断与取舍依据",
  "rating": "看多|看空|中性",
  "confidence": 0.6,
  "assumption_table": [
    {
      "code": "H1",
      "dimension": "竞争格局",
      "assumption": "一句话可核验命题（含量化锚点，如：市占率维持在 30% 以上）",
      "bull_evidence": ["Bull R1 论点②: 份额 35% 创三年新高"],
      "bear_evidence": ["Bear R2 论点①: 价格战下份额以价换量"],
      "inflection_signal": "季度份额连续两期跌破 28%",
      "status": "成立",
      "time_tag": "long"
    }
  ]
}
```

## 评级表态

- `rating` 必须给出明确的评级倾向：看多 / 看空 / 中性，不得回避表态
- `reasoning` 先概括多空双方核心理由，再给出你的判断与核心依据（引用辩论中的具体论据，不得泛泛而谈）
- 若多空证据均衡或信息不足，须在 `reasoning` 中说明取舍理由与依据，而不是含糊其辞
- `confidence`（0-1）表达你对评级方向的把握：证据一边倒时更高，均衡僵持时更低

## 假设表契约

辩论记录中每条发言以「R{n} 论点: ①…②…」编号行前置于正文。假设表把辩论综合为 4 条可核验假设——
它不是新的分析，是对辩论论点的结构化重组。

- 固定 4 行，维度固定且全部覆盖、禁止增删：H1 竞争格局 / H2 成长驱动 / H3 盈利质量 / H4 估值与预期
- `assumption`：一句话可核验命题，必须含量化锚点（比例/金额/倍数/期次）
- `bull_evidence` / `bear_evidence`：证据必须转引辩论论点，按「角色+轮次+论点序号」引用（如「Bull R1 论点②: <要点>」），
  至少一侧非空；禁止引入辩论记录中不存在的论据
- `inflection_signal`：必须是可观察的量化指标或具体事件（如「连续两季毛利率低于 40%」「解禁落地后两周换手率」）；
  给不出具体可观察信号的维度，其 `status` MUST NOT 为「成立」
- `status` 仅允许：成立 / 注意 / 不成立；`time_tag` 仅允许：short / long / short+long
- 论据不足以支撑某维度时，如实降级该行 status（注意 / 不成立）并在 reasoning 中说明取舍，禁止编造证据或空话填充
- 假设表缺失或结构非法会被系统丢弃（评级照常生效），但完整、可核验的假设表是你结论可信度的组成部分
````

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_prompt_contracts.py -q`
Expected: all PASS（含既有 `TestResearchManagerStance`）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/prompts/research_manager.md tests/test_prompt_contracts.py
git commit -m "feat(prompt): research_manager 假设表契约——4 行固定维度、论点编号转引、拐点信号与状态耦合"
```

---

### Task 5: 报告「多空辩论结论」节渲染假设表

**Files:**
- Modify: `src/finance_agent/nodes/report.py:436-453`（新增函数）、`:777-783`（调用点）
- Test: `tests/nodes/test_report_revision.py`（`TestDivergenceCard` 之后追加）

**Interfaces:**
- Consumes: Task 3 的 `format_assumptions_text`、state 键 `research_manager_assumptions`

- [ ] **Step 1: Write the failing test**

```python
class TestAssumptionsTableRendering:
    """add-rm-assumption-table：分歧卡与结论文本之后渲染假设表；缺失退化现状。"""

    @staticmethod
    def _table():
        return [
            {"code": c, "dimension": d, "assumption": f"{d}命题", "bull_evidence": [f"Bull R1 论点①: {d}利多"],
             "bear_evidence": [], "inflection_signal": f"{d}拐点", "status": s, "time_tag": "long"}
            for c, d, s in (("H1", "竞争格局", "成立"), ("H2", "成长驱动", "注意"),
                            ("H3", "盈利质量", "不成立"), ("H4", "估值与预期", "成立"))
        ]

    def test_table_rendered_after_card_and_body_without_duplication(self):
        from finance_agent.nodes.research_manager import format_assumptions_text
        state = _base_state()
        table = self._table()
        state["research_manager_rating"] = "看多"
        state["research_manager_confidence"] = 0.62
        state["research_manager_assumptions"] = table
        # RM 节点把表拼进了 conclusion 文本（消费端可见）——报告须剥离后按结构化键重渲染
        state["research_manager_conclusion"] = (
            "评级: 看多（置信度 0.62）\n多空交锋后偏多。\n\n" + format_assumptions_text(table)
        )
        report = generate_report(state)["final_report"]

        assert report.count("| H1 竞争格局 |") == 1
        assert "✅ 成立" in report and "⚠️ 注意" in report and "❌ 不成立" in report
        assert report.index("分歧焦点") < report.index("多空交锋后偏多") < report.index("| H1 竞争格局 |")

    def test_missing_structured_table_degrades(self):
        state = _base_state()
        state["research_manager_conclusion"] = "多空各执一词。"
        report = generate_report(state)["final_report"]
        assert "| 维度 |" not in report
        assert "多空各执一词。" in report
```

- [ ] **Step 2: Run test to verify fail**

Run: `uv run pytest tests/nodes/test_report_revision.py::TestAssumptionsTableRendering -v`
Expected: 第一条 FAIL（表出现 2 次或状态标记缺��）

- [ ] **Step 3: Write minimal implementation**

`report.py` 顶部 import：`from finance_agent.nodes.research_manager import format_assumptions_text`。
`_format_divergence_card` 之后新增：

```python
def _render_assumptions_section(state: dict, conclusion_body: str) -> str:
    """多空假设表：结构化键存在 → 剥离 RM 拼进 conclusion 的同格式后缀，按结构化键重渲染于
    分歧卡与结论文本之后；键缺失/None（历史会话、解析降级、表被丢弃）→ 原样返回，
    MUST NOT 渲染空表或编造行。"""
    table = state.get("research_manager_assumptions")
    if not isinstance(table, list) or not table:
        return conclusion_body
    table_md = format_assumptions_text(table)
    suffix = "\n\n" + table_md
    if conclusion_body.endswith(suffix):
        conclusion_body = conclusion_body[: -len(suffix)]
    return f"{conclusion_body}\n\n**多空假设表**\n\n{table_md}"
```

调用点（L781-783）改为：

```python
        divergence_card = _format_divergence_card(state)
        conclusion_body = f"{divergence_card}\n\n{conclusion}" if divergence_card else conclusion
        # add-rm-assumption-table：假设表置于分歧卡与结论文本之后；结构化缺失退化现状
        conclusion_body = _render_assumptions_section(state, conclusion_body)
        sections.append(f"{next_title('多空辩论结论')}\n{conclusion_body}\n")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/nodes/test_report_revision.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/report.py tests/nodes/test_report_revision.py
git commit -m "feat(report): 多空辩论结论节渲染假设表——剥离 RM 拼装后缀避免重复，缺失退化现状"
```

---

### Task 6: 门禁、部署与 PR

**Files:**
- 无新文件；运行检查与部署

- [ ] **Step 1: Lint / 类型 / 定向测试**

Run: `uv run ruff check src/finance_agent/nodes/debate.py src/finance_agent/nodes/research_manager.py src/finance_agent/nodes/report.py src/finance_agent/state.py tests/nodes/test_research_manager.py tests/nodes/test_debate_arguments.py tests/nodes/test_report_revision.py tests/test_prompt_contracts.py && uv run ruff format --check <同上文件> && uv run mypy src/finance_agent/nodes/debate.py src/finance_agent/nodes/research_manager.py src/finance_agent/nodes/report.py`
Expected: 全部通过（ruff format --check 是本地验证必跑项）

- [ ] **Step 2: 相关测试全量**

Run: `uv run pytest tests/nodes tests/test_prompt_contracts.py tests/test_graph_5layer.py tests/test_pipeline_stub.py -q`
Expected: all PASS

- [ ] **Step 3: 发布提示词**

Run: `uv run python scripts/deploy_prompts.py`
Expected: research_manager 新版本发布到 Langfuse，无报错

- [ ] **Step 4: 规范校验 + 推送 + PR**

```bash
openspec validate add-rm-assumption-table --strict
git push -u origin feat/rm-assumption-table
gh pr create --title "feat(rm): 研究经理结构化假设表（add-rm-assumption-table）" --body-file /tmp/pr-body.md
```

---

## 未在本轮完成、留给后续的 tasks.md 项

- 基线标注（人工三道关）与 judge「假设显式性」维度口径登记（`metrics.md` §1）
- E2E 两场景（真实浏览器 + 真实栈）与人工验证报告落 `tests/validation/`
- 单变量 cohort 评估收口（守门：RM 解析失败率 / citation_pass / pass@k）
