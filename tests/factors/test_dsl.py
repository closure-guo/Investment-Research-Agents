"""factors/dsl.py 单测（add-factor-dsl Task 1–4）。

计划 docs/superpowers/plans/2026-10-13-add-factor-dsl.md 关键设计决策 D1–D7：
- D1 语法：``expr := call | field | number``；无中缀运算符；空白自由；不允许尾逗号；
  括号不配对 / 空参数 / 多余字符 → ``DSLParseError``（消息必须含字符位置）；
- D2 元数表：window 位置必须是正整数数字字面量（``10.5`` / ``0`` / 非常量 → 报错）；
- D3 深度口径：叶子（Field/Number）深度 1；Call = 1 + max(子节点深度)；
- D4 自由参数 = Number 节点总数（含 window）；字段种类 = 去重后的 Field 名数量；
- D5 规范化：结合律展平（add/mul/max/min）+ 交换律排序 + 幂等；serialize 紧凑形态；
- D6 同构：同 op/同元数/字段名位置对应、数字只按「有」匹配（取值忽略），规范化后比较；
- D7 ``parse(enforce_complexity=False)`` 仅供工具/验证（Alpha101 round-trip 旁路），
  提案/生产默认路径必须开启复杂度检查。

期望值逐条独立手写（不复用被测模块生成期望，除枚举完整性外）。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from finance_agent.factors.dsl import (
    ARITY,
    FIELDS,
    MAX_AST_DEPTH,
    MAX_FIELDS,
    MAX_FREE_PARAMS,
    OPS,
    Call,
    DSLParseError,
    FactorComplexityError,
    Field,
    Number,
    ast_depth,
    count_fields,
    count_free_params,
    is_subtree_isomorphic,
    normalize,
    parse,
    serialize,
)

# ── 独立手写口径表（spec 逐字；与被测模块常量对照用） ───────────────────────

SPEC_OPS = {
    "ts_mean",
    "ts_std",
    "ts_corr",
    "ts_rank",
    "delta",
    "rank",
    "zscore",
    "add",
    "sub",
    "mul",
    "div",
    "max",
    "min",
    "abs",
    "log",
    "ref",
    "ts_sum",
    "sqrt",
}
SPEC_FIELDS = {"open", "high", "low", "close", "volume", "amount"}
SPEC_ARITY = {
    "ts_mean": 2,
    "ts_std": 2,
    "ts_corr": 3,
    "ts_rank": 2,
    "delta": 2,
    "rank": 1,
    "zscore": 1,
    "add": 2,
    "sub": 2,
    "mul": 2,
    "div": 2,
    "max": 2,
    "min": 2,
    "abs": 1,
    "log": 1,
    "ref": 2,
    "ts_sum": 2,
    "sqrt": 1,
}
# 每个算子一份合法样例（window 位置一律正整数数字字面量）
_WINDOW_OPS = {"ts_mean", "ts_std", "ts_rank", "delta", "ts_corr", "ref", "ts_sum"}
_OP_SAMPLES = {
    "ts_mean": "ts_mean(close, 5)",
    "ts_std": "ts_std(close, 5)",
    "ts_corr": "ts_corr(close, volume, 5)",
    "ts_rank": "ts_rank(close, 5)",
    "delta": "delta(close, 5)",
    "rank": "rank(close)",
    "zscore": "zscore(close)",
    "add": "add(close, volume)",
    "sub": "sub(close, volume)",
    "mul": "mul(close, volume)",
    "div": "div(close, volume)",
    "max": "max(close, volume)",
    "min": "min(close, volume)",
    "abs": "abs(close)",
    "log": "log(close)",
    "ref": "ref(close, 5)",
    "ts_sum": "ts_sum(volume, 5)",
    "sqrt": "sqrt(close)",
}


def _assert_parse_error(expr: str, *, contains: Sequence[str] = (), pos: int | None = None) -> str:
    """断言 parse(expr) 抛 DSLParseError；可选核对消息片段与字符位置。"""
    with pytest.raises(DSLParseError) as excinfo:
        parse(expr)
    message = str(excinfo.value)
    for fragment in contains:
        assert fragment in message, f"{expr!r} 的错误消息缺少 {fragment!r}：{message}"
    if pos is not None:
        assert excinfo.value.pos == pos, f"{expr!r} 位置应为 {pos}：{message}"
        assert f"位置 {pos}" in message, f"{expr!r} 的消息未含位置 {pos}：{message}"
    return message


# ── Task 1: 枚举逐字 ─────────────────────────────────────────────────────────


def test_ops_enumeration_verbatim_18_names():
    """算子枚举逐字（spec 列表：初版 15 + ref + ts_sum + sqrt）；元数表与 D2 逐条一致。"""
    assert OPS == SPEC_OPS
    assert len(OPS) == 18
    assert ARITY == SPEC_ARITY
    assert FIELDS == SPEC_FIELDS
    assert len(FIELDS) == 6


# ── Task 1: 全算子解析 + 序列化 round-trip ───────────────────────────────────


@pytest.mark.parametrize("op", sorted(SPEC_OPS))
def test_every_op_parses_with_expected_arity(op: str):
    """15 个算子逐一解析：根 op 正确、元数与 D2 一致、serialize 后可再解析且 AST 相等。"""
    node = parse(_OP_SAMPLES[op])
    assert isinstance(node, Call)
    assert node.op == op
    assert len(node.args) == SPEC_ARITY[op]
    if op in _WINDOW_OPS:
        assert isinstance(node.args[-1], Number)  # window 位置
    assert parse(serialize(node)) == node


def test_spec_scenario_rank_ts_corr_structure():
    """spec 场景：``rank(ts_corr(close, volume, 10))`` 根=rank、子树=ts_corr 三参。"""
    node = parse("rank(ts_corr(close, volume, 10))")
    assert isinstance(node, Call)
    assert node.op == "rank"
    assert len(node.args) == 1
    assert node.args[0] == Call(
        op="ts_corr",
        args=(Field("close"), Field("volume"), Number(10)),
    )
    assert serialize(node) == "rank(ts_corr(close,volume,10))"


def test_spec_scenario_round_trip_ast_equality():
    """spec 场景：serialize → 再 parse → AST 与原 AST 相等。"""
    original = parse("rank(ts_corr(close, volume, 10))")
    again = parse(serialize(original))
    assert again == original
    assert serialize(again) == serialize(original)


def test_spec_scenario_ref_parse_and_window_form():
    """spec 场景（extend-factor-dsl-ref）：ref 解析 + 窗口形态拒收（0 / 负 / 非常量）。

    负窗口在词法期即拒收（无负数字面量），零窗口/小数窗口在窗口形态检查拒收——
    三者都不得产出 AST（杜绝前瞻/恒等引用形态进入因子空间）。
    """
    node = parse("ref(close, 5)")
    assert node == Call(op="ref", args=(Field("close"), Number(5)))
    assert serialize(node) == "ref(close,5)"
    for bad in ("ref(close, 0)", "ref(close, -5)", "ref(close, x)"):
        with pytest.raises(DSLParseError):
            parse(bad)


def test_ref_nested_in_momentum_expression():
    """ref 复合形态（动量族典型式）：`sub(div(close,ref(close,20)),1)` 解析 + round-trip。"""
    expr = "sub(div(close,ref(close,20)),1)"
    node = parse(expr)
    assert node == Call(
        op="sub",
        args=(
            Call(
                op="div", args=(Field("close"), Call(op="ref", args=(Field("close"), Number(20))))
            ),
            Number(1),
        ),
    )
    assert serialize(node) == expr
    assert parse(serialize(node)) == node


def test_spec_scenario_ts_sum_sqrt_parse():
    """spec 场景（add-factor-dsl-ts-sum-sqrt）：`sqrt(ts_sum(volume, 5))` 解析 + round-trip。"""
    node = parse("sqrt(ts_sum(volume, 5))")
    assert node == Call(
        op="sqrt",
        args=(Call(op="ts_sum", args=(Field("volume"), Number(5))),),
    )
    assert serialize(node) == "sqrt(ts_sum(volume,5))"
    assert parse(serialize(node)) == node


def test_ts_sum_vwap_deviation_composite():
    """VWAP 偏离复合式（本 delta 的立项形态）：比值结构含双 ts_sum，round-trip 相等。"""
    expr = "sub(div(close,div(ts_sum(mul(volume,close),5),ts_sum(volume,5))),1)"
    node = parse(expr)
    assert serialize(node) == expr
    assert parse(serialize(node)) == node


@pytest.mark.parametrize("name", sorted(SPEC_FIELDS))
def test_field_leaf_verbatim(name: str):
    """6 行情字段名逐一作为叶子解析（前复权口径见模块 docstring）。"""
    node = parse(name)
    assert node == Field(name)
    assert serialize(node) == name


def test_number_literals_int_and_decimal():
    """数字字面量：整数 → int；小数 → float；0 合法（window 位置另有正整数约束）。"""
    assert parse("10") == Number(10)
    assert isinstance(parse("10").value, int)
    assert parse("1.5") == Number(1.5)
    assert isinstance(parse("1.5").value, float)
    assert parse("0") == Number(0)
    assert serialize(parse("1.50")) == "1.5"
    assert serialize(parse("2.0")) == "2"


def test_whitespace_free():
    """空白自由：token 间任意空白/换行不影响解析，serialize 输出紧凑形态。"""
    node = parse("  rank ( ts_corr( close ,\n  volume, 10 ) ) ")
    assert node == parse("rank(ts_corr(close,volume,10))")
    assert serialize(node) == "rank(ts_corr(close,volume,10))"
    assert serialize(parse(" add( close , 1.50 ) ")) == "add(close,1.5)"


def test_nested_round_trip_all_ops_shape():
    """多算子嵌套（深度 4、2 字段、3 参数，保留在复杂度上限内）：round-trip AST 相等。"""
    expr = "add(ts_std(volume, 20), mul(delta(high, 1), 1.5))"
    node = parse(expr)
    assert parse(serialize(node)) == node
    assert serialize(node) == "add(ts_std(volume,20),mul(delta(high,1),1.5))"


# ── Task 1: 非法输入（未知算子/字段/括号/空参/尾逗号/元数/window/中缀） ──────


def test_unknown_op_rejected_with_name_and_position():
    """未知算子 ``future_peek`` → 消息含算子名与字符位置 0。"""
    message = _assert_parse_error(
        "future_peek(close, 10)", contains=["future_peek", "未知算子"], pos=0
    )
    assert "位置 0" in message


def test_unknown_field_rejected_with_name_and_position():
    """未知字段（叶子形态）→ 消息含标识符与位置；嵌套内同规则。"""
    _assert_parse_error("peek", contains=["peek"], pos=0)
    _assert_parse_error("rank(peek)", contains=["peek"], pos=5)


def test_unknown_identifier_in_window_position():
    """``ts_mean(close, x)``：x 既非字段也非调用 → 报错（位置 = 标识符起点）。"""
    _assert_parse_error("ts_mean(close, x)", contains=["x"], pos=15)


@pytest.mark.parametrize(
    ("expr", "pos"),
    [
        ("rank(close", 4),  # 未闭合：指向 '(' 位置
        ("rank(close))", 11),  # 多余右括号
        ("rank((close)", 5),  # 意外 '('
        ("rank(close volume)", 11),  # 参数间缺逗号
    ],
)
def test_paren_and_token_structure_errors(expr: str, pos: int):
    """括号不配对 / 多余记号 / 缺逗号 → DSLParseError 且含字符位置。"""
    _assert_parse_error(expr, pos=pos)


def test_empty_args_rejected():
    """空参数列表 ``rank()`` → 报错，位置 = ')'。"""
    _assert_parse_error("rank()", contains=["参数"], pos=5)


def test_trailing_comma_rejected():
    """尾逗号 ``add(close,)`` → 报错，位置 = ')'。"""
    _assert_parse_error("add(close,)", contains=["尾逗号"], pos=10)


@pytest.mark.parametrize(
    ("expr", "expected_arity", "actual"),
    [
        ("ts_mean(close)", "2", "1"),
        ("ts_corr(close, volume)", "3", "2"),
        ("rank(close, volume)", "1", "2"),
        ("delta(close)", "2", "1"),
    ],
)
def test_arity_errors(expr: str, expected_arity: str, actual: str):
    """元数错误 → 消息含算子名、期望/实际参数个数与位置。"""
    message = _assert_parse_error(expr, contains=["需要", expected_arity, actual], pos=0)
    assert "参数" in message


@pytest.mark.parametrize(
    ("expr", "pos"),
    [
        ("ts_mean(close, 10.5)", 15),  # 非整数 window
        ("ts_mean(close, 0)", 15),  # 非正 window
        ("ts_std(volume, 1.0)", 15),  # 字面量是小数形态（1.0）→ 拒收（严格整数形态）
        ("ts_corr(close, volume, 10.5)", 23),  # ts_corr 的 window 在 3 号位
        ("ref(close, 0)", 11),  # ref 非正 window
        ("ref(close, 10.5)", 11),  # ref 非整数 window
        ("ts_sum(volume, 0)", 15),  # ts_sum 非正 window
        ("ts_sum(volume, 10.5)", 15),  # ts_sum 非整数 window
    ],
)
def test_window_must_be_positive_integer_literal(expr: str, pos: int):
    """window 位置必须是正整数数字字面量 → 否则报错，位置 = 该参数起点。"""
    _assert_parse_error(expr, contains=["window"], pos=pos)


def test_infix_operators_rejected():
    """v1 无中缀运算符：``close + volume`` → 词法期报错并带位置。"""
    _assert_parse_error("close + volume", contains=["+"], pos=6)
    _assert_parse_error("close-volume", pos=5)


def test_empty_expression_rejected():
    """空串 / 纯空白 → 报错（位置 0）。"""
    _assert_parse_error("", pos=0)
    _assert_parse_error("   ", pos=0)


def test_parse_error_is_value_error():
    """错误类型契约：DSLParseError 是 ValueError 子类（spec 拒绝语义）。"""
    assert issubclass(DSLParseError, ValueError)
    with pytest.raises(ValueError):
        parse("future_peek(close, 10)")


# ── Task 2: 复杂度硬约束（深度/自由参数/字段种类） ───────────────────────────


def test_complexity_caps_verbatim():
    """三条上限逐字：深度 6 / 自由参数 3 / 字段种类 3；错误类型为 ValueError 子类。"""
    assert MAX_AST_DEPTH == 6
    assert MAX_FREE_PARAMS == 3
    assert MAX_FIELDS == 3
    assert issubclass(FactorComplexityError, ValueError)


def test_parse_enforce_complexity_is_keyword_only():
    """D7 签名：``enforce_complexity`` 为仅关键字参数（parse(expr, *, ...)）。"""
    with pytest.raises(TypeError):
        parse("close", False)  # type: ignore[call-arg]


def test_depth_boundary_6_passes_7_rejected():
    """spec 场景：深度恰 6 通过；深度 7 → 拒收且消息含「深度 7 > 上限 6」逐字要素。"""
    ok = parse("log(abs(rank(zscore(ts_mean(close, 10)))))")
    assert ast_depth(ok) == 6
    with pytest.raises(FactorComplexityError) as excinfo:
        parse("sqrt(log(abs(rank(zscore(ts_mean(close, 10))))))")
    assert "深度 7 > 上限 6" in str(excinfo.value)


def test_depth_6_natural_forms_now_allowed():
    """本轮立项证据：CLV 平滑与 VWAP 偏离（外层统计量套一层比值）深度恰 6 → 通过。"""
    for expr in (
        # CLV 平滑：mean((2C−H−L)/(H−L+eps), 5)
        "ts_mean(div(sub(sub(mul(2,close),high),low),add(sub(high,low),0.000001)),5)",
        # VWAP 偏离：C/(ΣV·C/ΣV) − 1
        "sub(div(close,div(ts_sum(mul(volume,close),5),ts_sum(volume,5))),1)",
    ):
        node = parse(expr)
        assert ast_depth(node) == 6
        assert parse(serialize(node)) == node


@pytest.mark.parametrize(
    ("expr", "depth"),
    [
        ("close", 1),
        ("10", 1),
        ("rank(close)", 2),
        ("ts_mean(close, 10)", 2),
        ("zscore(rank(close))", 3),
        ("rank(zscore(ts_mean(close, 10)))", 4),
    ],
)
def test_ast_depth_leaf_is_one(expr: str, depth: int):
    """D3 口径：叶子（Field/Number）深度 1；Call = 1 + max(子节点深度)。"""
    assert ast_depth(parse(expr)) == depth


def test_free_params_boundary_3_passes_4_rejected():
    """自由参数（Number 总数）3 通过 / 4 拒收，消息含「自由参数 4 > 上限 3」。"""
    ok = parse("add(add(ts_mean(close, 1), ts_mean(close, 2)), ts_mean(close, 3))")
    assert count_free_params(ok) == 3
    assert ast_depth(ok) == 4
    with pytest.raises(FactorComplexityError) as excinfo:
        parse(
            "add(add(ts_mean(close, 1), ts_mean(close, 2)), "
            "add(ts_mean(close, 3), ts_mean(close, 4)))"
        )
    assert "自由参数 4 > 上限 3" in str(excinfo.value)


def test_count_free_params_counts_window_and_plain_literals():
    """D4：window 数字与其他位置数字都计入自由参数（含小数）。"""
    node = parse("add(ts_mean(close, 20), 1.5)")
    assert count_free_params(node) == 2
    assert count_free_params(parse("close")) == 0


def test_field_kinds_boundary_3_passes_4_rejected():
    """字段种类 3 通过 / 4 拒收，消息含「字段种类 4 > 上限 3」。"""
    ok = parse("add(add(close, volume), open)")
    assert count_fields(ok) == 3
    assert ast_depth(ok) == 3
    with pytest.raises(FactorComplexityError) as excinfo:
        parse("add(add(add(close, volume), open), high)")
    assert "字段种类 4 > 上限 3" in str(excinfo.value)


def test_count_fields_deduplicates_repeated_names():
    """D4：同一字段多次引用只计一种；3 次 close 引用不触字段上限。"""
    assert count_fields(parse("add(close, close)")) == 1
    node = parse("add(add(close, close), close)")
    assert count_fields(node) == 1
    assert ast_depth(node) == 3


def test_all_three_violations_listed_in_one_message():
    """三条约束同时违反 → 消息含全部违规项（实现钉住「不短路，全部列出」）。"""
    expr = (
        "sqrt(abs(add(add(add(ts_mean(close, 1), ts_mean(volume, 2)), ts_mean(open, 3)), "
        "add(ts_mean(high, 4), low))))"
    )
    with pytest.raises(FactorComplexityError) as excinfo:
        parse(expr)
    message = str(excinfo.value)
    assert "深度 7 > 上限 6" in message
    assert "自由参数 4 > 上限 3" in message
    assert "字段种类 5 > 上限 3" in message


def test_enforce_complexity_false_bypasses_caps():
    """D7：``enforce_complexity=False`` 旁路三条上限（仅供工具/验证）——默认路径同式拒收。"""
    expr = "sqrt(log(abs(rank(zscore(ts_mean(close, 10))))))"
    with pytest.raises(FactorComplexityError):
        parse(expr)
    node = parse(expr, enforce_complexity=False)
    assert ast_depth(node) == 7
    assert serialize(node) == "sqrt(log(abs(rank(zscore(ts_mean(close,10))))))"


# ── Task 3: 规范化（D5）与子树同构（D6） ─────────────────────────────────────


def test_normalize_flattens_associative_ops():
    """D5①：``add(add(a,b),c)`` → 三参 add（同 op 展平；子节点保持集合相等）。"""
    flat = normalize(parse("add(add(close, volume), open)"))
    assert isinstance(flat, Call)
    assert flat.op == "add"
    assert len(flat.args) == 3
    assert set(flat.args) == {Field("close"), Field("volume"), Field("open")}


@pytest.mark.parametrize("op", ["add", "mul", "max", "min"])
def test_normalize_flattens_all_four_associative_ops(op: str):
    """D5① 全集：add/mul/max/min 四个算子都执行结合律展平。"""
    flat = normalize(parse(f"{op}({op}(close, volume), open)"))
    assert isinstance(flat, Call)
    assert flat.op == op
    assert len(flat.args) == 3


def test_normalize_does_not_flatten_non_associative():
    """D5① 边界：sub 非结合律 → 结构原样保留（不展平、不排序）。"""
    node = normalize(parse("sub(sub(close, volume), open)"))
    assert node == parse("sub(sub(close, volume), open)")


def test_normalize_sorts_commutative_children():
    """D5②：交换律等价因子规范形态相同（按 serialize 字符串排序）。"""
    assert normalize(parse("add(close, volume)")) == normalize(parse("add(volume, close)"))
    assert normalize(parse("add(volume, close)")) == Call("add", (Field("close"), Field("volume")))


def test_normalize_keeps_non_commutative_arg_order_including_window():
    """D5② 边界：非交换算子（含 ts_corr 的 window 位置）保持参数顺序。"""
    node = normalize(parse("ts_corr(volume, close, 10)"))
    assert node == parse("ts_corr(volume, close, 10)")


def test_normalize_uses_canonical_serialize_for_sorting():
    """D5②④：排序键用规范序列化（数字整数无小数点、小数去尾零）。"""
    assert serialize(normalize(parse("add(close, 2.50)"))) == "add(2.5,close)"


def test_normalize_is_idempotent():
    """D5③：normalize(normalize(x)) == normalize(x)（含嵌套展平 + 排序）。"""
    expr = "add(mul(volume, close), add(open, mul(close, volume)))"
    once = normalize(parse(expr))
    twice = normalize(once)
    assert twice == once
    assert serialize(twice) == serialize(once)


def test_normalize_leaf_and_simple_call_unchanged():
    """D5 边界：叶子与已规范形态调用原样返回。"""
    assert normalize(Field("close")) == Field("close")
    assert normalize(Number(10)) == Number(10)
    assert normalize(parse("rank(close)")) == parse("rank(close)")


# ── Task 3: is_subtree_isomorphic（D6） ──────────────────────────────────────


def test_spec_scenario_window_skin_isomorphic():
    """spec 场景：``ts_mean(close,10)`` vs ``ts_mean(close,11)`` → True（仅窗口不同）。"""
    assert is_subtree_isomorphic(parse("ts_mean(close, 10)"), parse("ts_mean(close, 11)")) is True


def test_isomorphic_false_on_different_field():
    """字段名位置对应：close vs volume → False。"""
    assert is_subtree_isomorphic(parse("ts_mean(close, 10)"), parse("ts_mean(volume, 10)")) is False


def test_isomorphic_false_on_different_op_or_arity():
    """同 op、同元数才可能同构：op 不同 / 元数不同 → False。"""
    assert is_subtree_isomorphic(parse("ts_mean(close, 10)"), parse("ts_std(close, 10)")) is False
    assert is_subtree_isomorphic(parse("rank(close)"), parse("ts_mean(close, 10)")) is False


def test_isomorphic_commutative_equivalence():
    """D6 + D5②：交换律等价视为同构；非交换算子（sub）正反例。"""
    assert is_subtree_isomorphic(parse("add(close, volume)"), parse("add(volume, close)")) is True
    assert is_subtree_isomorphic(parse("sub(close, volume)"), parse("sub(volume, close)")) is False


def test_isomorphic_binary_vs_flattened_equivalence():
    """D5①+D6：结合律分组不同的等价式（add 二叉树两种分组）→ True。"""
    a = parse("add(add(close, volume), open)")
    b = parse("add(close, add(volume, open))")
    assert is_subtree_isomorphic(a, b) is True


def test_isomorphic_false_on_depth_difference():
    """深度不同 → False（元数/结构不可能逐位置对应）。"""
    a = parse("add(close, volume)")
    b = parse("add(close, add(volume, open))")
    assert is_subtree_isomorphic(a, b) is False


def test_isomorphic_false_on_number_vs_field_position_mismatch():
    """位置错配：数字占位 vs 字段占位 → False（类型不同，取值忽略不适用）。"""
    assert is_subtree_isomorphic(parse("add(close, 10)"), parse("add(close, volume)")) is False
    assert is_subtree_isomorphic(parse("mul(close, 1.5)"), parse("mul(close, 2)")) is True


def test_isomorphic_ignores_number_values_deep():
    """D6：深树中数字取值全忽略（窗口 9 vs 30、字面量 1 vs 2）。"""
    a = parse("ts_rank(rank(low), 9)")
    b = parse("ts_rank(rank(low), 30)")
    assert is_subtree_isomorphic(a, b) is True
    assert is_subtree_isomorphic(parse("add(1, 2)"), parse("add(3, 4)")) is True
    assert is_subtree_isomorphic(parse("add(close, 1)"), parse("add(volume, 1)")) is False


def test_isomorphic_mixed_association_and_numbers():
    """D6 综合：交换律 + 结合律 + 数字等价（位置一一对应）。"""
    a = parse("add(close, add(volume, 1))")
    b = parse("add(volume, add(1, close))")
    assert is_subtree_isomorphic(a, b) is True


# ── Review 修复回归：深嵌套转译 / AST 工具全域性 / 规范化非源形态 ─────────────


def test_pathological_nesting_raises_complexity_error_not_recursion_error():
    """~2000 层嵌套：解释器 RecursionError 必须被转译为 FactorComplexityError。

    parse 的错误契约是 ValueError 家族；深嵌套不得泄漏裸 ``RecursionError``
    （``pytest.raises(FactorComplexityError)`` 本身即断言不是 RecursionError）。
    """
    expr = "abs(" * 2000 + "close" + ")" * 2000
    with pytest.raises(FactorComplexityError) as excinfo:
        parse(expr)
    assert str(excinfo.value) == "复杂度超限：表达式嵌套过深（> 上限 6 层）"


def test_normal_depth_violation_message_stays_verbatim():
    """对照钉住：正常深度 7 超限路径消息逐字不变（嵌套转译不得污染该路径）。"""
    with pytest.raises(FactorComplexityError) as excinfo:
        parse("sqrt(log(abs(rank(zscore(ts_mean(close, 10))))))")
    message = str(excinfo.value)
    assert "深度 7 > 上限 6" in message
    assert "嵌套过深" not in message


def test_ast_depth_is_total_for_zero_arg_call():
    """全域性：零参 Call（parse 不可能产出，防御性构造）深度为 1，不抛裸 ValueError。"""
    assert ast_depth(Call("add", ())) == 1
    assert ast_depth(Call("abs", (Call("add", ()),))) == 2


def test_normalized_flat_form_is_not_a_parseable_source_form():
    """负向钉住 normalize/serialize 边界：展平多参形态是比较/查重键，不可 parse 回读。

    规范化输出超出 D2 元数表（``add`` 要求 2 参）→ 回读必抛元数 ``DSLParseError``；
    round-trip 契约只对未规范化的 AST 成立。
    """
    flat = normalize(parse("add(add(close, open), volume)"))
    assert serialize(flat) == "add(close,open,volume)"
    with pytest.raises(DSLParseError) as excinfo:
        parse(serialize(flat))
    message = str(excinfo.value)
    assert "需要 2 个参数" in message
    assert "实际 3 个" in message
