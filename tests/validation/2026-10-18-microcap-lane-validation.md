# 验证报告：含退市股池 + 小票通道（add-microcap-lane-guardrails，v1 后端包）

- 变更：`openspec/changes/add-microcap-lane-guardrails/`（spec + design，binding）
- 计划：`docs/superpowers/plans/2026-10-18-microcap-lane.md`（Task 1–6）
- 工作区：`.worktrees/personal-edge-program`（分支 `feat/personal-edge-program`）
- 日期：2026-10-18｜执行者：agent（SDD 子任务 1–6）
- 提交：`e6e0a21f`(T1) / `c1643e06`(T2) / `ebf94033`(T3) / `cf4ef0c4`(T4) / `c9b5d521`(T5) / 见 T6 收口提交

## 1. 机器门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| 回归套件 | `uv run pytest tests/data/ tests/screening/ tests/outcome/ -q` | **1249 passed**（4 warnings，均为既有 Deprecation） |
| 附加套件 | `uv run pytest tests/scripts/ tests/test_api_screening.py tests/test_api_screening_batches.py -q` | **163 passed**（脚本 124 + API 39） |
| Lint | `uv run ruff check src/ tests/ scripts/` | **All checks passed** |
| 类型 | `uv run mypy`（目标 `src`，配置 `files=["src"]`） | 每文件与 HEAD 对比**零新增**：`akshare_client.py` 9→9、`screening/store.py` 3→3、其余改动文件 0→0；新文件 `data/universe.py` **Success: no issues** |
| OpenSpec | `openspec validate add-microcap-lane-guardrails --strict` | **Change 'add-microcap-lane-guardrails' is valid** |

TDD 先红后绿证据逐任务见 `.superpowers/sdd/microcap/task-1-6-report.md` §2；核心回归（Task 3）
另有双向变异验证：`universe_visible` 改为恒 `True`（上界不判）→ `t>退市日` 用例红；
改为「带退市日一律剔除」→ `t<退市日` 用例红（两种幸存者偏差形态均被测试拦截）。

## 2. 真实数据实探（2026-10-18，本机 akshare 1.18.94）

退市清单抓取（`AKShareClient.fetch_delist_lists()`，一次真实调用）：

| 源 | 行数 | 含 `delist_date` | 列形映射 |
|---|---|---|---|
| `stock_info_sh_delist`（source=`sh_suspend`） | 159 | 159 | `公司代码/公司简称/上市日期/暂停上市日期` → `code/name/list_date/delist_date` |
| `stock_info_sz_delist`（source=`sz_delist`） | 209 | 209 | `证券代码/证券简称/上市日期/终止上市日期` → 同上 |
| 合计 | 368 | 368 | `sources_seen["delist"] == {"sh_suspend","sz_delist"}` |

- 沪源日期列确为「**暂停上市日期**」（口径已在 `docs/evals/metrics.md` §1.16 注明，不得读作交易所退市日）；
- 落主表 362 行：沪清单内部有 **6 条重复行**（同码同日期，如 600190/600555/600614/600625/600680/600695）→ 幂等 upsert 合并，与幂等契约一致；
- 样本：`600001 邯郸钢铁 1998-01-22 → 2009-12-29`；`600190 退市锦港 1998-05-19 → 2025-07-28`。

## 3. 退市股历史补采通道实测（Task 4）

`uv run python scripts/backfill_delisted.py`（临时库，不触生产库）：

- `--dry-run --limit 2`（真实 368 码池）：`退市股补采完成: symbols_ok=0 rows_written=0 skipped_resumed=0 history_missing=2 window_missing=0 [dry-run]`，逐票 `无历史可得（源无数据，如实标注）`；
- 真实补采 `--limit 1`（600190）：`history_missing=1`、`rows_written=0`、收尾完整性自检通过、退出码 0。

**实测结论（重要，遗留项）**：既有**双口径**回填通道 `fetch_symbol_history_raw_qfq`（东财 raw+qfq 两腿按日期内连接，
东财封禁时回退新浪）对退市码**两腿均不可得**（600190/600001/600555/600614/600625/000003 全 None：东财 `ConnectionError`、
新浪 `JSONDecodeError`）；而 `fetch_kline` 三源链（东财→新浪→**腾讯**）实测可由腾讯返回退市前历史
（600190 返回至 2025-07-18、600001 返回至 2009-12-15），但为**单口径**（无复权列）。脚本按 design
「数据源无历史时如实标注 `history_missing`，不伪造」处理：**未写入任何退市股价行**。
退市股价格序列要真正入池，需要「单口径 + 口径留痕」通道决策（见 §6 遗留 1）。

## 4. 池可见性核对（真实主表行）

`600190 退市锦港`（`list_date=1998-05-19`、`delist_date=2025-07-28`，真实清单抓取产物；行情行为内存态核对输入）：

| 时点 t | `universe_visible` | 池（`filter_snapshot_rows_for_date`） |
|---|---|---|
| 2025-07-18（< 退市日） | True | 含退市标的（行情行全保留） |
| 2025-07-28（= 退市日） | True | —（边界含当日，口径「≤」） |
| 2025-08-01（> 退市日） | False | 剔除（即使存在残留行情行） |

## 5. 人工项：批次数据核对（临时库，2026-10-18）

构造：临时快照库（3 只合成标的：1 亿 / **2000 万** / 500 万；80 交易日日历）+ 主表含**真实退市行**（600190）
+ `regime_daily` 当日行；因子库为空（走 `selection_mode=fallback`，不影响档位/门禁核对）。
先跑主档 `run_screening_batch`，再跑 `run_microcap_lane_daily`：

| 字段 | 主档批次行 | 小票通道批次行 |
|---|---|---|
| `pool` | `main` | `microcap` |
| `l1_passed` | **1**（2000 万标的被 5000 万档剔除） | **2**（1000 万档通过） |
| `l2_top` / `l3_keep` | 1 / 1 | 2 / 2 |
| `pool_size` | 0（首次运行，连续在榜 1 日 < 门槛 3） | 0（同上） |
| `frontier_gate` | `NULL` | **`ready`**（退市池就绪 + regime 新鲜，双判据齐备） |
| `selection_mode` | `fallback` | `fallback` |
| 记录字段 | — | `suggested_single_cap=0.05`、`slippage_tier_bump=1`、`orders_placed=0` |

- **主档零变化**：通道批次落库后，主档行仍为 `l1_passed=1 / pool='main' / frontier_gate=NULL`；
  既有读侧 `list_batches()`（缺省 `pool='main'`）只见主档 1 行，`latest_screening_batch()` 亦返回主档行；
- 门禁三态（双缺 / 单缺 ×2 / 齐备）与陈旧边界（7 日新鲜、8 日陈旧）在
  `tests/screening/test_microcap_lane.py::TestFrontierGate` 逐条覆盖；
- **不下单**：本核对全程未触碰 `execution/`（另有用例把 `run_execution_daily` / `run_paper_settle_daily`
  打桩为抛错，通道路径不触发）。

**待 owner 执行的生产侧人工核对**（本报告不含，因需真实数据积累）：

```bash
# 部署后（主档 15:40 与小票通道 16:45 各跑一轮）：
uv run python -c "
from finance_agent.screening.store import latest_screening_batch, list_batches
print(latest_screening_batch())                      # 主档：pool=main、frontier_gate=None
print(latest_screening_batch(pool='microcap'))       # 通道：pool=microcap + frontier_gate 披露
print(len(list_batches()), len(list_batches(pool='microcap')))
"
```

## 6. 遗留与待决策

1. **退市股价格历史口径（需 owner 决策）**：可得源（腾讯兜底）为单口径、无复权列，写入 `market_snapshot`
   需要 `close_qfq`；按「不产出半口径数据」纪律本轮**未落库**，脚本如实标注 `history_missing`。
   选项：(a) 增设「退市股单口径回填 + 口径留痕列」通道；(b) 维持现状（池可见性可用、价系列缺失）。
2. **E2E 未执行**：本变更 v1 明确无 UI；E2E 用例已随注册表计数同步更新（`18→19`、下标 17/18），
   未运行以免污染共享测试库（留待 CI 门禁）。
3. **生产库未写入**：`security_master.delist_date` 与退市清单在生产库的落库经**快照日批主表刷新分支**
   （名单为空或每周一）发生；首次部署后需等一次主表刷新（或手动触发日批）方有退市数据。
4. `selection_mode=fallback` 出现在核对中仅因临时因子库为空；真实环境沿用既有因子路径逻辑不变。
