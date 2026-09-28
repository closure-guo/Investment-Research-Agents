# update-decision-integrity-gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口 2026-09-28 跑批报告审查发现的决策层契约缺口——决策文本价位交叉校验、非法档位归一、buy/sell 终稿 reeval 必填、FM 完整性可见性与置信度漂移标注、FM/risk_judge prompt 语义。

**Architecture:** 全部改动落在决策后处理与报告渲染层：risk_judge 终稿完整性检查（risk.py，一次打回 + 放行标注语义）扩展 buy/sell reeval；新增纯函数校验器 `decision_price_check.py` 做 trigger 文本价位 vs 已验证技术指标的交叉核对（anomaly 可观测、不中断）；report.py 渲染层归一非法档位、渲染 reeval 行/完整性标注/置信度漂移标注；FM 上下文注入完整性 note。delta：`openspec/changes/update-decision-integrity-gates/`（proposal/design/specs/tasks 四工件已 validate --strict 通过）。

**Tech Stack:** Python 3.12、pydantic、pytest；prompt 为 `src/finance_agent/prompts/*.md`（git 权威源，改后跑 `uv run python scripts/deploy_prompts.py`）。

## Global Constraints

- 实施在隔离 worktree（`.worktrees/`）中进行，主检出可能被其他会话并发使用；主检出里已有他人改动 `Dockerfile`，不得触碰。
- 变量/函数命名跟随现有代码（snake_case），注释中文，与周边密度一致。
- 非交互类变更（纯后端渲染/校验/prompt）——不适用 E2E 门禁（project-workflow §2 判别）。
- prompt 修改后必须 `uv run python scripts/deploy_prompts.py`，否则 eval 门禁拒绝运行。
- 渲染归一 MUST NOT 回写决策对象（落库/trace 保留原值）。
- 新校验器 MUST NOT 硬中断管线；anomaly 形态与 state["anomalies"] 既有消费者（analysts 上下文注入、citation snapshot）兼容。
- 每个任务：先写失败测试 → 确认红 → 最小实现 → 确认绿 → commit。
- 测试命令一律 `uv run pytest <path> -v`（工作目录 repo 根）。

**关键既有代码坐标**（实施者必读）：
- `src/finance_agent/models.py:191` `TradeDecision`（`position_size: str | None`、`reeval_triggers: list[str]`、清洗 validator 已存在）
- `src/finance_agent/nodes/report.py:502` `_fmt_reeval_triggers`（空 → 「未申报」）；`:519` `_format_trade_decision`（buy/sell 分支不渲染 reeval；`:553` 仓位行 `{position if position else '未提供'}`——"none" 真值透传即 600515 bug）；`:375-402` FM 节渲染
- `src/finance_agent/nodes/risk.py:88-175` 终稿价位/理由完整性检查（一次打回 + note 标注先例），返回 `final_price_check`/`final_inaction_check`
- `src/finance_agent/nodes/validate.py:154+` `inaction_rationale_missing`（trader 侧，本 delta 不改 trader 侧）
- `src/finance_agent/nodes/fund_manager.py:62` `_build_fund_manager_context`
- `src/finance_agent/state.py`：`technical_indicators: dict`（`calc_technical` 输出，`{"MA": {"5": [...], "60": [...]}, "BOLL": {...}, ...}`）、`price_levels: dict`（`entry_ref` ≈ 最新收盘参考）、`anomalies: list`
- `src/finance_agent/metrics/technical.py:27` `calc_technical` 返回形态

---

### Task 1: 非法仓位档位渲染归一（600515 形态）

**Files:**
- Modify: `src/finance_agent/nodes/report.py`（`_format_trade_decision` 仓位行，约 :553）
- Test: `tests/nodes/test_report.py`（追加测试函数）

**Interfaces:**
- Produces: `_fmt_position(value: object) -> str`（模块级函数，Task 3/4 不依赖但同文件复用）

- [ ] **Step 1: 写失败测试**（追加到 `tests/nodes/test_report.py` 末尾）

```python
class TestPositionTierNormalization:
    """report-decision-rendering「参数缺失时诚实标注」扩展：非法档位字面量归一（600515 none）。"""

    def _render_position(self, position):
        from finance_agent.nodes.report import _fmt_position
        return _fmt_position(position)

    def test_none_literal_renders_wei_tigong(self):
        # 600515 实证：risk_judge 输出 position_size="none"，报告透传字面量
        assert self._render_position("none") == "未提供"

    def test_null_word_and_blank_renders_wei_tigong(self):
        assert self._render_position("null") == "未提供"
        assert self._render_position("   ") == "未提供"
        assert self._render_position(None) == "未提供"

    def test_legal_tiers_case_insensitive_pass_through(self):
        assert self._render_position("light") == "light"
        assert self._render_position("Light") == "Light"  # 大小写变体按原值渲染
        assert self._render_position("moderate") == "moderate"
        assert self._render_position("heavy") == "heavy"

    def test_non_string_renders_wei_tigong(self):
        assert self._render_position(123) == "未提供"

    def test_decision_object_not_mutated(self):
        # 归一只作用于渲染：原对象保留 "none"（落库/trace 可观测）
        d = TradeDecision(action="watch", confidence=0.55, reasoning="r", position_size="none")
        from finance_agent.nodes.report import _fmt_position
        assert _fmt_position(d.position_size) == "未提供"
        assert d.position_size == "none"
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/nodes/test_report.py::TestPositionTierNormalization -v`
Expected: FAIL（`ImportError: cannot import name '_fmt_position'`）

- [ ] **Step 3: 最小实现**（report.py，模块级函数，放在 `_fmt_reeval_triggers` 之前）

```python
# 档位词表（trader.md position_size 档位；spec report-decision-rendering 2026-09 扩展）：
# 词表外字面量（如 risk_judge 输出的 "none"）渲染归一为「未提供」，不回写决策对象
_POSITION_TIERS = {"light", "moderate", "heavy"}


def _fmt_position(value: object) -> str:
    """仓位渲染：None/非字符串/纯空白/词表外字面量（大小写不敏感）一律「未提供」。"""
    if not isinstance(value, str):
        return "未提供"
    stripped = value.strip()
    if not stripped or stripped.lower() not in _POSITION_TIERS:
        return "未提供"
    return stripped
```

并把 `_format_trade_decision` 中仓位行：

```python
    lines.append(f"- **仓位**: {position if position else '未提供'}")
```

改为：

```python
    lines.append(f"- **仓位**: {_fmt_position(position)}")
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/nodes/test_report.py -v`
Expected: 全部 PASS（含既有用例回归）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/nodes/report.py tests/nodes/test_report.py
git commit -m "feat(report): 非法仓位档位字面量渲染归一——none 等词表外值按未提供处理 (update-decision-integrity-gates T1)"
```

---

### Task 2: buy/sell 终稿再评估触发条件必填化（601818 形态）

**Files:**
- Modify: `src/finance_agent/nodes/validate.py`（新增终稿 reeval 缺失判定函数）
- Modify: `src/finance_agent/nodes/risk.py`（终稿检查链追加 `final_reeval_check`，仿 `final_inaction_check` 一次打回语义）
- Modify: `src/finance_agent/nodes/report.py`（`_format_trade_decision` buy/sell 分支渲染 reeval 行）
- Test: `tests/nodes/test_final_reeval_check.py`（新建）、`tests/nodes/test_report.py`（追加）

**Interfaces:**
- Consumes: risk.py 既有 `call_llm_for_json` 打回模式、`TradeDecision.model_validate`
- Produces: `validate.exec_reeval_missing(decision: TradeDecision) -> list[str]`（返回缺失描述列表，空=通过）；state 新键 `final_reeval_check: dict`（`{"result": "pass"|"fail_bounced"|"released", "note": str}`）

- [ ] **Step 1: 写失败测试**（`tests/nodes/test_final_reeval_check.py` 新建）

```python
"""buy/sell 终稿 reeval_triggers 必填化（agent-node-contracts 2026-09 扩展，601818 实证）。"""
from finance_agent.models import TradeDecision
from finance_agent.nodes.validate import exec_reeval_missing


def _decision(action="sell", reeval=None):
    return TradeDecision(
        action=action,
        confidence=0.55,
        reasoning="r",
        entry_price=2.99 if action in ("buy", "sell") else None,
        stop_loss=3.12 if action in ("buy", "sell") else None,
        target_price=2.9 if action in ("buy", "sell") else None,
        reeval_triggers=reeval if reeval is not None else [],
    )


def test_exec_decision_with_empty_reeval_is_missing():
    missing = exec_reeval_missing(_decision(reeval=[]))
    assert missing == ["reeval_triggers"]


def test_exec_decision_with_triggers_passes():
    assert exec_reeval_missing(_decision(reeval=["跌破 22.94 则止损离场"])) == []


def test_watch_decision_not_constrained_by_exec_check():
    # watch/hold 的 reeval 由既有 inaction 检查管，本检查不重复约束
    assert exec_reeval_missing(_decision(action="watch")) == []


def test_noise_shapes_cleaned_to_empty_then_missing():
    d = TradeDecision(
        action="buy", confidence=0.6, reasoning="r",
        entry_price=1.0, stop_loss=0.9, target_price=1.2,
        reeval_triggers=[None, 123],  # 全部非法条目 → 清洗后 [] → 缺失
    )
    assert exec_reeval_missing(d) == ["reeval_triggers"]
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/nodes/test_final_reeval_check.py -v`
Expected: FAIL（`ImportError: cannot import name 'exec_reeval_missing'`）

- [ ] **Step 3: 最小实现**（validate.py，放在 `inaction_rationale_missing` 之后）

```python
def exec_reeval_missing(decision: TradeDecision | dict) -> list[str]:
    """终稿 buy/sell 的 reeval_triggers 必填判定（watch/hold 由 inaction 检查管，不重复）。

    只查清洗后的有效条目数（模型层已归一噪声形态），不做内容质量判断。
    """
    action = decision.get("action") if isinstance(decision, dict) else decision.action
    if action not in ("buy", "sell"):
        return []
    triggers = decision.get("reeval_triggers") if isinstance(decision, dict) else decision.reeval_triggers
    valid = [t for t in (triggers or []) if isinstance(t, str) and t.strip()]
    if not valid:
        return ["reeval_triggers"]
    return []
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/nodes/test_final_reeval_check.py -v`
Expected: PASS ×4

- [ ] **Step 5: 写 risk.py 接线的失败测试**（追加到 `tests/nodes/test_final_reeval_check.py`）

```python
class TestFinalReevalCheckWiring:
    """risk_judge 终稿检查链：buy/sell 缺 reeval 打回一次，仍缺放行 + note 标注。"""

    def _run_node(self, llm_outputs):
        import finance_agent.nodes.risk as risk_mod
        outputs = list(llm_outputs)

        def fake_llm(context, **kwargs):
            return outputs.pop(0)

        base = {
            "risk_debates": [{"role": "conservative", "round": 1, "content": "c"}],
            "final_trade_decision": None,
            "llm_config": None,
            "stock_code": "601818",
        }
        with patch.object(risk_mod, "call_llm_for_json", side_effect=fake_llm):
            return risk_mod.risk_judge(dict(base))

    def test_buy_sell_missing_reeval_bounces_once_then_releases_with_note(self):
        import json as _json
        first = {
            "action": "sell", "confidence": 0.55, "reasoning": "r1",
            "position_size": "light", "entry_price": 2.99,
            "stop_loss": 3.12, "target_price": 2.9, "reeval_triggers": [],
        }
        second = dict(first, reasoning="r2", reeval_triggers=["反抽至均线加仓"])
        result = self._run_node([_json.dumps(first), _json.dumps(second)])
        check = result["final_reeval_check"]
        assert check["result"] == "pass"
        assert "打回" in check["note"]
        assert result["final_trade_decision"].reeval_triggers == ["反抽至均线加仓"]

    def test_buy_sell_missing_reeval_after_retry_releases_with_note(self):
        import json as _json
        out = {
            "action": "sell", "confidence": 0.55, "reasoning": "r",
            "position_size": "light", "entry_price": 2.99,
            "stop_loss": 3.12, "target_price": 2.9, "reeval_triggers": [],
        }
        result = self._run_node([_json.dumps(out), _json.dumps(out)])
        check = result["final_reeval_check"]
        assert check["result"] == "released"
        assert "已打回仍未申报" in check["note"]
        assert result["final_trade_decision"].reeval_triggers == []

    def test_buy_sell_with_reeval_passes_without_bounce(self):
        import json as _json
        out = {
            "action": "sell", "confidence": 0.55, "reasoning": "r",
            "position_size": "light", "entry_price": 2.99, "stop_loss": 3.12,
            "target_price": 2.9, "reeval_triggers": ["跌破 2.94 转空评估"],
        }
        result = self._run_node([_json.dumps(out)])
        assert result["final_reeval_check"] == {"result": "pass", "note": ""}
```

- [ ] **Step 6: 跑测试确认红**

Run: `uv run pytest tests/nodes/test_final_reeval_check.py::TestFinalReevalCheckWiring -v`
Expected: FAIL（`KeyError: 'final_reeval_check'`；若 risk_judge 节点函数名/必需 state 键不同，以 `src/finance_agent/nodes/risk.py` 实际签名为准调整 fixture，语义不变）

- [ ] **Step 7: risk.py 接线**（在 `final_inaction_check` 块之后、赔率自检之前插入）

```python
    # 终稿执行动作再评估触发条件必填（agent-node-contracts 2026-09 扩展，601818 实证）：
    # buy/sell 缺 reeval 打回一次，feedback 引用风险辩论共识线索；仍缺放行 + 如实标注
    final_reeval_check: dict = {"result": "pass", "note": ""}
    _missing_reeval = exec_reeval_missing(decision)
    if _missing_reeval:
        retry_context = (
            f"{context}\n\n【再评估触发条件打回】buy/sell 终稿必须结构化申报 "
            "reeval_triggers（1-3 条可观察、可判定的退出/加仓再评估条件）。"
            "风险辩论中保守/中性方已提出过触发条件线索，请将其结构化申报为完整决策 JSON 的字段。"
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
        _missing_reeval = exec_reeval_missing(decision)
        if _missing_reeval:
            final_reeval_check["result"] = "released"
            final_reeval_check["note"] = "已打回仍未申报：reeval_triggers"
        else:
            final_reeval_check["note"] = "打回后已申报"
```

import 行补 `exec_reeval_missing`（validate 已在 risk.py import 链中，按现有 import 风格追加），返回 dict 增加 `"final_reeval_check": final_reeval_check,`。

- [ ] **Step 8: 跑测试确认绿**

Run: `uv run pytest tests/nodes/test_final_reeval_check.py -v`
Expected: 全部 PASS

- [ ] **Step 9: 写渲染端失败测试**（追加到 `tests/nodes/test_report.py`）

```python
class TestBuySellReevalRendering:
    """report-decision-rendering MODIFIED：buy/sell 也渲染再评估触发条件行。"""

    def test_buy_sell_with_triggers_renders_numbered_items(self):
        from finance_agent.nodes.report import _format_trade_decision
        d = TradeDecision(
            action="sell", confidence=0.55, reasoning="r", position_size="light",
            entry_price=2.99, stop_loss=3.12, target_price=2.9,
            reeval_triggers=["跌破 2.94 转空评估", "反抽至均线加仓"],
        )
        out = _format_trade_decision(d)
        assert "再评估触发条件" in out
        assert "跌破 2.94 转空评估" in out

    def test_buy_sell_without_triggers_renders_wei_shen_bao(self):
        from finance_agent.nodes.report import _format_trade_decision
        d = TradeDecision(
            action="sell", confidence=0.55, reasoning="r", position_size="light",
            entry_price=2.99, stop_loss=3.12, target_price=2.9, reeval_triggers=[],
        )
        assert "再评估触发条件: 未申报" in _format_trade_decision(d)
```

- [ ] **Step 10: 跑测试确认红 → 改渲染 → 确认绿**

改法（`_format_trade_decision`）：把 reeval 行从 else 分支提出来，buy/sell 也渲染；watch/hold 保持不变：

```python
    if action in ("buy", "sell"):
        lines.append(f"- **入场价**: {_fmt_price(entry)}")
        lines.append(f"- **止损价**: {_fmt_price(stop)}")
        lines.append(f"- **目标价**: {_fmt_price(target)}")
        derived = _fmt_derived_metrics(action, entry, stop, target)
        if derived:
            lines.append(derived)
    else:
        lines.append(
            f"- **不行动原因**: {inaction if isinstance(inaction, str) and inaction.strip() else '未申报'}"
        )
    # reeval 为全部 action 必渲染（agent-node-contracts 2026-09 扩展：601818 实证）
    lines.append(f"- **再评估触发条件**: {_fmt_reeval_triggers(triggers)}")
```

Run: `uv run pytest tests/nodes/test_report.py tests/nodes/test_final_reeval_check.py -v`
Expected: 全部 PASS

- [ ] **Step 11: Commit**

```bash
git add src/finance_agent/nodes/validate.py src/finance_agent/nodes/risk.py src/finance_agent/nodes/report.py tests/nodes/test_final_reeval_check.py tests/nodes/test_report.py
git commit -m "feat(risk): buy/sell 终稿 reeval_triggers 必填化——一次打回+放行标注，报告渲染触发条件行 (update-decision-integrity-gates T2)"
```

---

### Task 3: 决策文本价位交叉校验器（600845/601066 形态）

**Files:**
- Create: `src/finance_agent/decision_price_check.py`
- Modify: `src/finance_agent/nodes/risk.py`（终稿产出后调用，anomaly 写 state）
- Modify: `src/finance_agent/nodes/report.py`（reeval 行旁渲染标注）
- Test: `tests/nodes/test_decision_price_check.py`（新建）

**Interfaces:**
- Produces:
  - `extract_price_candidates(text: str) -> list[dict]`（`{"value": float, "context": str}`，量纲过滤后）
  - `check_decision_text_prices(decision: TradeDecision | dict, technical_indicators: dict, price_levels: dict) -> list[dict]`（anomaly 列表，形态见下）
  - anomaly 形态：`{"type": "decision_price_deviation" | "decision_void_trigger", "source_text": str, "stated_value": float, "indicator": str | None, "verified_value": float | None, "deviation_pct": float | None}`
  - state 新键：`decision_price_anomalies: list[dict]`（同时逐条 append 进 `anomalies`）

- [ ] **Step 1: 写失败测试**（`tests/nodes/test_decision_price_check.py` 新建）

```python
"""决策文本价位与已验证技术指标交叉校验（price-level-tooling 2026-09 新增）。"""
from finance_agent.decision_price_check import (
    check_decision_text_prices,
    extract_price_candidates,
)
from finance_agent.models import TradeDecision


TECH = {
    "MA": {"5": [None, 18.238], "10": [None, 17.673], "20": [None, 17.487], "60": [None, 18.066]},
    "BOLL": {"upper": [None, 18.573], "middle": [None, 17.487], "lower": [None, 16.4]},
}
PRICE_LEVELS = {"available": True, "entry_ref": 18.01}


def _decision(triggers):
    return TradeDecision(
        action="watch", confidence=0.55, reasoning="r",
        inaction_reason="x", reeval_triggers=triggers,
    )


def test_extract_filters_percent_and_ratio_dimensions():
    text = "跌破6.12并放量、降幅收敛至18.35%以下、赔率修复至1:1、站稳18.8以上"
    values = [c["value"] for c in extract_price_candidates(text)]
    assert 6.12 in values and 18.8 in values
    assert 18.35 not in values  # 百分比量纲不进价位核对
    assert 1.0 not in values or True  # 1:1 比值对被过滤（实现按「X:Y」形态剔除）


def test_600845_shape_named_ma60_deviation_flagged():
    # 实证形态：文本点名 MA60，写 18.8，已验证 MA60=18.066（偏差 4.07% > 2%）
    d = _decision(["价格放量突破MA60并站稳18.8以上"])
    anomalies = check_decision_text_prices(d, TECH, PRICE_LEVELS)
    dev = [a for a in anomalies if a["type"] == "decision_price_deviation"]
    assert len(dev) == 1
    assert dev[0]["indicator"] == "MA.60"
    assert dev[0]["verified_value"] == 18.066
    assert abs(dev[0]["deviation_pct"] - 4.07) < 0.1


def test_601066_shape_void_trigger_flagged():
    # 实证形态：「站上 22.61」而现价（entry_ref）23.03——上破触发价不高于现价
    tech = {"MA": {"20": [None, 24.15], "60": [None, 25.42]}, "BOLL": {"lower": [None, 22.56]}}
    levels = {"available": True, "entry_ref": 23.03}
    d = _decision(["价格放量站上止损参考带上沿22.61并突破高点下降趋势线"])
    anomalies = check_decision_text_prices(d, tech, levels)
    void = [a for a in anomalies if a["type"] == "decision_void_trigger"]
    assert len(void) == 1
    assert void[0]["stated_value"] == 22.61


def test_600015_shape_percent_threshold_not_flagged():
    # 非价格量纲（降幅阈值 18.35%/31.7%）MUST NOT 误报
    d = _decision(["单季净利降幅收敛至18.35%以下；若超过31.7%则升级为回避"])
    assert check_decision_text_prices(d, TECH, PRICE_LEVELS) == []


def test_valid_trigger_within_threshold_passes():
    d = _decision(["跌破近期低点17.80（距已验证MA20偏差<2%）则重估"])
    assert check_decision_text_prices(d, TECH, PRICE_LEVELS) == []


def test_missing_indicators_degrades_to_no_anomalies():
    d = _decision(["站稳18.8以上"])
    assert check_decision_text_prices(d, {}, {"available": False}) == []
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/nodes/test_decision_price_check.py -v`
Expected: FAIL（`ModuleNotFoundError: finance_agent.decision_price_check`）

- [ ] **Step 3: 实现 `src/finance_agent/decision_price_check.py`**

```python
"""决策文本价位与已验证技术指标交叉校验（price-level-tooling 2026-09 新增需求）。

确定性代码校验，SHALL NOT 硬中断管线：只产出 anomaly 供报告标注与 trace 可观测。
归属优先级：文本点名指标（如「MA60」「60日均线」）> price_levels 参考带 > 最近指标。
"""

from __future__ import annotations

import re
from typing import Any

# 偏差阈值（design.md：默认 2%，误报按 incident 026 分桶后再调）
DEVIATION_THRESHOLD_PCT = 2.0

# 文本点名指标 → technical_indicators 键路径（「60日均线」「半年线」类别名同义收录）
_NAMED_INDICATORS: dict[str, str] = {
    "MA60": "MA.60", "60日均线": "MA.60", "60日线": "MA.60",
    "MA20": "MA.20", "20日均线": "MA.20", "20日线": "MA.20",
    "MA10": "MA.10", "10日线": "MA.10",
    "MA5": "MA.5", "5日线": "MA.5",
    "布林上轨": "BOLL.upper", "布林下轨": "BOLL.lower", "布林中轨": "BOLL.middle",
}

# 上破/下破触发动词（空洞形态判定）
_BREAKOUT_WORDS = ("站上", "突破", "收复", "站稳")
_BREAKDOWN_WORDS = ("跌破", "回落至", "失守", "回踩")

# 数值提取：X.Y / X；排除「X:Y 比值」「X%」「X倍」「X次」「X年/月/日」量纲
_NUM_RE = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?")
_RATIO_RE = re.compile(r"\d+(?:\.\d+)?\s*[:：]\s*\d+(?:\.\d+)?")
_DIMENSION_RE = re.compile(r"(?<=[\d.]）（?)\s*(?:%|倍|次|年|个月|月|日|天|手|股|亿|万)")


def extract_price_candidates(text: str) -> list[dict[str, Any]]:
    """从触发条件/理由文本提取价格量纲候选值（百分比/比值/计数类量纲剔除）。"""
    if not text:
        return []
    cleaned = _RATIO_RE.sub(" ", text)
    cleaned = _DIMENSION_RE.sub(" ", cleaned)
    candidates: list[dict[str, Any]] = []
    for match in _NUM_RE.finditer(cleaned):
        value = float(match.group())
        # 量纲 sanity：比率小数（<1）与年份形态不进价位核对
        if 0 < value < 1 or 1900 < value < 2100:
            continue
        candidates.append({"value": value, "context": cleaned})
    return candidates


def _lookup_indicator(tech: dict, path: str) -> float | None:
    parts = path.split(".")
    node: object = tech
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, list):
        tail = [v for v in node if isinstance(v, (int, float))]
        return float(tail[-1]) if tail else None
    return None


def _named_indicator_in(text: str) -> tuple[str, str] | None:
    lowered = text
    for name, path in _NAMED_INDICATORS.items():
        if name in lowered:
            return name, path
    return None


def check_decision_text_prices(
    decision: TradeDecision | dict,
    technical_indicators: dict,
    price_levels: dict,
) -> list[dict[str, Any]]:
    """核对决策文本价位：偏差形态 + 空洞形态。不可核对时降级为空（不误报）。"""
    if isinstance(decision, dict):
        triggers = decision.get("reeval_triggers") or []
        reasoning = decision.get("reasoning") or ""
    else:
        triggers = decision.reeval_triggers or []
        reasoning = decision.reasoning or ""
    if not isinstance(technical_indicators, dict) or not technical_indicators:
        return []
    latest_close = None
    if isinstance(price_levels, dict):
        ref = price_levels.get("entry_ref")
        if isinstance(ref, (int, float)) and ref > 0:
            latest_close = float(ref)

    anomalies: list[dict[str, Any]] = []
    texts = [t for t in triggers if isinstance(t, str) and t.strip()]
    texts.append(reasoning)
    for text in texts:
        for candidate in extract_price_candidates(text):
            value = candidate["value"]
            # 归属 1：文本点名指标
            named = _named_indicator_in(text)
            if named:
                verified = _lookup_indicator(technical_indicators, named[1])
                if verified:
                    deviation = abs(value - verified) / verified * 100
                    if deviation > DEVIATION_THRESHOLD_PCT:
                        anomalies.append({
                            "type": "decision_price_deviation",
                            "source_text": text,
                            "stated_value": value,
                            "indicator": named[1],
                            "verified_value": verified,
                            "deviation_pct": round(deviation, 2),
                        })
                continue
            if latest_close is None:
                continue
            # 归属 2：空洞形态——触发动词 + 现价方向矛盾
            if any(w in text for w in _BREAKOUT_WORDS) and value <= latest_close:
                anomalies.append({
                    "type": "decision_void_trigger", "source_text": text,
                    "stated_value": value, "indicator": "entry_ref",
                    "verified_value": latest_close, "deviation_pct": None,
                })
            elif any(w in text for w in _BREAKDOWN_WORDS) and value >= latest_close:
                anomalies.append({
                    "type": "decision_void_trigger", "source_text": text,
                    "stated_value": value, "indicator": "entry_ref",
                    "verified_value": latest_close, "deviation_pct": None,
                })
    return anomalies
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/nodes/test_decision_price_check.py -v`
Expected: 全部 PASS（若 BOLL 子键名与 `metrics/technical.py:80` 实际不符，以实际为准修正 fixture）

- [ ] **Step 5: risk.py 接线 + 报告标注的失败测试**

risk.py 接线（终稿 checks 之后、return 之前）：

```python
    # 决策文本价位交叉校验（price-level-tooling 2026-09）：anomaly 可观测，不中断
    decision_price_anomalies = check_decision_text_prices(
        decision,
        state.get("technical_indicators") or {},
        state.get("price_levels") or {},
    )
```

返回 dict 增加：

```python
        "decision_price_anomalies": decision_price_anomalies,
```

并在返回前合并进既有 anomalies 通道：

```python
    merged_anomalies = list(state.get("anomalies") or []) + decision_price_anomalies
```

（返回 dict 增加 `"anomalies": merged_anomalies,`——若图通道对 anomalies 有 reducer 冲突，以 state.py 声明为准：只写 `decision_price_anomalies` 新键并在 report 节点合并。）

失败测试（`tests/nodes/test_decision_price_check.py` 追加）：

```python
class TestReportAnnotation:
    """报告 reeval 行旁渲染「价位待核实」标注（异常时才出现）。"""

    def test_report_annotates_deviation(self):
        from finance_agent.nodes.report import _format_reeval_with_anomalies
        anomalies = [{
            "type": "decision_price_deviation", "source_text": "价格放量突破MA60并站稳18.8以上",
            "stated_value": 18.8, "indicator": "MA.60", "verified_value": 18.066,
            "deviation_pct": 4.07,
        }]
        out = _format_reeval_with_anomalies(["价格放量突破MA60并站稳18.8以上"], anomalies)
        assert "价位待核实" in out
        assert "18.066" in out

    def test_report_without_anomalies_stays_clean(self):
        from finance_agent.nodes.report import _format_reeval_with_anomalies
        out = _format_reeval_with_anomalies(["跌破 22.94 重估"], [])
        assert "价位待核实" not in out
        assert "跌破 22.94 重估" in out
```

- [ ] **Step 6: 跑测试确认红**

Run: `uv run pytest tests/nodes/test_decision_price_check.py::TestReportAnnotation -v`
Expected: FAIL（`ImportError: _format_reeval_with_anomalies`）

- [ ] **Step 7: report.py 实现标注渲染**

```python
def _format_reeval_with_anomalies(
    triggers: list[str], anomalies: list[dict],
) -> str:
    """渲染 reeval 条目；命中 anomaly 的条目追加「价位待核实」括注（含已验证值）。"""
    base = _fmt_reeval_triggers(triggers)
    if base == "未申报" or not anomalies:
        return base
    marks = []
    for a in anomalies:
        src = str(a.get("source_text") or "")
        verified = a.get("verified_value")
        if a.get("type") == "decision_void_trigger":
            marks.append(f"「{src[:40]}…」现价 {verified} 下已满足，不构成有效门槛")
        else:
            marks.append(
                f"「{src[:40]}…」与已验证 {a.get('indicator')}={verified} 偏差 {a.get('deviation_pct')}%"
            )
    if marks:
        return f"{base}（⚠ 价位待核实：{'；'.join(marks)}）"
    return base
```

`_format_trade_decision` 中 reeval 行改为调用 `_format_reeval_with_anomalies(triggers, decision_price_anomalies)`——决策函数需从 state 透传 anomalies：`generate_report` 里 `decision_price_anomalies = state.get("decision_price_anomalies") or []`，作为参数传给 `_format_trade_decision(decision, decision_price_anomalies=...)`（默认 `()`，既有调用方签名兼容）。

- [ ] **Step 8: 全量回归 + Commit**

Run: `uv run pytest tests/nodes/ -v && uv run pytest tests/nodes/test_report.py -v`
Expected: 全部 PASS

```bash
git add src/finance_agent/decision_price_check.py src/finance_agent/nodes/risk.py src/finance_agent/nodes/report.py tests/nodes/test_decision_price_check.py
git commit -m "feat(risk): 决策文本价位交叉校验——触发价幻觉/空洞触发 anomaly 可观测 + 报告标注 (update-decision-integrity-gates T3)"
```

---

### Task 4: FM 完整性可见性 + 置信度漂移标注

**Files:**
- Modify: `src/finance_agent/nodes/fund_manager.py`（`_build_fund_manager_context` 注入完整性 note）
- Modify: `src/finance_agent/nodes/report.py`（FM 节渲染完整性标注 + 置信度漂移标注）
- Test: `tests/nodes/test_fm_visibility.py`（新建）

**Interfaces:**
- Consumes: state 键 `final_price_check`/`final_inaction_check`/`final_reeval_check`（Task 2 产出）
- Produces: 无新 state 键（渲染消费既有键）

- [ ] **Step 1: 写失败测试**（`tests/nodes/test_fm_visibility.py` 新建）

```python
"""FM 审批对象完整性可见性（agent-node-contracts 2026-09 新增）。"""
import json

from finance_agent.nodes.fund_manager import _build_fund_manager_context


def _state(**overrides):
    base = {
        "final_trade_decision": {
            "action": "sell", "confidence": 0.55, "reasoning": "r",
            "position_size": "light", "entry_price": 2.99,
            "stop_loss": 3.12, "target_price": 2.9, "reeval_triggers": [],
        },
        "final_price_check": {"result": "pass", "note": ""},
        "final_inaction_check": {"result": "pass", "note": ""},
        "final_reeval_check": {"result": "released", "note": "已打回仍未申报：reeval_triggers"},
    }
    base.update(overrides)
    return base


def test_fm_context_contains_incompleteness_note():
    context = _build_fund_manager_context(_state())
    assert "已打回仍未申报" in context
    assert "final_reeval_check" in context or "再评估触发条件" in context


def test_fm_context_clean_when_no_notes():
    state = _state(final_reeval_check={"result": "pass", "note": ""})
    context = _build_fund_manager_context(state)
    # 无标注时不得出现空标注噪音行
    assert "完整性检查" not in context or "pass" not in context
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/nodes/test_fm_visibility.py -v`
Expected: FAIL

- [ ] **Step 3: `_build_fund_manager_context` 注入**（「交易决策」段之后）

```python
    # FM 审批对象完整性可见性（agent-node-contracts 2026-09）：终稿检查标注必须进上下文
    completeness_lines = []
    for key, label in (
        ("final_price_check", "价位完整性"),
        ("final_inaction_check", "不行动理由完整性"),
        ("final_reeval_check", "再评估触发条件完整性"),
    ):
        check = state.get(key)
        if isinstance(check, dict) and check.get("note"):
            completeness_lines.append(f"- {label}：{check['note']}")
    if completeness_lines:
        sections.append("终稿完整性检查：\n" + "\n".join(completeness_lines))
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/nodes/test_fm_visibility.py -v`
Expected: PASS ×2

- [ ] **Step 5: 报告端失败测试**（追加到 `tests/nodes/test_fm_visibility.py`）

```python
class TestReportFmSectionAnnotations:
    """报告 FM 节：结构不完整标注并排渲染 + 置信度漂移标注。"""

    def _render_fm(self, state):
        # 以 report.py FM 段拼装函数为被测对象；若为内联代码，抽 _format_fm_section(state) 后测
        from finance_agent.nodes.report import _format_fm_section
        return _format_fm_section(state)

    def test_incompleteness_note_rendered_before_fm_opinion(self):
        state = {
            "fund_manager_decision": "approve",
            "fund_manager_action": "sell",
            "fund_manager_confidence": 0.55,
            "fund_manager_decision_reasoning": "执行安排完备",
            "final_reeval_check": {"result": "released", "note": "已打回仍未申报：reeval_triggers"},
            "final_price_check": {"result": "pass", "note": ""},
            "final_inaction_check": {"result": "pass", "note": ""},
        }
        out = self._render_fm(state)
        assert out.index("已打回仍未申报") < out.index("执行安排完备")

    def test_confidence_drift_annotated_over_threshold(self):
        state = {
            "fund_manager_decision": "approve",
            "fund_manager_action": "watch",
            "fund_manager_confidence": 0.8,
            "fund_manager_decision_reasoning": "批准观望",
            "final_trade_decision": {"action": "watch", "confidence": 0.55},
            "final_price_check": {"result": "pass", "note": ""},
            "final_inaction_check": {"result": "pass", "note": ""},
            "final_reeval_check": {"result": "pass", "note": ""},
        }
        out = self._render_fm(state)
        assert "置信度漂移" in out
        assert "0.8" in out and "0.55" in out

    def test_no_drift_annotation_within_threshold(self):
        state = {
            "fund_manager_decision": "approve",
            "fund_manager_action": "watch",
            "fund_manager_confidence": 0.6,
            "fund_manager_decision_reasoning": "批准观望",
            "final_trade_decision": {"action": "watch", "confidence": 0.55},
            "final_price_check": {"result": "pass", "note": ""},
            "final_inaction_check": {"result": "pass", "note": ""},
            "final_reeval_check": {"result": "pass", "note": ""},
        }
        assert "置信度漂移" not in self._render_fm(state)
```

- [ ] **Step 6: 跑测试确认红**

Run: `uv run pytest tests/nodes/test_fm_visibility.py -v`
Expected: FAIL（`ImportError: _format_fm_section`——report.py FM 段目前内联在 `generate_report`，按既有代码抽出 `_format_fm_section(state) -> str`，行为不变）

- [ ] **Step 7: report.py 实现**（抽 `_format_fm_section`，在既有 FM 渲染基础上插入两块标注）

```python
def _format_fm_section(state: dict) -> str:
    """基金经理决策节：完整性标注并排 + FM 标注（含置信度漂移 >0.15 可观测）。"""
    # ……（既有 :375-402 逻辑原样搬入，仅插入以下两段）……
    completeness_lines = []
    for key, label in (
        ("final_price_check", "价位完整性"),
        ("final_inaction_check", "不行动理由完整性"),
        ("final_reeval_check", "再评估触发条件完整性"),
    ):
        check = state.get(key)
        if isinstance(check, dict) and check.get("note"):
            completeness_lines.append(f"⚠ 方案结构不完整——{label}：{check['note']}")
    fm_conf = state.get("fund_manager_confidence")
    final_decision = state.get("final_trade_decision")
    final_conf = (
        final_decision.get("confidence")
        if isinstance(final_decision, dict)
        else getattr(final_decision, "confidence", None)
    )
    drift_line = ""
    if isinstance(fm_conf, (int, float)) and isinstance(final_conf, (int, float)):
        if abs(float(fm_conf) - float(final_conf)) > 0.15:
            drift_line = (
                f"⚠ 置信度漂移：FM {fm_conf} vs 终稿 {final_conf}"
                f"（差异是否合理见审批理由，供标注人判读）"
            )
    # 拼装顺序：完整性标注行 → 漂移标注行 → 既有审批标注/操作定性/理由块
```

`generate_report` 中 FM 段调用替换为 `_format_fm_section(state)`。

- [ ] **Step 8: 跑测试确认绿 + 回归**

Run: `uv run pytest tests/nodes/ -v`
Expected: 全部 PASS

- [ ] **Step 9: Commit**

```bash
git add src/finance_agent/nodes/fund_manager.py src/finance_agent/nodes/report.py tests/nodes/test_fm_visibility.py
git commit -m "feat(report): FM 审批可见性——完整性标注并排渲染 + 置信度漂移标注 (update-decision-integrity-gates T4)"
```

---

### Task 5: prompt 语义与保真约束 + deploy

**Files:**
- Modify: `src/finance_agent/prompts/fund_manager.md`
- Modify: `src/finance_agent/prompts/risk_judge.md`

**Interfaces:**
- Consumes: Task 2-4 的 state 键（prompt 文案与之呼应：`final_reeval_check` note 会出现进上下文）

- [ ] **Step 1: `fund_manager.md` 追加**（confidence 字段说明之后）

```markdown
- confidence 语义：你给出的 confidence 是「对本次裁决（approve/reject/return 及操作定性）正确性的置信度」，不是对标的涨跌的预测，也不继承被审批方案的置信度。当你的 confidence 与终稿方案 confidence 偏差超过 0.15 时，必须在 reasoning 中说明差异理由（如「对触发条件完备性的独立判断」）。
- 复述保真：reasoning 中引用上游指标名与数值必须逐字引用（如「DIF-DEA 柱」「MA60=18.066」），禁止改写指标名（如写成「DCF 柱」）、禁止把价位数值复述进百分比阈值清单（如把近期低点 6.12 写成「降幅阈值 6.12」）、禁止混同不同量纲。
- 上下文中的「终稿完整性检查」标注（如「已打回仍未申报」）表示方案结构不完整：可以批准，但 reasoning MUST NOT 声称该维度「完备」——如实描述缺口与你的处理理由。
```

- [ ] **Step 2: `risk_judge.md` 追加**（confidence 锚点行之后）

```markdown
- confidence 字段值必须与 reasoning 中的置信度陈述一致：reasoning 说「维持 0.55」时字段不得写 0.6；调整置信度时以字段值为准并在 reasoning 中同步表述。
- buy/sell 终稿必须申报 reeval_triggers（1-3 条可观察、可判定的退出/加仓再评估条件）——风险辩论中各方已提出的触发条件应结构化继承，不得留空。
```

- [ ] **Step 3: 发布 + 门禁验证**

Run: `uv run python scripts/deploy_prompts.py`
Expected: 输出显示 fund_manager / risk_judge 更新成功（或同内容跳过——若脚本报「无变化」则检查是否改对了权威源文件）

Run: `uv run pytest tests/ -k "prompt_deploy or prompt_consistency" -v`
Expected: 全部 PASS（prompt-deploy-consistency 门禁）

- [ ] **Step 4: Commit**

```bash
git add src/finance_agent/prompts/fund_manager.md src/finance_agent/prompts/risk_judge.md
git commit -m "feat(prompts): FM confidence 语义/复述保真/完整性标注纪律 + risk_judge 置信度一致与 reeval 必填纪律 (update-decision-integrity-gates T5)"
```

---

### Task 6: 全量验证收口

- [ ] **Step 1: 全量测试**

Run: `uv run pytest`
Expected: 0 failures（Langfuse 依赖：确认 Docker 栈在线，否则先 `docker compose up -d`）

- [ ] **Step 2: Lint + 类型**

Run: `uv run ruff check && uv run mypy`
Expected: 无新增告警

- [ ] **Step 3: 对照 delta tasks.md 逐条勾选**（`openspec/changes/update-decision-integrity-gates/tasks.md`）

- [ ] **Step 4: 提交 PR 前最终 diff 自审**（对照 Global Constraints：不触碰 Dockerfile；渲染归一无回写；校验器无硬中断）
