# 因子 DSL 扩展 ref 后移引用（extend-factor-dsl-ref）

## Why

外部研发供给线（RD-Agent → 翻译 → 五道闸）的 spike 结论（2026-10-16）钉死了复跑条件：
**「扩 DSL `ref` 后重跑本批输入」**——10 条外部候选中 6 条不可翻译，全部倒在同一个缺口：
本 DSL 无后移引用算子（`Ref` = `x.shift(N)`），动量 / 反转 / 收益率族（外部表达式最高频的
形态）结构性不可表达。`ref` 是翻译覆盖率的单点瓶颈，也是「持续研发 → 持续供给」链条上
最便宜的解锁项（一个算子换一个因子大类）。

## What Changes

- **MODIFIED「因子表达式可解析契约」（factor-dsl）**：算子集合新增 `ref`（`ref(x, N)` = `x`
  沿日期轴后移 N 期，前 N 行为 NaN；窗口参数须正整数数字字面量——`ref(x, 0)`/负窗口在解析期
  拒收，杜绝前瞻引用形态）。既有 15 算子与字段/复杂度约束零变化。
- **实现面（非规范变更）**：`eval_engine` 新增 `ref` 求值（沿日期轴按标的 shift，与 `delta`
  同款 NaN 语义）；spike 翻译器 `Ref` 映射从 `missing` 改为 `equivalent`（pyqlib 0.9.7
  `series.shift(N)` 与本 DSL `ref` 语义严格等价，无热身期口径差）。

## Impact

- 代码：`factors/dsl.py`（OPS/ARITY/WINDOW_ARG_INDEX）、`factors/eval_engine.py`
  （`_reference` + `_WINDOW_OPS`）、`tests/scripts/spike/qlib_translate.py`（映射表条目 + 文档）。
- 测试：`tests/factors/test_dsl.py` / `test_eval_engine.py` 新增 ref 用例；
  `tests/scripts/spike/test_qlib_translate.py` 三条 Ref 断言翻转（不可翻译 → 等价翻译）。
- **不做（明确边界）**：LLM 提案 prompt 的算子清单暂不加入 `ref`（prompt 版本事件独立管理，
  避免与本次 DSL 变更混为一个变量）；滚动 Sum/Var/Skew 等其余缺失算子不在本 delta（覆盖率
  证据以复跑读数说话，逐个评估）；不做 `ref` 的负数/零窗口（前瞻引用是红线）。
- 复跑证据：翻译批重跑（原 10 条 + 持续研发新产出）→ 可翻译率与过闸读数随验证报告留档。
