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
}
# 每个算子一份合法样例（window 位置一律正整数数字字面量）
_WINDOW_OPS = {"ts_mean", "ts_std", "ts_rank", "delta", "ts_corr"}
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


def test_ops_enumeration_verbatim_15_names():
    """算子枚举逐字（spec 列表 15 个）；元数表与 D2 逐条一致。"""
    assert OPS == SPEC_OPS
    assert len(OPS) == 15
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
    """三条上限逐字：深度 4 / 自由参数 3 / 字段种类 3；错误类型为 ValueError 子类。"""
    assert MAX_AST_DEPTH == 4
    assert MAX_FREE_PARAMS == 3
    assert MAX_FIELDS == 3
    assert issubclass(FactorComplexityError, ValueError)


def test_parse_enforce_complexity_is_keyword_only():
    """D7 签名：``enforce_complexity`` 为仅关键字参数（parse(expr, *, ...)）。"""
    with pytest.raises(TypeError):
        parse("close", False)  # type: ignore[call-arg]


def test_depth_boundary_4_passes_5_rejected():
    """spec 场景：深度恰 4 通过；深度 5 → 拒收且消息含「深度 5 > 上限 4」逐字要素。"""
    ok = parse("rank(zscore(ts_mean(close, 10)))")
    assert ast_depth(ok) == 4
    with pytest.raises(FactorComplexityError) as excinfo:
        parse("abs(rank(zscore(ts_mean(close, 10))))")
    assert "深度 5 > 上限 4" in str(excinfo.value)


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
        "add(add(add(ts_mean(close, 1), ts_mean(volume, 2)), ts_mean(open, 3)), "
        "add(ts_mean(high, 4), low))"
    )
    with pytest.raises(FactorComplexityError) as excinfo:
        parse(expr)
    message = str(excinfo.value)
    assert "深度 5 > 上限 4" in message
    assert "自由参数 4 > 上限 3" in message
    assert "字段种类 5 > 上限 3" in message


def test_enforce_complexity_false_bypasses_caps():
    """D7：``enforce_complexity=False`` 旁路三条上限（仅供工具/验证）——默认路径同式拒收。"""
    expr = "abs(rank(zscore(ts_mean(close, 10))))"
    with pytest.raises(FactorComplexityError):
        parse(expr)
    node = parse(expr, enforce_complexity=False)
    assert ast_depth(node) == 5
    assert serialize(node) == "abs(rank(zscore(ts_mean(close,10))))"
