# 验证报告：add-screening-ic-bootstrap-guard

**日期**: 2026-10-22
**验证人**: agent（机器项 + 真实链路抽验）
**关联 delta**: `openspec/changes/add-screening-ic-bootstrap-guard/`
**E2E 门禁**: 不适用（纯后端选股批处理逻辑，无 UI/SSE/会话状态变更）

## 背景

2026-10-21 链路评估发现精选池连续 5 个交易日为 0（10-19 / 10-21 为 0，10-20 仅 1）。归因：
因子库 10-16 才开始写 IC，生产只有 3 个交易日样本，而 L2 权重直接由这 3 个样本估计；权重日间
摆动导致 Top-100 日间重叠仅 1/100，再乘「连续 3 日在榜」门槛 → 池恒空。

## 变更

- `ranker.MIN_IC_SAMPLES_FOR_SCORING = 10`：单因子近 60 交易日 RankIC < 10 条不参与打分。
- `ranker.ic_sample_counts` / `ranker.select_scorable_factors`：计数与切分（纯函数）。
- `pipeline`：L2 前先过滤薄样本因子；全部被排除 → 走既有 `fallback` 降级通道（流动性过渡排序），
  日志披露被排除因子 id 与样本数。
- 不改「连续 3 日在榜」门槛、不改 IC 窗口。

## 机器验证

| 项 | 命令 | 结果 |
|---|---|---|
| 定向单测 | `uv run pytest tests/screening/test_ranker.py tests/screening/test_pipeline.py -q` | 52 passed |
| API 层 | `uv run pytest tests/test_api_screening.py -q` | 33 passed |
| store / 池分析任务 | `uv run pytest tests/screening/test_screening_store.py tests/screening/test_pool_analysis_job.py -q` | 43 passed |
| Lint | `uv run ruff check`（4 个改动文件 + mutation_probe） | All checks passed |
| 类型 | `uv run mypy src/finance_agent/screening/ranker.py src/finance_agent/screening/pipeline.py` | Success |
| 变异探针锚点 | `tests/scripts/mutation_probe.py` 更新 `pipeline-fallback-condition` 锚点，新增 `pipeline-ic-sample-guard` / `ranker-ic-sample-boundary` | 锚点已对齐（未整批跑） |

## 真实链路验证（生产库、生产因子库）

触发：`POST /api/v1/ops/jobs/screening_daily/run`（2026-10-21 23:53:55 → 2026-10-22 00:28:25，约 34 分钟）。

日志（容器内）：

```
选股日批 L2 排除 IC 样本不足的因子（2026-10-21，门槛 10 条）：
factor=1 samples=3, factor=2 samples=3, factor=3 samples=3, factor=4 samples=2
选股日批降级（2026-10-21）：无足够 IC 样本的可打分因子（active=4，样本达标=0），
L2 显式跳过，按近 20 日日均成交额降级排序（selection_mode=fallback）
```

批次结果（`GET /api/screening/batches`）：

```json
{"trade_date":"2026-10-21","status":"ok","l1_passed":3986,"l2_top":100,
 "l3_keep":100,"pool_size":1,"selection_mode":"fallback"}
```

池接口（`GET /api/screening/pool`）：

```json
{"batch_status":"ok","selection_mode":"fallback",
 "notice":"因子库无可打分因子：本次为降级模式，按近 20 日日均成交额排序（非因子信号）",
 "pool":[{"symbol":"600519","consecutive_days":4,"factor_contribs":[]}]}
```

结论：门槛生效（4 个 active 因子全部被排除）→ 显式降级 → 精选池由 0 变 1，且三处披露
（批次 / 池接口 / 前端横幅）口径未变。

## 边界与遗留

1. **池规模仍取决于在榜连续性**：今日 1 只（600519 在 10-16/19/20 的 factor 榜与今日 fallback 榜
   均在前 100）。后续 fallback 榜趋稳后池会自然变大；不是「一次修复即满池」。
2. **自动切回**：IC 每日累积，单因子达到 10 条后自动回到 `factor` 路径（预计 10 月底，取决于日批
   是否每个交易日都写入 IC）。无需配置开关。
3. **未跑 hosted 全量评估**：本次不改变 judge 口径与报告产出，评估链仍处于 09-23 之后的度量空档
   （独立项，见链路评估报告）。
4. **本次重跑覆盖了 10-21 的原 `factor` 批**（原批 pool_size=0），L2 榜由因子排序替换为流动性排序；
   属预期行为，历史批次仍可按日期追溯（同日原子替换，无残留行）。
