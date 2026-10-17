# DSL ref 扩展 + 翻译批复跑验证报告（extend-factor-dsl-ref）

- 日期：2026-10-17（实现/单测 13:2x；复跑 A 13:35；复跑 B 13:38）
- 变更：`openspec/changes/extend-factor-dsl-ref`（前置与动机 = spike 记录复跑条件「扩 DSL ref 后重跑本批输入」）
- 运行方式：`uv run python tests/scripts/spike/run_translation_import.py --candidates <批> --out <产物>`
- 全量产物：`reports/spike-rdagent-translation-20261017.json`（批 A）+
  `reports/spike-rdagent-translation-batch2-20261017.json`（批 B）（本地留档，不入库）

## 1. DSL ref 契约（TDD 证据）

| 层 | 证据 |
|---|---|
| 解析（`dsl.py`） | 枚举 16 算子逐字钉死；`ref(close,5)` 解析 + round-trip；窗口形态拒收（`0` / `-5` / `x`）；动量复合式解析 |
| 求值（`eval_engine.py`） | `ref(close,2)` 逐标的后移 2 期（前 2 行 NaN、不跨标的）；`ref(x,1)` + `div(close,ref(close,1))` 复合语义 |
| 翻译器（spike 工具） | `Ref` 严格等价（`Ref($close,20)` → `ref(close,20)`）；负/零窗口拒收（前瞻红线）；组合阻塞清单翻转 |
| 回归 | `tests/factors` + `tests/screening` + api 相关：**505 passed**；`ruff` / `mypy` 零新增 |

## 2. 复跑 A：本批 10 候选（同输入重跑，10-16 → 10-17 对照）

| name | qlib_expression | 裁决 | 产物 / 阻塞 |
|---|---|---|---|
| MOM20 | $close / Ref($close, 20) - 1 | 可翻译 | sub(div(close,ref(close,20)),1) |
| VolumeRatio_20 | $volume / Mean($volume, 20) | 可翻译 | div(volume,ts_mean(volume,20)) |
| Momentum_20 | $close / Ref($close, 20) - 1 | 可翻译 | sub(div(close,ref(close,20)),1) |
| Reversal_5 | $close / Ref($close, 5) - 1 | 可翻译 | sub(div(close,ref(close,5)),1) |
| BIAS20 | $close / Mean($close, 20) - 1 | 可翻译 | sub(div(close,ts_mean(close,20)),1) |
| ClosePosition | ($close - $low) / ($high - $low) | 可翻译 | div(sub(close,low),sub(high,low)) |
| VOL20 | Std($close / Ref($close, 1) - 1, 20) | 不可翻译 | 复杂度超限：深度 5 > 上限 4 |
| REV5 | $close / Ref($close, 5) - 1 | 可翻译 | sub(div(close,ref(close,5)),1) |
| Volatility_20 | Std($close / Ref($close, 1) - 1, 20) | 不可翻译 | 复杂度超限：深度 5 > 上限 4 |
| VRATIO20 | $volume / Mean($volume, 20) | 可翻译 | div(volume,ts_mean(volume,20)) |

- 翻译：**8/10**（10-16 为 4/10）——6 条 Ref 阻塞全部解除；剩余 2 条为**复杂度深度上限**，非算子缺口。
- 过闸：0 入库 / 8 拒收（闸② WEAK_ICIR ×6、闸④ NO_POOL_INCREMENT ×2）/ 2 errors（深度超限 → 原始表达式预检失败）。
- 交叉验证：ClosePosition 由 gate① LOW_COVERAGE（10-16）转为 gate②（本次）——当日 high/low 回填生效，
  数据缺口关闭（extend-backfill-high-low 的正向印证）。

## 3. 复跑 B：持续研发新候选批（22 因子，2026-10-17 会话提取）

候选来源：会话日志「Factor information」块 → 22 因子（name/description/formulation）逐条转写 Qlib
表达式（faithful；`-(x-1)` 类代数等价改写已注记）；清单落
`tests/scripts/spike/candidates_2026-10-17.json`。

| name | qlib_expression | 裁决 | 产物 / 阻塞 |
|---|---|---|---|
| ret_1d_reversal | 1 - $close / Ref($close, 1) | 可翻译 | sub(1,div(close,ref(close,1))) |
| mom_5d | $close / Ref($close, 5) - 1 | 可翻译 | sub(div(close,ref(close,5)),1) |
| vol_ratio_5d | $volume / Mean(Ref($volume, 1), 5) - 1 | 不可翻译 | 复杂度超限：深度 5 > 上限 4 |
| range_5d | Mean(($high - $low) / $close, 5) | 可翻译 | ts_mean(div(sub(high,low),close),5) |
| overnight_gap_1d | $open / Ref($close, 1) - 1 | 可翻译 | sub(div(open,ref(close,1)),1) |
| klen_1d | ($high - $low) / $open | 可翻译 | div(sub(high,low),open) |
| intraday_ret_1d | $close / $open - 1 | 可翻译 | sub(div(close,open),1) |
| vol_ratio_20d | $volume / Mean(Ref($volume, 1), 20) - 1 | 不可翻译 | 复杂度超限：深度 5 > 上限 4 |
| roc_20d | $close / Ref($close, 20) - 1 | 可翻译 | sub(div(close,ref(close,20)),1) |
| ret_5d_reversal | 1 - $close / Ref($close, 5) | 可翻译 | sub(1,div(close,ref(close,5))) |
| mom_20d | $close / Ref($close, 20) - 1 | 可翻译 | sub(div(close,ref(close,20)),1) |
| park_vol_10d | Sqrt(Mean(Power(Log($high / $low), 2), 10)) | 不可翻译 | 未知算子 'Sqrt'（映射表无记录） |
| amihud_illiq_10d | 1000000 * Mean(Abs($close / Ref($close, 1) - 1) / ($volume * $close), 10) | 不可翻译 | 复杂度超限：深度 8 > 上限 4；自由参数 4 > 上限 3 |
| vol_ratio_10d | $volume / Mean(Ref($volume, 1), 10) - 1 | 不可翻译 | 复杂度超限：深度 5 > 上限 4 |
| max_ret_20d | Max($close / Ref($close, 1) - 1, 20) | 不可翻译 | 算子 Max：pyqlib 滚动窗口最大值 vs 本系统逐元素——同名不同义 |
| amihud_illiq_20d | 1000000 * Mean(Abs($close / Ref($close, 1) - 1) / ($volume * $close), 20) | 不可翻译 | 复杂度超限：深度 8 > 上限 4；自由参数 4 > 上限 3 |
| rev_5d | 1 - $close / Ref($close, 5) | 可翻译 | sub(1,div(close,ref(close,5))) |
| clv_5d | Mean((2 * $close - $high - $low) / ($high - $low + 0.000001), 5) | 不可翻译 | 复杂度超限：深度 6 > 上限 4 |
| dev_vwap_5d | $close / (Sum($volume * $close, 5) / Sum($volume, 5)) - 1 | 不可翻译 | 算子 Sum：滚动求和——本 DSL 无 |
| hl_range_1d | ($high - $low) / $close | 可翻译 | div(sub(high,low),close) |
| overnight_gap_5d | Mean($open / Ref($close, 1) - 1, 5) | 不可翻译 | 复杂度超限：深度 5 > 上限 4 |
| vwap_dev_5d | $close / (Sum($volume * $close, 5) / Sum($volume, 5)) - 1 | 不可翻译 | 算子 Sum：滚动求和——本 DSL 无 |

- 翻译：**11/22**；阻塞归因 = 复杂度深度上限 ×7、Sum 缺失 ×2、Sqrt 未知 ×1、Max（滚动）同名不同义 ×1。
- 过闸：0 入库 / 11 拒收（全部闸② WEAK_ICIR）/ 11 errors（不可翻译 → 原始表达式预检失败）。

## 4. 合计读数与结论

- **翻译覆盖：32 条候选 19 条可翻译（59%）**。ref 单点解锁后，主导阻塞已从算子缺口转为
  **复杂度深度上限（9/32 = 28%）**——深度 5 形态多为「滚动统计量套一层价量比值/差值」的
  自然因子（如 5 日均隔夜跳空 `ts_mean(div(open,ref(close,1)),5)`）。
- **过闸：0/19 入库**——闸② |ICIR| ≥ 0.3 门槛下，经典动量/反转/日内/区间族全部未达标；
  与 10-16 spike 结论一致（真实读数；闸门标定已独立验证，不放宽阈值）。
- 供给线状态（承接 spike ⑥）：**导入通道 + 翻译器维持「验证可用、不自动投用」**。
  下一步决策输入（各属策略变更，各立 delta）：① 深度上限 4 → 5 的取舍（证据 9/32）；
  ② 滚动 Sum / Sqrt 等算子补齐（证据 3/32）；③ 闸②门槛本身的复审（当前 0 通过率是
  设计意图还是门槛过严——需专门实验，不在本报告结论内）。

## 异常记录

- 复跑以**临时库**执行（`FACTOR_LIBRARY_DB_PATH=data/spike-ref-rerun-20261017.db`）——证据运行
  不触碰生产因子库（生产库 mtime 10-15 未变）；临时库已删除，读数全量留档于 `reports/`。
- 转写为**人工（agent）逐条转写**（与 10-16 批同工艺）——「持续研发 → 自动收割」的转写自动化
  仍属未决项（收割桥），本报告不越界。

## 结论

- [x] DSL ref 契约通过（TDD 全绿 + 505 回归绿）；复跑读数落档；供给线维持不自动投用
      （待深度/算子/门槛三线决策）
