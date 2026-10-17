"""Qlib Alpha 表达式 → 本系统因子 DSL 翻译器（unify-quant-research-architecture spike 工具）。

契约：spec factor-proposal「外部表达式翻译契约」——二值裁决（严格等价翻译 /
显式不可翻译 + 算子清单）、映射表可审计、字段与口径映射显式声明、
MUST NOT 输出语义近似的猜测翻译。

**等价基准 = formulation 语义**（窗口含当日的滚动统计量定义）；实现级口径差异
（pyqlib 0.9.7 `min_periods=1` 热身期部分窗口值、除零/对数边界约定）在映射表
逐条声明——差异不改变统计量定义，但审计上必须可见。

来源核对：pyqlib 0.9.7（`qlib/data/ops.py`，行号为该文件行号）：

- Rolling 基类：`series.rolling(self.N, min_periods=1)`（L752）——热身期输出
  部分窗口统计量；本系统 ts_* 为 min_periods=window（热身期 NaN）。N=0 为
  expanding（L747）、0<N<1 为 ewm（L750）→ 非整数正窗一律拒绝；
- `Ref` = `series.shift(N)`（L793）——与本系统 `ref`（extend-factor-dsl-ref）严格等价：
  同为空值前移 N 期的纯 shift，**无热身期口径差**（shift 无 min_periods 概念）；
  N 须正整数（pyqlib 负 N 为前瞻引用，超出支持面，拒绝）；
- `Mean/Std/Sum/...` = Rolling func（L827+）；
- `Rank` = 滚动 pct 秩（`rolling(N, min_periods=1).rank(pct=True)`，L1156）——
  **滚动秩而非截面排名**，与本系统 `ts_rank`（窗口内当前值 pct 秩、并列平均秩）
  formulation 等价；
- `Delta` = `series - series.shift(N)`（L1204）；
- `Max/Min` = **滚动**窗口算子（Rolling 子类，L951/L999）——与本系统逐元素二元
  `max/min` 同名不同义 → not_equivalent（拒绝机械替换）；
- `Sum` = Rolling 求和（L847）——与本系统 `ts_sum` formulation 等价（热身期口径差同其他滚动算子）；
- `Power` = NpPairOperator（L338，可变指数）——本 DSL 无幂算子；**特例**：`Power(x, 0.5)`
  ≡ `sqrt(x)`（0.5 次幂 = 平方根，负值两侧均 NaN）→ 特例映射启用；其余指数拒绝。
  注：pyqlib 无独立 `Sqrt` 算子（ops.py 实测）——平方根形态的合法转写即 `Power(·,0.5)`；
- `Div` = `np.divide`（L418；x/0 → ±inf）、`Log` = `np.log`（L167；x=0 → −inf）、
  `Abs` = `np.abs`（L122）、`Add/Sub/Mul` = `np.*`（L358+）。

本模块为 spike 工具（design.md Non-Goals：不进生产代码）；翻译产出即 DSL 候选，
经 `scripts/import_external_factors.py` 过同一五道闸；「不可翻译」候选按其外部
表达式形态计入通道 `errors` 桶（gate=None），裁决清单随报告留档。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finance_agent.factors.dsl import (
    ARITY,
    WINDOW_ARG_INDEX,
    AstNode,
    Call,
    DSLParseError,
    FactorComplexityError,
    Field,
    Number,
    parse,
    serialize,
)

# ── 映射表（可审计；dump_mapping_table() 随产物留存） ───────────────────────

# 滚动窗口族实现级口径（声明性差异，不改变统计量定义）
_WARMUP = (
    "pyqlib min_periods=1（热身期输出部分窗口值）vs 本系统 min_periods=window"
    "（热身期 NaN）——差异仅限各票前 N−1 日（口径声明）"
)


@dataclass(frozen=True)
class OperatorMapping:
    """逐算子映射条目：qlib_name → dsl_name（None=无对应）+ 状态 + 审计备注。"""

    qlib_name: str
    dsl_name: str | None
    status: str  # equivalent / not_equivalent / missing
    note: str


OPERATOR_MAPPINGS: dict[str, OperatorMapping] = {
    # ── 严格等价（formulation 语义；口径差异见备注） ──
    "Mean": OperatorMapping("Mean", "ts_mean", "equivalent", f"滚动均值（窗口含当日）；{_WARMUP}"),
    "Std": OperatorMapping(
        "Std", "ts_std", "equivalent", f"滚动样本标准差（ddof=1 两侧一致）；{_WARMUP}"
    ),
    "Corr": OperatorMapping(
        "Corr", "ts_corr", "equivalent", f"滚动皮尔逊相关（窗口含当日）；{_WARMUP}"
    ),
    "Rank": OperatorMapping(
        "Rank",
        "ts_rank",
        "equivalent",
        f"滚动 pct 秩（窗口含当日、并列平均秩一致；pyqlib 为滚动秩非截面排名，L1156）；{_WARMUP}",
    ),
    "Delta": OperatorMapping(
        "Delta",
        "delta",
        "equivalent",
        "x − x.shift(N) 逐值等价（L1204）；N 须 ≥1（N=0 为 x−首值语义）",
    ),
    "Abs": OperatorMapping("Abs", "abs", "equivalent", "逐元素绝对值（np.abs 同）"),
    "Log": OperatorMapping(
        "Log",
        "log",
        "equivalent",
        "自然对数（np.log 同）；x=0：pyqlib −inf vs 本系统 NaN（边界口径声明）",
    ),
    "Add": OperatorMapping("Add", "add", "equivalent", "逐元素加法（np.add 同）"),
    "Sub": OperatorMapping("Sub", "sub", "equivalent", "逐元素减法（np.subtract 同）"),
    "Mul": OperatorMapping("Mul", "mul", "equivalent", "逐元素乘法（np.multiply 同）"),
    "Div": OperatorMapping(
        "Div",
        "div",
        "equivalent",
        "逐元素除法；除零：pyqlib ±inf/NaN（np.divide）vs 本系统一律 NaN（边界口径声明）",
    ),
    # ── 同名不同义（拒绝机械替换） ──
    "Max": OperatorMapping(
        "Max",
        None,
        "not_equivalent",
        "pyqlib 为滚动窗口最大值（Rolling 子类，L951）；本系统 max 为逐元素二元算子——同名不同义，拒绝机械替换",
    ),
    "Min": OperatorMapping(
        "Min",
        None,
        "not_equivalent",
        "pyqlib 为滚动窗口最小值（Rolling 子类，L999）；本系统 min 为逐元素二元算子——同名不同义，拒绝机械替换",
    ),
    # ── 后移引用（extend-factor-dsl-ref：missing → equivalent） ──
    "Ref": OperatorMapping(
        "Ref",
        "ref",
        "equivalent",
        "后移引用 x.shift(N)（L793）——本系统 ref 同为沿日期轴后移 N 期（前 N 行 NaN），"
        "无热身期口径差；窗口须正整数（N≤0 为恒等/前瞻引用，拒绝）",
    ),
    # ── 本 DSL 无对应算子（扩 DSL 候选） ──
    "Sum": OperatorMapping("Sum", "ts_sum", "equivalent", f"滚动求和（窗口含当日）；{_WARMUP}"),
    "Var": OperatorMapping(
        "Var", None, "missing", "滚动方差——本 DSL 无（ts_std 为平方根形态，无 var 算子）"
    ),
    "Skew": OperatorMapping("Skew", None, "missing", "滚动偏度——本 DSL 无"),
    "Kurt": OperatorMapping("Kurt", None, "missing", "滚动峰度——本 DSL 无"),
    "Quantile": OperatorMapping("Quantile", None, "missing", "滚动分位数——本 DSL 无"),
    "Med": OperatorMapping("Med", None, "missing", "滚动中位数——本 DSL 无"),
    "Mad": OperatorMapping("Mad", None, "missing", "滚动平均绝对偏差——本 DSL 无"),
    "Count": OperatorMapping("Count", None, "missing", "滚动非空计数——本 DSL 无"),
    "Slope": OperatorMapping("Slope", None, "missing", "滚动回归斜率——本 DSL 无"),
    "Rsquare": OperatorMapping("Rsquare", None, "missing", "滚动回归 R²——本 DSL 无"),
    "Resi": OperatorMapping("Resi", None, "missing", "滚动回归残差——本 DSL 无"),
    "WMA": OperatorMapping("WMA", None, "missing", "加权移动平均——本 DSL 无"),
    "EMA": OperatorMapping("EMA", None, "missing", "指数移动平均——本 DSL 无"),
    "Cov": OperatorMapping("Cov", None, "missing", "滚动协方差——本 DSL 无"),
    "Sign": OperatorMapping("Sign", None, "missing", "符号函数——本 DSL 无"),
    "Power": OperatorMapping(
        "Power",
        None,
        "missing",
        "幂运算（NpPairOperator，L338，可变指数）——本 DSL 无；特例 Power(x,0.5) ≡ sqrt(x) "
        "（0.5 次幂 = 平方根）由 _build_call 特例分支映射，其余指数拒绝",
    ),
    "Greater": OperatorMapping("Greater", None, "missing", "比较算子——本 DSL 无"),
    "Less": OperatorMapping("Less", None, "missing", "比较算子——本 DSL 无"),
    "Gt": OperatorMapping("Gt", None, "missing", "比较算子——本 DSL 无"),
    "Ge": OperatorMapping("Ge", None, "missing", "比较算子——本 DSL 无"),
    "Lt": OperatorMapping("Lt", None, "missing", "比较算子——本 DSL 无"),
    "Le": OperatorMapping("Le", None, "missing", "比较算子——本 DSL 无"),
    "Eq": OperatorMapping("Eq", None, "missing", "比较算子——本 DSL 无"),
    "Ne": OperatorMapping("Ne", None, "missing", "比较算子——本 DSL 无"),
    "If": OperatorMapping("If", None, "missing", "条件算子——本 DSL 无"),
    "And": OperatorMapping("And", None, "missing", "逻辑算子——本 DSL 无"),
    "Or": OperatorMapping("Or", None, "missing", "逻辑算子——本 DSL 无"),
    "Not": OperatorMapping("Not", None, "missing", "逻辑算子——本 DSL 无"),
    "Mask": OperatorMapping("Mask", None, "missing", "掩码算子——本 DSL 无"),
}

_PRICE_CALIBER = "前复权口径（本系统六字段均前复权；外部数据源须同为前复权）"


@dataclass(frozen=True)
class FieldMapping:
    """字段映射条目：$qlib_field → dsl_field（None=无对应）+ 口径声明。"""

    qlib_field: str
    dsl_field: str | None
    note: str


FIELD_MAPPINGS: dict[str, FieldMapping] = {
    "$open": FieldMapping("$open", "open", _PRICE_CALIBER),
    "$high": FieldMapping("$high", "high", _PRICE_CALIBER),
    "$low": FieldMapping("$low", "low", _PRICE_CALIBER),
    "$close": FieldMapping("$close", "close", _PRICE_CALIBER),
    "$volume": FieldMapping(
        "$volume",
        "volume",
        "成交量；量纲口径：本系统快照为手（日批/回填归一），Qlib 社区数据为股——比值类因子量纲相消，"
        "绝对量纲因子不可投用（口径声明）",
    ),
    "$amount": FieldMapping("$amount", "amount", "成交额（元）——两侧同口径"),
    "$factor": FieldMapping("$factor", None, "复权因子列——本系统面板无此字段（复权在数据层完成）"),
}


# ── 结果类型 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TranslationResult:
    """二值裁决结果：translatable=True 时 dsl_expression 非空且 blockers 为空。"""

    source_expression: str
    translatable: bool
    dsl_expression: str | None
    blockers: tuple[str, ...]
    used_mappings: tuple[str, ...]


# ── 词法/语法（Qlib Alpha 表达式子集：$field / 数字 / 函数调用 / + - * / 括号） ──


class TranslateSyntaxError(ValueError):
    """Qlib 表达式超出翻译器支持面（含字符位置）。"""

    def __init__(self, message: str, pos: int) -> None:
        super().__init__(f"{message}，位置 {pos}")
        self.pos = pos


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")

_NUMBER = "number"
_FIELD = "field"
_IDENT = "ident"
_OP = "op"
_LPAREN = "lparen"
_RPAREN = "rparen"
_COMMA = "comma"
_EOF = "eof"


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    pos: int
    value: int | float | None = None


def _tokenize(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    length = len(expr)
    while index < length:
        char = expr[index]
        if char.isspace():
            index += 1
            continue
        if char in "+-*/":
            tokens.append(_Token(_OP, char, index))
            index += 1
            continue
        if char == "(":
            tokens.append(_Token(_LPAREN, char, index))
            index += 1
            continue
        if char == ")":
            tokens.append(_Token(_RPAREN, char, index))
            index += 1
            continue
        if char == ",":
            tokens.append(_Token(_COMMA, char, index))
            index += 1
            continue
        if char == "$":
            match = _IDENT_RE.match(expr, index + 1)
            if not match:
                raise TranslateSyntaxError("字段名缺失（$ 后须为标识符）", index)
            tokens.append(_Token(_FIELD, match.group(), index))
            index = match.end()
            continue
        number_match = _NUMBER_RE.match(expr, index)
        if number_match:
            text = number_match.group()
            value: int | float = int(text) if "." not in text else float(text)
            tokens.append(_Token(_NUMBER, text, index, value))
            index = number_match.end()
            continue
        ident_match = _IDENT_RE.match(expr, index)
        if ident_match:
            tokens.append(_Token(_IDENT, ident_match.group(), index))
            index = ident_match.end()
            continue
        raise TranslateSyntaxError(f"不支持的记号 {char!r}", index)
    tokens.append(_Token(_EOF, "", length))
    return tokens


# ── 中间 AST（Qlib 表达式形态；与 DSL AST 分离，构建期做映射裁决） ─────────


@dataclass(frozen=True)
class _Num:
    value: int | float
    pos: int


@dataclass(frozen=True)
class _NegNumber:
    value: int | float
    pos: int


@dataclass(frozen=True)
class _FieldNode:
    name: str
    pos: int


@dataclass(frozen=True)
class _CallNode:
    name: str
    args: tuple[_Node, ...]
    pos: int


@dataclass(frozen=True)
class _BinOp:
    op: str
    left: _Node
    right: _Node
    pos: int


type _Node = _Num | _NegNumber | _FieldNode | _CallNode | _BinOp


class _Parser:
    """递归下降 + 优先级（乘除 > 加减）；一元负号仅接受数字字面量。"""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def parse_root(self) -> _Node:
        node = self._parse_expr()
        token = self._peek()
        if token.kind != _EOF:
            raise TranslateSyntaxError(f"表达式结束后出现多余记号 {token.text!r}", token.pos)
        return node

    def _parse_expr(self) -> _Node:
        node = self._parse_term()
        while self._peek().kind == _OP and self._peek().text in "+-":
            op = self._advance()
            node = _BinOp(op.text, node, self._parse_term(), op.pos)
        return node

    def _parse_term(self) -> _Node:
        node = self._parse_factor()
        while self._peek().kind == _OP and self._peek().text in "*/":
            op = self._advance()
            node = _BinOp(op.text, node, self._parse_factor(), op.pos)
        return node

    def _parse_factor(self) -> _Node:
        token = self._peek()
        if token.kind == _OP and token.text == "-":
            self._advance()
            nxt = self._peek()
            if nxt.kind == _NUMBER and nxt.value is not None:
                self._advance()
                return _NegNumber(-nxt.value, token.pos)
            raise TranslateSyntaxError(
                "一元负号仅支持数字字面量（负数常量本 DSL 不可表达）", token.pos
            )
        return self._parse_atom()

    def _parse_atom(self) -> _Node:
        token = self._peek()
        if token.kind == _NUMBER and token.value is not None:
            self._advance()
            return _Num(token.value, token.pos)
        if token.kind == _FIELD:
            self._advance()
            return _FieldNode(token.text, token.pos)
        if token.kind == _IDENT:
            self._advance()
            if self._peek().kind != _LPAREN:
                raise TranslateSyntaxError(
                    f"裸标识符 {token.text!r}（行情字段须带 $ 前缀）", token.pos
                )
            self._advance()
            if self._peek().kind == _RPAREN:
                raise TranslateSyntaxError(f"算子 {token.text!r} 的参数列表为空", self._peek().pos)
            args: list[_Node] = []
            while True:
                args.append(self._parse_expr())
                nxt = self._peek()
                if nxt.kind == _COMMA:
                    self._advance()
                    continue
                if nxt.kind == _RPAREN:
                    self._advance()
                    break
                if nxt.kind == _EOF:
                    raise TranslateSyntaxError(
                        f"缺少右括号（算子 {token.text!r} 未闭合）", token.pos
                    )
                raise TranslateSyntaxError(f"意外的记号 {nxt.text!r}（期望 ',' 或 ')'）", nxt.pos)
            return _CallNode(token.text, tuple(args), token.pos)
        if token.kind == _LPAREN:
            self._advance()
            node = self._parse_expr()
            if self._peek().kind != _RPAREN:
                raise TranslateSyntaxError("缺少右括号", token.pos)
            self._advance()
            return node
        if token.kind == _EOF:
            raise TranslateSyntaxError("表达式意外结束（缺少表达式）", token.pos)
        raise TranslateSyntaxError(f"意外的记号 {token.text!r}（此处应为表达式）", token.pos)


# ── 构建：中间 AST → DSL AST（映射裁决 + 审计记录） ────────────────────────

_BINOP_TO_QLIB: dict[str, str] = {"+": "Add", "-": "Sub", "*": "Mul", "/": "Div"}


def _record(used: list[str], seen: set[str], entry: str) -> None:
    if entry not in seen:
        seen.add(entry)
        used.append(entry)


def _build(node: _Node, blockers: list[str], used: list[str], seen: set[str]) -> AstNode | None:
    """中间 AST → DSL AST；blockers/used 就地累积。任一叶子/算子不可映射 → None。"""
    if isinstance(node, _NegNumber):
        blockers.append(
            f"负数常量 {node.value}：本 DSL 数字字面量非负（可用 sub 重排表达，翻译器 v1 保守拒绝）"
        )
        return None
    if isinstance(node, _Num):
        return Number(node.value)
    if isinstance(node, _FieldNode):
        key = f"${node.name}"
        mapping = FIELD_MAPPINGS.get(key)
        if mapping is None:
            blockers.append(f"未知字段 {key}（映射表无记录）")
            return None
        if mapping.dsl_field is None:
            blockers.append(f"字段 {key}：{mapping.note}")
            return None
        _record(used, seen, f"字段 {key} → {mapping.dsl_field} [equivalent] {mapping.note}")
        return Field(mapping.dsl_field)
    if isinstance(node, _BinOp):
        return _build_call(
            _CallNode(_BINOP_TO_QLIB[node.op], (node.left, node.right), node.pos),
            blockers,
            used,
            seen,
        )
    # _CallNode
    return _build_call(node, blockers, used, seen)


def _build_call(
    node: _CallNode, blockers: list[str], used: list[str], seen: set[str]
) -> AstNode | None:
    """函数调用/二元运算映射裁决：等价→构建；缺失/不等价→blocker（含清单）；未知→blocker。"""
    # 特例映射（extend-factor-dsl-coverage）：Power(x, 0.5) ≡ sqrt(x)——0.5 次幂 = 平方根，
    # 精确等价（负值两侧均 NaN）；其余指数走通用分支（Power 条目 missing → 拒绝）。
    if (
        node.name == "Power"
        and len(node.args) == 2
        and isinstance(node.args[1], _Num)
        and node.args[1].value == 0.5
    ):
        inner = _build(node.args[0], blockers, used, seen)
        _record(
            used,
            seen,
            "算子 Power(·,0.5) → sqrt [equivalent] 0.5 次幂 = 平方根（负值 NaN 两侧一致）",
        )
        if inner is None:
            return None
        return Call(op="sqrt", args=(inner,))
    mapping = OPERATOR_MAPPINGS.get(node.name)
    if mapping is None:
        blockers.append(f"未知算子 {node.name!r}（映射表无记录）")
        for arg in node.args:  # 继续下探，完整收集内层不可翻译清单
            _build(arg, blockers, used, seen)
        return None
    if mapping.status != "equivalent" or mapping.dsl_name is None:
        blockers.append(f"算子 {node.name}：{mapping.note}")
        _record(used, seen, f"算子 {node.name} → 无 [{mapping.status}] {mapping.note}")
        for arg in node.args:  # 继续下探，完整收集内层不可翻译清单
            _build(arg, blockers, used, seen)
        return None
    dsl_name = mapping.dsl_name
    expected = ARITY[dsl_name]
    if len(node.args) != expected:
        blockers.append(f"算子 {node.name} 参数数 {len(node.args)} ≠ 映射 {dsl_name} 的 {expected}")
        _record(used, seen, f"算子 {node.name} → {dsl_name} [equivalent] {mapping.note}")
        return None
    window_index = WINDOW_ARG_INDEX.get(dsl_name)
    if window_index is not None:
        window_arg = node.args[window_index]
        if not (
            isinstance(window_arg, _Num)
            and isinstance(window_arg.value, int)
            and window_arg.value >= 1
        ):
            blockers.append(
                f"算子 {node.name} 的窗口参数必须是正整数数字字面量"
                "（pyqlib N=0 为 expanding、0<N<1 为 ewm——语义不同，拒绝）"
            )
            _record(used, seen, f"算子 {node.name} → {dsl_name} [equivalent] {mapping.note}")
            return None
    built_args = [_build(arg, blockers, used, seen) for arg in node.args]
    _record(used, seen, f"算子 {node.name} → {dsl_name} [equivalent] {mapping.note}")
    if any(arg is None for arg in built_args):
        return None
    return Call(op=dsl_name, args=tuple(arg for arg in built_args if arg is not None))


# ── 对外接口 ────────────────────────────────────────────────────────────────


def translate_qlib_expression(expr: str) -> TranslationResult:
    """Qlib Alpha 表达式 → 二值裁决（严格等价 DSL 或显式不可翻译）。

    支持面：``$field`` / 数字 / 函数调用 / ``+ - * /`` / 括号（无比较与逻辑中缀）。
    输出前经生产解析器 ``dsl.parse``（含复杂度硬约束）复验——「合法 DSL 表达式」
    是契约义务，不是期望。
    """
    if not expr.strip():
        return TranslationResult(expr, False, None, ("表达式为空",), ())
    blockers: list[str] = []
    used: list[str] = []
    seen: set[str] = set()
    try:
        root = _Parser(_tokenize(expr)).parse_root()
    except TranslateSyntaxError as exc:
        return TranslationResult(expr, False, None, (f"语法错误：{exc}",), ())
    dsl_node = _build(root, blockers, used, seen)
    if blockers or dsl_node is None:
        return TranslationResult(expr, False, None, tuple(blockers), tuple(used))
    text = serialize(dsl_node)
    try:
        parse(text)
    except FactorComplexityError as exc:
        return TranslationResult(expr, False, None, (str(exc),), tuple(used))
    except DSLParseError as exc:  # 防御：产出形态意外非法（不应发生）
        return TranslationResult(
            expr, False, None, (f"产出非合法 DSL（内部错误）：{exc}",), tuple(used)
        )
    return TranslationResult(expr, True, text, (), tuple(used))


def dump_mapping_table() -> str:
    """全量映射表文本（spike 产物留档：算子表 + 字段/口径表）。"""
    lines = [
        "# 算子映射表（Qlib Alpha → 本系统 DSL）",
        "# 等价基准 = formulation 语义（窗口含当日的滚动统计量定义）；实现级口径差异见备注",
    ]
    for name in sorted(OPERATOR_MAPPINGS):
        mapping = OPERATOR_MAPPINGS[name]
        target = mapping.dsl_name or "（无）"
        lines.append(f"- {name} → {target} [{mapping.status}] {mapping.note}")
    lines.append("")
    lines.append("# 字段映射表（口径显式声明）")
    for name in sorted(FIELD_MAPPINGS):
        field = FIELD_MAPPINGS[name]
        target = field.dsl_field or "（无）"
        lines.append(f"- {name} → {target} {field.note}")
    return "\n".join(lines)
