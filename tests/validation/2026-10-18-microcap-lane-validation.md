# 验证报告：含退市股池 + 小票通道（add-microcap-lane-guardrails，v1 后端包）

- 变更：`openspec/changes/add-microcap-lane-guardrails/`（spec + design，binding）
- 计划：`docs/superpowers/plans/2026-10-18-microcap-lane.md`（Task 1–6）
- 工作区：`.worktrees/personal-edge-program`（分支 `feat/personal-edge-program`）
- 日期：2026-10-18｜执行者：agent（SDD 子任务 1–6）
- 提交：`e6e0a21f`(T1) / `c1643e06`(T2) / `ebf94033`(T3) / `cf4ef0c4`(T4) / `c9b5d521`(T5) / `814be43e`(T6 收口)

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

**实测结论（两轮观测，结论已更正）**：既有**双口径**回填通道 `fetch_symbol_history_raw_qfq`
（东财 raw+qfq 两腿按日期内连接，东财封禁时回退新浪）对退市码的可得性**随源可用性波动**：

- 第一轮（18:0x）：600190/600001/600555/600614/600625/000003 全 None（东财 `ConnectionError` +
  新浪 `JSONDecodeError`）→ 脚本如实标注 `history_missing`，未写入任何行（旧判据下也**未谎报覆盖**）；
- 第二轮（19:1x，同一脚本同一码）：**源恢复**，600190 成功写入 **6175 行**（1999-06-09..2025-07-18，
  amount/high/low/close_qfq 全齐备，覆盖退市前全部交易日）→ 说明退市股历史**可经既有双口径通道入池**，
  第一轮的不可得属**源侧短暂不可用窗口**（非结构性缺口）；`fetch_kline` 三源链（腾讯兜底）在同一窗口仍可用，
  为单口径（无复权列），仅作交叉印证。

**口径提醒**：可得性有波动 → 运维补采需可重入（本脚本为 INSERT OR REPLACE + 覆盖判据，重跑安全）；
`history_missing` 非零只代表**本轮**源不可得，不代表该码永远无历史。

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

1. ~~**退市股价格历史口径（需 owner 决策）**~~ → **已澄清（round 2）**：既有双口径通道可用，第二轮实测
   600190 写入 6175 行（见 §3）；先前「不可得」为源侧短暂窗口，无需新增单口径通道。
   残余注意：源不可用窗口内 `history_missing` 非零属**如实标注**，重跑即可补齐（脚本幂等）。
2. **E2E 未执行**：本变更 v1 明确无 UI；E2E 用例已随注册表计数同步更新（`18→19`、下标 17/18），
   未运行以免污染共享测试库（留待 CI 门禁）。
3. **生产库未写入**：`security_master.delist_date` 与退市清单在生产库的落库经**快照日批主表刷新分支**
   （名单为空或每周一）发生；首次部署后需等一次主表刷新（或手动触发日批）方有退市数据。
4. `selection_mode=fallback` 出现在核对中仅因临时因子库为空；真实环境沿用既有因子路径逻辑不变。


## 7. 评审修复复核（round 2，2026-10-18）

| 项 | 修复 | 验证证据 |
|---|---|---|
| F1 断点判据缺覆盖语义 | `scripts/backfill_delisted.py` 改**整窗覆盖判据**（行齐备 + 最早行触及窗口起点锚 = 日历中首个 ≥ `list_date` 的交易日；只判头部），新增 `partial_overlap` 明细 | 先红：`test_single_window_row_is_not_skipped`（1 行日批行 → 旧判据跳过、零取数）→ 绿；`test_calendar_anchor_allows_skip_when_head_is_first_trading_day` 先红后绿；真实复跑摘要 `skipped_resumed=0 partial_overlap=0 rows_written=6175` |
| F2 审计来源未落库 | `security_master.delist_source` 列（ALTER 迁移）+ `upsert_delisted_masters` 写入 + `get_security_master` 读出；§1.16 措辞同步 | 先红 4 例 → 绿；真实主表读回 `delist_source='sh_suspend'` |
| F3 迁移原子性 | 重建改**显式事务**逐条执行（`BEGIN IMMEDIATE`…`ROLLBACK/COMMIT`，关闭 legacy 隐式提交）；`*__migrate` 残留**恢复路径**（数据以 migrate 表换入）；`_ensure_batch_columns` 列补齐在恢复之后 | 先红 3 例 → 绿：中断残留恢复（批次表/池表）、失败注入 `SELECT * FROM table_does_not_exist` → 原表与旧行在位、无残留 |
| F4 日期列漂移 | 缺**退市日列** → 该源判不可用（`[]` + ERROR）；缺**上市日列** → 降级保留 + WARN；行级缺失仍 None | 先红 3 例 → 绿（含行级/列级区分） |
| F4b 缺失明细持久留痕 | 非 dry-run 落 `snapshot_meta["delist_backfill_missing"]`（JSON：history/window/partial/skipped/updated_at） | 先红 2 例 → 绿；真实运行 meta 读回 `{"history_missing": [], "window_missing": [], ...}` |
| F5 批次执行约束记录字段 | `screening_batches.exec_constraints`（JSON）+ 3 SELECT/UPSERT/迁移同步 + microcap 写入 `{"suggested_single_cap":0.05,"slippage_tier_bump":1}` | 先红 3 例 → 绿（store 三读侧一致、主档 None、老库补列、非法值拒收） |
| F6 反向依赖 | `screening → outcome.regime` 改函数内延迟 import | `test_pipeline_has_no_module_level_regime_dependency` 先红后绿 |
| F7 形参遮蔽 | `list_selection_pool` 局部 `pool` 列表改名 `rows_out` | 既有 30 例回归绿 |

round 2 机器门禁：`uv run pytest tests/data/ tests/screening/ tests/outcome/ -q` → **1261 passed**；
`ruff check src/ tests/ scripts/` → **All checks passed**；`uv run mypy` 改动文件零新增
（`screening/store.py` 由 3 errors → **0**，全仓 81→78）。

真实复跑（临时库 `one.db`，600190）：`symbols_ok=1 rows_written=6175 skipped_resumed=0 partial_overlap=0
history_missing=0`；`read_universe("2025-07-18")` 池含退市标的（1/1）、`t=2025-07-28` 边界可见、
`t=2025-08-01` 剔除（残留行）。

**残余注意（F1 口径取舍）**：覆盖锚 = 窗口起点后的首个交易日，故当「源历史起点晚于 `list_date`」
（如 600190：清单 `list_date` 1998-05-19 有重复行/数据瑕疵，实际行情起于 1999-06-09）时，覆盖判据
永不满足 → **每次运行都会重取**（幂等、成本 = 一次取数）。属保守取舍（宁可重取不可谎报覆盖），
如后续需要可在清单侧修 `list_date` 或引入「源历史起点」留痕后再放宽。
