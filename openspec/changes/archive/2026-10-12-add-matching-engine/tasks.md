# Tasks: add-matching-engine

## 1. 撮合引擎纯函数
- [x] 1.1 实现订单→成交结果纯函数（T+1/涨跌停/整数倍/成本）+ 逐约束单测（每约束正反各至少 1 例）
  - 落地 `src/finance_agent/execution/matching.py`：`match_order` 校验顺序 ODD_LOT → 涨跌停 → T+1（M3），成交价 `open×(1±滑点)` round 2（M1），费用 = 佣金（max(最低 5 元, 成交额×0.00025)）+ 印花税（仅卖出 0.0005）（M5）；原因码逐字 `LIMIT_UP_UNFILLABLE/LIMIT_DOWN_UNFILLABLE/T1_SELL_BLOCKED/ODD_LOT`。单测 `tests/execution/test_matching.py`（每约束正反例、校验顺序、`qty<=0`/非法 side → ValueError；数值全部独立手算）。**实施期裁定（已回填计划「评审修正记录」）**：一字板阈值判定含边界（`>= ±4.8%`，与 `data/snapshot.py` D3 同源）；`prev_close <= 0` 同 `None` 处理；卖单零股全卖例外。
- [x] 1.2 实现分档滑点（按成交额分位）+ 参数外置配置 + 分档边界单测
  - `slippage_rate_for_amount`：分位 = `count(样本<=amount)/len(样本)`，恰在切点取更保守（更大滑点）档；缺失样本 → **费率最大档**（默认三档下 = 0.002，M4 实施期修正）。配置外置 `config/matching_engine.json` + `src/finance_agent/execution/config.py`（env `MATCHING_CONFIG_PATH` 覆盖，显式 path 优先；三档 0.002/0.001/0.0005、佣金/印花税逐字）。单测 `tests/execution/test_matching_config.py`（默认/覆盖整链/切点边界/缺文件/非法内容）。**偏离（实施期裁定，见计划「评审修正记录」3）**：配置错误契约采「文件缺失 → 默认 + WARN；文件存在但内容非法（坏 JSON/负费率/档位非升序或末档≠1.0）→ `ValueError` fail-loud」，原「坏 JSON/负费率 → 回落默认 + WARN」不采用（配置错误不静默）。

## 2. 三模式接入
- [x] 2.1 定义 backtest/paper/live 三模式数据源接口 + live 仅留 NotImplemented 抽象
  - `src/finance_agent/execution/modes.py`：`MarketDataSource` Protocol + `BacktestDataSource`（hfq 全量 K 线行构造 bar，`prev_close` 取上一行收盘，sealed 位走 M2）/ `PaperDataSource`（`market_snapshot` 真实盘面价目 bar）/ `LiveDataSource`（全方法 `NotImplementedError("实盘接口未实现…")`）+ `match_for_mode`（backtest/paper 同一 `match_order`；live NotImplementedError；未知 mode ValueError）。单测 `tests/execution/test_modes.py`：跨模式一致性（同数值两源 → `dataclasses.astuple` 逐字段相等）、缺数据 None、live/未知 mode 报错。
- [x] 2.2 `evals/backtest/` 成交计算切换至引擎 + 同一订单跨模式一致性单测
  - `evals/backtest/replay.py`：入场切 D+1 bar（无 D+1 → 既有「无结算」返回；缺「开盘」列 → 收盘回落 + `fill.reference="close_fallback"`）→ `match_order`（M6 卖单视作隔日仓位；未成交 fail-closed → `settlement=None` + `unfilled_reason`，绝不按涨停价假设成交）→ 成交价经 `resolve_prediction(entry_override=(D+1, 成交价))` 结算（judgment.py 纯加参，判定数学零改动）。`evals/backtest/run_backtest.py`：`unfilled_counts` 聚合（M7）+ methodology 披露（含 `costs` 费用口径；md 报告渲染未成交计数与成本口径，评审修正 A1/A2）。跨模式一致性单测见 `tests/execution/test_modes.py`（T3 同款场景）；回放/聚合侧既有测试零断言改动通过。

## 3. 验证
- [x] 3.1 全量测试通过
  - 见验证报告 §0：指定子集 `uv run pytest tests/execution tests/evals/backtest tests/outcome/test_track_record_judgment.py -q` → **335 passed**；全量 `uv run pytest tests/ -q -m "not live"` → 结果逐字落报告 §0.3；`uv run ruff check`（改动文件）→ All checks passed；`uv run mypy src/finance_agent/execution` → Success: no issues found in 4 source files。
- [x] 3.2 10 个涨跌停交易日案例人工核对，报告落 `tests/validation/`
  - 报告 `tests/validation/2026-10-13-add-matching-engine-validation.md` §1：10 组 fixture（主板/创业板一字涨停、ST 5% 买卖、一字跌停买卖、正常日×三档滑点、零股卖出例外）+ 独立手算期望，脚本 `tests/validation/2026-10-13-add-matching-engine-cases.py` 对照 → **10/10 逐字段一致**。**受阻如实标注**：本机东财 IP 封禁（incident 033 / issue #325）无法拉真实历史行情挑选真实涨跌停交易日，本表为 fixture 构造 + 手算期望的替代证据；实盘数据核对挂 follow-up（issue #325）。
- [x] 3.3 既有测试无修改通过（红线；backtest 内部替换如需调整断言，单独记录变更范围与理由）
  - **披露（评审已逐行审计并判定诚实）**：`tests/evals/backtest/test_replay.py` 共 **13 行 fixture 适配**（8 个既有用例；改的是 fixture 数据/桩签名/注释，**0 行断言改动**——复核：13 行替换行中 0 行含 `assert`）。理由：入场语义由「决策日收盘」改为「D+1 开盘撮合」——`_horizon_rows`/`_kline` 需插入 D+1 交易日行（否则静默走「无结算」分支）、`resolve_prediction` 桩需接受新增关键字参数 `entry_override`、注释/docstring 同步新语义。`tests/outcome/test_track_record_judgment.py`（+63）与 `tests/evals/backtest/test_run_backtest.py`（+57）、`tests/execution/*`（新文件）均为纯追加。其余既有测试零修改通过。
