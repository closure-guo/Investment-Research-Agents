# add-factor-dsl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地因子表达式 DSL 的解析/序列化/规范化/结构同构原语 + 复杂度硬约束（解析期拒收），作为 LLM 因子提案与验证之间的契约。

**Architecture:** 纯函数模块 `factors/dsl.py`（零 IO、零依赖）：手写递归下降解析器（无 infix 运算符，全部函数式调用形态）→ 冻结 dataclass AST；复杂度检查独立函数、由 `parse()` 默认调用；规范化与同构为 AST 层纯函数。

**Tech Stack:** Python 3.14 / dataclasses / re / pytest。

## Global Constraints

- 测试：`uv run pytest tests/factors -q`；ruff/mypy 同仓库纪律。
- TDD 红→绿；全新增文件（本 change 不修改既有文件）。
- 算子枚举（逐字，14 个）：`ts_mean / ts_std / ts_corr / ts_rank / delta / rank / zscore / add / sub / mul / div / max / min / abs / log`（= 15 个，逐字以 spec 为准；实施时以 spec 列表为唯一集合）。
- 字段枚举（逐字，6 个）：`open / high / low / close / volume / amount`；语义=前复权口径（写入模块 docstring）。
- 错误类型：`DSLParseError`（词法/语法/未知算子/未知字段/参数形态/元数错误，消息**必须含出错位置（字符偏移）**）；`FactorComplexityError`（三条复杂度约束，消息含违规细节）。
- 复杂度上限：`MAX_AST_DEPTH = 4`、`MAX_FREE_PARAMS = 3`、`MAX_FIELDS = 3`。

## 关键设计决策（实施者必读）

**D1 — 语法**：`expr := call | field | number`；`call := IDENT "(" expr ("," expr)* ")"`；`field` 为 6 字段名之一；`number :=` 非负整数或小数。**v1 无中缀运算符**（`add/sub/mul/div` 为函数形态）；空白自由；不允许尾逗号。括号不配对/空参数/多余字符 → `DSLParseError`（带位置）。

**D2 — 元数表**（逐字钉死，实施时以本表校验）：`ts_mean/ts_std/ts_rank/delta(expr, window:int)`=2；`ts_corr(expr, expr, window:int)`=3；`rank/zscore/abs/log(expr)`=1；`add/sub/mul/div/max/min(expr, expr)`=2。window 位置必须是**正整数数字字面量**（`ts_mean(close, 10.5)` / `ts_mean(close, 0)` / `ts_mean(close, x)` → `DSLParseError`）。其余位置的数字字面量允许整数或小数（如 `mul(close, 1.5)`）。

**D3 — 深度口径**：叶子（Field/Number）深度 1；Call 深度 = 1 + max(子节点深度)。`rank(zscore(ts_mean(close, 10)))` 深度 4（通过），再套一层 = 5（拒收）。

**D4 — 自由参数计数** = AST 中 Number 节点总数（含 window）；**字段种类计数** = 去重后的 Field 名数量。

**D5 — 规范化**：① 结合律展平：`add(add(a,b),c)` → `add(a,b,c)`（同 op 且 op ∈ {add, mul, max, min}）；② 交换律排序：展平后对 commutative op 的子节点按规范序列化字符串排序；③ 幂等（normalize(normalize(x)) == normalize(x)）；④ 规范序列化 `serialize(ast)` 输出紧凑形态 `op(a,b)`（数字：整数无小数点，小数去尾零）。

**D6 — 同构**：`is_subtree_isomorphic(a, b)`：同 op、同元数、同字段名（位置对应）、数字**只按「有」匹配（取值忽略）**；即 `ts_mean(close,10)` ≍ `ts_mean(close,11)` 为 True，`ts_mean(close,10)` vs `ts_mean(volume,10)` 为 False。比较在**规范化后**进行（`add(close, volume)` ≍ `add(volume, close)`）。

**D7 — 复杂度开关**：`parse(expr, *, enforce_complexity: bool = True)`；默认路径（LLM 提案/生产）必须开启；`enforce_complexity=False` 仅供工具/验证（Alpha101 全公式超出上限，round-trip 验证需旁路），docstring 明确「不得从提案路径传入 False」。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/factors/__init__.py`（新） | 包 |
| `src/finance_agent/factors/dsl.py`（新） | 全部 DSL 原语（解析/序列化/规范化/同构/复杂度） |
| `tests/factors/__init__.py` + `tests/factors/test_dsl.py`（新） | 全算子/非法输入/复杂度边界/规范化幂等/同构正反例 |
| `tests/validation/2026-10-13-add-factor-dsl-alpha101.py`（新） | 10 个 Alpha101 公式 round-trip 脚本（期望值内嵌） |
| `tests/validation/2026-10-13-add-factor-dsl-validation.md`（新） | 验证报告 |

## Interfaces（逐字）

```python
class DSLParseError(ValueError): ...
class FactorComplexityError(ValueError): ...
@dataclass(frozen=True) class Field: name: str
@dataclass(frozen=True) class Number: value: int | float
@dataclass(frozen=True) class Call: op: str; args: tuple[AstNode, ...]
AstNode = Field | Number | Call
OPS: frozenset[str]; FIELDS: frozenset[str]; ARITY: dict[str, int]
MAX_AST_DEPTH: int = 4; MAX_FREE_PARAMS: int = 3; MAX_FIELDS: int = 3
def parse(expr: str, *, enforce_complexity: bool = True) -> AstNode
def serialize(node: AstNode) -> str
def normalize(node: AstNode) -> AstNode
def is_subtree_isomorphic(a: AstNode, b: AstNode) -> bool
def ast_depth(node: AstNode) -> int
def count_free_params(node: AstNode) -> int
def count_fields(node: AstNode) -> int
```

---

### Task 1: 解析器 + 序列化

- [ ] **Step 1: 写失败测试**：14/15 个算子逐一解析（`rank(ts_corr(close, volume, 10))` 根=rank、子=ts_corr 三参）；6 字段叶子；数字（int/小数）；空白容错；**round-trip**（spec 场景：serialize→parse→AST 相等）；未知算子 `future_peek(close, 10)` → `DSLParseError` 且消息含 `future_peek` 与字符位置；未知字段；括号不配对；空参数 `rank()`；尾逗号；元数错误（`ts_mean(close)`、`ts_corr(close, volume)`）；window 位置非法（`ts_mean(close, 10.5)`、`ts_mean(close, 0)`）；`ts_mean(close, x)`（未定义标识符）；中缀式 `close + volume` → 报错（v1 无中缀）。
- [ ] **Step 2: 跑测试确认失败。**
- [ ] **Step 3: 实现**（递归下降；错误消息统一 `f"... 位置 {pos}"`；D1/D2 口径）。
- [ ] **Step 4: 绿 + ruff + mypy。**
- [ ] **Step 5: Commit** `feat(factor-dsl): parser + serializer with op/field enums (T1)`。

### Task 2: 复杂度硬约束

- [ ] **Step 1: 写失败测试**：深度 4 通过 / 5 拒收（spec 场景，消息含「深度 5 > 上限 4」逐字要素）；自由参数 3 通过 / 4 拒收；字段 3 种通过 / 4 种拒收；`enforce_complexity=False` 时深度 5 可解析；三条约束同时违反 → 消息含全部违规项（或首条 + 全部列出，实施钉一种并在 docstring 说明）。
- [ ] **Step 2–4: 红 → 实现（`ast_depth/count_free_params/count_fields` + `_check_complexity`）→ 绿。**
- [ ] **Step 5: Commit** `feat(factor-dsl): parse-time complexity caps (depth/params/fields) (T2)`。

### Task 3: 规范化 + 同构

- [ ] **Step 1: 写失败测试**：结合律展平（`add(add(a,b),c)` → 3 参）；交换律排序（`add(volume, close)` 与 `add(close, volume)` 规范形态相同）；幂等；`is_subtree_isomorphic`：`ts_mean(close,10)` vs `ts_mean(close,11)` → True（spec 场景）；`ts_mean(close,10)` vs `ts_mean(volume,10)` → False；深度不同 → False；交换律等价 → True；数字 vs 字段位置错配 → False。
- [ ] **Step 2–4: 红 → 实现 → 绿。**
- [ ] **Step 5: Commit** `feat(factor-dsl): normalization + subtree isomorphism primitive (T3)`。

### Task 4: Alpha101 round-trip 验证 + 收口

- [ ] 脚本 `tests/validation/2026-10-13-add-factor-dsl-alpha101.py`：内嵌 10 个 Alpha101 公开公式的 **DSL 函数式转写**（逐条注明原式与转写映射），对每条执行 `parse(enforce_complexity=False)` → `serialize` → 再 `parse` → AST 相等断言；**并额外报告该式在默认 `enforce_complexity=True` 下的拒收原因**（预期全部或绝大多数超限——把「上限严格性」作为证据落表）。执行并记录输出。
- [ ] 报告 `tests/validation/2026-10-13-add-factor-dsl-validation.md`：命令证据（pytest/ruff/mypy）、round-trip 表（10 行：原式/转写/round-trip 结果/复杂度拒收原因）、异常记录（无 live 依赖，本 change 不受 #325 影响）、结论。
- [ ] `tasks.md` 全勾 + 逐条注明实际实现与偏差 → `openspec validate add-factor-dsl --strict` →（控制器执行 archive）。
- [ ] Commit `docs(openspec): add-factor-dsl validation report + tasks 回填`。

## Self-Review 备注

- spec 覆盖：可解析契约（枚举/字段/报错）→T1；复杂度三条+边界场景→T2；同构原语+换皮场景→T3；round-trip 验证→T4。
- 类型一致性：`AstNode` 别名在三个任务的签名中一致；`parse` 的 `enforce_complexity` 关键字参数为 D7 唯一开关。
