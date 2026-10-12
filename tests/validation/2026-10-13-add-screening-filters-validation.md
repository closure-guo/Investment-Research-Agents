# 人工验证报告: add-screening-filters

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-screening-filters/`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 1–3（L1 确定性硬过滤纯函数 + 单一来源约束 + 验证收口）

> **硬约束声明（只记录实际发生）**：本机东财行情域 IP 封禁（incident 033 / issue #325）持续生效，
> `AKShareClient` 拿不到真实行情与快照，spec 任务 3.2「2026-09 全月过滤结果抽 50 例人工核对」
> **无法用真实快照数据执行**。§1 核对表为 **fixture 手工构造 + 独立手算期望**的替代证据，
> 未把受阻项伪装成实盘核对；live 全月核对挂 follow-up（issue #325）。

## 0. 命令证据（当场跑、逐字记录）

### 0.1 指定测试命令

```
$ uv run pytest tests/screening -q
41 passed in 0.59s
```

### 0.2 全量测试（排除 live）

```
$ uv run pytest tests/ -q -m "not live"
1 failed, 4818 passed, 6 skipped, 12 deselected, 30 warnings in 747.66s (0:12:27)
（失败用例：tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default）
```

唯一失败归因（如实记录，不掩饰）：**既有环境性失败，非本 change 引入**——① 单独运行该用例
`uv run pytest "tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default" -q`
→ `1 passed in 0.06s`（全量运行时受同进程环境污染）；② 同一失败在上一轮全量回归
（`2026-10-13-add-matching-engine-validation.md`，4777 passed 那轮）已出现并归因为宿主 `.env`
泄漏 `LLM_MAX_CONTEXT`；③ 本 change 的 diff（`git diff 52cdec5e..HEAD --name-only`）不触碰
`llm/` 任何文件；④ 交叉验证：4818 − 4777 = 41 = 本 change 新增 `tests/screening` 用例数。
归因为既有环境问题，不阻塞本 change。

取证诚实性补记：全量套件运行期间其后仅发生 `filters.py` 两处 **docstring 措辞微调**（零行为
变更，未触及任何逻辑行）；`tests/screening` 已在微调后的最终代码上复跑通过（§0.1 为最终代码结果）。

### 0.3 Lint 与类型检查（改动文件）

```
$ uv run ruff check src/finance_agent/screening tests/screening tests/validation/2026-10-13-add-screening-filters-cases.py
All checks passed!
```

```
$ uv run mypy src/finance_agent/screening
Success: no issues found in 3 source files
```

### 0.4 50 例核对脚本执行（逐字尾部输出）

```
$ uv run python tests/validation/2026-10-13-add-screening-filters-cases.py
------------------------------------------------------------------------------------------------
匹配 50/50；不一致 0
六码覆盖（期望命中例数）：ST_FLAG=8；SUSPENDED=8；IPO_RECENT=9；LIMIT_LOCKED=10；ILLIQUID=9；DATA_MISSING=13
全部 50 例与手算期望一致

$ echo $?
0
```

## 1. 验证结果表（50 例，fixture + 独立手算）

构造方式：`tests/validation/2026-10-13-add-screening-filters-cases.py` 内 50 例 fixture
（代码/标志位/上市天数/均额/日历/成交额序列），**期望原因码逐例手算后硬编码**（不从被测函数
推导），经 `filter_universe` 逐行比对。分组：A 通过基线 5 / B ST_FLAG 4 / C SUSPENDED 3 /
D IPO 边界 7 / E LIMIT_LOCKED 5 / F ILLIQUID 边界 7 / G DATA_MISSING 变体 10 /
H 构造链（build_candidate_record）9。

代表例子（§0.4 全量 50/50 一致；完整表见脚本打印输出与本文件附录说明）：

| # | 用例 | 手算期望原因码 | 实际 | 手算依据（独立于实现） |
|---|---|---|---|---|
| 1 | A01 全字段干净：60日/恰等于阈值 | () | 同手算 | 60 ≥ 60 且恰等于阈值不剔除（「低于」语义）；无其他命中 |
| 4 | A04 涨停触板未封死 + 60日/恰阈值 | () | 同手算 | D3：high 触板但未封死 → sealed 位 False，不进 LIMIT_LOCKED |
| 8 | B08 ST + 停牌 | (ST_FLAG, SUSPENDED) | 同手算 | 两标志位同真；声明顺序 ST 在 SUSPENDED 前 |
| 13 | D13 上市59日边界剔除 | (IPO_RECENT) | 同手算 | 59 < 60（交易日口径） |
| 14 | D14 上市60日边界通过 | () | 同手算 | 60 不满足「< 60」 |
| 15 | D15 上市61日通过 | () | 同手算 | 61 > 60 |
| 17 | D17 59日+停牌+一字涨停+均额低于 | (SUSPENDED, IPO_RECENT, LIMIT_LOCKED, ILLIQUID) | 同手算 | 四规则命中，按枚举声明顺序排列（无 DATA_MISSING） |
| 22 | E22 涨跌停双封位同真 | (LIMIT_LOCKED) | 同手算 | 两方向同一码，只出现一次 |
| 23 | E23 触板未封死（sealed 位均 False） | () | 同手算 | 同 A04 |
| 25 | F25 均额恰等于阈值 | () | 同手算 | 50,000,000.0 < 50,000,000.0 为假 |
| 26 | F26 均额 = 阈值 − 0.01 | (ILLIQUID) | 同手算 | 49,999,999.99 < 50,000,000.0 |
| 34 | G34 天数与均额都缺失 | (DATA_MISSING) | 同手算 | 同一码只报一次 |
| 39 | G39 ST+停牌+天数缺失+双封+均额缺失 | (ST_FLAG, SUSPENDED, LIMIT_LOCKED, DATA_MISSING) | 同手算 | 缺失两项均归入末位 DATA_MISSING；不虚构 IPO/ILLIQUID |
| 43 | H43 构造链：19 笔成交额 | (DATA_MISSING) | 同手算 | 历史 < 20 交易日 → 均额 None |
| 45 | H45 构造链：25 笔取最后 20 笔 | (ILLIQUID) | 同手算 | 前 5 笔 10 亿不参与（口径：只看近 20 日）；末 20 笔 = 阈值 − 0.01 |
| 48 | H48 构造链：list_date 不在日历 | (DATA_MISSING) | 同手算 | 日历未覆盖上市日 → 天数 None（fail-closed） |

**核对结论：50/50 全部一致，0 不一致。** 覆盖矩阵：六码均有正例与反例；边界含 59/60/61 交易日、
均额恰等于阈值 ±0.01/0、一字封死 vs 触板未封死、DATA_MISSING 单缺/双缺/叠加他因；
多因同单钉住枚举声明顺序（ST → SUSPENDED → IPO_RECENT → LIMIT_LOCKED → ILLIQUID → DATA_MISSING）。

## 2. 异常记录

| 项 | 状态 | 说明 |
|---|---|---|
| live 2026-09 全月过滤 + 真实快照抽 50 例（spec 任务 3.2 原口径） | **未执行（受阻）** | 本机东财 IP 封禁（incident 033 / issue #325）→ 无真实快照可跑；§1 以手工构造 50 例 + 独立手算期望替代，实盘核对挂 issue #325 follow-up |
| F2 非有限值检查范围（口径解释，主动披露） | 已钉住 | 计划 F2 文本「取最后至多 20 个 … 出现非有限值 → None」按最小解释：检查**判定窗口**（最后至多 20 个）；窗口外更早日期的非有限值不影响「近 20 日」均值（`test_avg_amount_20d_non_finite_before_window_ignored` 钉住）。50 例中 H45 钉住「只取最后 20 个」 |
| F1 list_date 必须在日历内（口径解释，主动披露） | 已钉住 | 计划测试项明文「list_date 不在日历 → None」；生产日历为全历史交易日历，覆盖所有在榜标的上市日，该检查为数据完整性 fail-closed（H48 钉住） |
| `RejectReason` 基类（实现注记） | 已披露 | 计划 Interface 写作 `class RejectReason(str, enum.Enum)`；ruff UP042 拒收该写法且仓库既有惯例为 `enum.StrEnum`（`harness/types.py`）→ 采用 `StrEnum`。成员名/值/声明顺序/`isinstance(member, str)` 与 `str, enum.Enum` 逐字等价（`test_enum_members_verbatim` 钉住） |
| T2 红→绿演示方式（过程记录） | 已披露 | T1 已按 F4 在调用期读 `constraints.DEFAULT_THRESHOLDS`，Task 2 的钉住用例首跑即绿（计划原文即允许「若 T1 已按 F4 做则只需确认」）。为证明用例确有判别力，做了**临时突变**：把默认阈值改为导入期固化（`_IMPORT_BOUND_DEFAULTS`）→ 5 例中 3 例失败（流动性/上市天数两个 override 用例 + 显式覆盖用例）→ `git checkout` 还原后 41 passed（突变未入库，工作区与提交均无残留） |
| 其他不一致 / 跳过的行 | 无 | 50 例全跑、0 例被手工放行或跳过 |

## 3. F4 冻结区提示（生产选股流动性预热期）

按计划 F2/冻结区口径：近 20 日成交额历史不足 `MIN_LIQUIDITY_HISTORY_DAYS`（=20 个交易日）时
→ `DATA_MISSING`（fail-closed 剔除，宁可漏选不可误选）。**后果**：生产快照上线后，选股需再积累
约 20 个交易日才会给出有意义的流动性判定；该窗口期内 L1 过滤会对绝大多数标的报 `DATA_MISSING`，
选股实质不可用属预期行为而非缺陷。后续 pipeline change（screening 编排/快照上线）必须按此排期，
或在窗口期显式提示运营侧，不得通过放宽该检查「绕过预热」。

## 4. 结论

**通过项（本报告取证）**：

- [x] `uv run pytest tests/screening -q` → 41 passed（Task 1 单测 36 + Task 2 单点场景 5）
- [x] 50 例人工核对（fixture + 手算期望）→ 50/50 一致，0 异常（§1/§0.4）
- [x] `uv run ruff check`（新增文件）→ All checks passed；`uv run ruff format --check` → 已格式化
- [x] `uv run mypy src/finance_agent/screening` → Success: no issues found in 3 source files
- [x] 全量测试 `-m "not live"` → 见 §0.2（唯一失败为既有环境性 flake，非本 change 引入）
- [x] 既有测试零修改（红线 3.3）：本 change 全部为新增文件；唯一既有文件改动为本 delta 自身的
  `tasks.md` 回填（条目文本保留，仅勾选 + 注记）

**未执行项**：

- [ ] 真实 2026-09 快照全月过滤 + 真实标的抽样核对（东财 IP 封禁，issue #325，挂 follow-up）
