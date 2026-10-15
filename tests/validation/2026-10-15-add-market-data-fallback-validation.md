# 人工验证报告: add-market-data-fallback

**日期**: 2026-10-15
**验证人**: ZCode 验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: openspec/changes/add-market-data-fallback/
**E2E 门禁**: 不适用（纯后端数据管道变更，无前端 UI / SSE / 会话切换 / 状态流转）
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\ui-wiring`（分支 feat/chain-repair，基于 origin/main）；执行时段北京时 2026-10-15 15:05–15:30（**收盘后**，spot 反映 2026-10-15 收盘）
**复现入口**: `uv run python tests/validation/2026-10-15-add-market-data-fallback-live-check.py`（自包含，真实 akshare；快照日批落 tempfile 隔离库，不动主检出 `data/market_snapshot.db`）

> **背景（本次解决的受阻项）**: `tests/validation/2026-10-13-add-market-snapshot-validation.md` 的 #1/#2/#6 因东财行情域 IP 封禁（#325）**全部受阻、无任何真实行数据**。本次新增新浪回退后，同类 live 步骤首次取得真实读数（见下）。

## 验证结果

| # | Scenario（delta spec） | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 主源可用直接返回 | 东财非空 → 原样返回、不调新浪、`sources_seen` 记 eastmoney | 单测 `test_eastmoney_success_returns_frame_without_touching_sina`（`out is spot` + `stock_zh_a_spot.assert_not_called()` + `{"eastmoney"}`）。**live 未命中**：本机东财恒失败（见异常 1），主源路径仅单测证据 | ✅（单测） |
| 2 | 主源失败回退新浪并保形 | 新浪经列映射 → 10 列东财列形；代码去前缀；成交量 ÷100；0 值→NaN | **live 命中**：真实 5575×14 帧 → 10 列；前缀 `sz2906/sh2319/bj350` 全去；600519 成交量 5767722 股 → 57677.22 手；6 个 0 值行→NaN。单测 16 项（含缺列保形/空表/非数值/不改写入参） | ✅ |
| 3 | 两源皆失败返回 None | 返回 None + ERROR；日批抛 `SnapshotError` 写失败标记；不写空快照 | 单测 `test_both_sources_fail_returns_none_with_alarm`、`test_sina_without_code_column_returns_none`、`test_sina_all_codes_blank_returns_none`；日批既有失败路径未改（`test_batch_failure_marks_meta_and_reraises` 仍绿） | ✅ |
| 4 | 日批摘要记录实际生效源 | 成功摘要含 `spot_source`（eastmoney/sina；未上报 → None） | 单测 2 项（端到端 sina / 假客户端 None）；**live**：摘要 `"spot_source": "sina"`，与 `sources_seen["market_spot"]={'sina'}`、`last_spot_source='sina'` 一致 | ✅ |
| 5 | 真实数据快照日批落库（隔离库） | 行数、双口径、抽样字段、生效源如实 | **live**：`written=5569`（当日全市场）、`close_qfq==close_raw` 100%、无 0/负收盘价、无前缀代码、`spot_source=sina`、耗时 54.7s（见 §6 读数） | ✅ |
| 6 | 回填取数新浪回退（`fetch_symbol_history_raw_qfq`） | 东财双口径失败 → 新浪 `stock_zh_a_daily` raw/qfq；列名/单位归一；按日期内连接 | **live**：600519（区间 20260915~20261015）东财腿 3 次重试失败 → 新浪生效（日志「回退新浪源: 600519」捕获），16 行 5 列契约；跨端点核对上一交易日收盘 == spot 昨收；成交量单位逐日核对 ÷100。单测 6 项（含 qfq 腿失败/two-source 全败/缺列/无交集） | ✅ |
| 7 | 既有语义不变 | 既有测试语义不改 | 全套 `uv run pytest -q`（见 §执行记录 5）；`tests/data/test_market_snapshot_job.py`、`tests/scripts/test_backfill_market_snapshot.py`、`tests/outcome/test_cohort_universe.py` 全绿 | ✅ |

## §6 真实读数（任务要求的四项，逐字取自复现脚本输出）

**① 新浪全市场 spot 实测**（`ak.stock_zh_a_spot()`）：

```
shape=(5575, 14) 耗时=12.4s
列: ['代码', '名称', '最新价', '涨跌额', '涨跌幅', '买入', '卖出', '昨收', '今开', '最高', '最低', '成交量', '成交额', '时间戳']
停牌 0 值行（最新价 == 0）: 6；代码前缀分布: {'sz': 2906, 'sh': 2319, 'bj': 350}
```

**② 真实快照日批（隔离库，`run_market_snapshot_batch("2026-10-15", db_path=<tempfile>)`）**：

```
隔离库: C:\Users\ADMINI~1\AppData\Local\Temp\val-snapshot-agscx756\market_snapshot.db
trade_date: 2026-10-15
日批耗时: 54.7s
摘要: {"trade_date": "2026-10-15", "written": 5569, "rebased": 0, "suspended": 0,
       "master": "refreshed", "calendar": "ok", "spot_source": "sina"}
库内行数: 5569（distinct code 5569）；close_raw<=0: 0；close_raw NULL: 0；volume=0: 5；
          带前缀代码: 0；close_qfq != close_raw: 0
amount 合计: 1.645e+12；最大成交额: ('300308', '中际旭创', 21778343697.0)
抽样 3 行（code|name|close_raw|close_qfq|pct_chg|volume|amount|prev_close|is_st|is_suspended|listed_days）:
  000001 | 平安银行 | 11.41 | 11.41 | -0.783 | 1511767.06 | 1719700512.0 | 11.5 | 0 | 0 | None
  000002 | 万 科Ａ | 3.6   | 3.6   | -1.907 | 3380549.09 | 1237097018.0 | 3.67 | 0 | 0 | None
  000006 | 深振业Ａ | 6.72 | 6.72 | -3.725 | 216366.8 | 147486227.0  | 6.98 | 0 | 0 | None
```

读数解读（如实）：
- **行数 5569 = spot 5575 − 6 个停牌行**。6 只停牌股（000016/002388/002717/002731/300773/301314，新浪 0.0 → NaN）在**空库首跑**无昨行可承价 → 按 D2 不编造、不落行（与东财源在同条件下行为一致：D2 合成需要昨行）。存量库（有上一交易日行）下这些票会落为 `is_suspended=1` 合成行——已用真实停牌行 + 承价行实测：6/6 合成 `is_suspended=1` 且 `close_raw` 承昨收（见脚本 §3）。
- **`listed_days` 全为 None**：编排顺序为「写行 → 刷新主表」（既有顺序，本次未动），空库首跑时主表为空 → `listed_days` NULL（不编造）；当日 `master=refreshed`，次日跑批即有值。
- **`volume=0` 5 行**（600293/600825/601059/601198/688496）：有效价格但当日零成交，新浪原样透传（非映射缺陷；东财源同形态）。
- **耗时 54.7s** 构成：东财失败 3 次重试 ≈5.5s + 新浪 spot 12.4s + 交易日历 + 主表刷新（沪 3 板 + 深 + 北，本次深市接口曾 15s 超时后重试成功）。其中 spot 相关成本 ≈18s，与改造前（东财可用时 ~10s 单源）同量级。

**③ 回填取数新浪回退真实读数**：

```
symbol=600519 区间 20260915~20261015：东财腿失败(None) 耗时 5.5s；完整调用耗时 21.8s；本调用生效源=sina（回退）
shape=(16, 5) 列=['日期', '开盘', '收盘_raw', '收盘_qfq', '成交量']
  2026-10-13 | 1287.0 | 1274.89 | 1274.89 | 26572.76
  2026-10-14 | 1270.1 | 1286.00 | 1286.00 | 23772.73
```

- 跨端点核对：hist 上一交易日（10-14）`收盘_raw=1286.0` == spot `昨收=1286.0`（两个独立端点交叉一致）。
- 单位自证：源 `amount/(volume×收盘)` ∈ [0.9939, 1.0047] → 源成交量确为**股**；出口 `成交量 == 源股数 ÷100` 逐日成立 → 快照 `volume` 列与东财来源（手）同口径。
- 耗时方差：完整调用实测 7.3s / 7.5s / 21.8s（新浪日线腿延迟波动）；东财腿稳定 ≈5.5s（3 次重试）。

**④ 成交额/涨跌幅抽样**（含 600519 交叉核对）：600519 `close_raw=1312.97`（= spot 最新价）、`pct_chg=2.097`、`amount=7537706224.0`；映射后 `成交量=57677.22` 手（= 新浪 5767722 股 ÷100）。

## 异常记录

1. **东财主源恒失败（#325 IP 封禁）**：本机 `stock_zh_a_spot_em` 3 次 `ConnectionError` 全部失败（日志逐字：「AKShare 调用异常: stock_zh_a_spot_em: ConnectionError (attempt 1/3..3/3)」）；`_fetch_symbol_history_em` 同样 3 次重试后 None。**主源路径（Scenario 1）本次无 live 证据，仅单测覆盖**——not 计为 live 通过。
2. **东财可用性/失败模式呈波动（观察，未定性）**：15:05 首次跑批时回填调用仅 1.2s 即返回 17 行（含 2026-10-15 bar）；15:15 与 15:21 两次均为东财腿 5.5–5.7s 失败 + 新浪回退 16 行（不含 10-15 bar）。1.2s 无法容纳 3 次重试（≥5.5s），故该次或为「东财快速失败（连接不可达路径，不重试）」或「东财短暂可用」，**证据不足以区分，两者均不作断言**。若后续需精确归因，应在回填路径落实际生效源留痕（见待裁决项）。
3. **新浪历史日线端点当日 bar 发布滞后/回撤**：同一区间同一符号，15:05 含 2026-10-15 bar、15:15 与 15:21 不含（16 行止于 10-14）。影响：**收盘后立刻回填「今日」可能缺当日行**——日频当日的正式来源是 15:30 日批（spot），回填用于历史区间，故不视为缺陷；脚本已把该场景移出断言（避免把上游发布滞后误判为回退缺陷）。
4. **volume=0 的 5 行与 listed_days NULL**：见 §6 读数解读，均为既有语义/上游形态，非本次引入。
5. **Live-check 脚本的日志陷阱（已修正，记录以免复现踩坑）**：脚本在内联 handler 记录「回退新浪源」INFO 行时必须显式 `setLevel(logging.INFO)`；否则 INFO 被丢弃、判别失效（本次首轮曾误报「生效源=eastmoney」，修正后为 `sina（回退）`）。另：向模块 logger 挂 handler 会抑制 `logging.lastResort`，同一时段 WARNING/ERROR 不再落 stderr——脚本 stdout 已是权威读数。

## 执行记录（原始命令）

```bash
# 1. delta 校验
$ openspec validate add-market-data-fallback --strict
Change 'add-market-data-fallback' is valid

# 2. 新单测（先红后绿）：红 = ImportError: cannot import name '_map_sina_spot_to_em'
$ uv run pytest tests/data/test_market_data_fallback.py -q
23 passed in 1.35s

# 3. 相关既有套件回归（语义不变）
$ uv run pytest tests/data/ tests/scripts/test_backfill_market_snapshot.py tests/outcome/test_cohort_universe.py -q
293 passed in 54.02s
$ uv run pytest tests/data/test_market_data_fallback.py tests/data/test_market_snapshot_job.py \
    tests/scripts/test_backfill_market_snapshot.py -q
59 passed in 3.57s

# 4. lint / 类型（新增代码零新增问题；mypy 9 项为 HEAD 既有）
$ uv run ruff check <4 个改动文件>          → All checks passed!
$ uv run ruff format --check <4 个改动文件> → 4 files already formatted
$ uv run mypy src/finance_agent/data/akshare_client.py src/finance_agent/data/snapshot_job.py
  → 9 errors（与 `git stash` 后 HEAD 基线逐项同数同因；本次实现经 TypeGuard 收窄，未新增）

# 5. 真实网络 live 验证（收盘后 15:21，隔离库）
$ uv run python tests/validation/2026-10-15-add-market-data-fallback-live-check.py
... 16 项断言全 PASS（EXIT=0）；读数见 §6
```

## 结论

- [x] 全部 live 读数与契约一致，单测/回归/lint/类型通过 → **可提交审查**
- [x] archive（tasks.md 全勾 + 生产实跑补录后归档，2026-10-15；人工终审签字待补）

**未达成/受限项声明（不得视为通过）**：① Scenario 1（主源可用直接返回）本次**无 live 证据**（东财 IP 封禁），仅单测覆盖；② 东财失败模式波动的归因（异常 2）未定性，留观察；③ 全市场 5500 票级别的回填端到端未跑（仅 600519 单票 live 验证 + 既有回填脚本单测），且回填在东财封禁下每票仍先付 ≈5.5s 东财失败成本（见 delta design.md Risks，建议后续加源健康冷却或 `--source` 参数）。

---

## 部署后生产实跑补录（2026-10-15 晚，浏览器自动化驱动 /ops 补跑；人工终审签署待补）

**部署路径**：c28b111c（#342 合并）经 `scripts/deploy.py` 真实拉取（首次实战）→ `docker compose restart backend` → 容器内确认新码（`_map_sina_spot_to_em` 存在）。

**① 快照日批（market_snapshot_daily，run 79，20:12:30）**：

```
成功 · {"trade_date": "2026-10-15", "written": 5569, "rebased": 0, "suspended": 0,
"master": "refreshed", "calendar": "ok", "spot_source": "sina"}
```

东财封禁下主源失败 → 新浪回退生效（`spot_source=sina` 如实落摘要）→ 全市场 5569 行落库；`/ops` 卡片成功，下游时效性校验从「快照库为空」推进（选股批错误随之从 SnapshotNotFresh 前移到 L2 因子阶段）。

**② 全市场历史回填端到端（未达成项 ③ 补验）**：

- 脚本：`scripts/backfill_market_snapshot.py --start 2026-07-10 --end 2026-10-14`，16 进程按标的分片并行（`write_snapshot_rows` 为 (日期,代码) upsert，分片安全）；宿主侧总耗时 ≈55 分钟。
- 读数：16 分片汇总 `symbols_ok≈5046 / symbols_failed≈347 / rows_written≈31.5 万`；窗口内覆盖 **5270/5617 票**（5270 + 347 = 5617，精确吻合）。
- 失败分布：**345 票为北交所 920xxx**——新浪 `stock_zh_a_daily` 不覆盖北交所（备用源历史口径的覆盖缺口，如实暴露）；另 2 票（301569、689009）两源皆无数据，重试仍 None。
- 交叉核对：回填后快照库 64 个交易日（07-10..10-15）；日批当日行（5569）与回填区间（以 10-14 收尾）互不覆盖；选股 60 交易日面板前置由此满足。
- 披露：回填期间宿主机 16 进程写 WAL 与容器读同库互斥（`disk I/O error`，另立 issue #344）；北交所历史缺席使 BJ 票无法进入选股面板（源能力边界，非回退逻辑缺陷）。

**③ 结论更新**：未达成项 ③ 已补验（全市场回填端到端真实跑通）；新增覆盖缺口一项（北交所历史源缺席）。
