# extend-factor-library-coverage P1（算子与计数口径）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans（本会话内联执行；subagent 供应商配额当日不可靠）。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 delta `extend-factor-library-coverage` 的 P1 段——自由参数去重计数 + 19 个新 DSL 算子（比较/条件、高阶时序统计、滚动回归残差、全市场均值、行业中性化、exp）+ 翻译器映射与一元负号 + builtin/提案侧零改动的前置基线与治理记录。

**Architecture:** 全部改动集中在 `factors/dsl.py`（算子表/计数）、`factors/eval_engine.py`（求值 + MarketPanel.groups）、`factors/qlib_translate.py`（映射 + 一元负号）。新算子沿既有 E1 口径（时序 min_periods=window、窗口含 NaN → NaN；横截面沿 code 轴）；`ts_count` 是唯一容忍窗口内 NaN 的时序算子；比较算子显式 NaN 传播。生成侧契约条款（禁横截面/禁回归）不动——`cs_mean`/`ts_resid`/`ind_neutralize` 无 Qlib 映射、不进白名单。

**Tech Stack:** Python 3.12 / pytest / pandas（rolling）/ numpy / ruff / mypy。零新依赖。

## Global Constraints

- 只放宽不收紧：既有全部合法表达式在新计数口径下仍合法；错误消息契约保留「深度 N > 上限 8」「自由参数 N > 上限 3」「字段种类 N > 上限 3」逐字格式。
- 复杂度上限不动：`MAX_AST_DEPTH=8` / `MAX_FREE_PARAMS=3` / `MAX_FIELDS=3`。
- 可选参考字段（float_cap/turnover/listed_days）属 **P2**（delta §7），本计划不碰 `FIELDS`。
- 既有映射回归全绿是翻译器改动的合入硬门禁；负窗口（前瞻引用）仍是翻译红线。
- 测试纪律：期望值逐条独立手写（不复用被测模块生成期望）；运行 pytest 前查 `GET /api/sessions` 无 `status=running`。
- pre-commit 缓存锁可能坏：`ruff check` + `ruff format --check` 等价检查后按需 `--no-verify`。
- 实施分支：`.worktrees/extend-factor-library-coverage`（分支 `extend-factor-library-coverage`，基线 8421efb2）。
- delta 追踪：`openspec/changes/extend-factor-library-coverage/tasks.md`（P1 = §1–§5）。

---

### Task 1: 基线冻结 + 治理修订记录 + 迁移确认（delta 1.1–1.3）

**Files:**
- Create: `tests/validation/2026-11-27-extend-factor-library-coverage-baseline.md`
- Modify: `docs/factor-governance.md`（文末追加 D5）

**Interfaces:** 无代码接口；产物供 §10（桶归属）与 §13（重放对比）引用。

- [ ] **Step 1.1 基线冻结**：只读取数（`GET /api/factors`、`GET /api/factor-research/harvest`、容器内 `data/factor_research/harvest_state.json`、`GET /api/screening/batches` + `/api/screening/pool` 最新成功批），写入 `tests/validation/2026-11-27-extend-factor-library-coverage-baseline.md`：库实态表（12 active + 2 retired 逐条表达式/IC60/ICIR60/pool_increment/category）、漏斗（2444→1772→653→12）、不可译分桶、选股基线（最新成功批 Top-100 代码序列）。标记 `[实测]` + 取数时刻。
- [ ] **Step 1.2 治理记录**：`docs/factor-governance.md` 文末追加 D5 节（口径修订：自由参数按去重数值计数，上限 3 不变；398 条实测依据；与 D4 裁决三「不增加拟合自由度」同向；仅放宽不收紧边界）。
- [ ] **Step 1.3 迁移确认**：读 `/api/factors` 的 category 分布——`外部×12` 仍在 ⇒ `update-harvest-factor-category` 生产迁移**未执行**（其 tasks 5/5 未勾）。记录进基线文档；delta §10（桶归属）实施前须先执行该迁移，本 P1 不依赖。
- [ ] **Step 1.4 提交**：`git add tests/validation/…baseline.md docs/factor-governance.md openspec/changes/extend-factor-library-coverage docs/superpowers/research/… && git commit -m "docs(factors): P1 基线冻结 + 治理 D5 计数口径修订记录"`

### Task 2: 自由参数去重计数（delta 2.1–2.4）

**Files:**
- Modify: `src/finance_agent/factors/dsl.py`（`count_free_params` + 模块 docstring D4 行）
- Test: `tests/factors/test_dsl.py`（新增 3 测试 + docstring D4 行）

**Interfaces:**
- Consumes: `Number.value: int | float`（既有 AST）
- Produces: `count_free_params(node: AstNode) -> int`（签名不变，语义改去重取值个数）

- [ ] **Step 2.1 失败测试**（追加到 `tests/factors/test_dsl.py`）：

```python
class TestFreeParamsDedup:
    """extend-factor-library-coverage：自由参数按去重取值计数（上限 3 不变）。"""

    def test_repeated_values_count_once(self):
        # Number 节点 6 个（1×4、20×2），去重后 {1, 20} = 2 ≤ 3 → 合法
        node = parse(
            "div(ts_mean(sub(div(close,ref(close,1)),1),20),"
            "ts_std(sub(div(close,ref(close,1)),1),20))"
        )
        assert count_free_params(node) == 2

    def test_int_and_float_same_value(self):
        node = parse("sub(mul(close,1),mul(close,1.0))")
        assert count_free_params(node) == 1

    def test_distinct_values_still_rejected(self):
        # 去重后 {5, 10, 20, 0.5} = 4 > 3 → 拒收，消息格式逐字
        with pytest.raises(FactorComplexityError) as exc:
            parse("sub(div(ts_mean(close,5),add(ts_std(close,10),ts_max(close,20))),0.5)")
        assert "自由参数 4 > 上限 3" in str(exc.value)

    def test_window_counts(self):
        assert count_free_params(parse("ts_mean(close,20)")) == 1
        assert count_free_params(parse("close")) == 0
```

- [ ] **Step 2.2 跑红**：`uv run pytest tests/factors/test_dsl.py::TestFreeParamsDedup -q` → 4 FAIL（现口径 6/2 个 Number 节点 → 6 与 2 不符）。
- [ ] **Step 2.3 实现**（`dsl.py`）：

```python
def _number_values(node: AstNode, values: set[int | float]) -> None:
    """收集子树中全部 Number 取值（``1`` 与 ``1.0`` 同值：Python 相等且同哈希）。"""
    if isinstance(node, Number):
        values.add(node.value)
    elif isinstance(node, Call):
        for arg in node.args:
            _number_values(arg, values)


def count_free_params(node: AstNode) -> int:
    """D4 自由参数计数 = Number 节点**去重取值**个数（含 window）。

    Previously: Number 节点总数——同一取值重复出现被重复计数（
    ``Mean(ret,20)/Std(ret,20)`` 形态使 ``1``/``20`` 各计两次）。上限 3 不变；
    重复出现的相同常数不是独立拟合自由度（治理 D5，2026-11-27 口径修订）。
    """
    values: set[int | float] = set()
    _number_values(node, values)
    return len(values)
```

- 同步 `dsl.py` 模块 docstring D4 行 → 「自由参数 = Number 节点**去重取值**个数（含 window）」；`tests/factors/test_dsl.py` 模块 docstring D4 行同步。
- [ ] **Step 2.4 四目录 grep**：`grep -rn "count_free_params\|数字字面量总数\|Number 节点总数" src tests evals scripts` → 核对消费方：`validate.py`（走 parse→复杂度检查，无独立计数）、`qlib_translate.py`（同）、提示词模板措辞「数字字面量总数」**随 P3 统一改并一次 deploy**（delta 12.2/12.3；P1 不动模板、不触发 deploy）。既有断言如与新口径冲突（同一表达式重复取值却断言拒收）逐条改期望并在测试注释注明口径修订出处。
- [ ] **Step 2.5 跑绿 + 提交**：`uv run pytest tests/factors/test_dsl.py -q` 全绿；`git add … && git commit -m "feat(factors): 自由参数按去重数值计数（extend-factor-library-coverage T2）"`

### Task 3: 比较与条件算子 + 翻译器映射 + 一元负号（delta 3.1–3.4）

**Files:**
- Modify: `src/finance_agent/factors/dsl.py`（OPS/ARITY 扩 7 算子）
- Modify: `src/finance_agent/factors/eval_engine.py`（`_compare`/`_if_else` 进 `_ELEMENTWISE_OPS`）
- Modify: `src/finance_agent/factors/qlib_translate.py`（9 条映射 missing→equivalent；`_parse_factor` 一元负号）
- Test: `tests/factors/test_dsl.py`、`tests/factors/test_eval_engine.py`、`tests/factors/test_qlib_translate.py`

**Interfaces:**
- Produces: 算子 `gt/lt/ge/le/eq/ne(a,b)`（1.0/0.0 浮点帧，NaN 传播）、`if_else(cond,a,b)`（cond 非 0 取 a、0 取 b、NaN → NaN）；翻译器支持 `-(expr)` → `sub(0,expr)`。

- [ ] **Step 3.1 失败测试（DSL 解析 + SPEC_OPS）**（`test_dsl.py`）：

```python
def test_comparison_and_conditional_parse():
    for expr in (
        "gt(close,10)", "if_else(gt(close,10),volume,0)",
        "if_else(eq(sub(high,low),0),0,div(sub(close,low),sub(high,low)))",
    ):
        assert serialize(parse(expr)) == expr.replace(" ", "")
    with pytest.raises(DSLParseError):
        parse("gt(close)")          # 元数
    with pytest.raises(DSLParseError):
        parse("if_else(close,high,low,open)")  # 元数 4 > 3
```
（`SPEC_OPS` 独立口径表补 7 名 + docstring 算子行同步。）

- [ ] **Step 3.2 失败测试（求值）**（`test_eval_engine.py`）：

```python
class TestComparisonConditional:
    def _panel(self, close, volume):
        return MarketPanel(fields={"close": _frame({"A": close}), "volume": _frame({"A": volume})})

    def test_comparison_nan_propagation(self):
        panel = self._panel([10.0, 11.0, _NAN, 9.0], [100.0, 200.0, 300.0, 400.0])
        gt = evaluate(parse("gt(close,10)"), panel)
        assert list(gt["A"].round(6)) == [0.0, 1.0, np.nan, 0.0]

    def test_if_else_branches_and_nan_cond(self):
        panel = self._panel([10.0, 11.0, _NAN, 9.0], [100.0, 200.0, 300.0, 400.0])
        out = evaluate(parse("if_else(gt(close,10),volume,0)"), panel)
        assert list(out["A"].round(6)) == [0.0, 200.0, np.nan, 0.0]

    def test_zero_denominator_guard(self):
        panel = self._panel([10.0, 10.0, 11.0], [1.0, 1.0, 1.0])
        panel.fields["high"] = _frame({"A": [10.0, 12.0, 13.0]})
        panel.fields["low"] = _frame({"A": [10.0, 10.0, 9.0]})
        out = evaluate(
            parse("if_else(eq(sub(high,low),0),0,div(sub(close,low),sub(high,low)))"), panel
        )
        assert list(out["A"].round(6)) == [0.0, 0.0, 0.5]
```

- [ ] **Step 3.3 跑红** → `DSLParseError: 未知算子 'gt'`。
- [ ] **Step 3.4 实现**：`dsl.py` OPS/ARITY 扩表（无 window 参数）；`eval_engine.py`：

```python
def _compare(op: Callable[[Any, Any], Any]) -> Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame]:
    def _cmp(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
        left_aligned, right_aligned = left.align(right)
        raw = op(left_aligned, right_aligned)  # NaN 比较 → False（numpy 语义）
        valid = left_aligned.notna() & right_aligned.notna()
        return raw.astype("float64").where(valid)
    return _cmp


def _if_else(cond: pd.DataFrame, left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    cond_a, left_a = cond.align(left)
    cond_a, right_a = cond_a.align(right)
    mask = cond_a.fillna(0.0).to_numpy("float64") != 0.0
    picked = pd.DataFrame(
        np.where(mask, left_a.to_numpy("float64"), right_a.to_numpy("float64")),
        index=cond_a.index,
        columns=cond_a.columns,
    )
    return picked.where(cond_a.notna())
```

`_ELEMENTWISE_OPS` 增 `"gt": _compare(operator.gt), "lt": _compare(operator.lt), "ge": _compare(operator.ge), "le": _compare(operator.le), "eq": _compare(operator.eq), "ne": _compare(operator.ne), "if_else": _if_else`。

- [ ] **Step 3.5 失败测试（翻译器映射 + 一元负号）**（`test_qlib_translate.py`）：

```python
class TestComparisonConditionalMapping:
    def test_qlib_comparison_operators(self):
        assert translate_qlib_expression("Greater($close, 0)").dsl_expression == "gt(close,0)"
        assert translate_qlib_expression("If(Gt($close, 0), $volume, 0)").dsl_expression == \
            "if_else(gt(close,0),volume,0)"

    def test_unary_minus_on_expression(self):
        assert translate_qlib_expression("-($close - 1)").dsl_expression == "sub(0,sub(close,1))"
        assert translate_qlib_expression("-$close").dsl_expression == "sub(0,close)"

    def test_double_negation_number(self):
        assert translate_qlib_expression("--3").dsl_expression == "3"

    def test_negative_literal_regression(self):
        # 既有契约场景逐字回归：-1 * x → sub(0,x)
        assert translate_qlib_expression("-1 * $close").dsl_expression == "sub(0,close)"
```

- [ ] **Step 3.6 跑红 → 实现**：`qlib_translate.py` 的 `OPERATOR_MAPPINGS` 将 `Greater/Less/Gt/Ge/Lt/Le/Eq/Ne/If` 九条 missing 改 equivalent（比较族备注统一声明 NaN 边界：「NaN 比较：pyqlib False vs 本系统 NaN（边界口径声明）」；If 备注同法）；`_parse_factor` 改为：

```python
def _parse_factor(self) -> _Node:
    token = self._peek()
    if token.kind == _OP and token.text == "-":
        self._advance()
        operand = self._parse_factor()
        if isinstance(operand, _Num):
            return _NegNumber(-operand.value, token.pos)
        if isinstance(operand, _NegNumber):
            return _Num(abs(operand.value), operand.pos)  # --k ≡ k
        # 表达式取反 → 严格等价 0 − x（extend-factor-library-coverage）
        return _BinOp("-", _Num(0, token.pos), operand, token.pos)
    return self._parse_atom()
```

- [ ] **Step 3.7 既有映射回归全绿**：`uv run pytest tests/factors/test_qlib_translate.py tests/factors/test_dsl.py tests/factors/test_eval_engine.py -q`。
- [ ] **Step 3.8 提交**：`git commit -m "feat(factors): 比较与条件算子 + 翻译器负号表达式改写（extend-factor-library-coverage T3）"`

### Task 4: 时序统计与计数算子 + 映射 + 性能预算（delta 4.1–4.4）

**Files:**
- Modify: `src/finance_agent/factors/dsl.py`（OPS/ARITY/WINDOW_ARG_INDEX 扩 8 算子 + ts_quantile q 校验）
- Modify: `src/finance_agent/factors/eval_engine.py`（7 个新求值函数 + `_evaluate_call` 分派）
- Modify: `src/finance_agent/factors/qlib_translate.py`（Med/Quantile/Count/Skew/Kurt/WMA 六条映射）
- Test: `tests/factors/test_dsl.py`、`tests/factors/test_eval_engine.py`

**Interfaces:**
- Produces: `ts_median/ts_skew/ts_kurt/ts_decay_linear/ts_arg_max/ts_arg_min(x,N)`、`ts_quantile(x,N,q)`（q∈[0,1] 数字字面量，解析期校验）、`ts_count(x,N)`（唯一容忍窗口内 NaN 的时序算子）。

- [ ] **Step 4.1 失败测试（解析）**（`test_dsl.py`）：

```python
def test_ts_quantile_q_validation():
    assert serialize(parse("ts_quantile(close,3,0.5)")) == "ts_quantile(close,3,0.5)"
    with pytest.raises(DSLParseError):  # q 越界
        parse("ts_quantile(close,3,1.5)")
    with pytest.raises(DSLParseError):  # q 非字面量
        parse("ts_quantile(close,3,x)")
    with pytest.raises(DSLParseError):  # window 非法
        parse("ts_quantile(close,0,0.5)")
```

- [ ] **Step 4.2 失败测试（求值，期望值独立手写）**（`test_eval_engine.py`）：

```python
class TestTimeSeriesStatsOps:
    def _panel_close(self, values):
        return MarketPanel(fields={"close": _frame({"A": values})})

    def test_ts_quantile_and_median(self):
        panel = self._panel_close([10.0, 11.0, 9.0, 12.0])
        med = evaluate(parse("ts_median(close,3)"), panel)["A"]
        assert list(med.round(6)) == [np.nan, np.nan, 10.0, 11.0]
        q25 = evaluate(parse("ts_quantile(close,3,0.25)"), panel)["A"]
        assert list(q25.round(6)) == [np.nan, np.nan, 9.5, 10.0]

    def test_ts_count_tolerates_window_nans(self):
        panel = self._panel_close([10.0, _NAN, 9.0, 12.0, _NAN])
        out = evaluate(parse("ts_count(close,3)"), panel)["A"]
        assert list(out.round(6)) == [np.nan, np.nan, 2.0, 2.0, 2.0]

    def test_ts_skew_kurt_anchor_pandas(self):
        values = [10.0, 11.0, 12.0, 16.0]
        panel = self._panel_close(values)
        ref = pd.Series(values).rolling(4, min_periods=4)
        skew = evaluate(parse("ts_skew(close,4)"), panel)["A"]
        kurt = evaluate(parse("ts_kurt(close,4)"), panel)["A"]
        assert skew.iloc[3] == pytest.approx(ref.skew().iloc[3])
        assert kurt.iloc[3] == pytest.approx(ref.kurt().iloc[3])
        assert np.isnan(skew.iloc[2]) and np.isnan(kurt.iloc[2])

    def test_ts_decay_linear(self):
        panel = self._panel_close([10.0, 11.0, 12.0])
        out = evaluate(parse("ts_decay_linear(close,3)"), panel)["A"]
        assert out.iloc[2] == pytest.approx(68.0 / 6.0)
        assert np.isnan(out.iloc[1])

    def test_ts_arg_extremes(self):
        panel = MarketPanel(fields={"close": _frame({
            "A": [10.0, 11.0, 9.0, 9.0], "B": [11.0, 11.0, 9.0, 9.0],
        })})
        amax = evaluate(parse("ts_arg_max(close,3)"), panel)
        amin = evaluate(parse("ts_arg_min(close,3)"), panel)
        assert list(amax["A"].round(6)) == [np.nan, np.nan, 1.0, 2.0]
        assert list(amin["A"].round(6)) == [np.nan, np.nan, 0.0, 0.0]
        assert amax["B"].iloc[2] == 0.0  # 并列取最近一期

    def test_window_nan_propagation(self):
        panel = self._panel_close([10.0, 11.0, _NAN, 12.0])
        out = evaluate(parse("ts_median(close,3)"), panel)["A"]
        assert out.iloc[2] is np.nan or np.isnan(out.iloc[2])  # 窗口含 NaN → NaN
```

- [ ] **Step 4.3 跑红 → 实现**（`eval_engine.py`）：

```python
def _ts_quantile(frame: pd.DataFrame, window: int, q: float) -> pd.DataFrame:
    return frame.rolling(window, min_periods=window).quantile(q)


def _ts_count(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    counts = frame.notna().astype("float64").rolling(window, min_periods=window).sum()
    return counts.where(counts > 0)


def _ts_decay_linear(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    total: pd.DataFrame | None = None
    for k in range(1, window + 1):
        weighted = frame.shift(window - k) * k
        total = weighted if total is None else total + weighted
    assert total is not None  # window ≥ 1（解析期保证）
    return total / (window * (window + 1) / 2.0)


def _ts_arg_extreme(frame: pd.DataFrame, window: int, *, largest: bool) -> pd.DataFrame:
    values = frame.to_numpy(dtype="float64")
    n_rows = values.shape[0]
    rolling = frame.rolling(window, min_periods=window)
    extreme = (rolling.max() if largest else rolling.min()).to_numpy(dtype="float64")
    result = np.full(values.shape, np.nan)
    if n_rows >= window:
        any_nan = np.zeros(values.shape, dtype=bool)
        for offset in range(window):  # 窗口完整性：任一格 NaN → 该行 NaN（统一口径）
            any_nan[offset:, :] |= np.isnan(values[: n_rows - offset, :])
        for offset in range(window):  # offset 升序：首中即最近一期（并列口径）
            current = values[offset:, :]
            is_hit = (
                np.isnan(result[offset:, :])
                & (current == extreme[offset:, :])
                & ~any_nan[offset:, :]
            )
            result[offset:, :] = np.where(is_hit, float(offset), result[offset:, :])
    return _wrap(result, frame)
```

`_WINDOW_OPS` 增 `ts_median/ts_skew/ts_kurt/ts_count/ts_decay_linear/ts_arg_max/ts_arg_min`（arg 两个用 lambda 绑 largest）；`ts_quantile` 与 `ts_resid`（Task 5）在 `_evaluate_call` 走 `ts_corr` 同款双参分支；`_window_of` 后对 `ts_quantile` 取 `args[2]`（`Number` 且 0≤q≤1，否则 `ValueError`——解析期已拦，防御性兜底）。`dsl.py` 的 `_parse_call` 增加 `ts_quantile` 的 q 字面量校验（`DSLParseError`，位置 `arg_positions[2]`）。

- [ ] **Step 4.4 翻译器映射**：`Med→ts_median`、`Quantile→ts_quantile`（备注 q 直传、线性插值两侧一致）、`Count→ts_count`（备注热身期）、`Skew→ts_skew`、`Kurt→ts_kurt`（超额峰度两侧一致）、`WMA→ts_decay_linear`（备注 pyqlib 权重 N→1 递减、最近一期最重，两侧一致）；既有映射回归全绿。
- [ ] **Step 4.5 性能预算测试**（`test_eval_engine.py` 追加）：

```python
def test_arg_extreme_perf_budget():
    """预算护栏：500×5500 面板上 ts_arg_max ≤ 4× ts_rank（同为窗口扫描族）。"""
    rng = np.random.default_rng(42)
    frame = pd.DataFrame(rng.standard_normal((500, 5500)).cumsum(axis=0))
    panel = MarketPanel(fields={"close": frame})
    t0 = time.perf_counter()
    evaluate(parse("ts_rank(close,20)"), panel)
    rank_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    evaluate(parse("ts_arg_max(close,20)"), panel)
    arg_s = time.perf_counter() - t1
    assert arg_s < max(rank_s * 4.0, 1.0)
```

- [ ] **Step 4.6 跑绿 + 提交**：`git commit -m "feat(factors): 高阶时序统计算子族（extend-factor-library-coverage T4）"`

### Task 5: 回归与横截面算子（delta 5.1–5.3）

**Files:**
- Modify: `src/finance_agent/factors/dsl.py`（OPS/ARITY/WINDOW_ARG_INDEX 扩 4 算子）
- Modify: `src/finance_agent/factors/eval_engine.py`（`_ts_resid`/`cross_section_mean`/`_ind_neutralize`/`_exp`；`MarketPanel.groups` 字段）
- Test: `tests/factors/test_dsl.py`、`tests/factors/test_eval_engine.py`

**Interfaces:**
- Produces: `MarketPanel.groups: dict[str, pd.DataFrame] | None = None`（新尾字段，默认 None，向后兼容）；`ts_resid(y,x,N)`、`cs_mean(x)`、`ind_neutralize(x)`（面板缺 industry 分组 → ValueError）、`exp`。

- [ ] **Step 5.1 失败测试（求值，期望值独立手写）**（`test_eval_engine.py`）：

```python
class TestRegressionCrossSectionOps:
    def test_ts_resid_closed_form(self):
        panel = MarketPanel(fields={
            "close": _frame({"A": [3.0, 5.0, 8.0, 9.0]}),
            "volume": _frame({"A": [1.0, 2.0, 3.0, 4.0]}),
        })
        out = evaluate(parse("ts_resid(close,volume,3)"), panel)["A"]
        assert out.iloc[2] == pytest.approx(1.0 / 6.0, abs=1e-9)
        assert out.iloc[3] == pytest.approx(-1.0 / 3.0, abs=1e-9)
        assert np.isnan(out.iloc[1])

    def test_ts_resid_constant_x_is_nan(self):
        panel = MarketPanel(fields={
            "close": _frame({"A": [3.0, 5.0, 8.0, 9.0]}),
            "volume": _frame({"A": [1.0, 1.0, 1.0, 1.0]}),
        })
        out = evaluate(parse("ts_resid(close,volume,3)"), panel)["A"]
        assert np.isnan(out.iloc[2]) and np.isnan(out.iloc[3])

    def test_cs_mean_broadcast_and_min_samples(self):
        frame = _frame({f"C{i:03d}": [float(i + 1)] * 2 for i in range(30)})
        extra = _frame({"CX": [np.nan, np.nan]})
        panel = MarketPanel(fields={"close": pd.concat([frame, extra], axis=1)})
        out = evaluate(parse("cs_mean(close)"), panel)
        assert out.iloc[0].nunique() == 1  # 全市场均值广播到含 NaN 的列
        panel29 = MarketPanel(fields={"close": frame.iloc[:, :29]})
        assert evaluate(parse("cs_mean(close)"), panel29).iloc[0].isna().all()

    def test_ind_neutralize(self):
        labels = _frame({
            "A": ["G1"] * 2, "B": ["G1"] * 2, "C": ["G1"] * 2,
            "D": ["G1"] * 2, "E": ["G1"] * 2, "F": ["G2"] * 2, "G": [np.nan] * 2,
        })
        panel = MarketPanel(
            fields={"close": _frame({
                "A": [1.0, 1.0], "B": [2.0, 2.0], "C": [3.0, 3.0],
                "D": [4.0, 4.0], "E": [5.0, 5.0], "F": [9.0, 9.0], "G": [7.0, 7.0],
            })},
            groups={"industry": labels},
        )
        out = evaluate(parse("ind_neutralize(close)"), panel)
        assert list(out.iloc[0, :5].round(6)) == [-2.0, -1.0, 0.0, 1.0, 2.0]
        assert np.isnan(out["F"].iloc[0]) and np.isnan(out["G"].iloc[0])  # 组样本 <5 / 无标签

    def test_ind_neutralize_requires_group(self):
        panel = MarketPanel(fields={"close": _frame({"A": [1.0, 2.0]})})
        with pytest.raises(ValueError, match="industry"):
            evaluate(parse("ind_neutralize(close)"), panel)

    def test_exp_overflow_and_nan(self):
        panel = MarketPanel(fields={"close": _frame({"A": [10.0, -1.0, 1e308]})})
        out = evaluate(parse("exp(log(close))"), panel)["A"]
        assert out.iloc[0] == pytest.approx(10.0)
        assert np.isnan(out.iloc[1]) and np.isnan(out.iloc[2])  # log 非正 NaN / exp 溢出 NaN
```

- [ ] **Step 5.2 跑红 → 实现**（`eval_engine.py`）：

```python
MIN_GROUP_SAMPLES: int = 5  # ind_neutralize 组内有效样本下限（spec 逐字）


def _ts_resid(y: pd.DataFrame, x: pd.DataFrame, window: int) -> pd.DataFrame:
    y_a, x_a = y.align(x)
    mean_y = y_a.rolling(window, min_periods=window).mean()
    mean_x = x_a.rolling(window, min_periods=window).mean()
    var_x = x_a.rolling(window, min_periods=window).var(ddof=0)
    mean_yx = (y_a * x_a).rolling(window, min_periods=window).mean()
    beta = (mean_yx - mean_y * mean_x) / var_x.where(var_x != 0)
    alpha = mean_y - beta * mean_x
    return y_a - (alpha + beta * x_a)


def cross_section_mean(frame: pd.DataFrame, *, min_samples: int = MIN_IC_SAMPLES) -> pd.DataFrame:
    counts = frame.notna().sum(axis=1)
    broadcast = pd.DataFrame(
        np.tile(frame.mean(axis=1).to_numpy()[:, None], (1, frame.shape[1])),
        index=frame.index,
        columns=frame.columns,
    )
    return broadcast.where(counts >= min_samples)


def _ind_neutralize(values: pd.DataFrame, panel: MarketPanel) -> pd.DataFrame:
    labels = (panel.groups or {}).get("industry")
    if labels is None:
        raise ValueError("面板缺少 industry 分组（ind_neutralize 需要行业标签帧）")
    label_by_col: dict[str, str] = {}
    for col in values.columns:
        observed = labels[col].dropna().unique() if col in labels.columns else []
        if len(observed) == 0:
            continue
        if len(observed) > 1:
            raise ValueError(f"列 {col!r} 的 industry 标签随日期变化（须逐列恒定）")
        label_by_col[col] = str(observed[0])
    by_label: dict[str, list[str]] = {}
    for col, lab in label_by_col.items():
        by_label.setdefault(lab, []).append(col)
    out = pd.DataFrame(np.nan, index=values.index, columns=values.columns)
    for cols in by_label.values():
        sub = values[cols]
        counts = sub.notna().sum(axis=1)
        out[cols] = sub.sub(sub.mean(axis=1), axis=0).where(counts >= MIN_GROUP_SAMPLES)
    return out


def _exp(frame: pd.DataFrame) -> pd.DataFrame:
    with np.errstate(over="ignore", invalid="ignore"):
        vals = np.exp(frame.to_numpy(dtype="float64"))
    return _wrap(np.where(np.isfinite(vals), vals, np.nan), frame)
```

`_ELEMENTWISE_OPS` 增 `"exp": _exp`；`_evaluate_call` 增两个特判分支（`cs_mean` / `ind_neutralize`，同 ts_corr 先例）；`MarketPanel` 增尾字段 `groups: dict[str, pd.DataFrame] | None = None`（docstring 注明：键 ∈ 参考分组，值与 fields 同形；None = 未携带，ind_neutralize 报错）。`dsl.py` 扩 `ts_resid`（元数 3、window 位 2）/`cs_mean`/`ind_neutralize`/`exp`。
- [ ] **Step 5.3 文档同步**：`dsl.py` / `eval_engine.py` 模块 docstring 的算子清单与口径段（ts_count 例外、比较 NaN、ts_quantile q、cs_mean 广播与 MIN_IC_SAMPLES、ind_neutralize 组门槛 5、标签逐列恒定）。
- [ ] **Step 5.4 全量门禁**：`uv run pytest tests/factors -q` 全绿 → `uv run ruff check && uv run ruff format --check` → `uv run mypy`；随后勾 delta tasks.md §1–§5 并提交 `git commit -m "feat(factors): 回归与横截面算子族（extend-factor-library-coverage T5）"`。

## Self-Review

- **Spec coverage**：delta §1（Task 1）§2（Task 2）§3（Task 3）§4（Task 4）§5（Task 5）逐条有任务；§6–§13 属 P2/P3，不在本计划。
- **占位符**：无 TBD/TODO；所有代码块完整。
- **类型一致性**：`count_free_params` 签名不变；`_WINDOW_OPS` 值类型 `(DataFrame, int) -> DataFrame`，arg 族用 lambda 绑定；`MarketPanel.groups` 尾字段默认 None；`cross_section_mean` 公开命名（闸④组合构建可复用）与既有 `cross_section_zscore` 对齐。

## P2/P3 计划入口（不在本文件）

- P2（delta §6–§11）：参考数据面侧表 / 读路径 / 面板可选字段 / builtin 七条 / 选股披露——独立计划 `2026-12-XX-extend-factor-library-coverage-p2.md`（实施前撰写）。
- P3（delta §12–§13）：提案枚举与模板 + deploy_prompts + 存量重放——独立计划。
