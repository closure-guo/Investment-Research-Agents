# extend-backfill-high-low 验证报告

- 日期：2026-10-17
- 变更：`openspec/changes/extend-backfill-high-low`（market-snapshot MODIFIED×1：回填列契约
  六列 → 八列 + 齐备判据扩展）
- 动机回顾：10-17 闸门验证实证回填行 `high`/`low` 覆盖 **0.0%**（1–9 月全空）→ 日内结构类
  因子（`(close-low)/(high-low)` 族）被闸① LOW_COVERAGE 结构性误拒——数据缺口被误读为因子质量。

## §1 实施（TDD 先红后绿）

- 取数层 `fetch_symbol_history_raw_qfq` 八列契约（+最高/最低，raw 口径）；东财/新浪两腿必需列集
  与归一名映射扩展；**源缺列按取数失败**（回退另一源；两源皆缺 → None，不产半口径帧）。
  证据：`tests/data/test_market_data_fallback.py` 29 用例（含新浪缺 high/low → None、东财缺 最高 →
  回退新浪、东财成功八列契约）。
- 回填脚本 `_history_rows` 的 high/low 真值映射 + `_has_rows_in_window` 齐备判据扩为
  **amount + high + low 全覆盖**。证据：`tests/scripts/test_backfill_market_snapshot.py` 10 用例
  （含「amount 齐备但 high/low 空 → 重取 upsert 补齐」新例）。
- `ruff check` / `ruff format` 干净；`openspec validate --strict` 过；mypy 9 错为 main 基线既有
  （对照核实，非本次引入）。

## §2 全量重跑读数（2026-10-17 04:00–05:05，64 分钟）

- 16 进程并行（`--symbols-file` 分片 5617 票 /16 ≈ 352），Start-Process 脱离会话启动，
  全程无硬杀（incident 043 纪律）；东财域 IP 封禁下按设计走新浪回退。
- 结果：**symbols_ok 5265 / symbols_failed 352**（北交所+特殊票——与首轮回填同一失败族群，
  源本无数据）；**回填区 high/low 覆盖 0.0% → 99.97%**。
- 月度覆盖（before → after）：1–6 月 0.0% → **100.0%**；7–9 月 0.0% → **99.9%**；10 月
  29.8% → **99.9%**（amount 维持 99.9–100%）。

## §3 闸①影响复核（上轮 LOW_COVERAGE 案例的归因闭环）

- 上轮被闸①拒的 `ClosePosition = div(sub(close,low),sub(high,low))`：其依赖的 high/low 现已
  全区间覆盖——该候选在下次准入批（提案月批/外部导入）将以**真实覆盖**参与闸口裁决，
  不再被数据缺口结构性拒收。

## §4 备注

- 失败 352 票 = 北交所+特殊票（新浪历史源不覆盖；与 add-watch 前次回填的 345 票同族群口径），
  属数据源边界而非本 delta 缺陷；如需覆盖另立数据源 delta。
- 运行产物：分片 `D:/tmp/bf-shards/`、日志 `D:/tmp/bf-{i}.out/err.log`（临时区，不入库）。
- 纪律遵守：全程无硬杀（票间单事务提交，中断可断点续跑）；未影响容器在线分析（cohort 已先完成）。
