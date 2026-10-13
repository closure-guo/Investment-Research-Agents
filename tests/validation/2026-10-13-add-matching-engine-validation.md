# 人工验证报告: add-matching-engine

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-matching-engine/`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 7（验证与收口）+ 评审修正 A1–A4（md 报告披露未成交计数与费用口径 / numpy 真值歧义 / fixture 注释校正）

> **硬约束声明（只记录实际发生）**：本机东财行情域 IP 封禁（incident 033 / issue #325）持续生效，无法拉取真实历史行情挑选 10 个真实涨跌停交易日。§1 核对表为 **fixture 构造 + 独立手算期望**的替代证据，未把受阻项伪装成实盘核对；实盘数据核对挂 follow-up（issue #325）。

## 0. 命令证据（当场跑、逐字记录）

### 0.1 指定测试命令

```
$ uv run pytest tests/execution tests/evals/backtest tests/outcome/test_track_record_judgment.py -q
............................................... [100%]
335 passed in 7.33s
```

### 0.2 Lint 与类型检查（改动文件）

```
$ uv run ruff check src/finance_agent/execution evals/backtest/report.py evals/backtest/run_backtest.py \
    tests/execution/test_matching.py tests/evals/backtest/test_report_render.py \
    tests/evals/backtest/test_run_backtest.py tests/evals/backtest/test_replay.py
All checks passed!
```

```
$ uv run mypy src/finance_agent/execution
Success: no issues found in 4 source files
```

### 0.3 全量测试（排除 live）

```
$ uv run pytest tests/ -q -m "not live"
FAILED tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default
1 failed, 4777 passed, 6 skipped, 12 deselected, 30 warnings in 784.63s (0:13:04)
```

唯一失败归因（如实记录，不掩饰）：**既有环境性失败，非本 change 引入**——① 单独运行该用例
`uv run pytest "tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default" -q`
→ `1 passed in 0.06s`（全量运行时受同进程环境污染）；② 同一失败在上一次全量回归
（`2026-10-13-add-market-snapshot-validation.md` 补录，4677 passed 那轮）已出现并归因为宿主 `.env`
泄漏 `LLM_MAX_CONTEXT`；③ 本 change 的 diff 不触碰 `llm/` 任何文件。归因为既有环境问题，不阻塞本 change。

### 0.4 openspec 校验（只读；archive 由控制器执行）

```
$ openspec validate add-matching-engine --strict
Change 'add-matching-engine' is valid
```

## 1. 10 个涨跌停/滑点案例人工核对表

构造方式：10 组 fixture 输入（代码/side/qty/prev_close/OHLC/成交额样本），**手算期望独立计算后写死**
在 `tests/validation/2026-10-13-add-matching-engine-cases.py`（期望值不以被测函数推导）；同一输入喂
`finance_agent.execution.matching` 后打印「手算 vs 引擎」逐字段对照（filled_qty / fill_price / fee /
reason / slippage_rate / sealed 位）。

默认口径（与 `config/matching_engine.json` 逐字一致）：佣金 0.00025（最低 5 元）、印花税 0.0005（仅卖出）、
三档滑点 0.2→0.002 / 0.6→0.001 / 1.0→0.0005；全市场成交额样本 10 档（100…1000），分位 = count(样本<=amount)/10。

| # | 场景 | fixture 输入 | 手算期望（sealed；成交；原因码；价；费；滑点） | 引擎输出 | 一致 | 手算依据（独立于引擎） |
|---|---|---|---|---|---|---|
| 1 | 主板 10% 一字涨停买单 | 600519 buy 100；prev=100；o=h=l=c=110；amount=None；样本=None | (True,False)；未成交；LIMIT_UP_UNFILLABLE；None；0.0；0.0 | 同手算 | ✅ | 110/100−1=+10% ≥ 4.8% 且 o=h=l=c → 一字涨停；买单被拒（fail-closed） |
| 2 | 创业板 20% 一字涨停买单 | 300750 buy 100；prev=100；o=h=l=c=120；amount=None；样本=None | (True,False)；未成交；LIMIT_UP_UNFILLABLE；None；0.0；0.0 | 同手算 | ✅ | 120/100−1=+20% ≥ 4.8% 且 o=h=l=c → 一字涨停；买单被拒 |
| 3 | ST 5% 一字涨停买单 | 600001 buy 100；prev=10；o=h=l=c=10.5；amount=None；样本=None | (True,False)；未成交；LIMIT_UP_UNFILLABLE；None；0.0；0.0 | 同手算 | ✅ | 10.5/10−1=+5% ≥ 4.8%（ST 档被 4.8 阈值覆盖）→ 一字涨停；买单被拒 |
| 4 | ST 5% 一字涨停卖单 | 600001 sell 100（可卖=总=100）；prev=10；o=h=l=c=10.5；amount=None；样本=None | (True,False)；成交 100；—；10.48；5.52；0.002 | 同手算 | ✅ | 涨停不阻卖；价=10.5×(1−0.002)=10.479→10.48；佣金 max(5, 1048×0.00025)=5；印花税 1048×0.0005=0.52；费 5.52 |
| 5 | 一字跌停卖单 | 600519 sell 100（可卖=总=100）；prev=100；o=h=l=c=90；amount=None；样本=None | (False,True)；未成交；LIMIT_DOWN_UNFILLABLE；None；0.0；0.0 | 同手算 | ✅ | 90/100−1=−10% ≤ −4.8% 且 o=h=l=c → 一字跌停；卖单被拒 |
| 6 | 一字跌停买单 | 600519 buy 100；prev=100；o=h=l=c=90；amount=500；样本=10 档 | (False,True)；成交 100；—；90.09；5.0；0.001 | 同手算 | ✅ | 跌停不阻买；分位=5/10=0.5→中档 0.001；价=90×1.001=90.09；佣金 max(5, 9009×0.00025)=5；买入无印花税 |
| 7 | 正常日 × 低流动性档（q=0.2 恰在切点） | 600519 buy 800；prev=100；o=101,h=102.5,l=100.5,c=102；amount=200；样本=10 档 | (False,False)；成交 800；—；101.2；20.24；0.002 | 同手算 | ✅ | o≠h → 非一字；分位=2/10=0.2 ≤ 0.2 → 低档 0.002；价=101×1.002=101.2；佣金 max(5, 80960×0.00025=20.24) |
| 8 | 正常日 × 中档滑点 | 600519 buy 800；prev=100；o=101,h=102.5,l=100.5,c=102；amount=500；样本=10 档 | (False,False)；成交 800；—；101.1；20.22；0.001 | 同手算 | ✅ | 分位=5/10=0.5 ≤ 0.6 → 中档 0.001；价=101×1.001=101.1；佣金 max(5, 80880×0.00025=20.22) |
| 9 | 正常日 × 高流动性档 | 600519 buy 800；prev=100；o=101,h=102.5,l=100.5,c=102；amount=900；样本=10 档 | (False,False)；成交 800；—；101.05；20.21；0.0005 | 同手算 | ✅ | 分位=9/10=0.9 > 0.6 → 高档 0.0005；价=101×1.0005=101.05；佣金 max(5, 80840×0.00025=20.21) |
| 10 | 零股卖出例外（350 股 = 持仓总量） | 600519 sell 350（可卖=总=350）；prev=20；o=20.5,h=21,l=20.2,c=20.8；amount=900；样本=10 档 | (False,False)；成交 350；—；20.49；8.59；0.0005 | 同手算 | ✅ | 350 非整手但 = 总持仓 → 允许全卖；分位 0.9 → 档 0.0005；价=20.5×0.9995=20.48975→20.49；佣金 max(5, 7171.5×0.00025)=5；印花税 7171.5×0.0005=3.59；费 8.59 |

脚本执行（逐字尾部输出）：

```
$ uv run python tests/validation/2026-10-13-add-matching-engine-cases.py
...
结果：10/10 例逐字段一致（filled_qty / fill_price / fee / reason / slippage / sealed）
```

**核对结论：10/10 全部一致，无异常记录。** 覆盖：主板 10% 一字涨停买单、创业板 20% 一字涨停买单、
ST 5% 一字涨停买/卖各一、一字跌停卖单/买单各一、正常日 × 三档滑点各一、零股卖出例外一例；
其中 #7 同时钉住「分位恰在切点取更保守档」的边界口径。

## 2. 既有测试修改披露（红线 3.3）

`tests/evals/backtest/test_replay.py` **13 行 fixture 适配**，涉及既有用例 8 个（直接改动行的 7 个 +
经 `_horizon_rows` 间接受影响的 1 个：`test_replay_neutral_action_maps_avoidance`，其调用行未改、
断言依赖夹具经 D+1 滑点反推后成交价仍 = 11.0）：

| 行类别 | 行数 | 内容 | 理由 |
|---|---|---|---|
| fixture 数据/签名 | 6 | `_horizon_rows` 签名加 `side` 参数 + 返回插入 D+1 行、`_horizon_rows(11.0, 9.9)` 调用补 `side="sell"`、`_kline` 三处补 D+1 日期（2→3 日，L165/L296/L319） | 入场语义由「决策日收盘」改为「D+1 开盘撮合」，缺 D+1 行会静默走「无结算」分支覆盖不到目标断言 |
| 桩签名 | 2 | `fake_resolve(prediction, kline, benchmark)` → 增加 `*, entry_override=None`（两处） | `resolve_prediction` 新增关键字参数（纯加参），桩需接受新签名 |
| 注释/docstring | 5 | 模块 docstring、`_dated_kline`/`_horizon_rows` docstring、一处行内注释、一处用例 docstring | 描述新入场语义与夹具口径 |

**0 行断言改动**（复核方式：`git diff <delta 起点>..HEAD -- tests/evals/backtest/test_replay.py`
的全部 13 行删除/替换行中，含 `assert` 者 0 行）；`tests/outcome/test_track_record_judgment.py`
（+63）、`tests/evals/backtest/test_run_backtest.py`（+57）与 `tests/execution/*`（新文件）均为纯追加。
评审已逐行审计并判定诚实。其余既有测试零修改通过。

## 3. 受阻与未执行项（如实记录）

| 项 | 状态 | 说明 |
|---|---|---|
| 真实历史行情挑选 10 个真实涨跌停交易日 | **未执行（受阻）** | 本机东财 IP 封禁（incident 033 / issue #325）→ `AKShareClient` 历史行情返回失败，无真实涨跌停样本可选；§1 以 fixture 构造 + 独立手算期望作替代证据。实盘数据核对挂 follow-up（issue #325） |
| 全量测试含 `@live` 用例 | **未执行** | 按仓库分层纪律 live 用例 nightly 单独触发，不进本次门禁；本报告 0.3 为 `-m "not live"` |

## 4. 结论

**通过项（本报告取证）**：

- [x] 指定子集 `tests/execution tests/evals/backtest tests/outcome/test_track_record_judgment.py` → 335 passed
- [x] `uv run ruff check`（改动文件）→ All checks passed
- [x] `uv run mypy src/finance_agent/execution` → Success: no issues found in 4 source files
- [x] 全量测试 `-m "not live"` → 4777 passed；唯一失败为既有环境性失败（§0.3 已归因，不阻塞）
- [x] 10 个涨跌停/滑点案例人工核对（fixture + 手算）→ 10/10 一致
- [x] 既有测试披露：13 行 fixture 适配（8 用例，0 断言行，评审逐行审计判定诚实）

**未执行项**：

- [ ] 真实历史行情涨跌停案例核对（东财 IP 封禁，incident 033 / issue #325，挂 follow-up）
