# Microcap Lane Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付「含退市股历史池 + 小票通道（observation_only）」：退市清单进主表、退市股历史经既有回填链补采、含退市标的的池可见性回归、`microcap` 约束档位产出独立标注批次、前置门禁（退市池就绪 + regime 数据就绪）。

**Architecture:** `security_master` 加 `delist_date` 列（ALTER 先例：screening/store.py 的 `selection_mode`）；`akshare_client` 新增退市清单 fetch（明细类 `[]`+error 约定）；池可见性 = 主表上市—退市窗口 + 快照行存在（退市股历史走既有 `scripts/backfill_market_snapshot.py --symbols-file` 通道）；小票通道 = `FilterThresholds` 第二档 + 批次 `pool`/`frontier_gate` 列 + 新调度 job。

**Tech Stack:** Python 3.12 / SQLite / APScheduler / pytest。无 UI（v1 明确非目标）。

## Global Constraints

- 工作目录 = `D:\WorkSpace\finance_analysis_agent\.worktrees\personal-edge-program`（分支 `feat/personal-edge-program`）。**提交一律带路径** `git commit -m "..." -- <文件列表>`；不碰主检出/openspec 主库。
- 规范：`openspec/changes/add-microcap-lane-guardrails/`（spec + design，binding）。
- 口径先登记 `docs/evals/metrics.md` §1（§1.16），再动代码。
- **v1 MUST NOT 向执行链产生订单**；通道批次 `observation_only` 或「可执行候选（记录字段）」二态，均不下单。
- 主档（main）行为逐字节不变：`DEFAULT_THRESHOLDS` 与既有调用路径不得变。
- 调度计数断言随实际 job 数量同步（本变更视实施时点数 N→N+1；现基线含 regime 15 个 + 公告线 3 个 = 18，则 18→19——以实施时刻的测试断言实际值为准）。
- 关键事实（侦查已核）：`stock_info_sh_delist` 列名是「暂停上市日期」（非退市日，需映射并在口径注明）；`stock_info_sz_delist` 有「终止上市日期」；`fetch_kline` 三源链对退市码可返回退市前历史（列集随源漂移：梁讯无「成交量」列——消费方容错）；回填行无状态位（不得接 rebase/停牌合成链）；`filter_universe`（filters.py:160）与 `build_candidate_record`（:207）是阈值消费点；批次表加列须同步 3 个 SELECT（`latest_screening_batch`:604 / `latest_successful_batch`:619 / `list_batches`:634）与 UPSERT（store.py:540）。

---

### Task 1: 口径登记（metrics.md §1.16）

- [ ] 追加小节：退市池口径（来源两所清单、字段映射与「暂停上市日期」语义注明、可见性规则 `上市日 ≤ t ≤ 退市日`、无历史如实标注）、小票通道档位（microcap：流动性阈值默认 1000 万、建议单票上限 5%、滑点档位提升）、前置门禁两判据（退市池就绪 / regime 数据就绪）。提交 `docs(microcap): metrics.md §1.16 登记`

### Task 2: 退市清单与主表扩展

**Files:** Modify `src/finance_agent/data/akshare_client.py`、`src/finance_agent/data/snapshot.py`；Test `tests/data/test_delist.py`（或并入 test_akshare_client.py）

**Interfaces:**
- `AKShareClient.fetch_delist_lists() -> list[dict]`：SH+SZ 合并，字段 `code/name/list_date/delist_date/source`（SH 的“暂停上市日期”→ `delist_date` 并保留 `source="sh_suspend"` 供审计；失败/空 → `[]` + `logger.error`）
- `snapshot.py`：DDL 增 `delist_date TEXT`（新库建表 + 老库 ALTER 迁移，照 `init_screening_db` 先例）；`upsert_security_master` 支持 `delist_date`（COALESCE 保值）；`get_security_master` SELECT 同步
- 新增 `upsert_delisted_masters(rows, db_path=None) -> int`

- [ ] 先红后绿：字段映射（含 SH 日期列名歧义）/ 幂等 upsert / 老库迁移（预造无列库 → init 后列存在且旧数据保留）/ `get_security_master` 读出 `delist_date`
- [ ] 提交 `feat(microcap): 退市清单落库 + security_master 扩展`

### Task 3: 池可见性回归（防幸存者偏差）

**Files:** Create `src/finance_agent/data/universe.py`；Test `tests/data/test_universe.py`

**Interfaces:**
- `universe_visible(master_row: dict, t: str) -> bool`（`list_date ≤ t ≤ delist_date`；`delist_date` 缺失按「未退市」）；`filter_snapshot_rows_for_date(rows, master, t)` 或等价助手（按需最小）

- [ ] **先红后绿（核心回归）**：tmp 快照库造 1 只退市（含退市日）+ 1 只存续；断言 t<退市日 时池含退市标的、t>退市日 时不含；**移除可见性逻辑时该测试必红**
- [ ] 提交 `feat(microcap): 含退市标的的池可见性（防幸存者偏差回归）`

### Task 4: 退市股历史补采通道

**Files:** Create `scripts/backfill_delisted.py`（薄封装，复用 `backfill_market_snapshot` 内部函数）；Test `tests/scripts/test_backfill_delisted.py`

**Interfaces:**
- 脚本：读 `get_security_master` 含 `delist_date` 的代码 → 按 `list_date..delist_date` 窗口逐票调用既有回填函数；`--dry-run/--limit/--db`；宿主机写护栏沿用（`_host_write_guard`）
- 无历史可得（源无数据）→ 计数并在摘要列出代码（如实标注，不伪造）

- [ ] 先红后绿：fake client（退市码返回退市前 5 行、活码返回空）→ 断言退市码行落库、摘要计数正确、无历史码进明细
- [ ] 提交 `feat(microcap): 退市股历史补采脚本`

### Task 5: microcap 档位与通道批次

**Files:** Modify `src/finance_agent/screening/constraints.py`、`filters.py`、`pipeline.py`、`store.py`、`outcome/ops/jobs.py`、`outcome/scheduler.py`；Test 对应各层

**Interfaces:**
- `constraints.py`：`MICROCAP_MIN_AVG_AMOUNT_20D = 1e7`、`MICROCAP_THRESHOLDS = FilterThresholds(min_avg_amount_20d=MICROCAP_MIN_AVG_AMOUNT_20D)`、`MICROCAP_SUGGESTED_SINGLE_CAP = 0.05`（记录字段用）
- 批次表加列 `pool TEXT DEFAULT 'main'`、`frontier_gate TEXT`（ALTER 迁移 + 3 SELECT + UPSERT 同步）；`record_screening_batch(..., pool="main", frontier_gate=None)` 
- `pipeline` 增 `run_microcap_lane_daily(...)`：以 `MICROCAP_THRESHOLDS` 跑 L1→L2→（L3 可选，v1 沿用）→ `record_screening_batch(pool="microcap", frontier_gate=...)`；门禁判据：`delist_ready = 主表含 ≥1 条 delist_date 记录` + `regime_ready = latest_regime 新鲜（≤7 自然日）`；缺任一 → `frontier_gate="observation_only:missing=<要点列表>"`；**不产生订单**
- 调度：新 job `microcap_screening_daily`（16:45，插在 event_settle 16:25 之后）四件套 + wrapper；计数断言同步

- [ ] 先红后绿：档位越档场景（2000 万标的 main 剔/microcap 过）；批次列迁移幂等；门禁三态（双缺/单缺/齐备）；`pool` 标注落库与读出；主档回归全绿
- [ ] 提交 `feat(microcap): 小票通道档位与批次（observation_only 门禁）`

### Task 6: 收口验证

- [ ] `uv run pytest tests/data/ tests/screening/ tests/outcome/ -q` 全绿；`ruff check` 干净；新文件 mypy 零新增
- [ ] 手工冒烟：`scripts/backfill_delisted.py --dry-run` 与真实抽取 1 只退市股补采（可得范围内）并核对池可见性
- [ ] `openspec validate add-microcap-lane-guardrails --strict` 通过；验证报告落 `tests/validation/`（人工项：批次数据核对）
