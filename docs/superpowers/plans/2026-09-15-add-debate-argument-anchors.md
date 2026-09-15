# add-debate-argument-anchors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把辩论/风控论点从裸字符串升级为结构化锚点（`{text, kind, anchors}`），由**代码**做锚点存在性校验（零 LLM、fail-open），产出确定性指标 `argument_anchor_coverage` 并进入 judge 材料、评估器与产物——让定性内容从"judge 事后判断"变为"生成侧申报 + 程序判存在性 + judge 判支持性"。

**Architecture:** 三段式：① 生成侧——`DebateArgument` 模型 + 三份辩手 prompt 的申报纪律；② 节点侧——`debate_anchors.check_argument_anchors` 纯函数校验（data 型复用 citation 的 `_resolve_field_ref`，event 型复用回声源集合），结果写声明的 state channel `debate_anchor_checks` + span metadata，**不改路由、不阻断**；③ 评估侧——`evals/extract.py` 计算覆盖率并进 judge 材料骨架行，`evals/run.py` 注册确定性评估器，`evals/task.py` 从 channel 读数。

**Tech Stack:** Python 3（uv）/ pydantic v2 / LangGraph / pytest / ruff / mypy

## Global Constraints

- 所有命令 `uv run` 前缀。
- 新增 state 键必须 `AnalysisState` 声明 + `TestStateChannelsDeclared` 断言（incident 027）。
- **fail-open**：锚点校验 SHALL NOT 改变路由 / 阻断 / 触发重跑；新校验器只观测（incident 026 纪律）。
- `kind` 对 LLM 只暴露 `data` / `event` / `inference`；`unspecified` 仅由校验器对旧格式/非法值赋予（显式降级，不猜）。
- `rebuttal_to` 的 1-based 位置语义不变；对手可见的历史只渲染 `text`（不暴露锚点）。
- 三份 prompt 改动后**必须** `uv run python scripts/deploy_prompts.py` 发布——**该步骤依赖 Langfuse 在线**；离线环境下代码任务可先行，发布与真实链路验证挂 Task 6 门控。
- `evals/ablation.py` / `tests/scripts/ablation_pilot.py` 正被并发工作流（`update-ablation-driver-parity-and-report-status`）修改——Task 5 执行前先 `git status` 检查，有未提交改动则**跳过并记录**（不得吞并/覆盖他人改动）。
- 提交用显式路径；工作区存在无关未跟踪文件（judge-sample 等）与他方未提交改动，禁止 `git add -A`。

---

### Task 1: `DebateArgument` 模型 + 旧格式兼容 + 渲染适配

**Files:**
- Modify: `src/finance_agent/models.py`（`DebateMessage` 区域）
- Modify: `src/finance_agent/nodes/debate.py:92-97`（编号行渲染）
- Modify: `src/finance_agent/nodes/_llm_utils.py`（TESTING stub）
- Test: `tests/nodes/test_debate_arguments.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `DebateArgument(text, kind, anchors)`；`DebateMessage.key_arguments: list[DebateArgument]`；旧格式 `list[str]` 自动降级 `unspecified`

- [ ] **Step 1: 写红灯**

```python
"""add-debate-argument-anchors Task 1：结构化论点模型与旧格式兼容。"""

from finance_agent.models import DebateArgument, DebateMessage


class TestDebateArgumentModel:
    def test_structured_parse(self):
        msg = DebateMessage.model_validate(
            {
                "role": "bull", "round": 1, "content": "x",
                "key_arguments": [
                    {"text": "MA5 上穿 MA20", "kind": "data",
                     "anchors": ["technical_indicators.MA.5.-1"]},
                ],
            }
        )
        arg = msg.key_arguments[0]
        assert isinstance(arg, DebateArgument)
        assert arg.kind == "data"
        assert arg.anchors == ["technical_indicators.MA.5.-1"]

    def test_legacy_string_list_downgrades_to_unspecified(self):
        msg = DebateMessage.model_validate(
            {"role": "bull", "round": 1, "content": "x",
             "key_arguments": ["论点1", "论点2"]}
        )
        assert [a.kind for a in msg.key_arguments] == ["unspecified", "unspecified"]
        assert [a.text for a in msg.key_arguments] == ["论点1", "论点2"]
        assert all(a.anchors == [] for a in msg.key_arguments)

    def test_invalid_kind_downgrades_not_raises(self):
        msg = DebateMessage.model_validate(
            {"role": "bear", "round": 1, "content": "x",
             "key_arguments": [{"text": "t", "kind": "citation", "anchors": []}]}
        )
        assert msg.key_arguments[0].kind == "unspecified"

    def test_empty_text_raises(self):
        import pytest
        with pytest.raises(Exception):
            DebateMessage.model_validate(
                {"role": "bull", "round": 1, "content": "x",
                 "key_arguments": [{"text": "", "kind": "data", "anchors": []}]}
            )

    def test_rebuttal_to_position_semantics_unchanged(self):
        msg = DebateMessage.model_validate(
            {"role": "bear", "round": 2, "content": "x",
             "key_arguments": [
                 {"text": "a", "kind": "inference", "anchors": []},
                 {"text": "b", "kind": "inference", "anchors": []},
             ],
             "rebuttal_to": [2]}
        )
        assert msg.rebuttal_to == [2] and len(msg.key_arguments) == 2
```

- [ ] **Step 2: 运行红灯**

Run: `uv run pytest tests/nodes/test_debate_arguments.py -v`
Expected: FAIL —— `ImportError: cannot import name 'DebateArgument'`

- [ ] **Step 3: 实现模型**

`src/finance_agent/models.py`：在 `DebateMessage` 之前新增，并改 `key_arguments` 类型 + 加 before-validator：

```python
class DebateArgument(BaseModel):
    """辩论论点（add-debate-argument-anchors）。

    kind 对 LLM 只暴露 data/event/inference；unspecified 是旧格式或非法值的
    显式降级（不猜 kind，与 direction=None 计覆盖缺口的先例一致）。
    anchors：data 型为 state 英文键路径（与分析师 claim 同一词表）；event 型为
    来源事件标题要点；inference 型可为空。
    """

    text: str = Field(min_length=1)
    kind: Literal["data", "event", "inference", "unspecified"] = "unspecified"
    anchors: list[str] = Field(default_factory=list)
```

`DebateMessage` 内：

```python
    key_arguments: list[DebateArgument]

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_arguments(cls, data: object) -> object:
        """旧格式兼容：裸字符串列表 → unspecified（历史会话/夹具/TESTING stub）。"""
        if not isinstance(data, dict):
            return data
        args = data.get("key_arguments")
        if not isinstance(args, list):
            return data
        coerced: list[dict] = []
        for item in args:
            if isinstance(item, str):
                coerced.append({"text": item, "kind": "unspecified", "anchors": []})
            elif isinstance(item, dict):
                d = dict(item)
                if d.get("kind") not in ("data", "event", "inference", "unspecified"):
                    d["kind"] = "unspecified"
                coerced.append(d)
            else:
                coerced.append({"text": str(item), "kind": "unspecified", "anchors": []})
        return {**data, "key_arguments": coerced}
```

（models.py 顶部确认已 import `model_validator`；若无则补 `from pydantic import model_validator`。）

- [ ] **Step 4: 渲染适配**

`src/finance_agent/nodes/debate.py:94-96` 的编号行改为取 `text`：

```python
                numbered = " ".join(
                    f"{'①②③④⑤⑥⑦⑧⑨⑩'[i] if i < 10 else i + 1}."
                    f"{a.text if hasattr(a, 'text') else a}"
                    for i, a in enumerate(args)
                )
```

`src/finance_agent/nodes/_llm_utils.py` 的 stub 改为：

```python
                "key_arguments": [
                    {"text": f"STUB 论据：{role} 方观点成立", "kind": "inference", "anchors": []}
                ],
```

- [ ] **Step 5: 绿 + 回归**

Run: `uv run pytest tests/nodes/test_debate_arguments.py tests/nodes/test_debate.py tests/nodes/test_debate_focus.py tests/nodes/test_risk.py tests/test_pipeline_stub.py -v`
Expected: 全 PASS（既有 `len(msg.key_arguments) == 2` 断言对结构项同样成立）。若 test_debate.py:51 依赖字符串比较则按结构化更新并说明。

- [ ] **Step 6: 提交**

```bash
git add src/finance_agent/models.py src/finance_agent/nodes/debate.py src/finance_agent/nodes/_llm_utils.py tests/nodes/test_debate_arguments.py
git commit -m "feat(debate): 论点结构化模型 DebateArgument + 旧格式显式降级 unspecified + 渲染/stub 适配"
```

---

### Task 2: 锚点校验纯函数 + state 通道

**Files:**
- Create: `src/finance_agent/debate_anchors.py`
- Modify: `src/finance_agent/citation.py`（抽 `collect_text_sources`，`_verify_textual` 改为调用）
- Modify: `src/finance_agent/state.py`（声明 `debate_anchor_checks`）
- Modify: `tests/nodes/test_validate_trade_prices.py`（`TestStateChannelsDeclared` 加键）
- Test: `tests/nodes/test_debate_anchors.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `DebateArgument` / `DebateMessage`
- Produces: `check_argument_anchors(msg: DebateMessage, state: dict) -> list[dict]`；`anchor_stats(checks: list[dict]) -> dict`；`AnalysisState.debate_anchor_checks`（append reducer）

- [ ] **Step 1: 抽 `collect_text_sources`（行为保持不变）**

`src/finance_agent/citation.py`：把 `_verify_textual` 内的 sources 收集段（news_list/key_events/announcements/research_reports/share_unlock/block_trades 逐项）原样抽成：

```python
def collect_text_sources(state: dict) -> list[str]:
    """回声源集合（归一前原文）：news/key_events/公告/研报/解禁/大宗——文本 claim 与
    辩论 event 锚点共用同一集合（单一实现，勿复制）。"""
    sources: list[str] = []
    news = state.get("news_list") or []
    if isinstance(news, list):
        sources += [str(n.get("title") or "") for n in news if isinstance(n, dict)]
    events = state.get("key_events") or []
    if isinstance(events, list):
        for e in events:
            sources.append(str(e.get("title") or "") if isinstance(e, dict) else str(e))
    for key, fields in (
        ("announcements", ("title",)),
        ("research_reports", ("title",)),
        ("share_unlock", ("date",)),
        ("block_trades", ("date", "buyer", "seller")),
    ):
        items = state.get(key) or []
        if isinstance(items, list):
            sources += [
                str(i.get(f) or "")
                for i in items
                if isinstance(i, dict)
                for f in fields
                if i.get(f)
            ]
    return sources
```

`_verify_textual` 改为 `sources = collect_text_sources(state)` + 原有 `_resolve_field_ref` 补充分支不变。既有 `tests/test_citation*.py` 全绿为行为保持证据。

- [ ] **Step 2: 写红灯（校验函数）**

```python
"""add-debate-argument-anchors Task 2：论点锚点存在性校验（零 LLM）。"""

import pandas as pd

from finance_agent.debate_anchors import anchor_stats, check_argument_anchors
from finance_agent.models import DebateMessage


def _msg(args: list[dict], role: str = "bull", rnd: int = 1) -> DebateMessage:
    return DebateMessage.model_validate(
        {"role": role, "round": rnd, "content": "x", "key_arguments": args}
    )


class TestAnchorChecks:
    def test_data_anchor_resolved_and_unresolved(self):
        state = {"technical_indicators": {"MA": {"5": [1.0, 2.0]}}}
        checks = check_argument_anchors(
            _msg([{"text": "MA5", "kind": "data",
                   "anchors": ["technical_indicators.MA.5.-1", "not_a_root.x"]}]),
            state,
        )
        assert checks[0]["anchor_statuses"] == ["resolved", "unresolved"]
        assert checks[0]["anchored"] is True and checks[0]["status"] == "resolved"

    def test_dataframe_row_column_path(self):
        state = {"income_statement": pd.DataFrame([{"报告日": "20251231", "营业总收入": 1e9}])}
        checks = check_argument_anchors(
            _msg([{"text": "营收", "kind": "data",
                   "anchors": ["income_statement.20251231.营业总收入"]}]), state)
        assert checks[0]["anchored"] is True

    def test_event_echo_hit(self):
        state = {"news_list": [{"title": "公司公告拟回购不超过 10 亿元"}]}
        checks = check_argument_anchors(
            _msg([{"text": "回购", "kind": "event", "anchors": ["拟回购不超过 10 亿元"]}]),
            state,
        )
        assert checks[0]["anchored"] is True

    def test_data_zero_anchor_is_missing(self):
        checks = check_argument_anchors(_msg([{"text": "t", "kind": "data", "anchors": []}]), {})
        assert checks[0]["status"] == "missing" and checks[0]["anchored"] is False

    def test_inference_zero_anchor_is_none(self):
        checks = check_argument_anchors(
            _msg([{"text": "t", "kind": "inference", "anchors": []}]), {})
        assert checks[0]["status"] == "none" and checks[0]["anchored"] is False

    def test_unspecified_not_checked(self):
        checks = check_argument_anchors(
            _msg([{"text": "t", "kind": "unspecified", "anchors": ["x"]}]), {})
        assert checks[0]["status"] == "unspecified"

    def test_stats_split(self):
        checks = [
            {"anchored": True, "status": "resolved", "kind": "data"},
            {"anchored": False, "status": "none", "kind": "inference"},
            {"anchored": False, "status": "unresolved", "kind": "data"},
            {"anchored": False, "status": "missing", "kind": "event"},
            {"anchored": False, "status": "unspecified", "kind": "unspecified"},
        ]
        assert anchor_stats(checks) == {
            "total": 5, "anchored": 1, "unanchored_inference": 1,
            "unresolved": 1, "missing_required": 1, "unspecified": 1,
        }
```

- [ ] **Step 3: 运行红灯**

Run: `uv run pytest tests/nodes/test_debate_anchors.py -v`
Expected: FAIL —— `ModuleNotFoundError: finance_agent.debate_anchors`

- [ ] **Step 4: 实现模块**

```python
"""辩论论点锚点校验（add-debate-argument-anchors）。

只判「有没有锚、锚存不存在」——锚是否支持结论由 judge 承担（忠实性 = 可追溯性
（程序）+ 支持性（judge)）。零 LLM；fail-open（调用方不以其结果改路由）。
"""

from __future__ import annotations

from finance_agent.citation import _norm_text, _resolve_field_ref, collect_text_sources
from finance_agent.models import DebateArgument, DebateMessage


def _anchor_status(anchor: str, kind: str, state: dict, sources: list[str]) -> str:
    if kind in ("data", "inference") and _resolve_field_ref(anchor, state) is not None:
        return "resolved"
    if kind in ("event", "inference"):
        a_norm = _norm_text(anchor)
        if a_norm and any(a_norm in s or s in a_norm for s in (_norm_text(x) for x in sources)):
            return "resolved"
    return "unresolved"


def _argument_status(arg: DebateArgument, statuses: list[str]) -> str:
    if arg.kind == "unspecified":
        return "unspecified"
    if not arg.anchors:
        return "missing" if arg.kind in ("data", "event") else "none"
    return "resolved" if "resolved" in statuses else "unresolved"


def check_argument_anchors(msg: DebateMessage, state: dict) -> list[dict]:
    """逐论点锚点校验；返回可 JSON 序列化的检查记录列表（供 state channel 落盘）。"""
    sources = collect_text_sources(state)
    checks: list[dict] = []
    for i, arg in enumerate(msg.key_arguments, start=1):
        statuses = [_anchor_status(a, arg.kind, state, sources) for a in arg.anchors]
        checks.append(
            {
                "role": msg.role,
                "round": msg.round,
                "index": i,
                "kind": arg.kind,
                "anchors": list(arg.anchors),
                "anchor_statuses": statuses,
                "status": _argument_status(arg, statuses),
                "anchored": "resolved" in statuses,
            }
        )
    return checks


def anchor_stats(checks: list[dict]) -> dict:
    """覆盖统计（零 LLM）；total=0 由调用方决定是否记 null。"""
    return {
        "total": len(checks),
        "anchored": sum(1 for c in checks if c.get("anchored")),
        "unanchored_inference": sum(1 for c in checks if c.get("status") == "none"),
        "unresolved": sum(1 for c in checks if c.get("status") == "unresolved"),
        "missing_required": sum(1 for c in checks if c.get("status") == "missing"),
        "unspecified": sum(1 for c in checks if c.get("status") == "unspecified"),
    }
```

- [ ] **Step 5: state 声明 + 通道契约**

`src/finance_agent/state.py`（辩论区，`debate_history` 附近）：

```python
    debate_anchor_checks: Annotated[list[dict], add]  # 论点锚点校验记录（两层辩论追加）
```

`tests/nodes/test_validate_trade_prices.py` 的 `TestStateChannelsDeclared` 增加一个方法：

```python
    def test_graph_channels_declare_debate_anchor_keys(self):
        from finance_agent.graph import build_5layer_graph

        channels = set(build_5layer_graph().builder.channels)
        assert "debate_anchor_checks" in channels, "AnalysisState 缺少声明: debate_anchor_checks"
```

- [ ] **Step 6: 绿 + 回归**

Run: `uv run pytest tests/nodes/test_debate_anchors.py tests/test_citation.py tests/test_citation_buckets.py tests/test_citation_internal.py tests/nodes/test_validate_trade_prices.py -v`
Expected: 全 PASS（抽取 `collect_text_sources` 不改 `_verify_textual` 行为）。

- [ ] **Step 7: 提交**

```bash
git add src/finance_agent/debate_anchors.py src/finance_agent/citation.py src/finance_agent/state.py tests/nodes/test_debate_anchors.py tests/nodes/test_validate_trade_prices.py
git commit -m "feat(debate): 论点锚点存在性校验纯函数 + debate_anchor_checks 通道声明"
```

---

### Task 3: 节点接入（bull/bear + 风控三方），fail-open

**Files:**
- Modify: `src/finance_agent/nodes/debate.py`（两处节点返回）
- Modify: `src/finance_agent/nodes/risk.py`（`_risk_debater`）
- Test: `tests/nodes/test_debate_anchor_wiring.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `check_argument_anchors` / `anchor_stats`
- Produces: 节点返回 `{"debate_history": [msg], "debate_anchor_checks": [...]}` / `{"risk_debate_history": [msg], "debate_anchor_checks": [...]}`

- [ ] **Step 1: 写红灯（节点级，stub LLM）**

```python
"""add-debate-argument-anchors Task 3：节点接入（不触网）。"""

from unittest.mock import patch

from finance_agent.nodes.debate import bull_debater
from finance_agent.nodes.risk import aggressive_debater


def _fake_llm(data):
    def _call(ctx, **kwargs):
        return data
    return _call


class TestNodeWiring:
    def test_bull_returns_anchor_checks(self):
        payload = {
            "role": "bull", "round": 1, "content": "x",
            "key_arguments": [
                {"text": "MA5 上穿", "kind": "data", "anchors": ["technical_indicators.MA.5.-1"]},
                {"text": "龙头受益", "kind": "inference", "anchors": []},
            ],
        }
        state = {"technical_indicators": {"MA": {"5": [1.0, 2.0]}}}
        with patch("finance_agent.nodes.debate.call_llm_for_json", _fake_llm(payload)):
            out = bull_debater(state)
        checks = out["debate_anchor_checks"]
        assert [c["status"] for c in checks] == ["resolved", "none"]
        assert out["debate_history"][0].key_arguments[1].text == "龙头受益"

    def test_risk_debater_returns_anchor_checks(self):
        payload = {
            "role": "aggressive", "round": 1, "content": "x",
            "key_arguments": [{"text": "超卖反弹", "kind": "inference", "anchors": []}],
        }
        with patch("finance_agent.nodes.risk.call_llm_for_json", _fake_llm(payload)):
            out = aggressive_debater({"risk_metrics": {"max_drawdown": -0.2}})
        assert "risk_debate_history" in out and len(out["debate_anchor_checks"]) == 1
```

- [ ] **Step 2: 运行红灯**

Run: `uv run pytest tests/nodes/test_debate_anchor_wiring.py -v`
Expected: FAIL —— `KeyError: 'debate_anchor_checks'`

- [ ] **Step 3: 实现接入**

`debate.py` 两个节点在 `msg = DebateMessage.model_validate(data)` 之后：

```python
    checks = check_argument_anchors(msg, state)
    update_current_span(metadata={"anchor_stats": anchor_stats(checks)})

    return {"debate_history": [msg], "debate_anchor_checks": checks}
```

（import：`from finance_agent.debate_anchors import anchor_stats, check_argument_anchors`；`from finance_agent.langfuse_tracing import update_current_span`。若 `update_current_span` 在无 span 时抛错，按其既有实现容错——citation_node 同样用法，参照其写法。）

`risk.py` 的 `_risk_debater` 同样两行，返回 `{"risk_debate_history": [msg], "debate_anchor_checks": checks}`。

- [ ] **Step 4: fail-open 回归**

Run: `uv run pytest tests/nodes/test_debate_anchor_wiring.py tests/nodes/test_debate.py tests/nodes/test_risk.py tests/nodes/test_debate_focus.py -v`
Expected: 全 PASS；另跑 `uv run pytest tests/test_graph_5layer.py -q` 确认路由未动。

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/nodes/debate.py src/finance_agent/nodes/risk.py tests/nodes/test_debate_anchor_wiring.py
git commit -m "feat(debate): 辩手/风控节点接入锚点校验（fail-open，span 落 stats）"
```

---

### Task 4: 辩手 prompt 锚点申报纪律（3 份）+ 契约测试

**Files:**
- Modify: `src/finance_agent/prompts/bull_debater.md` / `bear_debater.md` / `risk_debater.md`
- Test: `tests/test_prompt_anchor_contract.py`（新建）

**Interfaces:**
- Consumes: Task 1 的模型契约
- Produces: 三份 prompt 的 `key_arguments` 结构化示例 + 纪律；发布动作挂 Task 6 门控（Langfuse 在线）

- [ ] **Step 1: 写红灯（契约测试）**

```python
"""add-debate-argument-anchors Task 4：辩手 prompt 锚点申报纪律契约。"""

from pathlib import Path

import pytest

PROMPTS = ["bull_debater.md", "bear_debater.md", "risk_debater.md"]
ROOT = Path(__file__).resolve().parent.parent / "src" / "finance_agent" / "prompts"


@pytest.mark.parametrize("name", PROMPTS)
def test_schema_is_structured(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert '"kind"' in text and '"anchors"' in text, f"{name} 的 key_arguments 示例未结构化"


@pytest.mark.parametrize("name", PROMPTS)
def test_anchor_discipline_present(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "data 型" in text and "field_ref" in text  # data 型必须附 field_ref
    assert "禁止" in text and "伪造" in text          # 禁止为推断伪造锚点
    assert "inference" in text                        # 拿不准标 inference
```

- [ ] **Step 2: 运行红灯**

Run: `uv run pytest tests/test_prompt_anchor_contract.py -v`
Expected: FAIL（三份模板仍是裸字符串示例）

- [ ] **Step 3: 修改三份 prompt**

以 `bull_debater.md` 为例（bear 同构，role 改 `bear`；risk 用 `{role}` 占位并同构）：

```json
{
  "role": "bull",
  "round": 1,
  "content": "看多论述",
  "key_arguments": [
    {"text": "MA5 上穿 MA20，短期动能转强", "kind": "data",
     "anchors": ["technical_indicators.MA.5.-1", "technical_indicators.MA.20.-1"]},
    {"text": "回购公告提振市场情绪", "kind": "event", "anchors": ["拟回购不超过 10 亿元"]},
    {"text": "龙头份额集中利好，但无近期数据支撑", "kind": "inference", "anchors": []}
  ],
  "rebuttal_to": [1, 3]
}
```

纪律段追加：

```markdown
**论点锚点申报纪律**：每条 key_argument 必须标注 `kind`——
- `data` 型**必须**附至少 1 个 `anchors` field_ref，且只能引用输入数据段标题内联标注的 state 英文键路径（与分析师 claim 同一词表，序列用负索引：-1=最新一期）；
- `event` 型附来源事件标题要点（取自新闻/公告/事件列表原文）；
- `inference` 型可不附锚点，但须明示为推断；
- **禁止为推断型论点伪造 field_ref**；拿不准是否有数据支撑时标 `inference`，不得编造锚点。
```

- [ ] **Step 4: 绿**

Run: `uv run pytest tests/test_prompt_anchor_contract.py tests/test_prompt_loader.py -v`
Expected: PASS

- [ ] **Step 5: 提交（发布留 Task 6）**

```bash
git add src/finance_agent/prompts/bull_debater.md src/finance_agent/prompts/bear_debater.md src/finance_agent/prompts/risk_debater.md tests/test_prompt_anchor_contract.py
git commit -m "feat(prompts): 三份辩手 prompt 锚点申报纪律（发布待 Langfuse 在线，Task 6 门控）"
```

---

### Task 5: judge 材料 + 评估器 + task 输出（消融接线**门控**）

**Files:**
- Modify: `evals/extract.py`（`anchor_coverage` + 材料骨架行与 `[kind 状态]` 前缀）
- Modify: `evals/run.py`（注册 `eval_argument_anchor_coverage`）
- Modify: `evals/task.py`（读 channel → output 键）
- Test: `tests/evals/test_extract.py`、`tests/evals/test_run.py`、`tests/evals/test_task.py`
- **门控**：`evals/ablation.py` / `tests/scripts/ablation_pilot.py` —— 执行前 `git status` 检查他方未提交改动；有则跳过消融接线并在报告/ledger 记明

**Interfaces:**
- Consumes: Task 2 的检查记录结构；Task 3 的 state channel
- Produces: `anchor_coverage(checks) -> dict | None`；judge 材料锚点骨架行；`eval_argument_anchor_coverage`；输出键 `argument_anchor_coverage` / `_detail`

- [ ] **Step 1: 写红灯（extract）**

在 `tests/evals/test_extract.py` 追加：

```python
class TestAnchorMaterial:
    def test_coverage_and_skeleton_line(self):
        from evals.extract import anchor_coverage, extract_judge_vars

        checks = [
            {"role": "bull", "round": 1, "index": 1, "kind": "data",
             "anchors": ["x"], "anchor_statuses": ["resolved"], "status": "resolved",
             "anchored": True},
            {"role": "bear", "round": 1, "index": 1, "kind": "inference",
             "anchors": [], "anchor_statuses": [], "status": "none", "anchored": False},
        ]
        cov = anchor_coverage(checks)
        assert cov["value"] == 0.5 and cov["unanchored_inference"] == 1

        state = {
            "debate_history": [
                {"role": "bull", "round": 1, "content": "c",
                 "key_arguments": [{"text": "MA5 上穿", "kind": "data", "anchors": ["x"]}]},
            ],
            "debate_anchor_checks": checks[:1],
        }
        debate = extract_judge_vars(state, query="q")["debate_history"]
        assert "【锚点覆盖】" in debate
        assert "[data ✓]" in debate
```

- [ ] **Step 2: 运行红灯**

Run: `uv run pytest tests/evals/test_extract.py -k anchor -v`
Expected: FAIL —— `ImportError: anchor_coverage`

- [ ] **Step 3: 实现 extract**

```python
def anchor_coverage(checks: list) -> dict | None:
    """论点锚点覆盖率（零 LLM）：anchored/total + 拆项；无论点返回 None。"""
    valid = [c for c in checks if isinstance(c, dict)]
    if not valid:
        return None
    anchored = sum(1 for c in valid if c.get("anchored"))
    return {
        "value": round(anchored / len(valid), 4),
        "total": len(valid),
        "anchored": anchored,
        "unanchored_inference": sum(1 for c in valid if c.get("status") == "none"),
        "unresolved": sum(1 for c in valid if c.get("status") == "unresolved"),
        "missing_required": sum(1 for c in valid if c.get("status") == "missing"),
        "unspecified": sum(1 for c in valid if c.get("status") == "unspecified"),
    }


_STATUS_MARK = {"resolved": "✓", "unresolved": "✗", "missing": "✗", "none": "○", "unspecified": "?"}
```

`_summarize_debate`：在收敛骨架行之后追加锚点骨架行（数据来自 `state.get("debate_anchor_checks")`，按 `(role, round)` 汇总），并把每条论点渲染为 `[kind mark] text`：

```python
    checks = [c for c in (state.get("debate_anchor_checks") or []) if isinstance(c, dict)]
    by_key = {(c.get("role"), c.get("round"), c.get("index")): c for c in checks}
    cov = anchor_coverage(checks)
    if cov:
        parts.append(
            f"【锚点覆盖】anchored {cov['anchored']}/{cov['total']}"
            f"（推断无锚 {cov['unanchored_inference']}，未解析 {cov['unresolved']}，"
            f"data/event 无锚 {cov['missing_required']}，旧格式 {cov['unspecified']}）"
        )
```
论点行渲染：对每个 `(role, rnd)` 的第 i 条论点，取 `by_key` 状态拼 `f"[{kind} {mark}] {text}"`；无记录时仅渲染 text（旧路径兼容）。

（`_summarize_debate` 需拿到 state：若现签名为 `(history)`，改为 `(history, checks)` 并在调用处传入 `state.get("debate_anchor_checks")`——调用点在同文件 `extract_judge_vars` 内，一并更新。）

- [ ] **Step 4: 写红灯 + 实现（评估器 + task 输出）**

`tests/evals/test_run.py`：十五 → 十六，名称集合加 `eval_argument_anchor_coverage`。
`tests/evals/test_task.py`：`_FakeGraph.invoke` 返回 `{"debate_anchor_checks": [...一条 anchored=True...]}`，断言 `out["argument_anchor_coverage"] == 1.0` 且 `out["argument_anchor_coverage_detail"]["total"] == 1`。

`evals/run.py` `all_evaluators()` 追加：

```python
        eval_citation_counter("argument_anchor_coverage", "辩论论点锚点覆盖率（anchored/total，零 LLM）"),
```
（该工厂按 output 键取 float，与既有 counter 同构；如 comment 需带拆项，读 `output["argument_anchor_coverage_detail"]` 拼接。）

Hmm — `eval_citation_counter` 是 citation 家族命名；若其实现只做 `float(output.get(name))`，可直接复用；若带 citation 前缀语义，则写一个同构的 `eval_metric_counter`。执行者按实现选择并在报告中写明（首选复用，避免重复工厂）。

`evals/task.py` 输出字典追加：

```python
        "argument_anchor_coverage": (
            _anchor["value"] if (_anchor := anchor_coverage(state.get("debate_anchor_checks") or [])) else None
        ),
        "argument_anchor_coverage_detail": (
            {k: v for k, v in _anchor.items() if k != "value"} if _anchor else None
        ),
```
（`from evals.extract import anchor_coverage` 若存在循环导入风险则在函数内 import。）

- [ ] **Step 5: 绿 + 门控检查（消融接线）**

Run: `uv run pytest tests/evals/test_extract.py tests/evals/test_run.py tests/evals/test_task.py -v`
然后 `git status --short evals/ablation.py tests/scripts/ablation_pilot.py tests/evals/test_ablation.py`：
- 干净 → 追加小步：`evals/ablation.py` run 记录携带 `argument_anchor_coverage`（从 variant 运行的 state 取，`run_variant_once` 返回值加键）＋ `aggregate_results` 对该指标按既有配对 bootstrap 口径出 CI（推广现有 dim 循环，保持 judge 维度行为不变）＋零 LLM 用例；
- 有他方改动 → **跳过**，在报告与 ledger 写明「消融接线延后，待 `update-ablation-driver-parity-and-report-status` 落地后单独小任务补」。

- [ ] **Step 6: 提交**

```bash
git add evals/extract.py evals/run.py evals/task.py tests/evals/test_extract.py tests/evals/test_run.py tests/evals/test_task.py
git commit -m "feat(evals): 论点锚点进 judge 材料骨架行与确定性评估器（含 task 输出拆项）"
```
（若 Step 5 消融接线可做，同 commit 追加 `evals/ablation.py` 等文件并在消息中注明。）

---

### Task 6（门控）：发布 + 真实链路验证 + 口径登记

**Files:**
- Modify: `docs/evals/metrics.md`（§1.2 新增指标行；时间线标注 judge 材料切点）
- Create: `tests/validation/2026-09-15-add-debate-argument-anchors-validation.md`
- Modify: `openspec/changes/add-debate-argument-anchors/tasks.md`（勾选）

**门控前置（任一不满足则本任务挂起，记录在 ledger）：**
- Langfuse 在线（`scripts/deploy_prompts.py` 可运行）→ 执行发布并记录输出；
- Docker/网络可用 → 真实 deep 一次，核对：① 每个辩手 span metadata 含 `anchor_stats`；② state `debate_anchor_checks` 条目数 = 论点总数；③ 遵从率分布（unspecified / missing_required / unresolved / unanchored_inference 占比）与 token 增量实测；
- `evals/ablation.py` 已无他方未提交改动 → 消融端到端复跑（若 Task 5 已接线）。

**判定（写进验证报告）**：预登记——`unspecified` 占比 > 50% 或 `missing_required`（data 型无锚）> 30% 视为 prompt 未生效，处置对象是 prompt 迭代（登记候选），**不得**放松校验或改判 kind；达标则记录基线分布供后续 rubric 校准轮使用。

- [ ] 6.1 发布三份 prompt（`uv run python scripts/deploy_prompts.py`，记录输出）
- [ ] 6.2 真实 deep 一次 + 三项核对 + token 增量
- [ ] 6.3 验证报告落 `tests/validation/2026-09-15-add-debate-argument-anchors-validation.md`
- [ ] 6.4 `docs/evals/metrics.md` §1.2 指标行 + 时间线切点
- [ ] 6.5 `openspec validate add-debate-argument-anchors --strict` + 全量/定向测试 + ruff/mypy；delta tasks.md 回填

---

## 计分卡（自审）

- **Spec 覆盖**：`agent-node-contracts`「辩论论点结构化锚点」8 个 Scenario → Task 1（解析/降级/空 text）、Task 2（data 解析/event 回声/计数/fail-open 语义）、Task 2 Step 5（通道契约）、Task 1 Step 4（rebuttal_to 与只渲染 text）；`agent-prompt-contracts`（对抗性指令 MODIFIED）→ Task 4；`evaluation`（锚点覆盖率 ADDED）→ Task 5（评估器/材料/消融/口径）。发布与真实链路 → Task 6。
- **Placeholder 扫描**：无 TBD；每个代码步骤含完整代码或明确的二选一裁决规则（Task 5 工厂复用/新写、消融门控）。
- **类型一致性**：`DebateArgument{text,kind,anchors}`（Task 1 定义 = Task 2/3/5 消费）；检查记录键 `{role,round,index,kind,anchors,anchor_statuses,status,anchored}`（Task 2 定义 = Task 3/5 消费）；`anchor_coverage` 返回键（Task 5 内 extract/task/evaluator 一致）；state 键 `debate_anchor_checks`（Task 2 声明 = Task 3 写入 = Task 5 读取）。
- **已知风险**：`update_current_span` 无活动 span 时的行为（按 citation_node 既有用法）；`_summarize_debate` 签名变更的调用点；prompt 发布依赖 Langfuse（Task 6 门控）。
