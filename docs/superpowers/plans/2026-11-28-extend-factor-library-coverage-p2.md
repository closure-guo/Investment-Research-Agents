# extend-factor-library-coverage P2/P3（参考数据面 + 治理 + 提案侧）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans（本会话内联执行）。Steps use checkbox (`- [ ]`) syntax.

**Goal:** 落地 delta `extend-factor-library-coverage` 的 P2（§6–§11）与 P3（§12–§13）：参考数据侧表与读路径、面板可选字段与行业分组、增量落库与历史回填、准入预检与桶归属、builtin 5→7、选股缺口披露、提案 category 5→9 与提示词模板。

**Architecture:** 侧表 `market_ref_daily` 与主表 `industry_l1` 列都在快照库（同库使三处面板加载器与缓存键零改动）；读路径在 `read_universe*` 的 SQL 内 LEFT JOIN（旧库缺表/列时退化为原查询）；`build_panel_from_rows` 按行携带列构造可选字段帧与 `groups["industry"]`；增量数据搭拥挤度日批（15:35 已在全市场取市值）落侧表，历史由新浪历史日线（流通股本/换手率）与巨潮逐票（行业）回填。`market_snapshot` 行一行不改。

**Tech Stack:** Python 3.12 / SQLite / pandas / AKShare / pytest / ruff / mypy。零新依赖。

## Global Constraints

- `market_snapshot` 行不可变：侧表与主表新列是唯一写面；回填脚本 MUST NOT 触碰快照行。
- 读路径 `?mode=ro`、去 DDL 纪律不变；侧表/列缺失必须退化为原查询，MUST NOT 被误判为 `SnapshotEmptyError`。
- 参考字段缺失 → NaN（不前向填充、不以 0 冒充）；闸①按既有缺失率口径裁决。
- 回填脚本沿用 `hotdb_guard.host_write_guard` 护栏（宿主机拒跑，容器内执行）。
- 提示词模板改后必须 `uv run python scripts/deploy_prompts.py` 发布。
- 分支 `.worktrees/extend-factor-coverage-p2`（`extend-factor-coverage-p2`，基线 `ee8faeba`）。

---

### Task A: 参考数据侧表 + 读路径 JOIN + 面板可选字段（delta §6–§7）

**Files:**
- Modify: `src/finance_agent/data/snapshot.py`（DDL/migration/upsert_market_ref_rows/update_security_master_industry/read_universe*/get_security_master）
- Modify: `src/finance_agent/factors/eval_engine.py`（OPTIONAL_PANEL_FIELDS + build_panel_from_rows 可选帧/分组）
- Modify: `src/finance_agent/factors/dsl.py`（FIELDS 加 3 可选参考字段 + 模块 docstring）
- Modify: `src/finance_agent/screening/pipeline.py`（`_build_default_panel` 拼装可选帧 + groups）
- Test: `tests/test_snapshot_ref_data.py`（新）、`tests/factors/test_eval_engine.py`、`tests/factors/test_dsl.py`、`tests/screening/`（panel 拼装）

**Interfaces:**
- Produces: `upsert_market_ref_rows(rows, db_path) -> int`（rows: `{trade_date, code, float_cap?, total_cap?, turnover?, source}`）
- Produces: `update_security_master_industry(mapping: dict[str, str], db_path) -> int`
- Produces: `build_panel_from_rows` 构造 `float_cap/turnover/listed_days` 帧与 `groups={"industry": ...}`（行携带时）
- Produces: `dsl.FIELDS` 含 `float_cap/turnover/listed_days`

- [ ] **Step A1 失败测试（侧表/迁移/读路径）** `tests/test_snapshot_ref_data.py`：

```python
def test_ref_table_ddl_and_upsert_idempotent(tmp_path):
    from finance_agent.data.snapshot import init_snapshot_db, upsert_market_ref_rows
    db = tmp_path / "s.db"; init_snapshot_db(db)
    rows = [{"trade_date": "2026-11-27", "code": "600519", "float_cap": 2.0e9,
             "turnover": 0.0125, "source": "tencent"}]
    assert upsert_market_ref_rows(rows, db_path=db) == 1
    assert upsert_market_ref_rows(rows, db_path=db) == 1  # 幂等 upsert 不重复

def test_industry_column_migration_and_update(tmp_path):
    from finance_agent.data.snapshot import (init_snapshot_db, update_security_master_industry,
                                             upsert_security_master, get_security_master)
    db = tmp_path / "s.db"; init_snapshot_db(db)
    upsert_security_master([{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db)
    assert update_security_master_industry({"600519": "食品饮料"}, db_path=db) == 1
    assert get_security_master(db_path=db)["600519"]["industry_l1"] == "食品饮料"
    # upsert 上市清单不带 industry → 不得清空
    upsert_security_master([{"code": "600519", "name": "贵州茅台"}], db_path=db)
    assert get_security_master(db_path=db)["600519"]["industry_l1"] == "食品饮料"

def test_read_universe_attaches_ref_columns(tmp_path):
    # 构造 market_snapshot 行（六字段）+ 主表 + 侧表 → read_universe 行含 float_cap/turnover/
    # industry_l1；listed_days 为 NULL 时由 list_date 派生
    ...

def test_read_universe_degrades_on_old_db(tmp_path):
    # 旧库（无市场侧表/无 industry_l1 列）→ 行键集合与升级前一致（不含附加列），不抛错
    ...
```

- [ ] **Step A2 实现**（`snapshot.py`）：
  1. `SNAPSHOT_DDL` 追加 `market_ref_daily` 建表 + 索引（列：trade_date/code/float_cap/total_cap/turnover/source/updated_at）。
  2. `init_snapshot_db` 迁移：`PRAGMA table_info(security_master)` 无 `industry_l1` → `ALTER TABLE ... ADD COLUMN industry_l1 TEXT`。
  3. `upsert_security_master` 的 INSERT/UPDATE 增 `industry_l1`（COALESCE 保值）；`get_security_master` 返回该键。
  4. 新 `upsert_market_ref_rows`（幂等 upsert）；新 `update_security_master_industry`（只写非空，COALESCE）。
  5. `read_universe` / `read_universe_range`：先尝试增强 SQL（LEFT JOIN `market_ref_daily` 与 `security_master`，`listed_days` 别名 `_ref_listed_days` 后 Python 合并）；`sqlite3.OperationalError` 且信息含 `no such table: market_ref_daily` / `no such column: industry_l1` / `no such table: security_master` → 回退原 SQL（再按既有 `no such table` 语义映射 `SnapshotEmptyError`）。`include_suspended` 条件作用在 `m.is_suspended` 上。
- [ ] **Step A3 失败测试（面板/DSL）**：`build_panel_from_rows` 行含四列 → 三帧 + groups；行不含 → 不构造；停牌行 float_cap/turnover 为 NaN、listed_days 保留；`parse("log(float_cap)")` 成功且 `count_fields` 计入；未携带帧求值 `ValueError`（既有）。
- [ ] **Step A4 实现**：`eval_engine.OPTIONAL_PANEL_FIELDS=("float_cap","turnover","listed_days")`；`build_panel_from_rows` 内按列构造帧（停牌行 float_cap/turnover 置 NaN）与 `groups={"industry": pivot(industry_l1)}`；`dsl.FIELDS` 加三字段。
- [ ] **Step A5 实现**：`pipeline._build_default_panel` 拼装 `chunks[0].fields` 的可选帧（不在 PANEL_FIELDS 的键）与 `groups`（同键 concat）。
- [ ] **Step A6 门禁 + 提交**：`uv run pytest tests/test_snapshot_ref_data.py tests/factors tests/screening -q` 全绿；ruff/format/mypy；commit `feat(data): 参考数据侧表与读路径 JOIN + 面板可选字段（extend-factor-library-coverage T6/T7）`

### Task B: 增量落库（delta §8）

**Files:** `src/finance_agent/data/akshare_client.py`（`_parse_tencent_batch_ref` + `fetch_market_ref_bulk`；`fetch_market_caps_bulk` 复用）、`src/finance_agent/outcome/regime/job.py`（落侧表 best-effort）、`src/finance_agent/data/snapshot_job.py`（周一行业补抓）
**Interfaces:** `fetch_market_ref_bulk(codes) -> dict[str, dict[str,float]] | None`（`float_cap` 元 / `total_cap` 元 / `turnover` 小数）；regime 兼容旧 stub（无该方法 → 回退 `fetch_market_caps_bulk`，不落侧表行）。

- [ ] **Step B1 失败测试**：腾讯批量串解析出三字段（turnover % ÷100）；`fetch_market_caps_bulk` 返回类型不变；regime `_Stub` 含 ref 方法时落侧表（tmp 快照库）、写入抛错只 WARN（monkeypatch upsert 抛错，regime 摘要仍 produced）、旧 stub 无方法时行为不变；主表行业补抓（stub `fetch_industry`）写入、失败不拖垮日批。
- [ ] **Step B2 实现**三处；regime 侧表写入用 `try/except Exception: logger.warning`，位置在 caps 取得后。
- [ ] **Step B3 门禁 + 提交**：`tests/outcome tests/data tests/test_*` 相关 + 全 factors；commit `feat(data): 参考数据增量落库（拥挤度日批）+ 周一行业补抓（T8）`

### Task C: 历史回填脚本（delta §9）

**Files:** Create `scripts/backfill_factor_ref.py`、`scripts/backfill_industry.py`；`src/finance_agent/data/akshare_client.py`（`fetch_symbol_share_history`）；Test: `tests/scripts/test_backfill_factor_ref.py`、`tests/scripts/test_backfill_industry.py`
**Interfaces:** `fetch_symbol_share_history(code, start, end) -> DataFrame[date, close, outstanding_share, turnover] | None`（新浪 raw 腿；缺列 → None）；两脚本均支持 `--dry-run/--sleep/--limit/--allow-host-write`，缺省标的池 = 主表全量（空且未显式 → 退 2），护栏 `hotdb_guard.host_write_guard`。

- [ ] **Step C1 失败测试**：share history 解析（缺列 None）；ref 回填口径（float_cap=outstanding_share×close、turnover 原值）、源缺列计 `symbols_failed`、幂等、不动 `market_snapshot`；行业回填（探针模式成功率、只写非空、幂等）。
- [ ] **Step C2 实现**两脚本 + client 方法。
- [ ] **Step C3 提交**：`feat(scripts): 参考数据与行业历史回填脚本（T9）`

### Task D: 准入预检 + 桶归属 + builtin 七条（delta §10）

**Files:** `src/finance_agent/factors/dsl.py`（`collect_fields` 公开函数）、`src/finance_agent/factors/validate.py`（阶段 A 预检；阶段 C 桶继承）、`src/finance_agent/factors/library.py`（`find_quota_retired_duplicate`）、`src/finance_agent/factors/builtin.py`（+size_float/turnover_20）、`tests/factors/test_validate.py`、`test_library.py`、`test_builtin.py`（`_EXPECTED` 扩 7）、`test_style_exposure.py`（basket 7）
**Interfaces:** `collect_fields(node) -> tuple[str, ...]`；`find_quota_retired_duplicate(ast_json, *, db_path) -> dict | None`（返回 retired 行，取 id 最小）。

- [ ] **Step D1 失败测试**：引用未携带字段 → 闸①拒收（LOW_COVERAGE + `detail.missing_fields`）、整批不中断、落 rejected；`ind_neutralize` 无分组 → 闸①拒收（`missing_groups`）；桶继承两场景（同构 retired 且最近迁移 BUCKET_QUOTA → 沿用 category + `inherited_category_from`；否则自报）；builtin 七条逐字（`_EXPECTED` 表 + 幂等 skipped=7 + 字段缺失时两条闸①拒收）。
- [ ] **Step D2 实现**四处。
- [ ] **Step D3 门禁 + 提交**：commit `feat(factors): 闸①字段预检 + 桶归属沿用 + builtin 七条（T10）`

### Task E: 选股缺口披露（delta §11）

**Files:** `src/finance_agent/screening/ranker.py`（`rank_universe(..., stats_out=None)`）、`src/finance_agent/screening/pipeline.py`（收集 `factor_value_gaps`）、`tests/screening/test_ranker.py` / `tests/screening/test_pipeline*.py`
**Interfaces:** `stats_out: dict[int, dict] | None`，逐因子写 `{"valid_n": int}`（as_of 在 `passed_codes` 上的非 NaN 计数）。

- [ ] **Step E1 失败测试**：as_of 全 NaN 因子 → 摘要 `factor_value_gaps` 含（`valid_n==0` + 引用可选字段）；排序与「该因子不参与」逐项一致；无缺口 → `[]`。
- [ ] **Step E2 实现**；`referenced_optional_fields` 用 `collect_fields(parse(expr)) ∩ OPTIONAL_PANEL_FIELDS`。
- [ ] **Step E3 提交**：`feat(screening): 因子当日值缺口披露（T11）`

### Task F: 提案侧（delta §12）

**Files:** `src/finance_agent/factors/propose.py`（CATEGORIES 九类）、`src/finance_agent/prompts/factor_proposal.md`（算子/字段/类别/计数口径）、`tests/factors/test_propose.py`
- [ ] **Step F1 失败测试**：九类枚举逐字；枚举外拒收；模板含「九选一」「去重后的数字字面量」与新算子/字段名。
- [ ] **Step F2 实现** + `uv run python scripts/deploy_prompts.py` 发布（需 Langfuse 在线）。
- [ ] **Step F3 提交**：`feat(factors): 提案 category 九类 + 提示词模板同步（T12）`

### Task G: 收口（delta §13）

- [ ] **Step G1** 全量门禁：`uv run pytest tests/factors tests/screening tests/scripts tests/data tests/outcome -q` + ruff + format + mypy；全量 `uv run pytest -q`（环境性失败沿用 P1 归因口径）。
- [ ] **Step G2** 容器内执行回填：参考数据历史（先 20 票 dry-run → 全量，夜/闲时段）；行业（50 票探针 ≥90% → 全量）；记录覆盖率。
- [ ] **Step G3** 部署 + 真实读数：合并 PR → 主检出 pull → `docker compose restart backend`（红线三查）→ 手动补跑 `factor_builtin_register`（POST /api/v1/ops/jobs/{id}/run）观察两条真基线过闸结论；`GET /api/factors` 核对。
- [ ] **Step G4** 人工验证报告 `tests/validation/2026-11-28-extend-factor-library-coverage-p2-validation.md` + tasks.md §6–§13 勾选 + delta archive（sync 主规范）。
