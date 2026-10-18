# Convertible Bond Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付「可转债覆盖」：转债标的/行情/条款事件落库 + 确定性强赎状态推导 + 接入既有事件观察清单（`source=cbb`，规则化标注，零 LLM）。

**Architecture:** 复用公告线（change 3）的 `outcome/events/` 存储与清单端点，新增 `cb_master`/`cb_events` 表（同一 `EVENTS_DB_PATH` 库）+ `cb_model.py`/`cb_sync.py` 小文件；数据源用侦查已实证的 akshare 接口；状态推导纯函数；新 job 16:45 段（以实施时点实际空档为准）。

**Tech Stack:** Python 3.12 / SQLite / APScheduler / pytest。无 UI 新增（复用选股页事件观察区块，`source` 过滤）。

## Global Constraints

- 工作目录 = `D:\WorkSpace\finance_analysis_agent\.worktrees\personal-edge-program`；提交带路径；不碰主检出/openspec 主库。
- 规范：`openspec/changes/add-convertible-bond-coverage/`（spec + design，binding）。
- 口径先登记 `docs/evals/metrics.md` §1（§1.17）。
- **依赖前置：本计划假设 change 3（add-event-driven-lane）已落地**（`outcome/events/` 四表、清单端点 `GET /api/events/watchlist?source=`、`build_watchlist`）。未落地时停手等前置。
- 状态推导 MUST NOT 调用 LLM；要素缺失 → 不可判定 + 注明，MUST NOT 猜测。
- 不接执行链（不产生订单）；不做双低选股/回测（当期无双低标的，见调研备忘录 §2）。
- 侦查已核事实：`bond_zh_cov()`（1062 行，含 `转股价/转股价值/转股溢价率/正股代码/信用评级`，**无剩余规模**）；`bond_cb_redeem_jsl()`（317 行，含 **`强赎触发价/强赎触发比/强赎天计数`（形如 `"12/15｜30"`）`/强赎状态/最后交易日/剩余规模`**）；`bond_zh_hs_cov_spot()`（全市场快照 ~320 只）；`bond_cb_jsl()` 仅 30 行（分页/cookie 未解，**不作为主源**）；`bond_zh_hs_cov_daily` 单券日线可用。

---

### Task 1: 口径登记（metrics.md §1.17）

- [ ] 追加小节：转债字段口径（来源接口与单位：规模=亿元、溢价率=%）、事件类型（强赎/下修/回售——**实现范围以可用数据源为准**）、状态推导规则（天计数解析、倒计时按交易日历、不可判定语义）、清单准入（规则化、零 LLM）、家族隔离声明（不进 T+20 统计）。提交 `docs(cbb): metrics.md §1.17 登记`

### Task 2: 转债数据落库（`outcome/events/cb_model.py` + `cb_sync.py`）

**Interfaces:**
- `cb_model.py`：DDL `cb_master(code PK, name, stock_code, convert_price, premium_rate, remain_scale, rating, updated_at)`；`cb_events(event_key PK, code, event_type, announce_date, trigger_price, trigger_ratio, count_text, last_trade_date, remain_scale, raw_json, updated_at)`（`event_key = code+event_type+announce_date`）；`init_cb_db` 并入 `init_events_db` 迁移链；upsert/查询函数
- `cb_sync.py`：`sync_cb_master(db_path=None, client=None)`（`bond_zh_cov` → master；溢价率随行情更新）、`sync_cb_events(db_path=None, client=None)`（`bond_cb_redeem_jsl` → 强赎事件；**探针步骤**：试 `bond_cb_adj_logs_jsl`（转股价调整=下修）与回售类接口是否存在——存在则同法接入，不存在则事件类型仅强赎并在报告提请 owner 决策是否修订 spec）
- 侦查要点：`bond_zh_cov` 的 `转股溢价率` 为 %（核对样本）、规模单位亿元（核对）；`bond_cb_redeem_jsl` 的 `强赎天计数` 形如 `"12/15｜30"`（解析规则：已触发天数/所需天数 | 上限天数）

- [ ] 先红后绿：fakes 驱动 —— 字段映射与单位归一 / upsert 幂等 / 事件唯一键 / 接口失败 `[]`+error 不落行
- [ ] 探针并记录（下修/回售接口存在性），提交 `feat(cbb): 转债标的与条款事件落库（强赎先行）`

### Task 3: 强赎状态推导（纯函数）

**Interfaces:**
- `cb_status.derive_redeem_status(event: dict, *, as_of: str, calendar: Sequence[str]) -> dict`
  → `{"state": "counting"|"triggered"|"announced"|"undetermined", "days_done": int|None, "days_needed": int|None, "trading_days_to_last": int|None, "note": str|None"}`
  规则：解析 `count_text` → 已触发天数/所需天数；`as_of ≥ announce_date` 且天计数满足 → `triggered`；`强赎状态=="已公告强赎"` → `announced`；`last_trade_date` 存在 → 按交易日历算剩余交易日；任一要素缺失 → `undetermined` + note 指明缺失字段

- [ ] 先红后绿：手算对照（含边界：天数恰好达标/差一天；日历跨周末；缺 `last_trade_date` → undetermined）；提交 `feat(cbb): 强赎状态推导纯函数（先红后绿）`

### Task 4: 清单接线（复用事件观察）

**Files:** Modify `outcome/events/watchlist.py`（`build_watchlist` 增 `source="cbb"` 分支）；Test 扩展

- [ ] 先红后绿：`sync_cb_events` 造 2 条（1 强赎 counting + 1 undetermined 缺要素）→ `build_watchlist` 产出 `source=cbb` 条目，要素含 现价（`bond_zh_hs_cov_spot` 可得时）/溢价率/剩余规模/倒计时（不可得如实 null + note）；零 LLM 调用断言；`GET /api/events/watchlist?source=cbb` 仅回转债条目
- [ ] 提交 `feat(cbb): 条款事件接入事件观察清单（source=cbb）`

### Task 5: 日批 job + 调度

- [ ] `run_cb_daily`（sync_master → sync_events → build_watchlist）注册进四件套（时刻取实施时点空档，如 16:45；与 microcap 通道时刻协调，均占用时以实际清单为准错峰）；计数断言同步（以实施时点实际 job 数为准）；幂等 + 失败留痕 + 进 ops 清单
- [ ] 先红后绿（job 三态：正常/接口失败不落行/重复运行零重复）；提交 `feat(cbb): 转债日批 job 与调度注册`

### Task 6: 收口验证

- [ ] `uv run pytest tests/outcome/ tests/test_api_events.py -q` 全绿；`ruff check` 干净；新文件 mypy 零新增
- [ ] 前端冒烟：选股页事件观察区块按 `source=cbb` 过滤可见（用既有 vitest/E2E 基建抽样一条，不新增大套件）
- [ ] `openspec validate add-convertible-bond-coverage --strict` 通过；验证报告落 `tests/validation/`（人工项：清单条目字段与倒计时手算抽样核对）
