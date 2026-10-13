# 人工验证报告: add-factor-dsl

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-factor-dsl/`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 1–4（DSL 解析/序列化 → 复杂度硬约束 → 规范化/子树同构 → Alpha101 round-trip 收口）
**全量回归时 HEAD**: `5a10acb8`（T1–T3 三个 feat 提交之上的工作区；此后仅新增本报告与验证脚本，零 src 改动）

> **边界声明（只记录实际发生）**：本 change 为**纯函数模块**，无 IO、无网络、无 LLM 调用，
> **不受**本机东财 IP 封禁（incident 033 / issue #325）影响；Alpha101 核对不依赖 live 数据。

## 0. 命令证据（当场跑、逐字记录）

### 0.1 指定测试命令

```
$ uv run pytest tests/factors -q
82 passed in 1.07s

$ uv run pytest tests/factors tests/screening -q
137 passed in 1.90s
```

（`tests/factors` 82 例 = T1 解析/序列化/非法输入 47 例 + T2 复杂度 15 例 + T3 规范化/同构 20 例；
`tests/screening` 55 例为本次 review 修复后的存量。）

### 0.2 全量测试（排除 live）

```
$ uv run pytest tests/ -q -m "not live"
1 failed, 4914 passed, 6 skipped, 12 deselected, 30 warnings in 861.66s (0:14:21)
（失败用例：tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default）
```

唯一失败归因（如实记录，不掩饰）：**既有环境性失败，非本 change 引入**——① 单独运行该用例
`uv run pytest "tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default" -q`
→ `1 passed in 0.05s`（全量运行时受宿主 `.env` 泄漏 `LLM_MAX_CONTEXT` 污染：`assert 256000 == 128000`）；
② 同一失败在上一轮全量回归（`2026-10-13-add-screening-filters-validation.md` §0.2，4818 passed 那轮）
已出现并归因于同一环境问题；③ 本 change 的 src 改动不含 `llm/` 任何文件（仅 `src/finance_agent/factors/`）；
④ 交叉验证：4914 − 4818 = 96 = 本 change 新增用例数（`tests/factors` 82 + screening review 修复 14）。
归因为既有环境问题，不阻塞本 change。

### 0.3 Lint 与类型检查

```
$ uv run ruff check src/finance_agent/factors src/finance_agent/screening \
    tests/factors tests/screening tests/validation/2026-10-13-add-factor-dsl-alpha101.py
All checks passed!

$ uv run ruff format --check src/finance_agent/factors src/finance_agent/screening \
    tests/factors tests/screening tests/validation/2026-10-13-add-factor-dsl-alpha101.py
10 files already formatted

$ uv run mypy src/finance_agent/factors src/finance_agent/screening
Success: no issues found in 5 source files
```

### 0.4 Alpha101 round-trip 脚本执行

```
$ uv run python tests/validation/2026-10-13-add-factor-dsl-alpha101.py
Alpha101 round-trip 核对（10 条；D7 旁路 enforce_complexity=False）
...（逐条明细见 §1 表；尾部汇总如下）...
============================================================================================================
汇总表
alpha     深度    参数    字段    round-trip  默认上限判定
Alpha#2   6     3     3     OK          拒收 —— 复杂度超限：深度 6 > 上限 4
Alpha#4   4     2     1     OK          通过（未超限）
Alpha#6   3     2     2     OK          通过（未超限）
Alpha#12  5     4     2     OK          拒收 —— 复杂度超限：深度 5 > 上限 4；自由参数 4 > 上限 3
Alpha#13  5     3     1     OK          拒收 —— 复杂度超限：深度 5 > 上限 4
Alpha#14  6     4     3     OK          拒收 —— 复杂度超限：深度 6 > 上限 4；自由参数 4 > 上限 3
Alpha#15  6     3     2     OK          拒收 —— 复杂度超限：深度 6 > 上限 4
Alpha#16  5     2     2     OK          拒收 —— 复杂度超限：深度 5 > 上限 4
Alpha#53  6     2     3     OK          拒收 —— 复杂度超限：深度 6 > 上限 4
Alpha#101 4     1     4     OK          拒收 —— 复杂度超限：字段种类 4 > 上限 3

round-trip 一致 10/10；默认上限拒收 8/10（通过：Alpha#4、Alpha#6）
全部核对一致（exit 0）
```

## 1. Round-trip 核对表（10 条公开公式）

构造方式：`tests/validation/2026-10-13-add-factor-dsl-alpha101.py` 内嵌 10 条 Kakushadze
*101 Formulaic Alphas*（2015）公开公式的**DSL 函数式转写**（每条注明原式与映射），
`parse(enforce_complexity=False)` → `serialize` → 再 `parse` → **AST 相等断言**；
深度/自由参数/字段种类按 D3/D4 口径**手算内嵌**后期望（不从被测函数推导）并断言。
**转写验证的是结构契约（可解析 / round-trip / 复杂度上限），不验证数值等价**——
DSL 初版没有 `sum/covariance/sign/returns/adv20/vwap` 等算子，映射见下表「转写映射」。

| # | 原式（公开公式） | 转写映射 | DSL 转写（函数形态） | 深度/参数/字段（手算=实测） | round-trip | 默认上限判定 |
|---|---|---|---|---|---|---|
| Alpha#2 | `(-1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6))` | correlation→ts_corr；delta/log/rank 原样；(close-open)/open→div(sub,·)；一元负→sub(0,·) | `sub(0, ts_corr(rank(delta(log(volume), 2)), rank(div(sub(close, open), open)), 6))` | 6/3/3 | OK | **拒收**：深度 6 > 上限 4 |
| Alpha#4 | `(-1 * Ts_Rank(rank(low), 9))` | Ts_Rank→ts_rank；一元负→sub(0,·) | `sub(0, ts_rank(rank(low), 9))` | 4/2/1 | OK | 通过（未超限） |
| Alpha#6 | `(-1 * correlation(open, volume, 10))` | correlation→ts_corr；一元负→sub(0,·) | `sub(0, ts_corr(open, volume, 10))` | 3/2/2 | OK | 通过（未超限） |
| Alpha#12 | `(sign(delta(volume, 1)) * (-1 * delta(close, 1)))` | sign(x)→div(x, abs(x))（x≠0 结构性等价） | `mul(div(delta(volume, 1), abs(delta(volume, 1))), sub(0, delta(close, 1)))` | 5/4/2 | OK | **拒收**：深度 5 > 上限 4；自由参数 4 > 上限 3 |
| Alpha#13 | `(-1 * rank(sum(delta(close, 1), 5)))` | sum(x,n)→ts_mean(x,n)（均值同构替换，量纲差常数 n） | `sub(0, rank(ts_mean(delta(close, 1), 5)))` | 5/3/1 | OK | **拒收**：深度 5 > 上限 4 |
| Alpha#14 | `((-1 * rank(delta(returns, 3))) * correlation(open, volume, 10))` | returns→delta(close,1)；correlation→ts_corr | `mul(sub(0, rank(delta(delta(close, 1), 3))), ts_corr(open, volume, 10))` | 6/4/3 | OK | **拒收**：深度 6 > 上限 4；自由参数 4 > 上限 3 |
| Alpha#15 | `(-1 * sum(rank(correlation(rank(high), rank(volume), 3)), 3))` | correlation→ts_corr；sum→ts_mean | `sub(0, ts_mean(rank(ts_corr(rank(high), rank(volume), 3)), 3))` | 6/3/2 | OK | **拒收**：深度 6 > 上限 4 |
| Alpha#16 | `(-1 * rank(covariance(rank(high), rank(volume), 5)))` | covariance→ts_corr（标准化同族结构性替换） | `sub(0, rank(ts_corr(rank(high), rank(volume), 5)))` | 5/2/2 | OK | **拒收**：深度 5 > 上限 4 |
| Alpha#53 | `(-1 * delta((((close - low) - (high - close)) / (close - low)), 9))` | 四则运算→sub/div；delta 原样 | `sub(0, delta(div(sub(sub(close, low), sub(high, close)), sub(close, low)), 9))` | 6/2/3 | OK | **拒收**：深度 6 > 上限 4 |
| Alpha#101 | `((close - open) / ((high - low) + .001))` | `.001`→`0.001`（DSL 数字语法要求整数部分） | `div(sub(close, open), add(sub(high, low), 0.001))` | 4/1/4 | OK | **拒收**：字段种类 4 > 上限 3 |

**核对结论**：

- **round-trip 10/10**（每条 parse→serialize→parse 后 AST 相等），且 10 条的
  深度/自由参数/字段种类**手算期望与实测全部一致**（脚本内断言）。
- **默认上限拒收 8/10**（与计划「预期全部或绝大多数超限」一致）：深度超限 7 条
  （Alpha#2/#12/#13/#14/#15/#16/#53，其中 2 条同时自由参数超限）、字段种类超限 1 条
  （Alpha#101，深度 4 内仍因 4 字段被拒）。
- **通过 2/10**（Alpha#4、Alpha#6）：均为「单序列深 ≤4、字段 ≤2、参数 ≤3」的简单式——
  如实记录：上限不是「全部拒收」，简单公式在默认路径可直接使用，这佐证上限标定在
  「拒绝复杂的公开因子形态、放行简单形态」的位置上。

## 2. 异常记录与口径注记

| 项 | 状态 | 说明 |
|---|---|---|
| 转写 ≠ 数值等价（核对边界，主动披露） | 已声明 | DSL 初版算子集没有 `sum/covariance/sign/returns/adv20/vwap`；转写按脚本内注释映射做结构性替换，round-trip 只检验**结构契约**，不检验因子数值。数值验证属后续 `add-factor-library-and-monitor`（快照数据 + 求值引擎） |
| D4 口径的连带成本（主动披露） | 已披露 | D4 把**所有** Number 节点计入自由参数 → 一元负的 `sub(0,·)` 改写额外消耗 1 个自由参数（0 也是 Number）。10 条中 Alpha#12/#14 因此出现「深度+自由参数」双违规；这是上限的预期严格性而非缺陷 |
| 手算期望当场被脚本抓错（过程记录） | 已修正 | 初版内嵌 `params` 手算漏计 `sub(0,·)` 的 `0`，脚本断言在 10 条中抓出 9 条不符并 exit 1；按 D4「计全部 Number 节点」修正期望后 10/10 全符、exit 0。**证明脚本断言确有判别力**（不是自证式空跑），也证明「手算 → 运行 → 修正」流程未把错误放行 |
| D5 展平形态超出 D2 元数表（主动披露） | 已钉住 | 规范化把 `add(add(a,b),c)` 展平为三参 `add`——其 serialize 输出**不可再 parse 回读**（D2 元数表只认二参）。该边界已写入 `serialize` docstring：「round-trip 契约只对未规范化的 AST 成立」；规范形态仅用于比较/查重键 |
| window 严格整数形态（口径解释） | 已钉住 | `ts_mean(close, 1.0)` 拒收（字面量 `1.0` 为小数形态）；D2「正整数数字字面量」按最严解释 = 整数形态字面量 + 值 ≥ 1。单测 `test_window_must_be_positive_integer_literal` 钉住（含 `ts_corr` 的 3 号位） |
| 接口外追加（实现注记） | 已披露 | ① `WINDOW_ARG_INDEX`（D2 window 位置表，Interfaces 清单外的新公开常量，供解析器与后续工具复用）；② `DSLParseError.pos`（结构化字符位置属性，`str(exc)` 末尾仍固定 `"，位置 <pos>"`）；③ `_CANONICAL_OPS`（D5 展平/排序算子集，私有） |
| D7 旁路调用点审查 | 已 grep 核实 | `enforce_complexity=False` 在 `src/` 仅出现于 docstring（无任何调用）；实际调用点仅两处：`tests/factors/test_dsl.py` 旁路单测、`tests/validation/2026-10-13-add-factor-dsl-alpha101.py` round-trip 脚本。提案路径（未来 `add-factor-proposal-agent`）目前不存在调用，docstring 明文禁止 |
| 既有测试零修改（红线 4.3） | 见 §0.2 | 本 change 的 T1–T3 三个提交（`d7f41e13`/`c50b1004`/`5a10acb8`）只新增 `src/finance_agent/factors/` 与 `tests/factors/`；范围内 `24787f71..5a10acb8` 另有 screening 三文件改动，属**另一独立提交** `532aad38`（screening review 修复，非本 change 内容）；本 change 未修改任何既有 src/测试文件 |
| live 依赖 | 无 | 本 change 零 IO、零网络；Alpha101 公式为脚本内嵌文本，不受 #325 影响 |

## 3. 结论

**通过项（本报告取证）**：

- [x] `uv run pytest tests/factors -q` → 82 passed（T1 47 + T2 15 + T3 20）
- [x] `uv run pytest tests/factors tests/screening -q` → 137 passed
- [x] `uv run ruff check`（新增/涉及文件）→ All checks passed；`ruff format --check` → 10 files already formatted
- [x] `uv run mypy src/finance_agent/factors src/finance_agent/screening` → Success: no issues found in 5 source files
- [x] Alpha101 round-trip 10/10（手算度量 10/10 相符；默认上限拒收 8/10，逐条原因见 §1）
- [x] `openspec validate add-factor-dsl --strict` → 通过（见提交前执行记录）
- [x] 既有测试零修改（§2 末行）

**未执行项 / 移交**：

- [ ] 因子数值求值验证（DSL 只到结构契约；求值引擎属 `add-factor-library-and-monitor`）
- [ ] 人工终审签字（本报告为自动化执行明细，待 controller/人工复核确认）
