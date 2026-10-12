"""因子表达式 DSL（add-factor-dsl）：解析/序列化 + 复杂度硬约束 + 结构查重原语。

纯函数模块（零 IO、零第三方依赖）。计划
``docs/superpowers/plans/2026-10-13-add-factor-dsl.md`` 关键设计决策 D1–D7：

- **D1 语法**：``expr := call | field | number``；
  ``call := IDENT "(" expr ("," expr)* ")"``；**v1 无中缀运算符**
  （``add/sub/mul/div`` 为函数形态）；token 间空白自由；不允许尾逗号；
  括号不配对 / 空参数 / 多余字符 → ``DSLParseError``（消息必含字符位置）。
- **D2 元数表**：见 ``ARITY``；``WINDOW_ARG_INDEX`` 所列位置必须是**正整数数字
  字面量**（``ts_mean(close, 10.5)`` / ``ts_mean(close, 0)`` / ``ts_mean(close, x)``
  → ``DSLParseError``）；其余位置数字允许整数或小数（D1：非负）。
- **D3 深度口径**：叶子（Field/Number）深度 1；Call 深度 = 1 + max(子节点深度)。
- **D4 自由参数** = AST 中 Number 节点总数（含 window）；字段种类 = 去重后的
  Field 名数量。
- **D5 规范化**：① 结合律展平（同 op 且 op ∈ {add, mul, max, min}）；② 交换律排序
  （对 commutative op 的子节点按规范序列化字符串排序）；③ 幂等；④
  ``serialize`` 输出紧凑形态 ``op(a,b)``（整数无小数点，小数去尾零）。
- **D6 同构**：``is_subtree_isomorphic`` 同 op、同元数、字段名位置对应，数字
  **只按「有」匹配（取值忽略）**；比较在规范化后进行（交换律等价 → True）。
- **D7 复杂度开关**：``parse(expr, *, enforce_complexity: bool = True)``；默认
  路径（LLM 因子提案 / 生产）必须开启；``enforce_complexity=False`` **仅供工具与
  验证**（Alpha101 全公式超出上限，round-trip 核对需旁路），**不得从提案路径传入
  False**。

字段语义：``open/high/low/close/volume/amount`` 均为**前复权口径**（复权由数据层
在写入快照前完成，DSL 层不感知，也不接受未复权数据）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

# ── 枚举（逐字；spec「初版」列表） ───────────────────────────────────────────

OPS: frozenset[str] = frozenset(
    {
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
)

FIELDS: frozenset[str] = frozenset({"open", "high", "low", "close", "volume", "amount"})

# 元数表（D2，逐字钉死）
ARITY: dict[str, int] = {
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

# window 参数所在位置（D2）：必须为正整数数字字面量
WINDOW_ARG_INDEX: dict[str, int] = {
    "ts_mean": 1,
    "ts_std": 1,
    "ts_rank": 1,
    "delta": 1,
    "ts_corr": 2,
}

# 复杂度上限（解析期硬约束；默认路径超限即拒收）
MAX_AST_DEPTH: int = 4
MAX_FREE_PARAMS: int = 3
MAX_FIELDS: int = 3


# ── 错误类型 ────────────────────────────────────────────────────────────────


class DSLParseError(ValueError):
    """词法/语法/未知算子/未知字段/参数形态/元数错误。

    ``str(exc)`` 末尾固定附带 ``"，位置 <pos>"``（字符偏移，0 起）；结构化位置
    同时以 ``pos`` 属性暴露（测试与工具按需读取）。
    """

    def __init__(self, message: str, pos: int) -> None:
        super().__init__(f"{message}，位置 {pos}")
        self.pos = pos


class FactorComplexityError(ValueError):
    """复杂度硬约束超限（深度 / 自由参数 / 字段种类）。

    消息**不短路**：一次列出全部违规项，形如
    ``复杂度超限：深度 5 > 上限 4；自由参数 4 > 上限 3``
    （spec 场景要求的「深度 5 > 上限 4」子串逐字保留）。
    """


# ── AST（冻结 dataclass） ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Field:
    """行情字段叶子；``name`` ∈ ``FIELDS``（前复权口径）。"""

    name: str


@dataclass(frozen=True)
class Number:
    """数值字面量（非负整数或小数；window 位置要求正整数形态）。"""

    value: int | float


@dataclass(frozen=True)
class Call:
    """算子调用节点；``op`` ∈ ``OPS``，``args`` 为子表达式（元数见 ``ARITY``）。"""

    op: str
    args: tuple[AstNode, ...]


type AstNode = Field | Number | Call


# ── AST 工具：深度 / 自由参数 / 字段种类 / 复杂度检查 ───────────────────────


def ast_depth(node: AstNode) -> int:
    """D3 深度口径：叶子（Field/Number）深度 1；Call = 1 + max(子节点深度)。"""
    if isinstance(node, Call):
        return 1 + max(ast_depth(arg) for arg in node.args)
    return 1


def count_free_params(node: AstNode) -> int:
    """D4 自由参数计数 = AST 中 Number 节点总数（含 window 数字）。"""
    if isinstance(node, Number):
        return 1
    if isinstance(node, Call):
        return sum(count_free_params(arg) for arg in node.args)
    return 0


def count_fields(node: AstNode) -> int:
    """D4 字段种类计数 = 去重后的 Field 名数量（同名字段多次引用只计一种）。"""
    if isinstance(node, Field):
        return 1
    if isinstance(node, Call):
        return len(_field_names(node))
    return 0


def _field_names(node: Call) -> set[str]:
    """收集子树中的 Field 名集合（``count_fields`` 递归辅助）。"""
    names: set[str] = set()
    for arg in node.args:
        if isinstance(arg, Field):
            names.add(arg.name)
        elif isinstance(arg, Call):
            names |= _field_names(arg)
    return names


def _check_complexity(node: AstNode) -> None:
    """三条上限逐条检查；**不短路**——一次列出全部违规项（见错误类 docstring）。"""
    violations: list[str] = []
    depth = ast_depth(node)
    if depth > MAX_AST_DEPTH:
        violations.append(f"深度 {depth} > 上限 {MAX_AST_DEPTH}")
    free_params = count_free_params(node)
    if free_params > MAX_FREE_PARAMS:
        violations.append(f"自由参数 {free_params} > 上限 {MAX_FREE_PARAMS}")
    fields = count_fields(node)
    if fields > MAX_FIELDS:
        violations.append(f"字段种类 {fields} > 上限 {MAX_FIELDS}")
    if violations:
        raise FactorComplexityError("复杂度超限：" + "；".join(violations))


# ── 词法 ────────────────────────────────────────────────────────────────────

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")

_IDENT = "ident"
_NUMBER = "number"
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
    """字符串 → token 序列（末尾恒有 EOF）；非法字符 → ``DSLParseError``（带位置）。"""
    tokens: list[_Token] = []
    index = 0
    length = len(expr)
    while index < length:
        char = expr[index]
        if char.isspace():
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
        number_match = _NUMBER_RE.match(expr, index)
        if number_match:
            text = number_match.group()
            value = int(text) if "." not in text else float(text)
            tokens.append(_Token(_NUMBER, text, index, value))
            index = number_match.end()
            continue
        ident_match = _IDENT_RE.match(expr, index)
        if ident_match:
            tokens.append(_Token(_IDENT, ident_match.group(), index))
            index = ident_match.end()
            continue
        raise DSLParseError(f"意外的字符 {char!r}", index)
    tokens.append(_Token(_EOF, "", length))
    return tokens


# ── 语法（递归下降） ────────────────────────────────────────────────────────


class _Parser:
    """内部递归下降解析器；对外入口仅 ``parse()``。"""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def parse_root(self) -> AstNode:
        node, _ = self._parse_expr()
        token = self._peek()
        if token.kind != _EOF:
            raise DSLParseError(f"表达式结束后出现多余记号 {token.text!r}", token.pos)
        return node

    def _parse_expr(self) -> tuple[AstNode, int]:
        """解析一个表达式，返回 (节点, 起始字符位置)。"""
        token = self._peek()
        if token.kind == _NUMBER and token.value is not None:
            self._advance()
            return Number(token.value), token.pos
        if token.kind == _IDENT:
            if self._tokens[self._index + 1].kind == _LPAREN:
                return self._parse_call()
            self._advance()
            if token.text in FIELDS:
                return Field(token.text), token.pos
            raise DSLParseError(f"未知字段或未定义标识符 {token.text!r}", token.pos)
        if token.kind == _EOF:
            raise DSLParseError("表达式意外结束（缺少表达式）", token.pos)
        raise DSLParseError(f"意外的记号 {token.text!r}（此处应为表达式）", token.pos)

    def _parse_call(self) -> tuple[AstNode, int]:
        op_token = self._advance()
        op = op_token.text
        if op not in OPS:
            raise DSLParseError(f"未知算子 {op!r}", op_token.pos)
        lparen_token = self._advance()
        if self._peek().kind == _RPAREN:
            raise DSLParseError(f"算子 {op!r} 的参数列表为空", self._peek().pos)
        args: list[AstNode] = []
        arg_positions: list[int] = []
        while True:
            arg, arg_pos = self._parse_expr()
            args.append(arg)
            arg_positions.append(arg_pos)
            token = self._peek()
            if token.kind == _COMMA:
                self._advance()
                if self._peek().kind == _RPAREN:
                    raise DSLParseError(f"尾逗号不允许（算子 {op!r} 期望参数）", self._peek().pos)
                continue
            if token.kind == _RPAREN:
                self._advance()
                break
            if token.kind == _EOF:
                raise DSLParseError(f"缺少右括号（算子 {op!r} 未闭合）", lparen_token.pos)
            raise DSLParseError(f"意外的记号 {token.text!r}（期望 ',' 或 ')'）", token.pos)
        expected = ARITY[op]
        if len(args) != expected:
            raise DSLParseError(
                f"算子 {op!r} 需要 {expected} 个参数，实际 {len(args)} 个", op_token.pos
            )
        window_index = WINDOW_ARG_INDEX.get(op)
        if window_index is not None:
            window = args[window_index]
            if not (
                isinstance(window, Number) and isinstance(window.value, int) and window.value >= 1
            ):
                raise DSLParseError(
                    f"算子 {op!r} 的 window 参数必须是正整数数字字面量",
                    arg_positions[window_index],
                )
        return Call(op=op, args=tuple(args)), op_token.pos


# ── 对外接口：解析 / 序列化 ─────────────────────────────────────────────────


def parse(expr: str, *, enforce_complexity: bool = True) -> AstNode:
    """解析因子表达式字符串为 AST（D1/D2；错误带字符位置）。

    空白自由、无中缀运算符；未知算子/字段、元数错误、window 形态非法、
    括号不配对等一律 ``DSLParseError``。

    ``enforce_complexity=True``（默认，**提案/生产路径唯一允许值**）时解析成功后
    立即执行 D3/D4 三条复杂度上限检查，超限 → ``FactorComplexityError``。
    ``enforce_complexity=False`` **仅供工具与验证旁路**（如 Alpha101 公开公式的
    round-trip 核对——它们普遍超出上限）；**不得从提案（LLM 因子提案）路径传入
    False**，否则复杂度硬约束形同虚设。
    """
    if not expr.strip():
        raise DSLParseError("表达式为空", 0)
    node = _Parser(_tokenize(expr)).parse_root()
    if enforce_complexity:
        _check_complexity(node)
    return node


def serialize(node: AstNode) -> str:
    """规范序列化：紧凑形态 ``op(a,b)``；数字整数无小数点、小数去尾零（D5④）。

    注意：规范化（``normalize``）后的结合律展平形态（如三参 ``add``）是本模块
    约定内的**比较用规范形**，超出 D2 元数表，不可再用 ``parse`` 回读——round-trip
    契约只对未规范化的 AST 成立。
    """
    if isinstance(node, Field):
        return node.name
    if isinstance(node, Number):
        return _format_number(node.value)
    return f"{node.op}({','.join(serialize(arg) for arg in node.args)})"


def _format_number(value: int | float) -> str:
    """数字 → 规范文本：整数无小数点；小数去尾零（定点形态，避免科学计数法）。"""
    if isinstance(value, int):
        return str(value)
    text = format(Decimal(repr(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
