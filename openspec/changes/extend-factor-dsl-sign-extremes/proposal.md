# Proposal: extend-factor-dsl-sign-extremes

## Why

PR #405 复盘（2026-10-19）把「LLM 拒译族」钉死为供给线第二大损耗：40 条不可翻译中 21 条
死于**转译 LLM 按算子清单显式拒译**，其中两族缺口边界清晰、语义无歧义：

| 缺口族 | 条数 | 真实候选 | 需要的算子 |
|---|---|---|---|
| 符号/条件判断 | 5 | `obv5`（能量潮）、`updays5`（上涨天数）、`mfi14` 等 | `sign`（正 1/负 −1/零 0） |
| 滚动极值 | 3 | `pos20`（价格位置）、`maxret20`、`max_ret_5d` | `ts_max`/`ts_min`（N 期最大/最小） |

映射表侧的对应边界：`Max`/`Min` 当前标 not_equivalent（pyqlib 为滚动算子、本 DSL `max`/`min`
为逐元素二元——同名不同义，拒绝机械替换是对的）；`Sign` 标 missing。补齐这三个算子后，
pyqlib `Max/Min/Sign` 全部翻为严格等价映射，转译 prompt 的算子清单同步放开。

先例：`ref`（#365）、`ts_sum`+`sqrt`（#369）两次同类扩展均实测解锁批次（翻译覆盖 4/10→91%）。

## What Changes

- **MODIFIED「因子表达式可解析契约」（factor-dsl）**：算子枚举追加 `sign`（符号，NaN 传播）、
  `ts_max`/`ts_min`（N 期滚动最大/最小，窗口含当日，窗口不足或含 NaN → NaN——与 ts_mean
  同 min_periods=window 口径）。
- **实现面**：`dsl.py`（OPS/ARITY/WINDOW_ARG_INDEX）、`eval_engine.py`（向量化实现）、
  `qlib_translate.py`（`Max`/`Min` not_equivalent→equivalent（映射 `ts_max`/`ts_min`）、
  `Sign` missing→equivalent）、`prompts/factor_transcription.md`（算子清单 + 拒译反例改写）
  并按 prompt-deploy-consistency 发布。
- **不做**：`Skew`/`Kurt`（滚动高阶矩，`skew20` 的缺口——矩法展开超自由参数上限，另议）；
  比较指示算子（`updays5`/`mfi14` 的 `1{x>y}` 形态——可先尝试让 LLM 用 `sign` 组合表达，
  若拒译再评估）；复杂度上限与闸门零改动。

## Capabilities

- **Modified Capabilities**: `factor-dsl`（因子表达式可解析契约）

## Impact

- 代码：`factors/{dsl,eval_engine,qlib_translate}.py`、`prompts/factor_transcription.md`
- 测试：`tests/factors/test_dsl.py`（新算子解析/求值）、`tests/factors/test_eval_engine.py`
  （语义）、`tests/factors/test_qlib_translate.py`（映射翻转 + 复合形态）
- **预期管理（预设判据）**：解锁 ≠ 入库——#405 恢复的 6 条全倒闸②（|ICIR| 0.069–0.273）。
  本扩展的判据 = 重放读数：21 条 LLM 拒译族在新清单下的可转译数 + 过闸读数分布；
  若 sign/极值族读数普遍 <0.15，该方向关闭，不再扩。
