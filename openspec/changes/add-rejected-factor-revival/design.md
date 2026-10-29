# Design: add-rejected-factor-revival

## Approach

新模块 `factors/revival.py`，单入口 `run_factor_revival(*, dry_run=False, db_path=None, snapshot_db_path=None, panel=None)`：

```
rejected 行（list_factors(status="rejected")）
  → 逐行 list_ic_series(limit=IC_WINDOW)：点数 ≥ 60？
  → recent_ic_stats（复用 screening.ranker 公式单一真相源）：|滚动 ICIR| ≥ MIN_ABS_ICIR？
  → list_events(factor_id)：最近 created/re_rejected 事件 < REVIVAL_COOLDOWN_DAYS？→ cooldown_skipped
  → 按 |滚动 ICIR| 降序（并列 factor_id 升序）取前 REVIVAL_DAILY_CAP
  → run_factor_external_import(payload, ledger_source="revival")   # 复用预检+五道闸+拒收回写
```

关键复用与既有语义的咬合：

- **`run_factor_external_import`**（`factors/external.py`）：预检（DSL 合法性）→ `admit_candidates`（闸⓿~⑤）→ 拒收经 `record_rejected_factor` 同构 upsert 回写拒绝池。revival 的再拒因此**天然**落 `re_rejected` 事件并重置冷却，零额外代码。
- **闸⓿查重排除 rejected**（`_DEDUP_STATES = candidate/active/degraded`）：同构复苏候选可重过闸——这是整个通道可行的前提（既有设计，非本次改动）。
- **`source` 通道强制标注**：external.py 强制 `source=EXTERNAL_SOURCE`（不采信自称）——revival 候选落库 source 同为 external；审计口径走 `ledger_source="revival"`（检验账本区分）。
- **冷却时钟**：取该行 `created` 与 `re_rejected` 事件时间戳的最大值。初始拒收的 `created` 事件 detail 携带 gate/reason/读数（add-rejected-factor-pool 落库语义），无需新事件类型。
- **面板**：仅 revived 非空时由 external 路径构建（`_panel_from_snapshot`，500 日）；dry_run 与空选零面板。fail loud 语义随 external 路径自然继承。
- **调度**：17:10——外资快讯（17:05）之后、诊断（17:15）之前；mon-fri（IC 只在交易日增长，周末跑无意义）。与诊断的 5 分钟间隔内若 revival 超时溢出属良性并发（两者写不同表，WAL 短争用）。

## Alternatives Considered

- **状态机加 `rejected → candidate` 迁移边**：改动状态机契约（library.py 迁移表 + 全部守卫测试），且「原地复活」会混淆同构行的历史裁决（IC 序列连续性与 191 行账本）。不选——新行走完整管线，旧行保持审计原样。
- **触发条件用「面板加深」**（optimize-factor-supply-funnel B4 原文）：面板 750 日是 2027 年等待项，现在无事可做。改为「滚动静默线」即刻可运行，面板加深后通道自动受益。已回写 umbrella tasks.md 口径。
- **复评时跳过部分闸口**（如只补闸②）：准入权威碎片化，闸④撞池/闸⑤样本外同样可能翻案。不选——五道闸全跑。
- **把复苏候选塞进 15:55 收割批**：与收割共享批次会挤占闸③ FDR 家族构成、且收割批语义是「RD-Agent 新提案」。不选——独立 job、独立 ledger_source。

## Risks

- **复苏→再拒→冷却→再复苏的乒乓**：冷却 30 天 + 单次 cap 5 + 静默线与闸②同值三重约束；且每次再拒消耗一次准入面板成本（48s 级），频次天然受 cap 限制。
- **同批多个同构族复苏互踩闸③ FDR / 闸④**：可能，但这是**真实统计结论**（同族确实冗余），拒绝结果回写拒绝池供后续参考——不是缺陷而是通道在正常工作。
- **窗口期与诊断 job 并发**：17:10/17:15 相邻；revival 通常亚分钟级（多数日子 eligible=0 零面板），溢出概率低、写冲突无（表不相交）。
- **cap 选择偏差**：按 |滚动 ICIR| 降序取前 5 可能系统性偏向高波动 IC 序列（ICIR 估计噪声）。接受——闸⑤/闸②在准入时复核，且 30 天冷却限制重试频率。
