# 验证报告：add-cohort-quota-guard（cohort 当日额度守卫）

**日期**: 2026-10-22
**关联 delta**: `openspec/changes/add-cohort-quota-guard/`
**E2E 门禁**: 不适用（后端日批逻辑，无 UI/SSE 变更）

## 背景（实测数据）

近 14 天 36 次失败中，8 次是 provider 的 **5 小时滚动额度**：

```
2026-10-09 18:00:00 / 18:00:27 / 18:00:53  002916 / 300033 / 600015
2026-10-16 18:00 / 18:02 / 18:04           002916 / 300033 / 600015
RateLimitError: 已达到 5 小时的使用上限，限额将在 2026-10-xx 2x:xx 恢复
```

三次都在 18:00 cohort 批的同一窗口、**顺序**触发（间隔 26 秒～2 分钟，不是并发打爆）；10-20 22:31–23:18 另有 4 次同标的（600519）手动重试的 5xx。原实现只有单轮预算熔断，撞限后仍逐只尝试。

## 变更

1. **预检跳过（批次开始前）**
   - 当日已有 `quota` 失败 → 整批 skipped（原因 `quota`，硬证据）；
   - 当日 `cohort_runs.tokens_total` 合计 + 单只预估 > `COHORT_DAILY_TOKEN_BUDGET`（默认 3,000,000）→ 整批 skipped（原因 `budget_daily`）。
2. **批中停止（每只标的启动前）**：余量不足 → 剩余 skipped（`budget_daily`）；任一只失败归因为 `quota` → 剩余立即 skipped（`quota`）。
3. **失败归因**：`no_report_ready` / `exception` 路径先匹配额度特征（使用上限/速率限制/RateLimit/429/额度/quota），命中记稳定码 `quota`，异常原文仍只进日志。
4. **估算口径**：单只成本 = 历史成功 run 的 token **p75**（实测 210,952；中位数仅 83,082，会低估）；无样本回退 250,000。
5. `FAILURE_CODES` 扩为 `… budget_daily / quota / duplicate`；幂等 `duplicate` 优先于当日跳过。

## 验证

| 项 | 命令 | 结果 |
|---|---|---|
| 新用例（先红） | `pytest tests/outcome/test_cohort_runner.py -k "quota or daily_budget or duplicate_takes"` | 5 failed → 实现后全绿 |
| runner 全量 | `pytest tests/outcome/test_cohort_runner.py -q` | 32 passed |
| outcome 全量 | `pytest tests/outcome -q` | 931 passed, 2 skipped |
| Lint / 类型 | `ruff check` / `mypy`（runner） | 通过 |
| OpenSpec | `openspec validate add-cohort-quota-guard --strict` | valid |
| 容器内真实数据 | `_today_tokens` / `_estimate_run_tokens` / `_preflight_skip_reason` | 当日 0、p75 210,952、默认预检 None；quota→`quota`、低预算→`budget_daily`、额度文本匹配 True / 连接错误 False |

上线：后端已重建加载新代码（进程内 `--reload` 在 WSL2 bind mount 下不传播，故整容器重建）；今日 18:00 批将走新逻辑。

## 边界与说明（重要）

1. **当日口径只统计 cohort 自己的 token**：15:40 选股分诊、16:22 自动池分析、用户会话的消耗不在 `cohort_runs`，所以 `budget_daily` 是**保守下界**，不能保证在撞限前拦住。
2. **真正防重复失败的是 `quota` 信号**：一旦第一次撞限，剩余标的立即跳过（10-20 那类「4 次连续重试」不会再发生）。
3. **默认 3,000,000 是宽松上限**（历史单日 cohort 约 1.2–2.1M）。若希望更早保守跳过，把 `COHORT_DAILY_TOKEN_BUDGET` 调到实测安全值（如 1,500,000）；该值应按订阅的真实 5 小时额度标定，不来自本仓库。
4. 若要「事前」而非「撞后」拦截，需要全站 usage 台账（非 cohort 单表），属独立 delta。
