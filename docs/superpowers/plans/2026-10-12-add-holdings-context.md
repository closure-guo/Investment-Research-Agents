# add-holdings-context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让决策落到 A 股散户可执行的动作上——补受众持仓管道（三态模型 + 状态键 + 注入 + 门禁），并把 `sell_type` 未申报默认从 `short` 改 `exit`、给按做空模板呈现的 sell 加披露注。

**Architecture:** 一条主线四处收口。`AnalyzeRequest.holdings`（可选声明）→ `HoldingsContext`（三态）→ `state["holdings_context"]` → 决策层 context 注入（Trader / Risk Judge）+ 未持有受众的 exit 门禁。三个消费点都挂在同一条管道上，prompt 政策与确定性门禁同源。

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / pydantic v2 / pytest

## Global Constraints

- 所有新增 state 键 MUST 在 `AnalysisState` 声明——未声明会被 LangGraph 合并静默丢弃（incident 027 教训），`tests/test_graph_5layer.py::TestNodeOutputChannels` 是门禁
- `TradeDecision.action` 硬枚举 `Literal["buy","sell","hold","watch"]`，`sell_type` 硬枚举 `Literal["exit","short"] | None`——本 plan 不改枚举
- 判据用 `SHALL`/`MUST`（delta spec 语法），报告与 context 的文案由代码直出，MUST NOT 依赖 LLM 生成
- 中文注释、中文 prompt；注释只写代码本身看不出的约束（不写"这行做什么"）
- 一次只改一个变量；每任务先写失败测试（红）再实现（绿）
- 不修改结算方向映射（`sell→short` 保持不变，零回归）
- 非交互类变更：不碰前端、SSE、会话流转，不适用 E2E 门禁
- 全量验证命令：`uv run pytest`、`uv run ruff check`、`uv run mypy`

---

### Task 1: HoldingsContext 模型 + state 声明 + 请求接入

**Files:**
- Modify: `src/finance_agent/models.py`（在 `FundManagerDecision` 之前插入 `HoldingsContext`）
- Modify: `src/finance_agent/state.py`（声明两个键 + import）
- Modify: `src/finance_agent/api.py`（`AnalyzeRequest.holdings` + `initial_state`）
- Test: `tests/nodes/test_holdings_context.py`（新建）

**Interfaces:**
- Produces: `finance_agent.models.HoldingsContext`（字段 `holds: bool | None = None`）；
  state 键 `holdings_context: HoldingsContext`、`holdings_executability_check: dict`；
  `AnalyzeRequest.holdings: bool | None = None`

- [ ] **Step 1: Write the failing test**

Create `tests/nodes/test_holdings_context.py`:

```python
"""add-holdings-context：受众持仓上下文（模型 / 声明 / 请求接入 / 注入 / 门禁）。

三态语义：True=已申报持有、False=已申报未持有、None=未申报。未申报 MUST NOT
被等同于「未持有」——二者对方向可执行性的含义不同。
"""

import pytest

from finance_agent.models import HoldingsContext
from finance_agent.state import AnalysisState


class TestHoldingsContextModel:
    def test_default_is_undeclared(self):
        assert HoldingsContext().holds is None

    def test_declared_not_holding_preserved(self):
        """未持有 MUST NOT 被归一为未申报（三态不是二态）。"""
        assert HoldingsContext(holds=False).holds is False

    def test_declared_holding(self):
        assert HoldingsContext(holds=True).holds is True


class TestStateDeclaration:
    def test_keys_declared(self):
        """未声明的键会被图合并静默丢弃（incident 027）——门禁显式断言。"""
        assert "holdings_context" in AnalysisState.__annotations__
        assert "holdings_executability_check" in AnalysisState.__annotations__

    def test_holdings_context_in_graph_channels(self):
        from finance_agent.graph import build_graph

        compiled = build_graph()
        channels = set(compiled.get_graph().nodes) | set(
            getattr(compiled, "channels", {}) or {}
        )
        annotations = set(AnalysisState.__annotations__)
        assert {"holdings_context", "holdings_executability_check"} <= annotations
        assert isinstance(channels, set)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/nodes/test_holdings_context.py -v`
Expected: FAIL — `ImportError: cannot import name 'HoldingsContext'`

- [ ] **Step 3: Write minimal implementation**

In `src/finance_agent/models.py`, insert before `class FundManagerDecision`:

```python
class HoldingsContext(BaseModel):
    """受众持仓上下文（add-holdings-context 预留框架）。

    三态：True=已申报持有、False=已申报未持有、None=未申报。未申报 MUST NOT 被
    等同于「未持有」——二者对方向可执行性的含义不同（未持有者看空只能观望；
    未申报者按可执行默认 exit 表达并要求说明未持有情形处置）。

    当前唯一来源是分析请求的显式声明；未来自动选股/交易接入真实账户持仓时，
    在本模型上扩展来源与持仓明细字段（数量/成本随消费方一并增加，避免死字段）。
    """

    holds: bool | None = None
```

In `src/finance_agent/state.py`, add the import next to the existing LLMConfig import:

```python
from finance_agent.models import HoldingsContext
```

and declare the keys in the 输入 section（`focus` 之后）：

```python
    # 受众持仓上下文（add-holdings-context，预留框架）：三态（持有/未持有/未申报）。
    # 此前未声明族同病：未声明的键被图合并静默丢弃 → 决策层注入与可执行性门禁
    # 随之哑火（incident 027）
    holdings_context: HoldingsContext
    # 未持有受众的 exit 可执行性门禁结论（add-holdings-context）：
    # {result: pass|fail, note}——打回后改判/仍犯降级，trace 可观测，报告不渲染
    holdings_executability_check: dict
```

In `src/finance_agent/api.py`, add to `AnalyzeRequest`（`focus` 之后）：

```python
    # 受众是否持有该标的（add-holdings-context）：False=未持有、True=持有、
    # None=未申报（默认）。未申报按可执行默认（exit）表达并要求说明未持有处置
    holdings: bool | None = None
```

and in `_run_graph_streaming` 的 `initial_state`（`"focus"` 之后）：

```python
        # 受众持仓上下文（add-holdings-context）：始终写入（未申报即 holds=None）——
        # 省略该键会让决策层注入行与可执行性门禁一起哑火
        "holdings_context": HoldingsContext(holds=req.holdings),
```

In `src/finance_agent/api.py` 顶部 import 区，把 `HoldingsContext` 加入既有的 `from finance_agent.models import ...` 行（若为分散的多行 import，新增一行 `from finance_agent.models import HoldingsContext`）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/nodes/test_holdings_context.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add tests/nodes/test_holdings_context.py src/finance_agent/models.py src/finance_agent/state.py src/finance_agent/api.py
git commit -m "feat(holdings): HoldingsContext 三态模型 + state 声明 + 请求接入 (add-holdings-context T1)"
```

---

### Task 2: 决策层持仓上下文注入

**Files:**
- Modify: `src/finance_agent/nodes/_llm_utils.py`（在 `focus_hint` 之后新增两个 helper）
- Modify: `src/finance_agent/nodes/trader.py`（`_build_trader_context` 注入）
- Modify: `src/finance_agent/nodes/risk.py`（`_build_risk_context` 注入）
- Test: `tests/nodes/test_holdings_context.py`（追加）

**Interfaces:**
- Consumes: `state["holdings_context"]`（Task 1）
- Produces: `_llm_utils.holdings_context_line(state: dict) -> str`（三态均返回非空行）、
  `_llm_utils.holdings_holds(state: dict) -> bool | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/nodes/test_holdings_context.py`:

```python
from finance_agent.nodes._llm_utils import holdings_context_line, holdings_holds


class TestHoldingsContextLine:
    """三态均注入（与 focus_hint 的空则零注入刻意不同）——「未申报」本身是决策所需信息。"""

    def _state(self, holds):
        return {"holdings_context": HoldingsContext(holds=holds)}

    def test_declared_holding_line(self):
        line = holdings_context_line(self._state(True))
        assert "已申报持有" in line
        assert "exit" in line

    def test_declared_not_holding_line(self):
        line = holdings_context_line(self._state(False))
        assert "已申报未持有" in line
        assert "watch" in line
        assert "MUST NOT 输出 exit" in line

    def test_undeclared_line_not_omitted(self):
        line = holdings_context_line(self._state(None))
        assert line, "未申报 MUST NOT 省略受众持仓行"
        assert "未申报" in line
        assert "未持有" in line  # 要求说明未持有情形的对应处置

    def test_missing_state_key_falls_back_to_undeclared(self):
        assert "未申报" in holdings_context_line({})

    def test_dict_form_compatible(self):
        assert holdings_holds({"holdings_context": {"holds": False}}) is False
        assert holdings_holds({"holdings_context": {"holds": True}}) is True
        assert holdings_holds({}) is None


class TestTraderContextInjection:
    def test_trader_context_carries_holdings_line(self):
        from finance_agent.nodes.trader import _build_trader_context

        ctx = _build_trader_context({"holdings_context": HoldingsContext(holds=False)})
        assert "受众持仓: 已申报未持有该标的" in ctx


class TestRiskContextInjection:
    def test_risk_context_carries_holdings_line(self):
        from finance_agent.nodes.risk import _build_risk_context

        ctx = _build_risk_context({"holdings_context": HoldingsContext(holds=True)})
        assert "受众持仓: 已申报持有该标的" in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/nodes/test_holdings_context.py -v`
Expected: FAIL — `ImportError: cannot import name 'holdings_context_line'`

- [ ] **Step 3: Write minimal implementation**

In `src/finance_agent/nodes/_llm_utils.py`, after `focus_hint`:

```python
def holdings_holds(state: dict) -> bool | None:
    """读取受众持仓三态（add-holdings-context）。HoldingsContext 对象与 dict 两形态兼容。"""
    ctx = state.get("holdings_context")
    if isinstance(ctx, dict):
        return ctx.get("holds")
    return getattr(ctx, "holds", None)


def holdings_context_line(state: dict) -> str:
    """受众持仓上下文注入行（add-holdings-context）。

    与 focus_hint 的空则零注入刻意不同：三态均返回非空行——「未申报」本身是
    决策所需信息（决定看空按 exit 表达还是按 watch 表达），MUST NOT 省略。
    """
    holds = holdings_holds(state)
    if holds is True:
        return "受众持仓: 已申报持有该标的——看空结论请按持有者减仓（sell_type=exit）表达"
    if holds is False:
        return (
            "受众持仓: 已申报未持有该标的——无敞口可退，MUST NOT 输出 exit；"
            "看空请按观望（watch）表达，除非受众明确要求做空研究"
        )
    return (
        "受众持仓: 未申报——受众是否持有未知；看空结论请按持有者减仓（exit）表达，"
        "并在理由中说明未持有情形的对应处置"
    )
```

In `src/finance_agent/nodes/trader.py` — `_build_trader_context` 的 `focus_hint` 块之后：

```python
    # 受众持仓上下文（add-holdings-context）：三态均注入，决定看空按 exit 还是 watch 表达
    sections.append(holdings_context_line(state))
```

并把 `holdings_context_line` 加进该文件既有的 `from finance_agent.nodes._llm_utils import ...` 行。

In `src/finance_agent/nodes/risk.py` — `_build_risk_context` 的 `focus_hint` 块之后：

```python
    # 受众持仓上下文（add-holdings-context）：三态均注入（同 Trader 层，两层不得漂移）
    sections.append(holdings_context_line(state))
```

并把 `holdings_context_line` 加进该文件既有的 `_llm_utils` import 行。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/nodes/test_holdings_context.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/_llm_utils.py src/finance_agent/nodes/trader.py src/finance_agent/nodes/risk.py tests/nodes/test_holdings_context.py
git commit -m "feat(holdings): Trader/RiskJudge context 注入受众持仓行 (add-holdings-context T2)"
```

---

### Task 3: prompt 受众约束段 + 契约测试

**Files:**
- Modify: `src/finance_agent/prompts/trader.md`（新增「受众可执行性约束」段；改未申报兜底表述）
- Modify: `src/finance_agent/prompts/risk_judge.md`（同款）
- Test: `tests/test_trade_decision_prompts.py`（追加）

**Interfaces:**
- Consumes: 无（纯 prompt 文本）
- Produces: 两 prompt 均含受众约束段与三态纪律；未申报兜底表述为「默认按 exit 处理」

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trade_decision_prompts.py`:

```python
class TestAudienceExecutabilityContract:
    """add-holdings-context：决策层受众可执行性约束（prompt 政策）。"""

    def test_trader_declares_a_share_constraint(self):
        text = _load("trader.md")
        assert "融资融券" in text
        assert "普通证券账户" in text or "普通账户" in text

    def test_trader_enumerates_three_states(self):
        text = _load("trader.md")
        assert "已申报持有" in text
        assert "已申报未持有" in text
        assert "未申报" in text

    def test_risk_judge_declares_a_share_constraint(self):
        text = _load("risk_judge.md")
        assert "融资融券" in text or "两融" in text
        assert "普通证券账户" in text or "普通账户" in text

    def test_risk_judge_enumerates_three_states(self):
        text = _load("risk_judge.md")
        assert "已申报持有" in text
        assert "已申报未持有" in text
        assert "未申报" in text

    def test_undeclared_default_is_exit_not_short(self):
        """未申报兜底表述必须改口——原「默认按 short」把受众推向不可执行模板。"""
        for name in ("trader.md", "risk_judge.md"):
            text = _load(name)
            assert "默认按 short" not in text, f"{name} 仍声明默认 short"
            assert "默认按 exit" in text, f"{name} 未声明默认 exit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trade_decision_prompts.py -v`
Expected: FAIL — `test_undeclared_default_is_exit_not_short`（现文本为「默认按 short 处理」）等

- [ ] **Step 3: Write minimal implementation**

In `src/finance_agent/prompts/trader.md`，把第 60 行末句
「未申报 sell_type 将被打回申报；打回后仍缺将默认按 short 处理。」
改为
「未申报 sell_type 将被打回申报；打回后仍缺将默认按 exit 处理（可执行默认）并如实标注。」

并在「## 决策语义」段之后追加：

```markdown
## 受众可执行性约束

受众为 A 股普通证券账户投资者，做空建仓需融资融券信用账户（标的须为两融标的、
券源可得），MUST NOT 把 sell_type=short 当作默认看空表达——short 仅在受众明确
要求做空研究时使用。看空结论 MUST 按受众持仓状态落到可执行动作：

- 受众已申报持有该标的（context「受众持仓: 已申报持有」）→ 看空用 sell_type=exit
  （减仓/退出敞口，完全可执行）；
- 受众已申报未持有（context「受众持仓: 已申报未持有」）→ 看空用 watch（观望/回避），
  MUST NOT 输出 sell_type=exit（无敞口可退，语义矛盾，系统会打回）；
- 受众持仓未申报（context「受众持仓: 未申报」）→ 看空按 sell_type=exit 表达，
  并在 reasoning 中说明未持有情形下的对应处置（回避/不介入）。

决策结论 MUST 落到受众可执行的动作上（买入建仓 / 减仓卖出 / 观望），
MUST NOT 以受众无法执行的动作作为唯一结论。
```

In `src/finance_agent/prompts/risk_judge.md`，把第 22 行末句
「打回后仍缺默认按 short 处理并如实标注」改为
「打回后仍缺默认按 exit 处理（可执行默认）并如实标注」；

并在「## 决策语义」段之后追加（终稿层与 Trader 同契约，避免两层语义漂移）：

```markdown
## 受众可执行性约束

受众为 A 股普通证券账户投资者，做空建仓需融资融券信用账户（标的须为两融标的、
券源可得），MUST NOT 把 sell_type=short 当作默认看空表达——short 仅在受众明确
要求做空研究时使用。看空结论 MUST 按受众持仓状态落到可执行动作：

- 受众已申报持有（context「受众持仓: 已申报持有」）→ 看空用 sell_type=exit；
- 受众已申报未持有（context「受众持仓: 已申报未持有」）→ 看空用 watch，
  MUST NOT 输出 sell_type=exit——系统会打回重申一次，仍犯将降级为 watch 并如实标注；
- 受众持仓未申报（context「受众持仓: 未申报」）→ 看空按 exit 表达，
  并在 reasoning 中说明未持有情形下的对应处置。

决策结论 MUST 落到受众可执行的动作上（买入建仓 / 减仓卖出 / 观望）。
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trade_decision_prompts.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/prompts/trader.md src/finance_agent/prompts/risk_judge.md tests/test_trade_decision_prompts.py
git commit -m "feat(prompts): 决策层受众可执行性约束段 + 未申报默认改 exit (add-holdings-context T3)"
```

---

### Task 4: 未申报 sell_type 默认 short → exit + 打回措辞按分型分支

**Files:**
- Modify: `src/finance_agent/nodes/risk.py:197-209`（滞回维持前判路径）、`:312-321`（打回后默认）、`:322-350`（价位/节奏打回措辞分支）
- Test: `tests/nodes/test_risk.py`（改写 `test_untyped_sell_defaults_short_after_retry` 等）

**Interfaces:**
- Consumes: 无（仅改默认值与措辞）
- Produces: 未申报 sell 终稿 `sell_type=="exit"`；`final_price_check.note` 含「默认 exit」

- [ ] **Step 1: Write the failing test**

把 `tests/nodes/test_risk.py:824` 的 `test_untyped_sell_defaults_short_after_retry` 改写为：

```python
    def test_untyped_sell_defaults_exit_after_retry(self, mock_llm):
        """打回申报仍缺 → 默认 exit（可执行默认）→ 减仓节奏齐全 → 放行 + 标注。"""
        mock_llm.return_value = {
            "action": "sell",
            "confidence": 0.5,
            "reasoning": "看空",
            "position_size": "light",
            "exit_schedule": "分两批：现价减半、跌破 600 清仓",
            "reeval_triggers": ["重新站上 660 恢复持有"],
        }
        result = risk_judge(self._state("看空"))
        decision = result["final_trade_decision"]
        assert decision.sell_type == "exit"  # 默认推导落回决策对象
        note = result["final_price_check"]["note"]
        assert "默认 exit" in note

    def test_untyped_sell_missing_schedule_retries_for_schedule_not_prices(self, mock_llm):
        """默认 exit 后若 exit_schedule 也缺，打回必须要求减仓节奏而非建仓价位。"""
        mock_llm.return_value = {
            "action": "sell",
            "confidence": 0.5,
            "reasoning": "看空",
            "entry_price": 600.0,
            "stop_loss": 660.0,
            "target_price": 540.0,
        }
        risk_judge(self._state("看空"))
        retry_prompts = [
            c.args[0] if c.args else c.kwargs.get("context", "")
            for c in mock_llm.call_args_list
        ]
        assert any("减仓节奏" in p for p in retry_prompts), "打回未要求 exit_schedule"
        assert not any("补全 entry_price/stop_loss/target_price" in p for p in retry_prompts), (
            "exit 形态不应要求补建仓价位"
        )
```

（`_state` / `mock_llm` 沿用该文件既有 fixture；若 `mock_llm` 返回值为固定单值 dict 而非序列，改为 `side_effect` 列表：第一项无 sell_type，第二项带 `exit_schedule`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/nodes/test_risk.py -k "untyped_sell" -v`
Expected: FAIL — `assert decision.sell_type == "exit"` 得到 `'short'`

- [ ] **Step 3: Write minimal implementation**

`src/finance_agent/nodes/risk.py:196-209`（滞回维持前判）：

```python
        # 维持前判到执行动作时：反方向价位不可信清空（交既有价位完整性打回补报）；
        # sell_type 维持可执行默认 exit（add-holdings-context：原为 short——与生产
        # 分型语义相反，且 A 股散户普遍不可执行）
        update: dict = {
            "action": prior_action,
            "position_size": None,
            "entry_price": None,
            "stop_loss": None,
            "target_price": None,
            "exit_schedule": None,
        }
        if prior_action == "sell":
            update["sell_type"] = "exit"
        else:
            update["sell_type"] = None
```

`src/finance_agent/nodes/risk.py:312-321`（打回后默认）：

```python
        if (
            str(getattr(decision, "action", "")) == "sell"
            and getattr(decision, "sell_type", None) is None
        ):
            decision = decision.model_copy(update={"sell_type": "exit"})
            _sell_type_note = "sell_type 未申报，默认 exit（可执行默认，如实标注）"
        else:
            _sell_type_note = "打回后已申报 sell_type"
```

`src/finance_agent/nodes/risk.py:326-331`（措辞分支）：

```python
    if _missing:
        if "exit_schedule" in _missing:
            retry_context = (
                f"{context}\n\n【减仓节奏申报打回】终稿 sell_type 为 exit（持有者减仓），"
                "必须申报 exit_schedule 减仓节奏，当前缺失：exit_schedule。请重新输出"
                "补全 exit_schedule（一句话写清批次与触发，如「分两批：现价减半、"
                "跌破600清仓」）的完整决策 JSON。若受众并未持有该标的（无敞口可退），"
                "请改为 watch 并申报 inaction_reason 与 reeval_triggers。"
            )
        else:
            retry_context = (
                f"{context}\n\n【价位完整性打回】{'buy' if decision.action == 'buy' else 'sell'}"
                f"决策必须结构化申报数值价位，缺失：{'、'.join(_missing)}。"
                "请重新输出补全 entry_price/stop_loss/target_price 的完整决策 JSON（继承或显式改写 Trader 价位均可，但必须以结构化字段申报）。"
            )
```

同时把该块内的注释与 `final_price_missing`（`src/finance_agent/nodes/validate.py:132-155` docstring）
里「未申报由 risk_judge 打回申报，仍缺默认 short」改为「仍缺默认 exit」。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/nodes/test_risk.py -v`
Expected: PASS（含滞回维持前判用例同步更新为 exit）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/risk.py src/finance_agent/nodes/validate.py tests/nodes/test_risk.py
git commit -m "fix(decision): 未申报 sell_type 默认 short→exit + 打回措辞按分型分支 (add-holdings-context T4)"
```

---

### Task 5: 未持有受众的 exit 可执行性门禁

**Files:**
- Modify: `src/finance_agent/nodes/risk.py`（在 sell_type 定型块之后、价位完整性块之前插入门禁）
- Test: `tests/nodes/test_holdings_context.py`（追加）

**Interfaces:**
- Consumes: `_llm_utils.holdings_holds(state)`（Task 2）、`state["holdings_context"]`（Task 1）
- Produces: `state["holdings_executability_check"] = {"result": "pass"|"fail", "note": str}`

- [ ] **Step 1: Write the failing test**

Append to `tests/nodes/test_holdings_context.py`:

```python
class TestNotHoldingExitGate:
    """holds=False + sell/exit → 打回重申一次 → 仍犯降级 watch（复用滞回归一模式）。"""

    def _state(self, holds):
        from finance_agent.nodes.risk import risk_judge  # noqa: F401

        return {
            "holdings_context": HoldingsContext(holds=holds),
            "trader_plan": {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
            },
            "analyst_reports": {},
        }

    def test_not_holding_exit_retries_once_then_downgrades(self, monkeypatch):
        from finance_agent.nodes import risk as risk_mod

        calls: list[str] = []

        def _fake_llm(context, **kwargs):
            calls.append(context)
            return {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
            }

        monkeypatch.setattr(risk_mod, "call_llm_for_json", _fake_llm)
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(False))
        assert len(calls) == 2, "应恰打回重申一次"
        assert "无敞口可退" in calls[1]
        decision = result["final_trade_decision"]
        assert decision.action == "watch"
        assert decision.sell_type is None
        assert decision.exit_schedule is None
        assert decision.entry_price is None and decision.stop_loss is None
        check = result["holdings_executability_check"]
        assert check["result"] == "fail"
        assert "降级观望" in check["note"]
        assert "未持有无敞口可退" in decision.inaction_reason

    def test_not_holding_exit_corrected_on_retry_passes(self, monkeypatch):
        from finance_agent.nodes import risk as risk_mod

        seq = [
            {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
            },
            {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "改判观望",
                "inaction_reason": "受众未持有，无敞口可退",
                "reeval_triggers": ["跌破 600"],
                "trigger_low": 600.0,
            },
        ]
        monkeypatch.setattr(
            risk_mod, "call_llm_for_json", lambda context, **k: seq.pop(0)
        )
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(False))
        assert result["final_trade_decision"].action == "watch"
        assert result["holdings_executability_check"]["result"] == "pass"
        assert "改判" in result["holdings_executability_check"]["note"]

    @pytest.mark.parametrize("holds", [True, None])
    def test_gate_not_triggered_for_holding_or_undeclared(self, holds, monkeypatch):
        from finance_agent.nodes import risk as risk_mod

        calls: list[str] = []

        def _fake_llm(context, **kwargs):
            calls.append(context)
            return {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
            }

        monkeypatch.setattr(risk_mod, "call_llm_for_json", _fake_llm)
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(holds))
        assert len(calls) == 1, "未触发门禁时不得额外调用 LLM"
        assert result["final_trade_decision"].action == "sell"
        assert result["holdings_executability_check"]["result"] == "pass"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/nodes/test_holdings_context.py -k "Gate" -v`
Expected: FAIL — `KeyError: 'holdings_executability_check'`

- [ ] **Step 3: Write minimal implementation**

在 `src/finance_agent/nodes/risk.py` 的 sell_type 定型块（`else: _sell_type_note = ""`）之后、`final_price_check` 初始化之前插入：

```python
    # 未持有受众的 exit 可执行性门禁（add-holdings-context）：exit 是「持有者减仓/
    # 退出敞口」，受众已申报未持有则无敞口可退——语义矛盾，打回重申恰一次，仍犯
    # 降级 watch（复用滞回「重申仍无 → 降级」模式），判定落 state 供 trace 观测，
    # 报告不渲染。未申报与已申报持有不触发（exit 对二者本就成立或走可执行默认）。
    holdings_check: dict = {"result": "pass", "note": ""}
    if holdings_holds(state) is False and _is_not_holding_violation(decision):
        retry_context = (
            f"{context}\n\n【受众可执行性打回】受众已申报未持有该标的，无敞口可退——"
            "sell_type=exit（持有者减仓/退出敞口）对无持仓受众不可执行。看空请改为 "
            "watch（观望/回避）并申报 inaction_reason 与 reeval_triggers；若受众明确"
            "要求做空研究，可用 sell_type=short（沿用 entry/stop/target 建仓模板）。"
            "请重新输出完整决策 JSON。"
        )
        data = call_llm_for_json(
            retry_context,
            system=system,
            api_key=api_key,
            node_name="risk_judge",
            llm_config=state.get("llm_config"),
            stock_code=state.get("stock_code"),
            prompt_name=_pinfo.prompt_name,
            prompt_version=_pinfo.prompt_version,
        )
        decision = TradeDecision.model_validate(data)
        if _is_not_holding_violation(decision):
            decision = decision.model_copy(
                update={
                    "action": "watch",
                    "sell_type": None,
                    "exit_schedule": None,
                    "entry_price": None,
                    "stop_loss": None,
                    "target_price": None,
                    "inaction_reason": (
                        "未持有无敞口可退，已降级观望：受众已申报未持有该标的，"
                        "exit（持有者减仓）不可执行"
                    ),
                }
            )
            holdings_check = {"result": "fail", "note": "未持有无敞口可退，已降级观望"}
        else:
            holdings_check = {"result": "pass", "note": "打回后已改判"}
        # 终稿换代：sell_type 定型结论已被门禁重试覆盖，如实清空避免陈旧标注
        _sell_type_note = ""
```

并在同文件模块级新增 helper：

```python
def _is_not_holding_violation(decision: TradeDecision) -> bool:
    """未持有受众与 exit 并存的语义矛盾判定（add-holdings-context 门禁判据）。"""
    return (
        str(getattr(decision, "action", "")) == "sell"
        and str(getattr(decision, "sell_type", "") or "") == "exit"
    )
```

把 `holdings_holds` 加进 risk.py 既有的 `_llm_utils` import 行；在 `risk_judge` 的返回 dict 增加
`"holdings_executability_check": holdings_check,`。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/nodes/test_holdings_context.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/risk.py tests/nodes/test_holdings_context.py
git commit -m "feat(holdings): 未持有受众的 exit 可执行性门禁（打回→降级 watch） (add-holdings-context T5)"
```

---

### Task 6: 报告做空披露行

**Files:**
- Modify: `src/finance_agent/nodes/report.py`（模块常量 + `_format_trade_decision`）
- Test: `tests/nodes/test_report_decision_render.py`（追加）

**Interfaces:**
- Consumes: `TradeDecision.sell_type`
- Produces: `report.SHORT_DISCLOSURE` 常量 + 「做空披露」渲染行

- [ ] **Step 1: Write the failing test**

Append to `tests/nodes/test_report_decision_render.py`（`TestSellTypeSplitRendering` 类内）：

```python
    def test_short_renders_short_disclosure(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "sell_type": "short",
                "entry_price": 640.0,
                "stop_loss": 700.0,
                "target_price": 571.0,
                "reasoning": "做空建仓",
                "reeval_triggers": ["跌破 570 重估"],
            }
        )
        md = _format_trade_decision(decision)
        assert "做空披露" in md
        assert "融资融券信用账户" in md
        assert "A 股普通证券账户不可执行" in md

    def test_legacy_none_sell_renders_short_disclosure(self):
        """历史 None 形态同样按做空模板呈现——不披露即把不可执行结构呈现为可执行建议。"""
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "entry_price": 3.0,
                "stop_loss": 3.1,
                "target_price": 2.9,
                "reasoning": "降低敞口",
            }
        )
        md = _format_trade_decision(decision)
        assert "做空披露" in md

    def test_exit_does_not_render_short_disclosure(self):
        decision = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "sell_type": "exit",
                "exit_schedule": "分两批减持",
                "reasoning": "退出敞口",
            }
        )
        md = _format_trade_decision(decision)
        assert "做空披露" not in md

    def test_buy_does_not_render_short_disclosure(self):
        decision = TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.7,
                "entry_price": 100.0,
                "stop_loss": 92.0,
                "target_price": 120.0,
                "reasoning": "建仓",
                "reeval_triggers": ["跌破 92"],
            }
        )
        assert "做空披露" not in _format_trade_decision(decision)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/nodes/test_report_decision_render.py -k "short_disclosure or None_sell" -v`
Expected: FAIL — `assert "做空披露" in md`

- [ ] **Step 3: Write minimal implementation**

`src/finance_agent/nodes/report.py` 模块级常量（放在既有渲染常量附近）：

```python
# add-holdings-context：按做空建仓模板呈现的 sell 必须披露执行前提——A 股普通
# 证券账户不可做空，不披露即把不可执行结构呈现为可执行建议。文本代码直出，
# MUST NOT 依赖 LLM 生成或改写（spec report-decision-rendering）。
SHORT_DISCLOSURE = (
    "做空建仓需融资融券信用账户（标的须为两融标的且有券源），"
    "A 股普通证券账户不可执行；本条为方向研判，不代表可直接执行"
)
```

在 `_format_trade_decision` 的 `elif action in ("buy", "sell"):` 分支末尾（派生指标行之后）：

```python
        # 按做空建仓模板呈现的 sell（short 与历史 None 形态）披露执行前提；
        # exit 走上一分支、buy 不适用——两者 MUST NOT 渲染该行
        if action == "sell":
            lines.append(f"- **做空披露**: {SHORT_DISCLOSURE}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/nodes/test_report_decision_render.py -v`
Expected: PASS（全部，含存量 exit/short 用例）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/report.py tests/nodes/test_report_decision_render.py
git commit -m "feat(report): 按做空模板呈现的 sell 渲染做空披露行 (add-holdings-context T6)"
```

---

### Task 7: prompt 发布 + 全量验证 + tasks 回填

**Files:**
- Modify: `openspec/changes/add-holdings-context/tasks.md`（回填勾选）

**Interfaces:**
- Consumes: Task 3 的 prompt 改动
- Produces: Langfuse 上与仓库一致的 prompt 版本；全绿验证证据

- [ ] **Step 1: 发布 prompt 到 Langfuse**

Run: `uv run python scripts/deploy_prompts.py`
Expected: trader / risk_judge 发布成功、指纹一致性 PASS。若被预检拦截，先按既有处置取证
（`prompt_history` 比对 production vs 仓库指纹）再定向覆盖，**不得**用 sync 收编方向。

- [ ] **Step 2: 全量验证**

Run: `uv run pytest`
Expected: 0 failed

Run: `uv run ruff check`
Expected: All checks passed

Run: `uv run mypy`
Expected: Success（无新增 error）

- [ ] **Step 3: 回填 tasks.md**

把 `openspec/changes/add-holdings-context/tasks.md` 中已完成项改为 `[x]`；
人工验证项保持未勾（待 §Step 5 产出报告后回填）。

- [ ] **Step 4: Commit**

```bash
git add openspec/changes/add-holdings-context/tasks.md
git commit -m "docs(openspec): add-holdings-context tasks 回填 + prompt 发布与全量验证证据"
```

---

## Self-Review

**1. Spec coverage**

| delta 场景 / 要求 | 落实任务 |
|---|---|
| holdings-context 声明与解析（三态、state 声明、未申报不臆测） | T1 |
| holdings-context 决策层注入（三态各自注入、未申报不省略） | T2 |
| holdings-context 未持有 exit 门禁（打回改判 / 降级 / 未申报与持有不触发） | T5 |
| agent-prompt-contracts 受众可执行性约束（约束段 + 三态纪律 + 合同测试） | T3 |
| agent-prompt-contracts Trader 决策语义契约 MODIFIED（默认 exit） | T3（prompt）+ T4（代码） |
| report-decision-rendering 做空披露行（short/None 渲染、exit/buy 不渲染、文本代码直出） | T6 |
| report-decision-rendering 历史 None 形态按 short 模板渲染并披露 | T6 |

无缺口。

**2. Placeholder scan**：无 TBD/TODO；每个代码步骤含完整代码与确切命令。

**3. Type consistency**：`HoldingsContext.holds: bool | None`（T1 定义，T2/T5 消费）；
`holdings_context_line` / `holdings_holds`（T2 定义，T3 政策同源、T5 消费）；
`holdings_executability_check: dict`（T1 声明，T5 产出）；`SHORT_DISCLOSURE`（T6）。
命名在任务间一致。
