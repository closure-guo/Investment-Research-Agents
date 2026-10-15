# 人工验证报告: wire-builtin-factor-registration

**日期**: 2026-10-15（晚）
**验证人**: ZCode 验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: openspec/changes/wire-builtin-factor-registration/
**E2E 门禁**: stub 套件适用并已通过（本 delta 使 /ops 时间线 +1 卡片、eval-ops 分区 +1 job——两套 E2E 钉数（`daily-ops.spec.ts` 12→13 + nth 位移、`eval-ops-console.spec.ts` JOB_IDS）同步更新，PR #347 CI stub 套件绿）
**执行环境**: 主检出 @ 6e5958b7（#347 合并后经 `scripts/deploy.py` 拉取 + `docker compose restart backend`）
**复现入口**: `uv run pytest tests/factors/test_builtin_register_job.py`（4 项）；生产读数经 `GET /api/v1/ops/jobs`（job_runs 摘要）与 `/ops` 页面（浏览器自动化）

## 验证结果

| # | Scenario（delta spec） | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 缺失条目过闸注册 | 空库 + 面板 → 缺失条目逐条五道闸，过闸入库、未过留原因码 | 单测 `test_empty_library_registers_missing_on_strong_panel`（强面板：registered≥1、reasons 逐条）；**生产 run 92**：5 条逐条评估，`reasons` 逐条落摘要（gate/reason 齐备） | ✅ |
| 2 | 日批注册入口幂等（含 retired 不回填） | 全量已存在（任意状态）→ skipped=5、不触面板、retired 不复活 | 单测 `test_all_present_including_retired_skips_without_panel`（快照库不存在亦不失败；retired 保持 retired）+ `test_partial_missing_submits_only_missing`（部分缺失只提交子集：skipped=4 时仅 1 条过闸） | ✅ |
| 3 | 面板不可得 fail loud | 缺失条目存在 + 快照库空 → 上抛 | 单测 `test_missing_entries_without_panel_fail_loud`（`ValueError: 快照库为空`）；**生产前史**：WAL 争用期 4 次 500（`OperationalError`）均为真故障如实落 failed，不以空面板放行 | ✅ |
| 4 | builtin 因子受同等准入约束 | builtin 与 LLM 提案同阈值 | 既有 `tests/factors/test_builtin.py`（spec 场景：ICIR≈0.12 → 闸②拒收）未改；本次生产读数全部走同一 `admit_candidates` | ✅ |

## §6 生产实跑读数（2026-10-15 23:15，run 92）

```
{"registered": 0, "rejected": 5, "skipped": 0,
 "reasons": [
   {"expression": "delta(close,20)", "gate": 1, "reason": "LOW_COVERAGE"},
   {"expression": "div(ts_mean(close,5),close)", "gate": 1, "reason": "LOW_COVERAGE"},
   {"expression": "div(volume,ts_mean(volume,20))", "gate": 1, "reason": "LOW_COVERAGE"},
   {"expression": "ts_std(delta(close,1),20)", "gate": 1, "reason": "LOW_COVERAGE"},
   {"expression": "ts_mean(amount,20)", "gate": 1, "reason": "LOW_COVERAGE"}]}
```

- `/ops` 卡片：`15:50 · 内置因子注册 · 成功 · registered=0 · rejected=5 · skipped=0 · reasons=[…]`（时间线卡位正确：15:45 因子衰减监控 → 15:50 内置因子注册 → 16:00 判定）。
- **读数解读（重要，防误读）**：五条全 LOW_COVERAGE 含**本次回填中途终止留下的「部分覆盖窗口」伪影**——扩展回填（2026-01-05..07-09，因宿主机-容器 WAL 互斥被提前终止，落 ~3200/5617 票）使 90 日面板中 06-03..07-09 段仅有部分标的行 → 覆盖缺失率被抬过 20% 阈值（clean 64 日面板上 reversal_5 覆盖是通过的，见下表）。

## §7 ② 裁决证据：深面板真实统计量（170 交易日，绕过闸①直测闸②）

面板 2026-01-28..2026-10-15 × 5616 票（含部分覆盖窗口），远期收益 1 日，RankIC 逐日序列：

| builtin | 缺失率 | IC 均值 | ICIR | \|ICIR\|≥0.3（闸②） |
|---|---|---|---|---|
| momentum_20 | 38.0% | −0.0281 | −0.150 | ✗ |
| reversal_5 | 29.2% | −0.0014 | −0.008 | ✗（真实数据上几乎零 IC） |
| **turnover_proxy** | 37.9% | −0.0344 | **−0.341** | **✓** |
| volatility_20 | 38.5% | −0.0363 | −0.138 | ✗ |
| size_proxy | **100.0%** | — | — | 不可计算 |

**结论**：① 入口已通、结论如实；② 深面板上 **turnover_proxy（量比）过闸②**（|ICIR|=0.341，负号 = 量比高者次日收益偏低——关注度过热反转，方向由 IC 符号承载）。其余三条真实数据 IC 不足（拒收为正确行为）；size_proxy **100% 缺失 = 回填取数不含「成交额」列**（`fetch_symbol_history_raw_qfq` 五列契约无 amount）——回填口径的数据缺口。

**闸①口径算术**（20 日窗因子热身期 19 日 + 北交所缺席 ≈6%）：64 日面板 → 30%+ ✗；90 日（`_PANEL_LOOKBACK_DAYS=DECAY_WINDOW+30`）→ 21%+6% ✗；170 日 → 11%+6% ≈ 17% ✓。即：**面板深度 ≥~150 交易日是长窗因子过闸①的必要条件**；`turnover_proxy` 在 170 日完整面板上可过闸①+闸②。

## 未达成项 / 披露

1. 本次注册入库 **0/5**（② 未动）：库仍空 → 因子衰减监控 `checked=0`、选股 L2 仍「无有效 IC 信息」。**② 建议（待拍板）**：(a) 准入面板深度 90→~170（独立常量或改 `_PANEL_LOOKBACK_DAYS`）；(b) 完成扩展回填（断点续跑，~40 分钟）；(c) 回填补「成交额」列（size_proxy 可计算性）。三项落地后 turnover_proxy 可入库 → 库非空 → L2 解锁。
2. 部分覆盖窗口伪影：扩展回填被提前终止（WAL 互斥，issue #344）——补齐后重跑注册任务可得 clean 读数（日批 15:50 自愈，或手动补跑）。
3. 回填中途终止留下 ≤16 票 partial 行（断点续跑判据=票级「已有行即跳过」）——补齐时需对 partial 票按缺失日期子区间补取。
