# 选股漏斗复查与「散户量化策略」落点现状（2026-10-23）

> 触发：用户问「选股池是什么逻辑？合理吗」，随后问「那套散户量化选股策略上线了吗」。
> 本文是当次复查的现场读数、问题清单与两条线的状态对照；便于下次直接接着做，不用重新查。
> 复查方式：读代码 + 查三个库（`screening.db` / `factor_library.db` / `sessions.db` job_runs）+ 打
> `GET /api/screening/pool`。全部为 2026-10-22 收盘后（10-23 00:50 左右）读数。

## 1. 结论

1. 漏斗结构（快照新鲜度 → L1 硬过滤 → L2 因子打分 → L3 LLM 排雷 → 连续 3 日在榜 → 精选池）合理，
   且确定性、fail-closed、降级显式披露这三条纪律都落到位了。
2. **但当前池子不含因子信息**：11 个因子 IC 样本 1–4 条（门槛 10），`selection_mode=fallback`，
   L2 实际是「近 20 日日均成交额排序」。预计 2026-11-02 才回到因子模式。
3. **最大结构缺陷是「连续 3 日在榜」与「排序口径换挡」耦合**：换挡会重置所有人 streak，池子清零
   2–3 天（见 §3.1）。这在 11-02 因子模式恢复时会再发生一次。
4. **散户量化策略已开发并已合并**（2026-10-18 调研的五个落点全落地，见 §4）：结论不是「没开发」，
   而是「账没记」——4 个 change 从未回填 tasks、从未归档，主 `openspec/specs/` 也没有对应 capability，
   导致任何只看 openspec 状态的人（含本笔记初版）都会误判为「未实施」。**初版 §4 的判断已作废，以现表为准。**

## 2. 当日漏斗实测（`pool=main`，2026-10-22）

| 层 | 实测 | 说明 |
|---|---|---|
| 全市场快照 | 5574 | `market_snapshot` 当日行数 |
| L1 硬过滤 | 3995（72%） | ST/停牌/上市<60 交易日/一字板/20 日日均成交额<5000 万/数据缺失 |
| L2 Top-N | 100 | `selection_mode=fallback`（成交额排序），`factor_contribs` 为空 |
| L3 keep | 100（0 drop，0 降级） | 4 个批次合计 400 条决策**全部 keep** |
| 精选池 | 3 | 600519(rank 28)、600900(rank 75)、601398(rank 87)，全部为大市值 |

因子库：11 条（10 active / 1 retired），IC 记录 1–4 条/因子（最新 2026-10-21）；
`factor_signal.expected_factor_mode_date = 2026-11-02`。

批次历史（main）：10-22 pool 3、10-21 pool 1、10-20 pool 1、10-19 pool 0、10-16 pool 0。

## 3. 问题清单

### P0-1 三天门槛 × 换挡 → 池子剧烈抖动

- 证据：L2 榜日间重叠 `10-22 vs 10-21 = 98/100`（同为 fallback，极稳）；
  `10-21 vs 10-20 = 3/100`、`10-19 vs 10-16 = 1/100`（发生 factor↔fallback 切换）。
  10-22 榜上 100 只的 streak 分布：`{1: 2, 2: 95, 3: 2, 5: 1}` → 只有 3 只过 3 日门槛。
- 含义：`consecutive_days_in_top` 只问「在不在榜」，不问「为什么在榜」；一次口径切换被读成「信号消失」。
  预测 10-23 池子会跳到约 98 只（95 只 streak 明天变 3）；11-02 恢复因子模式时再次清零。
- 建议：streak 按 `selection_mode` 分段累计；或改为「近 5 个交易日命中 ≥3 次」的滚动门槛；
  换挡日在池接口/页面显式披露「口径切换、门槛重置」。

### P0-2 L3 排雷目前零区分度

- 证据：`triage_decisions` 4 个交易日 × 100 条 = 400 条，全部 `keep`，`degraded` 全 0。
- 两种可能：①`fetch_key_events` 注入的事件基本为空；②prompt「证据不足时倾向 keep」过宽。
- 建议：先查 trace 里每只候选的事件条数（零成本）；若普遍为空 → 该层当前只是成本，
  应改为「无事件则跳过 LLM」；若有料仍全 keep → 审判据与 few-shot。

### P1-1 权重方案偏脆（因子模式恢复前应硬化）

- 现状：`w_i = IC均值_i / Σ|IC均值|`（[ranker.py:87](/src/finance_agent/screening/ranker.py:87)），
  无收缩、ICIR 只记录不参与、无单因子权重上限、无行业/市值中性化。
- 证据：10-20 批（factor 模式、4 条 IC 样本）榜日间重叠仅 44/100，直接导致 10-21/10-22 池子近零。
- 建议：`IC均值 × ICIR` 收缩 + 单因子上限（如 0.4）+ 行业中性化后再 z-score。

### P1-2 池子无数量与结构约束

- `pool_size` 完全由 keep ∩ 门槛决定，无 min/max、无行业上限 → 3 只与 98 只都「合法」；
  下游 `pool_analysis` 每日自动分析 cap 默认 10（`AUTO_POOL_ANALYSIS_MAX_SYMBOLS`），
  按 rank 取前 10，池子抖动会直接变成分析量与覆盖的抖动。

### P2 细节

- `recent_ic_stats` 取「最近 60 条记录」而非「最近 60 个交易日」，因子停更后旧 IC 仍有权重。
- 当日 IC 滞后一天（10-22 收盘后库内最新为 10-21），权重天然滞后。
- L1 通过率 72%，只做硬性排除，不控市值/行业暴露；配合成交额排序会系统性偏大盘。

## 4. 「散户量化选股策略」落点现状（2026-10-23 复核，**已开发并已合并**）

调研原文（[docs/evals/2026-10-18-个人小资金量化策略调研.md](/docs/evals/2026-10-18-个人小资金量化策略调研.md)）
的五个落点全部落地于 **commit `cceb1a31`「个人优势程序五变更落地（成绩单/拥挤度regime/公告线/小票通道/可转债）(#403)」**，
该 commit 是 `main` 的祖先（`git merge-base --is-ancestor cceb1a31 HEAD` 通过）。

| 落点 | change | 代码/接线 | 日批 | openspec 账 |
|---|---|---|---|---|
| P0 策略族 scorecard | `add-strategy-family-scorecards` | `outcome/scorecards.py`（512 行，四槽：净期望/回撤/容量/尾部）+ 前端 `StrategyScorecardCard`（TrackRecordPage:622） | 随战绩页 | **未归档、tasks 0/15** |
| P0 拥挤度 / regime 仓位 | `add-crowding-regime-positioning` | `outcome/regime/*`（7 文件）+ `execution/driver.py` 读 `regime.db` → `judge()` → `regime_multiplier` 传入 `aggregate_targets`（总仓位系数）+ `MarketRegimeCard`（ScreeningPage:217） | `regime_daily` 15:35 每日 ok | **未归档、tasks 0/19** |
| P1 事件驱动 lane | `add-event-driven-lane` | `outcome/events/*`（cb_status/cb_sync/journal/triage/watchlist 等）+ `prompts/event_triage.md` + `EventWatchlistCard`（ScreeningPage:224） | `event_settle_daily` 16:25 每日 ok | **未归档、tasks 0/22** |
| P2 转债覆盖 | `add-convertible-bond-coverage` | `outcome/events/cb_*` | `cb_daily` 16:50 每日 ok | 已归档（2026-10-19） |
| P2 小票通道 | `add-microcap-lane-guardrails` | `screening/pipeline.run_microcap_lane`（档位 `MICROCAP_THRESHOLDS` 1000 万） | `microcap_screening_daily` 16:45（10-22 20:57 补跑 ok） | **未归档、tasks 0/13** |

验证材料齐全：`tests/validation/2026-10-18-{add-strategy-family-scorecards,add-crowding-regime-positioning,add-event-driven-lane,microcap-lane}-validation.md`；
单测 `tests/outcome/test_scorecards.py`、`test_regime_*.py`、`tests/test_api_scorecards.py`、`tests/test_api_regime.py`；
E2E `tests/e2e/playwright/tests/{strategy-scorecard,market-regime}.spec.ts`。

**仍未闭合的三件事（真正的缺口）：**

1. **治理账**：四条 change 未回填 tasks、未 `openspec validate`、未 sync 主规范库（`openspec/specs/` 无
   `crowding-regime` / `strategy-scorecards` / `earnings-event-lane` / `microcap-lane`），也未 archive。
   后果：任何按 openspec 状态判读的人都会误认为「未实施」（本笔记初版即如此）。
2. **退市股池没回填**：`security_master` 有 `delist_date` / `delist_source` 列但 **5618 行全为空**，
   故小票通道 `frontier_gate=observation_only:missing=delist_pool`、`pool_size=0`（仅观察、不下单）。
   `scripts/backfill_delisted.py`（406 行）已就位但未见入库证据。
3. **选股漏斗自身的缺陷**（与这五个落点无关，见 §3）：streak 换挡清零、L3 零 drop、权重脆、池无约束。

## 5. 待办（按顺序）

- [x] **治理补账**（2026-10-23 完成）：四条 change 回填 tasks → `validate --strict` → sync 主规范库
      （新增 `strategy-scorecards` / `crowding-regime` / `earnings-event-lane` / `microcap-lane`）→ archive；
      全库 86 specs + 10 changes 校验通过
- [ ] **退市股池回填**：跑 `scripts/backfill_delisted.py` 落 `security_master.delist_date`，
      然后复核小票通道 `frontier_gate` 是否从 `observation_only:missing=delist_pool` 转 `ready`
- [ ] 查 L3 事件注入量（trace，零代码改动），判定该层是空转还是有料不判
- [ ] 修 streak 口径（P0-1）——**11-02 之前必须做**，否则因子模式恢复当天池子再次归零
- [ ] 评估权重硬化（P1-1），等 60 日 IC 样本达标后回测对比
- [ ] 决定池子数量/行业约束（P1-2）
- [x] **选股页信息层级**（2026-10-23，delta `update-screening-page-priority`）：池子置顶卡片化；
      事件观察改固定高度滚动框 + 行折叠 + 首屏 50 条分页（原 432 条平铺 ≈ 8 万 px，池子在首屏之外）

## 6. 复现命令

```bash
# 池与批次
curl -s localhost:8000/api/screening/pool | jq '{selection_mode, notice, factor_signal, n: (.pool|length)}'
# 批次漏斗
sqlite3 data/screening.db "select trade_date,status,l1_passed,l2_top,l3_keep,pool_size,selection_mode from screening_batches where pool='main' order by trade_date desc limit 10"
# L3 决策分布
sqlite3 data/screening.db "select trade_date,verdict,count(*) from triage_decisions group by 1,2"
# 因子 IC 覆盖
sqlite3 data/factor_library.db "select factor_id,count(*),max(trade_date) from factor_ic group by 1"
```
