# 验证报告：可转债覆盖（add-convertible-bond-coverage，v1 后端包）

- 变更：`openspec/changes/add-convertible-bond-coverage/`（spec + design，binding）
- 计划：`.superpowers/sdd/cbb/task-1-brief.md` … `task-6-brief.md`（Task 1–6）
- 工作区：`.worktrees/personal-edge-program`（分支 `feat/personal-edge-program`）
- 日期：2026-10-18｜执行者：agent（SDD 子任务 1–6）
- 提交：`3929e177`(T1) / `444fa445`(T2) / `05feadb5`(T3) / `dcb30103`(T4) / `eb4b7827`(T5) / 本报告(T6 收口)

## 1. 机器门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| 回归套件 | `uv run pytest tests/outcome/ tests/test_api_events.py -q` | **877 passed**（3 warnings，均为既有 Deprecation） |
| 抓取映射套件 | `uv run pytest tests/data/test_akshare_cb_fetchers.py -q` | **11 passed**（fake DataFrame，零网络） |
| 本变新增单测 | `uv run pytest tests/outcome/test_cb_model.py tests/outcome/test_cb_sync.py tests/outcome/test_cb_status.py tests/outcome/test_cb_job.py tests/outcome/test_events_watchlist.py -q` | **72 passed** |
| Lint | `uv run ruff check`（改动文件 + `tests/outcome/` + `tests/data/`） | **All checks passed** |
| 格式 | `uv run ruff format --check`（改动文件） | 全绿（pre-commit hook 亦强制） |
| 类型 | `uv run mypy`（新文件 `cb_model.py`/`cb_sync.py`/`cb_status.py`/`watchlist.py`/`events/job.py`） | 逐文件对比 HEAD **零新增**（`akshare_client.py` 9→9 均为存量；新文件 Success） |
| OpenSpec | `openspec validate add-convertible-bond-coverage --strict` | **Change 'add-convertible-bond-coverage' is valid** |
| 前端冒烟 | `cd frontend && npx vitest run src/test/eventWatchlistCard.test.tsx` | **22 passed**（含 cbb 四要素渲染 + 按 `source=cbb` 过滤取数） |

TDD 先红后绿证据逐任务见 `.superpowers/sdd/cbb/task-1-6-report.md` §2。

## 2. 真实数据实探（2026-10-18，本机 akshare 1.18.94）

| 接口 | 行数 | 结论 |
|---|---|---|
| `ak.bond_zh_cov()` | 1063 | 标的要素主源；列含 `债券代码/债券简称/正股代码/转股价/转股溢价率/发行规模/信用评级`——**无剩余规模列** |
| `ak.bond_cb_redeem_jsl()` | 317 | 强赎事件主源；列含 `强赎触发价/强赎触发比/强赎天计数`（样本 `"12/15 | 30"`）/`强赎状态`（`''` 275 / `公告不强赎` 35 / `已公告强赎` 7）/`最后交易日`/`剩余规模`；**无公告日字段** |
| `ak.bond_zh_hs_cov_spot()` | 320 | 行情源；`trade`（元）/`amount`（元）可得（设计「行情源若不含成交额则置空」不触发） |
| `ak.bond_cb_adj_logs_jsl(symbol=…)` | 逐代码 | 下修源**存在且可用**（样本 广汇转债 2024-05-21 4.03→1.5）；**无全市场批量模式**（空 symbol 返回 0 行） |
| 回售类接口 | — | **不存在**（`dir(ak)` 无回售接口、接口 docstring 无「回售」命中；仅 `bond_zh_cov_info` 逐代码详情含回售条款文本/触发价） |

单位核验（交叉核对）：`剩余规模` = **亿元**（金田转债 规模 15.000 / 剩余规模 9.905，与 `bond_zh_cov.发行规模` 同口径同数量级）；
`转股溢价率` = **%**；`trade`/`债现价` = 元（两源同代码取样一致，如 110076 双方均 109.948）。
代码覆盖：317 个强赎代码中 315 个在 `bond_zh_cov`（2 个缺，`132024/132026`）、303 个在 spot 源（14 个无有效价，抓取层跳过并计数）。

裁决（本次实施确认，已落 metrics §1.17）：① 下修 = **转股价变化触发有界接入**（变化检出集合逐代码查询，日调用量 0~10）；
② 强赎 `announce_date` = **首次观测日**（源无公告日，键稳定幂等）；③ v1 事件类型 = 强赎 + 下修，回售源缺失待 owner 决策。

## 3. 人工项：清单条目字段与倒计时手算抽样核对（真实数据实跑）

实跑（临时库，非测试）：`run_cb_daily(client=AKShareClient(), as_of="2026-10-16", snapshot_db_path=<含真实交易日历的临时快照库>)`。

- 摘要：`master 1063/1063`、`quotes 306`（14 条无有效价跳过）、`events 317`、`adj queried=0`（首跑无变化基线）、
  `watchlist_added=317`、`failed_core_sources=[]`、`failed_secondary_sources=[]`、`calendar_available=true`。

**倒计时手算核对**（as_of = 2026-10-16（周五）；日历 = `tool_trade_date_hist_sina` 真实交易日）：

| 代码 | 名称 | 源 最后交易日 | 载荷 `call_countdown_days` | 手算（as_of 之后含端点） | 一致 |
|---|---|---|---|---|---|
| 110076 | 华海转债 | 2026-10-27 | 7 | 10-19,20,21,22,23,26,27 = 7 | ✅ |
| 113046 | 金田转债 | 2026-10-20 | 2 | 10-19,20 = 2 | ✅ |
| 123076 | 强力转债 | 2026-11-13 | 20 | 10-19…23 + 10-26…30 + 11-02…06 + 11-09…13 = 5×4 = 20 | ✅ |
| 127025 | 冀东转债 | 2026-10-30 | 10 | 10-19…23 + 10-26…30 = 10 | ✅ |
| 128136 | 立讯转债 | 2026-10-28 | 8 | 10-19…23 + 10-26,27,28 = 8 | ✅ |
| 128138 | 侨银转债 | 2026-11-11 | 18 | 10-19…23 + 10-26…30 + 11-02…06 + 11-09,10,11 = 5+5+5+3 = 18 | ✅ |

**四要素抽样（与源值逐项比对）**：

| 代码 | 载荷 `price` | 源 spot `trade` | 载荷 `premium_rate` | 源 `转股溢价率` | 载荷 `remaining_size` | 源 `剩余规模` |
|---|---|---|---|---|---|---|
| 110076 华海转债 | 109.948 | 109.948 | 9.88 | 9.88 | 18.384 | 18.384 |
| 113046 金田转债 | 101.332 | 101.332 | 1.63 | 1.63 | 9.905 | 9.905 |

**载荷覆盖率（317 条 cbb 条目，缺值一律 null 不折算）**：

| 载荷键 | 非空 | null | null 归因 |
|---|---|---|---|
| `price` | 303 | 14 | 该 14 只无有效 spot 行（停牌/零成交/未上市，抓取层跳过）——含 128136 立讯/128138 侨银两只已公告强赎 |
| `premium_rate` | 315 | 2 | `132024`/`132026` 不在 `bond_zh_cov` 标的源（主表无覆盖，不编造） |
| `remaining_size` | 317 | 0 | 全部来自强赎源 `剩余规模`（亿元） |
| `call_countdown_days` | 6 | 311 | 仅「已公告强赎 + 源含最后交易日」者可算（其余无最后交易日，MUST NOT 猜） |
| 标的 `name` | 315 | 2 | 同 `premium_rate`（主表未覆盖的 2 只） |

**规则化理由抽样**（零 LLM）：- 113046：`已公告强赎；天计数 12/15；距最后交易日 2 个交易日`（状态取源 `强赎状态='已公告强赎'`，天计数原样保留）；
- 110077 洪城转债：`强赎状态不可判定；已公告强赎但缺 last_trade_date（不可判定剩余交易日）`、
  `call_countdown_days=null`——**真实数据里确实存在「已公告强赎但源缺最后交易日」形态**（spec 场景「要素缺失如实标注」），
  实现按不可判定处理、未猜数值；
- 5 条「临近到期 <span>…」文案（华海/强力/冀东/立讯/侨银）天计数不可解析，但源状态为 `已公告强赎`
  → 走 announced 分支 + 倒计时（若按「天计数先行」会误判为不可判定，故状态优先级取 announced 最高，已在 §1.17 写明）。

## 4. 边界与负面路径（测试覆盖，非实跑）

- 接口失败不落行：核心源（标的/强赎）失败 → 成功部分先落库 + 上抛（`run_job` 记 failed）；
  次要源（行情/逐代码下修）失败 → 摘要 `failed_secondary_sources` 留痕不阻断（对应要素 null）。
- 非交易日：`as_of` 不在快照日历 → 行情跳过（`skipped_reason=non_trading_day`，且不发起抓取）；
  **日历不可得 → 同样跳过（`skipped_reason=calendar_unavailable`）**，两种情形均 MUST NOT 用过期价冒充当日（评审 F4）。
- 幂等：主表（代码唯一，`remain_scale` COALESCE 保值）/行情（交易日+代码唯一）/事件（`code|event_type|announce_date` 唯一，空键不绕过）/清单（`forecast_key` 唯一）
  重复运行零重复；cbb 条目注记在**日历可得**时刷新（倒计时递减）但 `status`/`session_id`/`created_at` 零改写；
  日历不可得时只插不覆盖（评审 F2）。条款事件条目的深度分析入口 v1 禁用（后端 409、前端不渲染，评审 F3）。
- 状态推导边界：天数恰好达标（含边界）→ triggered、差一天 → counting；日历跨周末；
  缺 `last_trade_date`（已公告）→ undetermined + note；斜杠日期/越界计数 → 不可解析不 triggered（评审 F1）；
  `as_of` 非规范 ISO → ValueError。

## 5. 遗留与待决策

1. **回售事件源缺失**：spec 事件类型清单含回售，akshare 无回售公告接口 → v1 仅强赎 + 下修；
   提请 owner 决策是否修订 spec 事件类型清单（或另立数据源提案）。
2. **清单条目量**：首跑 317 条 cbb 条目（强赎列表全量）。当前无准入过滤（spec 场景要求事件入清单）；
   若 owner 认为量级过大，可后续加规则化准入（如仅 announced/triggered 入清单）——属行为变更，需新 delta。
3. **`132024/132026` 两只代码不在标的源**（主表无覆盖）：其 cbb 条目 `name`/`premium_rate` 如实为 null（前端 `—`），未编造。
4. **`cb_events` 无专用状态列**（DDL 按 brief 冻结）：`强赎状态` 落在 `raw_json`，状态推导在读时提取；
   留痕损坏 → 不可判定（写明原因），已测。
5. **`cb_quotes` 行情源为实时快照**（无历史回补）：日批仅在交易日落当日行；历史行情回补不在本变范围。
6. **`cb_master.remain_scale` v1 恒 NULL（来源约束）**：标的源（`bond_zh_cov`）无剩余规模列，MUST NOT 以发行规模冒充；
   清单条目的 `remaining_size` 改由强赎事件行的 `剩余规模`（亿元）供给（317 条全覆盖）。
   若后续要从更全来源回填该列（强赎列表子集之外的标的），需另立数据源/提案（本变不做）；
   upsert 已加 COALESCE 保值（评审 F6），日后回填不会被日常同步清空。控制器已同步在 delta spec 增「可得来源内」限定。

## 6. 结论

- [x] 全部机器门禁通过（pytest / ruff / mypy 零新增 / openspec strict / 前端 vitest 冒烟）
- [x] 人工项（清单条目四要素与倒计时手算抽样核对）**通过**（§3 六条倒计时全部与手算一致；两例四要素与源值逐项一致）
- [x] 本变纯增量：不新增页面、不接执行链、不产生订单、零 LLM 准入
- 可进入 sync + archive（回售事件类型清单待 owner 决策，见 §5.1）

## 7. 评审修复复核（round 2，2026-10-18）

评审结论 Needs fixes（5 Important + 4 小项），逐项修复（TDD 先红后绿，逐条有先红证据）：

| 项 | 问题 | 修复 | 提交 | 先红 → 后绿 |
|---|---|---|---|---|
| F1 | 天计数正则过宽：`2026/10/27` 被读成 `2026/10` → 误判 triggered | 结构锚定（必须带 `| 上限天数` 段）+ 合理性校验（`1 ≤ days_needed`、`days_done ≤ days_needed`） | `f062b5fb` | 4 failed → 27 passed |
| F2 | 兄弟日批（预告分诊）无日历调 `build_watchlist` → 清空 cbb 倒计时注记 + 误称「日历不可用」 | 无日历只插不覆盖（`refresh = bool(days)`） | `8d11ed4f` | 2 failed → 30 passed |
| F3 | cbb 条目可发起空标的深度分析（`symbol=""` 建会话入队） | v1 禁用：API 对 `source=cbb` 409 + 前端不渲染入口（控制器裁决） | `2f163fd8` | 后端 1 failed / 前端 1 failed → 18 passed / 23 passed |
| F4 | 日历不可得时按 `as_of` 落行情（与 §1.17「MUST NOT 拿前一日价冒充当日」冲突） | `calendar is None` → 跳过（`skipped_reason=calendar_unavailable`）不抓取不落行 | `abe1275f` | 4 failed → 42 passed |
| F5 | `cb_master.remain_scale` 恒 NULL 与 spec 措辞差 | 代码侧不动；§5.6 登记来源约束（spec 由控制器改「可得来源内」） | 本报告 | — |
| F6① | counting 缺 `last_trade_date` 无 note | 补 note（与 §1.17 字面一致） | `f062b5fb` | 含上 |
| F6② | master upsert 会清空已回填的 `remain_scale` | 该列 `COALESCE(excluded, 既有)` | `abe1275f` | 含上 |
| F6③ | 显式 `event_key=None` 绕过合成（SQLite TEXT PK 允许 NULL → 重复行） | `if not item.get("event_key")` 合成 | `abe1275f` | 含上 |
| F6④ | 字面量 `"转债名称"` / `WARNNING` 拼写 | 走 `_CB_ADJ_NAME_COL` 常量 + 拼写修正 | `c40869e4` | 29 passed（映射行为不变） |

修复后复跑门禁：`uv run pytest tests/outcome/ tests/test_api_events.py -q` → **888 passed**；
`uv run ruff check` 改动文件全绿；新文件 mypy 零新增（`api.py` 8→8、`akshare_client.py` 9→9 均为存量）；
前端 `npx vitest run src/test/eventWatchlistCard.test.tsx` → **23 passed**、`npx tsc --noEmit` 干净；
真实数据实跑复核（§3 同款，2026-10-18 二次跑）：标的 1063 / 行情 306 / 事件 **318**（源当日新增 1 只）→
清单 318 条、倒计时非空 **6 条且数值与首跑逐一相同**（7/2/20/10/8/18）——6/6 手算一致结论不因修复漂移；
状态分布 311 counting / 6 announced / 1 undetermined（`强赎天计数` 311 条 pipe 结构全解析、6 条到期文案如实不解析）。
