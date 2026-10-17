# 因子 DSL 翻译覆盖收口：深度 5→6 + ts_sum + sqrt + Power(·,0.5) 特例（extend-factor-dsl-coverage）

## Why

第三轮复跑（深度 4→5 之后）把剩余阻塞的**联合结构**暴露出来：批 B 15/22 之外的可翻译候选，
几乎全部需要**多条变更同时到位**才能解锁——单独任一变更收益为 0 或 +1（实测）：

| 剩余阻塞（批 B 7 条） | 需要 |
|---|---|
| `clv_5d`（CLV 平滑，深度 6） | 深度 6 |
| `dev_vwap_5d` / `vwap_dev_5d`（VWAP 偏离，深度 6 + Sum） | 深度 6 **且** `ts_sum` |
| `park_vol_10d`（Parkinson 波动率，深度 6 + Power） | 深度 6 **且** 平方根路径（`Power(·,0.5)` 特例） |
| `amihud_illiq_10d/20d`（深度 8 + 自由参数 4） | 越界（本轮明确不开，留残余清单） |
| `max_ret_20d`（滚动 Max 同名不同义） | 独立算子线（本轮不开） |

**归因纪律说明**：本 delta 破例一次携带三条变更（深度 6 / `ts_sum` / `sqrt` + 特例），因为它们
**联合必要**——归因不依赖统计推断，而是**机械逐候选**：翻译器逐条输出阻塞清单，每条候选解锁与否
可精确对到具体变更（报告含全量对照表）。拆成三个独立 PR 会产出两个「收益 0」的中间态，不增加信息。

**归因更正（本轮侦察发现）**：`park_vol_10d` 此前记为「Sqrt 缺失 ×1」——不准确：pyqlib 0.9.7
**没有 `Sqrt` 算子**（ops.py 实测无此类），此前转写的 `Sqrt(...)` 不是合法 qlib 表达式；其合法
转写为 `Power(Mean(Power(Log($high / $low), 2), 10), 0.5)`，实际阻塞 = `Power`（可变指数）+ 深度 6。

## What Changes

- **MODIFIED「因子表达式可解析契约」（factor-dsl）**：算子集合新增 `ts_sum`（N 期滚动求和，窗口
  须正整数）与 `sqrt`（逐元素平方根，负值 → NaN）。
- **MODIFIED「复杂度硬约束」（factor-dsl）**：AST 深度上限 5 → 6（自由参数 ≤3 / 字段种类 ≤3 不动）。
- **实现面（非规范变更）**：`eval_engine` 新增 `_ts_sum`（min_periods=window，与其他 ts_* 同口径）
  与 `_sqrt`（负值域 NaN，与 `log` 非正 NaN 同约定）；spike 翻译器 `Sum` 映射 `missing` →
  `equivalent`（→ `ts_sum`，热身期口径差声明同其他滚动算子）+ `Power(x, 0.5)` 特例映射 →
  `sqrt`（精确等价：0.5 次幂 = 平方根，负值两侧均 NaN；其余指数仍不可翻译）。
- **prompt 同步**：`factor_proposal.md` 算子清单 + `ref`（补记）/`ts_sum`/`sqrt`，复杂度说明
  「深度 ≤ 6」；发布 Langfuse（预检拦截非本 delta prompt 时 `--exclude` 定向发布）。

## Impact

- 代码：`factors/dsl.py`、`factors/eval_engine.py`、`prompts/factor_proposal.md`（+发布）、
  spike 翻译器。
- 测试：`tests/factors/test_dsl.py`（枚举 18、深度边界 6/7）、`test_eval_engine.py`（滚动求和/
  平方根语义）、`tests/scripts/spike/test_qlib_translate.py`（Sum/特例映射 + 翻转）。
- **不做（明确边界，残余清单）**：深度 7+（amihud 深度 8 且自由参数 4——两条上限同时越界，
  不在本轮）；滚动 `ts_max/ts_min`（max_ret_20d，涉及同名不同义历史，独立 delta）；自由参数/
  字段种类上限；闸②门槛（第三条线，独立实验）。
- 复跑证据：批 A（10 条）+ 批 B（22 条）第四轮读数 → 验证报告（预期批 B 15 → 19/22）。
