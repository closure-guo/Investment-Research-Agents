# ground-comparative-delta-claims Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让比较型差值数字（`comparative` claim 的非枚举 `stated_value`）可观测、把生产中失效的 `derived_series` 通路接回、并预生成均线差幅——全部为确定性后端逻辑，零 LLM 新调用、零 prompt 变更。

**Architecture:** 三块递进：① `derived_series` 升格为已声明 state channel（修 incident-027 同构断点：未声明键被图合并静默丢弃 + context 前缀与真实键不一致），并把技术面 context 的引用前缀对齐到真实键；② `calc_derived_series` 抽取 `_calc_ma` 复用既有均线口径、新增四个均线差幅字段，`derived_series` 注册进计算型重算注册表与有符号量根键集合；③ `_verify_comparative` 对非枚举 `stated_value` 判 UNVERIFIABLE + 独立桶 `comparative_delta_unregistered` + 覆盖缺口计数，并沿既有拆报链路（state 键 → `evals/task.py` → `evals/run.py` evaluator）输出为第三类 UNVERIFIABLE 计数。

**Tech Stack:** Python 3（uv 管理）/ pandas / pydantic / LangGraph / pytest / ruff / mypy

## Global Constraints

- 所有命令用 `uv run` 前缀：`uv run pytest ...`、`uv run ruff check`、`uv run ruff format`、`uv run mypy`。
- 容差语义不得改动：引用校验用 `citation.py` 的 `ABS_TOL=0.01` / `REL_TOL=0.005` 常量（测试断言用 `pytest.approx`，不复制数值）。
- 新增 state 键必须同时在 `src/finance_agent/state.py` 声明 + 图通道契约测试断言（incident 027：未声明键被图合并静默丢弃；本项目已因此踩坑两次）。
- 本 delta **不修改任何 `src/finance_agent/prompts/*.md`**，因此**不需要**执行 `scripts/deploy_prompts.py`（prompt 发布门禁与本次无关）。
- 不改 `comparative` 三枚举（`greater_than` / `less_than` / `equal_to`）路径的任何行为；不新增差值重算语义（判 PASS/FAIL）——那是被显式推迟的「路线 2」。
- 代码注释用中文，与仓库现有风格一致（解释「为什么」而非「做什么」）。
- 工作区已有与本计划无关的未跟踪文件（`evals/judge_calibration/data/judge-sample-*`、被删除的 `gui-test-screenshots/*.png`、已修改的 `openspec/changes/BACKLOG.md`）——**提交时只 `git add` 本任务涉及的文件**，不得 `git add -A`。
- 每个 Task 结束必须提交一次（消息格式见各 Task Step）。

---

### Task 1: `derived_series` channel 声明 + context 前缀修复（A 类 bug，先红后绿）

**Files:**
- Modify: `src/finance_agent/state.py`（在 `technical_indicators` / `risk_metrics` 声明附近新增一行）
- Modify: `src/finance_agent/nodes/analysts.py:385`（context 前缀文案）
- Modify: `tests/nodes/test_validate_trade_prices.py:300-308`（`TestStateChannelsDeclared` 断言集合）
- Modify: `tests/nodes/test_toolize_quick.py:42-56`（`TestDerivedSeriesInjection` 增加前缀断言）
- Create: `tests/nodes/test_derived_series_channel.py`（编译图端到端 + claim 可解析）

**Interfaces:**
- Consumes: `finance_agent.nodes.compute.compute_metrics` 写入的 `derived_series` 键（现状）；`finance_agent.nodes.analysts._build_technical_context(state) -> str`
- Produces: `AnalysisState.derived_series: dict`（声明）；context 中可引用前缀 `derived_series.`；后续 Task 2/3 依赖该键名

- [ ] **Step 1: 写红灯①——channel 声明断言**

修改 `tests/nodes/test_validate_trade_prices.py` 的 `test_graph_channels_declare_validate_keys`，在元组末尾加一行：

```python
        for key in (
            "price_check",
            "price_check_feedback",
            "price_check_attempts",
            "price_level_corrected",
            "price_level_correction_reason",
            "derived_metrics",
            "derived_series",  # ground-comparative-delta-claims：compute 写入但从未声明
        ):
            assert key in channels, f"AnalysisState 缺少声明: {key}"
```

- [ ] **Step 2: 运行红灯①，确认失败**

Run: `uv run pytest tests/nodes/test_validate_trade_prices.py::TestStateChannelsDeclared -v`
Expected: FAIL —— `AssertionError: AnalysisState 缺少声明: derived_series`

- [ ] **Step 3: 写红灯②——context 前缀断言**

在 `tests/nodes/test_toolize_quick.py` 的 `test_derived_table_in_context` 末尾追加：

```python
        assert "derived_series." in ctx  # 前缀必须等于真实 state 键（否则 claim 不可解析）
```

在 `tests/nodes/test_toolize_quick.py` 的 `test_no_derived_no_section` 末尾追加：

```python
        assert "derived." not in ctx.replace("derived_series.", "")  # 不得再出现旧前缀
```

- [ ] **Step 4: 运行红灯②，确认失败**

Run: `uv run pytest tests/nodes/test_toolize_quick.py::TestDerivedSeriesInjection -v`
Expected: FAIL —— `assert 'derived_series.' in ctx` 为 False（当前文案是 `field_ref 前缀 derived.`）

- [ ] **Step 5: 写红灯③——编译图端到端**

创建 `tests/nodes/test_derived_series_channel.py`：

```python
"""ground-comparative-delta-claims Task 1：derived_series 通道 + 技术面 context 端到端。

教训（incident 027 同构）：toolize 验证只在节点函数层用 dict state 测了「注入」，
没走编译图——而真实图中键未声明会被图合并静默丢弃。本文件必须走编译图。
"""

import os

# 测试导入链可能触发 Langfuse 初始化；清空密钥避免连接拖慢（同 test_pipeline_stub 手法）
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)


class TestDerivedSeriesChannel:
    def test_graph_channels_declare_derived_series(self):
        from finance_agent.graph import build_5layer_graph

        channels = set(build_5layer_graph().builder.channels)
        assert "derived_series" in channels, "AnalysisState 缺少声明: derived_series"

    def test_derived_series_reaches_technical_context_in_real_graph(self, monkeypatch):
        """TESTING=1 确定性 stub 下跑通编译图：派生值存活且进入技术面 context。"""
        monkeypatch.setenv("TESTING", "1")
        import finance_agent.nodes.analysts as analysts_mod

        captured: dict = {}
        original = analysts_mod._build_technical_context

        def spy(state):
            ctx = original(state)
            captured.setdefault("runs", []).append((bool(state.get("derived_series")), ctx))
            return ctx

        monkeypatch.setattr(analysts_mod, "_build_technical_context", spy)
        from finance_agent.graph import build_5layer_graph

        result = build_5layer_graph().invoke(
            {"stock_code": "600519", "stock_name": "贵州茅台"},
            config={"recursion_limit": 100},
        )
        assert result.get("derived_series"), "derived_series 被图合并丢弃（未声明 channel）"
        runs = captured.get("runs") or []
        assert runs, "技术分析师 context 未构建"
        state_has_derived, ctx = runs[0]
        assert state_has_derived is True
        assert "常用派生值" in ctx
        assert "derived_series." in ctx

    def test_derived_claim_resolves_and_passes(self):
        """以 derived_series.<字段> 为 field_ref 的数值 claim 可解析并通过校验。"""
        from finance_agent.citation import Claim as CitationClaim
        from finance_agent.citation import verify_claims

        state = {"derived_series": {"chg_5d": 0.03}}
        claim = CitationClaim(
            claim_type="numerical",
            source_type="data",
            field_ref="derived_series.chg_5d",
            stated_value=0.03,
            interpretation="近 5 日涨跌幅 0.03",
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "PASS"
```

- [ ] **Step 6: 运行红灯③，确认失败**

Run: `uv run pytest tests/nodes/test_derived_series_channel.py -v`
Expected: 第一个用例 FAIL（缺声明）；第二个用例 FAIL（`result.get("derived_series")` 为 None——图合并丢弃）；第三个用例 PASS（解析器本身无问题，作为对照）。

- [ ] **Step 7: 修复——声明键 + 对齐前缀**

`src/finance_agent/state.py` 在 `risk_metrics: dict` 行之后插入：

```python
    derived_series: dict  # calc_derived_series() 输出（toolize-price-levels；此前未声明被图丢弃）
```

`src/finance_agent/nodes/analysts.py:385` 将：

```python
                "常用派生值（工具预生成，直接引用；field_ref 前缀 derived.）:\n"
```

改为：

```python
                "常用派生值（工具预生成，直接引用；field_ref 前缀 derived_series.）:\n"
```

- [ ] **Step 8: 运行全部相关测试，确认转绿且不回归**

Run: `uv run pytest tests/nodes/test_derived_series_channel.py tests/nodes/test_toolize_quick.py tests/nodes/test_validate_trade_prices.py -v`
Expected: 全 PASS。若第二个用例耗时 > 60s，在 Task 报告里记录实测时长（stub 全图运行，预计 10–60s）。

- [ ] **Step 9: 提交**

```bash
git add src/finance_agent/state.py src/finance_agent/nodes/analysts.py tests/nodes/test_validate_trade_prices.py tests/nodes/test_toolize_quick.py tests/nodes/test_derived_series_channel.py
git commit -m "fix(pipeline): derived_series 声明为 state 通道 + context 前缀对齐真实键（incident 027 同构断点）"
```

---

### Task 2: 均线差幅预生成（`calc_derived_series` 扩展）

**Files:**
- Modify: `src/finance_agent/metrics/technical.py`（`calc_technical` 抽 `_calc_ma`；`calc_derived_series` 加四个字段）
- Test: `tests/metrics/test_levels.py`（`TestCalcDerivedSeries` 追加用例）

**Interfaces:**
- Consumes: 无（纯函数）
- Produces: `_calc_ma(close: pd.Series) -> dict[str, list[float | None]]`；`calc_derived_series` 新增键 `ma_spread_5_20_pct` / `ma_spread_20_60_pct` / `close_vs_ma20_pct` / `close_vs_ma60_pct`（百分比，round 2 位小数，缺失或分母为 0 时为 None）——Task 3 依赖这些键名

- [ ] **Step 1: 写红灯——四个差幅字段与边界**

在 `tests/metrics/test_levels.py` 的 `TestCalcDerivedSeries` 类内追加：

```python
    def test_ma_spread_fields(self):
        # trend=1.0：收盘 100→179；MA5 末值=177.0，MA20=169.5，MA60=149.5
        d = calc_derived_series(_kline(80, base=100.0, trend=1.0))
        assert d["ma_spread_5_20_pct"] == pytest.approx(4.42, abs=0.01)
        assert d["ma_spread_20_60_pct"] == pytest.approx(13.38, abs=0.01)
        assert d["close_vs_ma20_pct"] == pytest.approx(5.60, abs=0.01)
        assert d["close_vs_ma60_pct"] == pytest.approx(19.73, abs=0.01)

    def test_ma_spreads_match_technical_ma(self):
        # 与 calc_technical 的均线口径必须同源（同一份 _calc_ma）
        from finance_agent.metrics.technical import calc_technical

        k = _kline(80, base=100.0, trend=1.0)
        d = calc_derived_series(k)
        ma = calc_technical(k)["MA"]
        ma5, ma20 = ma["5"][-1], ma["20"][-1]
        assert d["ma_spread_5_20_pct"] == pytest.approx((ma5 - ma20) / ma20 * 100, abs=0.01)

    def test_ma60_spreads_none_when_short(self):
        d = calc_derived_series(_kline(30))
        assert d["ma_spread_20_60_pct"] is None
        assert d["close_vs_ma60_pct"] is None
        assert d["ma_spread_5_20_pct"] is not None

    def test_ma_spreads_none_kline(self):
        d = calc_derived_series(None)
        assert d["ma_spread_5_20_pct"] is None
        assert d["close_vs_ma60_pct"] is None
```

- [ ] **Step 2: 运行红灯，确认失败**

Run: `uv run pytest tests/metrics/test_levels.py::TestCalcDerivedSeries -v`
Expected: FAIL —— `KeyError: 'ma_spread_5_20_pct'`

- [ ] **Step 3: 实现——抽 `_calc_ma` + 四个字段**

`src/finance_agent/metrics/technical.py`：把 `calc_technical` 中 MA 段（第 28–36 行）替换为调用新函数：

```python
    # ── MA（简单移动平均；与 calc_derived_series 共用 _calc_ma，口径唯一）──
    result["MA"] = _calc_ma(close)
```

并在 `calc_technical` 之前新增：

```python
def _calc_ma(close: pd.Series) -> dict[str, list[float | None]]:
    """简单移动平均（5/10/20/60）；数据不足该周期全 None。

    calc_technical 与 calc_derived_series 共用此实现——均线口径只允许一份。
    """
    ma_result: dict[str, list[float | None]] = {}
    for period in (5, 10, 20, 60):
        if len(close) >= period:
            ma = close.rolling(window=period).mean()
            ma_result[str(period)] = [None if pd.isna(v) else float(v) for v in ma]
        else:
            ma_result[str(period)] = [None] * len(close)
    return ma_result
```

`calc_derived_series` 的空 K 线分支 keys 列表与主体各加四字段：

```python
    if kline is None or len(kline) == 0:
        keys = [f"chg_{w}d" for w in windows] + [
            "drawdown_from_high_250d",
            "rebound_from_low_250d",
            "ma_spread_5_20_pct",
            "ma_spread_20_60_pct",
            "close_vs_ma20_pct",
            "close_vs_ma60_pct",
        ]
        return dict.fromkeys(keys)
```

在 `result["rebound_from_low_250d"] = ...` 之后追加：

```python
    # 均线差幅（ground-comparative-delta-claims）：MA/收盘两个水平量的相对差（%），
    # 正 = 前者高于后者；复用 _calc_ma 同一口径，缺失/分母为 0 → None 如实标缺
    ma = _calc_ma(close)
    ma_last = {w: (ma[str(w)][-1] if ma[str(w)] else None) for w in (5, 20, 60)}
    result["ma_spread_5_20_pct"] = _pct_spread(ma_last[5], ma_last[20])
    result["ma_spread_20_60_pct"] = _pct_spread(ma_last[20], ma_last[60])
    result["close_vs_ma20_pct"] = _pct_spread(last, ma_last[20])
    result["close_vs_ma60_pct"] = _pct_spread(last, ma_last[60])
```

并在 `calc_derived_series` 之前新增辅助函数（`last` 变量已存在于该函数中，无需重复定义）：

```python
def _pct_spread(a: float | None, b: float | None) -> float | None:
    """(a − b) / b × 100（%）；任一缺失或 b 为 0 → None。"""
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 2)
```

- [ ] **Step 4: 运行测试，确认转绿且不回归**

Run: `uv run pytest tests/metrics/test_levels.py tests/metrics/test_technical.py -v`
Expected: 全 PASS（`calc_technical` 既有用例证明 `_calc_ma` 抽取未改口径）。

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/metrics/technical.py tests/metrics/test_levels.py
git commit -m "feat(metrics): 派生值扩展均线差幅四字段，均线口径收敛 _calc_ma 单一实现"
```

---

### Task 3: 重算注册表 + 有符号量根键（`derived_series` 可验）

**Files:**
- Modify: `src/finance_agent/citation.py`（`_SIGNED_ROOTS`、`_COMPUTATIONAL_RECALC`）
- Test: `tests/test_citation.py`（注册表测试类追加）

**Interfaces:**
- Consumes: Task 2 的 `calc_derived_series` 与四个字段名
- Produces: `_COMPUTATIONAL_RECALC["derived_series"]`；`_SIGNED_ROOTS` 含 `"derived_series"`

- [ ] **Step 1: 写红灯——注册表覆盖 + 计算型 claim 校验 + 方向校验**

在 `tests/test_citation.py` 中 `test_registry_covers_all_metric_families` 所在类内追加（该类已有 `_state` helper）：

```python
    def test_registry_includes_derived_series(self):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        assert "derived_series" in _COMPUTATIONAL_RECALC

    def test_derived_series_recalc_and_claim(self):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-01", periods=80).strftime("%Y-%m-%d"),
                "开盘": [10.0] * 80,
                "收盘": [10.0 + i * 0.1 for i in range(80)],
                "最高": [10.5 + i * 0.1 for i in range(80)],
                "最低": [9.5 + i * 0.1 for i in range(80)],
                "成交量": [1000.0] * 80,
            }
        )
        state = {"kline": kline}
        truth = _COMPUTATIONAL_RECALC["derived_series"](state)
        spread = float(truth["ma_spread_5_20_pct"])
        assert spread == pytest.approx(4.42, abs=0.01)

        ok = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="derived_series.ma_spread_5_20_pct",
            stated_value=spread,
            interpretation="",
        )
        assert verify_claims([ok], state)[0].status == "PASS"

        bad = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="derived_series.ma_spread_5_20_pct",
            stated_value=spread * 1.1,
            interpretation="",
        )
        (r,) = verify_claims([bad], state)
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"

    def test_derived_series_direction_check(self):
        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-01", periods=80).strftime("%Y-%m-%d"),
                "开盘": [10.0] * 80,
                "收盘": [10.0 + i * 0.1 for i in range(80)],
                "最高": [10.5 + i * 0.1 for i in range(80)],
                "最低": [9.5 + i * 0.1 for i in range(80)],
                "成交量": [1000.0] * 80,
            }
        )
        state = {"kline": kline}
        base = dict(
            claim_type="computational",
            source_type="data",
            field_ref="derived_series.ma_spread_5_20_pct",
            stated_value=4.42,
            interpretation="",
        )
        (ok,) = verify_claims([Claim(**base, direction="positive")], state)
        assert ok.status == "PASS"
        (bad,) = verify_claims([Claim(**base, direction="negative")], state)
        assert bad.status == "FAIL"
        assert bad.bucket == "direction_mismatch"
```

- [ ] **Step 2: 运行红灯，确认失败**

Run: `uv run pytest tests/test_citation.py -k "derived_series" -v`
Expected: FAIL —— `assert "derived_series" in _COMPUTATIONAL_RECALC` 为 False（注册表缺失）

- [ ] **Step 3: 实现——注册进重算注册表与有符号根键**

`src/finance_agent/citation.py`：

① 在 import 区把 `calc_derived_series` 加入既有 `finance_agent.metrics.technical` 导入（与 `calc_technical` 同来源）。

② `_COMPUTATIONAL_RECALC` 字典在 `"anomalies": ...` 行之后追加：

```python
    # ground-comparative-delta-claims：派生值以同一份 calc_derived_series 重算
    "derived_series": lambda s: calc_derived_series(s["kline"]),
```

③ `_SIGNED_ROOTS` 改为：

```python
_SIGNED_ROOTS = frozenset({"growth_rates", "quarterly_trend", "derived_series"})
```

- [ ] **Step 4: 运行测试，确认转绿且不回归**

Run: `uv run pytest tests/test_citation.py tests/test_citation_buckets.py -v`
Expected: 全 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/citation.py tests/test_citation.py
git commit -m "feat(citation): derived_series 注册重算根键与有符号量，派生值 claim 可验"
```

---

### Task 4: comparative 非枚举差值的显式降级（独立桶 + 覆盖缺口）

**Files:**
- Modify: `src/finance_agent/citation.py`（`CitationResult.bucket` 的 `Literal` + `_verify_comparative` 非枚举分支）
- Test: `tests/test_citation_buckets.py`

**Interfaces:**
- Consumes: `CitationResult` / `_verify_comparative`（现状）
- Produces: bucket 枚举值 `"comparative_delta_unregistered"`——Task 5 的计数以它为准

- [ ] **Step 1: 写红灯——非枚举降级、三枚举不回归、不重算**

在 `tests/test_citation_buckets.py` 的 `TestFailBuckets` 类内追加：

```python
    def test_comparative_numeric_delta_unverifiable_with_gap(self):
        """非枚举 stated_value（差值数字）→ UNVERIFIABLE + 独立桶 + 覆盖缺口。"""
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value=2.3,
            interpretation="2024 年 ROE 较 2023 年低约 2.3",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "UNVERIFIABLE"
        assert r.bucket == "comparative_delta_unregistered"
        assert r.coverage_gap is True

    def test_comparative_delta_not_recomputed(self):
        """差值再离谱也不判 FAIL（本变更不新增验证语义）——仍是 UNVERIFIABLE。"""
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value=999.0,
            interpretation="x",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "UNVERIFIABLE"
        assert r.bucket == "comparative_delta_unregistered"

    def test_comparative_enum_path_unchanged(self):
        """三枚举路径行为不变：方向正确 PASS。"""
        state = {"profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}}}
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value="greater_than",
            interpretation="2024 年 ROE 高于 2023 年",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "PASS"
        assert r.bucket is None
```

- [ ] **Step 2: 运行红灯，确认失败**

Run: `uv run pytest tests/test_citation_buckets.py -k comparative -v`
Expected: FAIL —— 新用例中 `r.bucket` 为 None（当前非枚举分支无 bucket、无 gap）；`test_comparative_enum_path_unchanged` 应已 PASS（回归基线）。

- [ ] **Step 3: 实现——bucket 枚举 + 非枚举分支降级**

`src/finance_agent/citation.py`：

① `CitationResult.bucket` 的 `Literal` 列表加一项（注释同步）：

```python
        Literal[
            "value_mismatch",
            "path_unresolvable",
            "semantic_term_mismatch",
            "semantic_period_mismatch",
            "internal_inconsistency",
            "direction_mismatch",
            # 比较型差值申报（非三枚举 stated_value）：显式降级，独立桶计缺口
            "comparative_delta_unregistered",
        ]
        | None
```

② `_verify_comparative` 的 `else` 分支替换为：

```python
    else:
        # 差值数字填入 stated_value（「MA5 较 MA20 低约 2.3%」的 2.3）：非三枚举
        # → 显式降级。不重算差值、不判 PASS/FAIL（ground-comparative-delta-claims
        # 明确推迟的「路线 2」）；独立桶 + 覆盖缺口使问题规模可见。
        return CitationResult(
            status="UNVERIFIABLE",
            claim=claim,
            bucket="comparative_delta_unregistered",
            coverage_gap=True,
        )
```

- [ ] **Step 4: 运行测试，确认转绿且不回归**

Run: `uv run pytest tests/test_citation_buckets.py tests/test_citation.py tests/test_citation_internal.py -v`
Expected: 全 PASS（含既有 `test_unverifiable_has_no_bucket` 等回归用例；若该用例恰好命中 comparative 分支则需人工判读——预期不命中，它用 numerical claim）。

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/citation.py tests/test_citation_buckets.py
git commit -m "feat(citation): 比较型差值申报显式降级为独立桶 + 覆盖缺口（不新增验证语义）"
```

---

### Task 5: 拆报计数链路（state → task → evaluator）

**Files:**
- Modify: `src/finance_agent/nodes/citation_node.py:419-425`（新增计数；`unregistered` 排除该桶避免重复计数）
- Modify: `src/finance_agent/state.py:138-139`（新增 state 键声明）
- Modify: `evals/task.py:105-112`（读取新键）
- Modify: `evals/run.py:201-204`（注册 evaluator）
- Test: `tests/nodes/test_citation_node.py`（计数用例）、`tests/evals/test_task.py`、`tests/evals/test_run.py`

**Interfaces:**
- Consumes: Task 4 的 bucket 值 `comparative_delta_unregistered`
- Produces: state 键 `citation_unverifiable_comparative_delta`；输出键同名；evaluator 名 `eval_citation_unverifiable_comparative_delta`

- [ ] **Step 1: 写红灯①——node 计数与不重复计数**

在 `tests/nodes/test_citation_node.py` 中含 `_report` helper 的测试类内追加：

```python
    def test_comparative_delta_counted_and_split_from_unregistered(self):
        """非枚举差值：独立计数，且不再计入 unregistered（拆报三类不重叠）。"""
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value=2.3,
            interpretation="2024 年 ROE 较 2023 年低约 2.3",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        report = _report("fundamental", [claim], "ROE 较上年下滑 2.3")
        state = {
            "analyst_reports": {"fundamental": report},
            "profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}},
        }
        out = verify_citations(state)
        assert out["citation_unverifiable_comparative_delta"] == 1
        assert out["citation_unverifiable_unregistered"] == 0
        assert out["citation_blocked"] is False
```

- [ ] **Step 2: 运行红灯①，确认失败**

Run: `uv run pytest tests/nodes/test_citation_node.py -k comparative_delta_counted -v`
Expected: FAIL —— `KeyError: 'citation_unverifiable_comparative_delta'`（计数键未产出）

- [ ] **Step 3: 写红灯②——evaluator 注册与 task 输出**

`tests/evals/test_run.py`：把 `test_fourteen_evaluators` 改名为 `test_fifteen_evaluators`，计数改 15，名称集合加 `"eval_citation_unverifiable_comparative_delta"`，docstring 追加「comparative 差值 1 项」。

`tests/evals/test_task.py` 的 `test_deep_output_includes_citation_metrics`：在 `_FakeGraph.invoke` 返回字典中加 `"citation_unverifiable_comparative_delta": 1,`，并在断言区加：

```python
        assert out["citation_unverifiable_comparative_delta"] == 1.0
```

- [ ] **Step 4: 运行红灯②，确认失败**

Run: `uv run pytest tests/evals/test_run.py tests/evals/test_task.py -v`
Expected: FAIL —— evaluator 数量为 14 ≠ 15；`out["citation_unverifiable_comparative_delta"]` 缺失。

- [ ] **Step 5: 实现——四处接线**

① `src/finance_agent/state.py` 在 `citation_unverifiable_unregistered` 行后加：

```python
    citation_unverifiable_comparative_delta: int  # 跟踪：比较型差值申报（非三枚举 stated_value）
```

② `src/finance_agent/nodes/citation_node.py` 返回字典中，`citation_unverifiable_unregistered` 的计数增加排除条件、并新增一个键：

```python
        "citation_unverifiable_unregistered": sum(
            1
            for r in results
            if r.status == "UNVERIFIABLE"
            and r.claim.claim_type not in ("entity", "regulatory")
            and r.claim.source_type != "event"
            and r.bucket != "comparative_delta_unregistered"  # 单独计，避免重复计数
        ),
        "citation_unverifiable_comparative_delta": sum(
            1 for r in results if r.bucket == "comparative_delta_unregistered"
        ),
```

③ `evals/task.py` 在 `citation_unverifiable_unregistered` 读取块之后追加（同款式）：

```python
        "citation_unverifiable_comparative_delta": (
            float(state["citation_unverifiable_comparative_delta"])
            if state.get("citation_unverifiable_comparative_delta") is not None
            else None
        ),
```

④ `evals/run.py` 的 `all_evaluators()` 在 `citation_unverifiable_unregistered` evaluator 之后追加：

```python
        eval_citation_counter(
            "citation_unverifiable_comparative_delta",
            "比较型差值申报（非三枚举 stated_value，不计阻断分母）",
        ),
```

- [ ] **Step 6: 运行测试，确认转绿且不回归**

Run: `uv run pytest tests/nodes/test_citation_node.py tests/evals/test_run.py tests/evals/test_task.py -v`
Expected: 全 PASS。

- [ ] **Step 7: 提交**

```bash
git add src/finance_agent/nodes/citation_node.py src/finance_agent/state.py evals/task.py evals/run.py tests/nodes/test_citation_node.py tests/evals/test_run.py tests/evals/test_task.py
git commit -m "feat(evals): comparative 差值 UNVERIFIABLE 进拆报三类，state→task→evaluator 全链路计数"
```

---

### Task 6: 口径登记 + 全量收口

**Files:**
- Modify: `docs/evals/metrics.md`（§1.3 拆报表新增行；§3 follow-up ① 改写）
- Modify: `openspec/changes/ground-comparative-delta-claims/tasks.md`（勾选已完成的 1–4 节条目）

**Interfaces:**
- Consumes: 全部前序任务
- Produces: 台账口径与 delta 进度一致

- [ ] **Step 1: metrics.md §1.3 表格加一行**

在 `docs/evals/metrics.md` §1.3 拆报表中 `citation_unverifiable_unregistered` 行之后追加：

```markdown
| citation_unverifiable_comparative_delta | 跟踪 | 比较型差值申报（comparative 的 stated_value 非三枚举，如「MA5 较 MA20 低约 2.3%」的 2.3）；不计阻断分母，与 unregistered 互斥不重复计数 |
```

- [ ] **Step 2: metrics.md §3 follow-up ① 改写**

把「**校验器 follow-up（2026-09-13，22 条终裁副产品）**」条目 ① 的「仍开放」段替换为：

```markdown
- ① 比较型 claim（「MA5 较 MA20 低约 X」）重算注册——**计数已落地（2026-09-15，delta `ground-comparative-delta-claims`）**：非枚举差值数学判 UNVERIFIABLE + 独立桶 `comparative_delta_unregistered` + 覆盖缺口，拆报第三类 `citation_unverifiable_comparative_delta`。组件数字此前即可从 state 推导（双端申报已验），差值本身仍不重算。**路线 2 触发条件**：首轮实验后统计该计数占 UNVERIFIABLE 比例并逐条归因（均线/涨跌幅类可枚举组合 vs 长尾）；比例可观且长尾占多 → 立项 formula 申报重算；否则继续扩 `calc_derived_series` 清单
- ①a `derived_series` 通路修复（同日）：此前未声明 channel（图合并丢弃）+ context 前缀 `derived.` 与真实键不一致 → 技术面派生值在生产从未注入且不可引用；已修复并补编译图端到端测试，均线差幅四字段随本次扩展
```

- [ ] **Step 3: 勾选 delta tasks.md**

`openspec/changes/ground-comparative-delta-claims/tasks.md`：把第 1–4 节中已完成的条目 `- [ ]` 改为 `- [x]`（第 5 节真实链路验证与第 6 节收口留待 Task 7 后勾）。

- [ ] **Step 4: 全量验证**

Run: `uv run pytest -q --ignore=tests/e2e` → 期望 0 failed（基线 2371 passed / 2 skipped，本 delta 新增用例后总数上升）
Run: `uv run ruff check src/finance_agent/citation.py src/finance_agent/metrics/technical.py src/finance_agent/state.py src/finance_agent/nodes/analysts.py src/finance_agent/nodes/citation_node.py evals/task.py evals/run.py tests/nodes/test_derived_series_channel.py tests/metrics/test_levels.py tests/test_citation.py tests/test_citation_buckets.py tests/nodes/test_citation_node.py tests/evals/test_run.py tests/evals/test_task.py tests/nodes/test_validate_trade_prices.py tests/nodes/test_toolize_quick.py`
Run: `uv run ruff format --check`（同上文件）→ 全绿
Run: `uv run mypy src/finance_agent/citation.py src/finance_agent/metrics/technical.py` → Success
Run: `openspec validate ground-comparative-delta-claims --strict` → valid

- [ ] **Step 5: 提交**

```bash
git add docs/evals/metrics.md openspec/changes/ground-comparative-delta-claims/tasks.md
git commit -m "docs(evals): comparative 差值计数口径登记 + derived 通路修复记录 + delta 进度勾选"
```

---

### Task 7: 真实链路验证（人工环节，需 LLM 预算——不可跳过）

**Files:**
- Create: `tests/validation/2026-09-15-ground-comparative-delta-claims-validation.md`

**Interfaces:**
- Consumes: 全部前序任务（真实 deep 一次）
- Produces: 归档前置的验证报告（delta tasks.md §5）

- [ ] **Step 1: 跑一次真实 deep 分析（本机，ark 端点）**

Run: `uv run python -c "from evals.task import _run_deep; import json; print(json.dumps(_run_deep({'stock_code': '600519', 'query': '综合评估投资价值'}), ensure_ascii=False)[:2000])"`
（若 `_run_deep` 需要标准入口，改用 `uv run python -m evals.run --help` 确认既有单 item 入口后等价执行。）

- [ ] **Step 2: 核对三件事并逐条记录**

1. Langfuse trace 中技术分析师输入 context 实际包含「常用派生值」块（含四个差幅字段或「数据不足」）；
2. 技术面 claim 中出现 `derived_series.*` 引用且校验 PASS（若有）；
3. 该 trace 全部 comparative claim：非枚举 `stated_value` 条数、是否全部落 `comparative_delta_unregistered`、逐条记录是均线/涨跌幅类还是长尾——作为路线 2 决策首批数据。

- [ ] **Step 3: 落验证报告**

按 `.worktrees` 外仓库既有格式写 `tests/validation/2026-09-15-ground-comparative-delta-claims-validation.md`（变更范围 / 单元与门禁输出 / 真实链路证据 / 边界与遗留）。

- [ ] **Step 4: 提交 + 勾选 delta tasks.md §5**

```bash
git add tests/validation/2026-09-15-ground-comparative-delta-claims-validation.md openspec/changes/ground-comparative-delta-claims/tasks.md
git commit -m "test(validation): ground-comparative-delta-claims 真实链路验证报告（派生值注入 + 差值计数首批数据）"
```

---

## 计分卡（自审）

- **Spec 覆盖**：`price-level-tooling` 修改后的 5 个 Scenario → Task 1（真实图可见可引用 / 通道契约）、Task 2（差幅值 / 数据不足 / 均线口径同源）、Task 3（计算型可重算 / 方向申报）；`citation-verification` 新增 4 个 Scenario → Task 4（计数与桶 / 不新增语义）、Task 5（拆报单列 / 三枚举不变）、Task 6（口径登记）。delta tasks.md §5 真实链路 → Task 7。无遗漏。
- **Placeholder 扫描**：无 TBD/TODO；每个代码步骤含完整代码块。
- **类型一致性**：`derived_series`（键名全篇一致）；字段名 `ma_spread_5_20_pct` / `ma_spread_20_60_pct` / `close_vs_ma20_pct` / `close_vs_ma60_pct`（Task 2 定义 = Task 3 引用）；bucket `comparative_delta_unregistered`（Task 4 定义 = Task 5 引用）；state 键 `citation_unverifiable_comparative_delta`（Task 5 内 state/task/run 三处同名）。
- **已知风险（执行者注意）**：Task 1 Step 6 的编译图用例依赖 `TESTING=1` stub 全链；若 stub 覆盖缺口导致图跑不通，先核对 `tests/test_pipeline_stub.py` 是否仍绿（基线证据），再判断是环境问题还是本测试写法问题。
