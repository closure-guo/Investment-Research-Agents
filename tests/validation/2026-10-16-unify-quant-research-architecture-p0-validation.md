# 人工验证报告: unify-quant-research-architecture（P0 实施阶段）

**日期**: 2026-10-16
**验证人**: ZCode 验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: openspec/changes/unify-quant-research-architecture/
**E2E 门禁**: 不适用（纯后端：因子/数据/脚本；无前端 UI / SSE / 会话切换 / 状态流转；ops 注册表未变——外部导入为脚本通道无新 job）
**执行环境**: 主检出（分支 feat/unify-quant-research-architecture @ 6e3511a0+）；宿主机 16 进程并行回填
**复现入口**: `uv run pytest tests/factors tests/data tests/scripts -q`；回填 `scripts/backfill_market_snapshot.py`

## 验证结果

| # | Requirement / 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | market-snapshot「历史回填补齐成交额」：回填行含 amount / 源缺按失败 | TDD：`test_eastmoney_failure_falls_back_to_sina_pair`（六列+成交额 raw 口径）、`test_eastmoney_success_includes_amount_six_columns`、`test_sina_missing_amount_returns_none`、`test_rows_mapping_field_by_field`（amount 映射）；真机取数探针：600519 区间 186 行六列、amount 单位元（1.37e9 量级） | ✅ |
| 2 | factor-library「准入面板深度下限」：150 独立常量 / 浅面板 fail loud / 深面板取数 | TDD：`test_admission_lookback_independent_of_monitor`（150 与监控 90 互不影响）、`test_panel_from_snapshot_takes_admission_lookback`（160 日→取 150）、`test_panel_from_snapshot_shallow_fails_loud`（149 日→ValueError）、`test_shallow_panel_fails_loud`（基线注册入口共用路径） | ✅ |
| 3 | factor-proposal「外部研发供给接入」：同一五道闸 / source 强制 / 外部结论不采信 / 不可表达拒收 | TDD：`test_admitted_and_rejected_with_external_source`（external_ic=0.9 不替代闸②读数）、`test_unparseable_counts_errors_without_blocking_batch`、`test_source_is_forced_to_external`、`test_shallow_snapshot_panel_fails_loud`；脚本：`tests/scripts/test_import_external_factors.py`（3 项） | ✅ |
| 4 | factor-proposal「拒收原因回喂」+「提案月批排程」MODIFIED（深度批依序运行） | TDD：`test_deep_prompt_carries_rejection_distribution_and_admits`（深度批 prompt 含本批 WEAK_ICIR 分布、深度候选过闸入库、摘要 deep 分桶）、`test_recent_rejection_feedback_reads_job_runs_and_degraded`（运行历史聚合 + degraded 复盘；**测试抓到 `_aggregate_rejections` 推导式 stale key 真 bug**）、`test_recent_rejection_feedback_empty_is_empty` | ✅ |

**回归**：`tests/factors` 300 passed；`tests/data` + `tests/scripts` 全绿；ruff/mypy 干净；`openspec validate --strict` 过。

## §6 关键读数

**① 回填写入提交放大治理（实测）**：逐日提交 190 txn/票 在 16 进程并行下 → 实测吞吐 ~8 只/分钟（WAL 写锁争用 + Windows fsync）；`write_snapshot_rows_bulk`（每票单事务）单写者实测 1.6s→~0s；**索引损坏 incident 043 即发生在此窗口**。

**② 快照库恢复（incident 043）**：索引层损坏（唯一索引失效 → 17 重复键）→ 全扫描 rowid 两遍法精确去重 + 表重建（原 DDL/索引 SQL 逐字）→ `integrity_check: ok`、756,588 行、187 交易日（周末 0）；附带清理性能探针误写的 `T99999` 行 190。**表数据零丢失**。

**③ 深面板预览（前序实测，64 日面板）**：五条 builtin `registered=0`（4×LOW_COVERAGE + reversal_5 WEAK_ICIR）——本 delta P0-1/P0-2 即针对该读数；170 日深面板实测 `turnover_proxy |ICIR|=0.341` 过闸②（见 wire-builtin-factor-registration 验证报告 §7）。

## §7 生产读数（2026-10-16 08:31，run 93——回填完成后部署实跑）

部署路径：#349 合并（dd043090）→ `deploy.py` 拉取 → 后端重启（`admission depth: 150` 容器内确认）→ `/api/v1/ops/jobs/factor_builtin_register/run`。

```
{"registered": 0, "rejected": 5, "skipped": 0, "reasons": [
  {"expression": "delta(close,20)",                    "gate": 2, "reason": "WEAK_ICIR"},
  {"expression": "div(ts_mean(close,5),close)",        "gate": 2, "reason": "WEAK_ICIR"},
  {"expression": "div(volume,ts_mean(volume,20))",     "gate": 4, "reason": "NO_POOL_INCREMENT"},
  {"expression": "ts_std(delta(close,1),20)",          "gate": 1, "reason": "LOW_COVERAGE"},
  {"expression": "ts_mean(amount,20)",                 "gate": 2, "reason": "WEAK_ICIR"}]}
```

**对比昨晚（90 日面板 0/5 全 LOW_COVERAGE）的读数演进**：

| builtin | 90 日面板（前） | 150 日深面板（今） | 解读 |
|---|---|---|---|
| momentum_20 | 闸① LOW_COVERAGE | 闸② WEAK_ICIR | 深度修复生效；真实 IC 弱（ICIR<0.3） |
| reversal_5 | 闸② WEAK_ICIR | 闸② WEAK_ICIR | 与深面板实测一致（ICIR≈−0.008，近零） |
| **turnover_proxy** | 闸① LOW_COVERAGE | **闸④ NO_POOL_INCREMENT（过①②③）** | 覆盖/ICIR/显著性全过，倒在样本外池增量 ≤0——最接近入库的一条 |
| volatility_20 | 闸① LOW_COVERAGE | 闸① LOW_COVERAGE | 21 日热窗在 150 日面板 ≈14%+覆盖缺口，贴线未过 |
| size_proxy | 闸① LOW_COVERAGE | 闸② WEAK_ICIR | **成交额补齐生效**（coverage 过了），真实 IC 弱 |

**结论**：管线已在真实数据上端到端运行（数据→面板→五道闸→逐条裁决），裁决全部 fail-closed 如实留痕。因子库保持空库的原因从「机制阻断」变为「**基线因子真实 IC 不达标**」——这是系统在做它该做的事。解锁路径二选一（owner 裁决）：(a) 供给线产出真正有预测力的候选（RD-Agent spike / 提案阈值再校准）；(b) 闸②/闸④阈值再校准（0.3 为文档背书值，动它属口径变更）。

**回填终态**：16/16 分片完成（ok=3797 / failed=352〔北交所 345+7 特殊〕/ skipped=1468，合计 5617）；本 pass 写 699,880 行；库终态 977,327 行 / 187 交易日 / amount 覆盖 5265 票 / `quick_check ok`。

## 未达成项（本阶段不做完，披露）

1. **全量重取回填仍在运行**（amount 覆盖 1746/5617 @02:24，网络侧限速 ~28 只/分钟，ETA ~2h；后台 16 进程、不再中断）。**生产基线注册深面板读数**待回填覆盖足够后补录（面板最后 150 交易日需 ~全票 amount/行覆盖，否则闸①缺失率仍被部分覆盖拉高）。
2. **RD-Agent spike**（供给线③投用前置）：WSL2 Ubuntu + Docker 集成 + Qlib/社区数据 + 单循环 + 翻译 + 过闸读数——环境重、多交互步骤，**未启动**（见 tasks P0-3 第 3 项；下一步专门会话执行）。
3. archive 待 tasks 全勾（上述两项）后执行；本阶段以实施 PR #349 落代码。
