## 1. 数据层：归属列与迁移

- [ ] 1.1 `screening/store.py`：`pool_analysis_runs` 加 `pool TEXT NOT NULL DEFAULT 'main'`（幂等 ALTER + 写侧显式传参），失败测试先行
- [ ] 1.2 `track_record/model.py`：`predictions` 加 `lane TEXT NOT NULL DEFAULT 'main'`（幂等 ALTER + 写侧参数），失败测试先行
- [ ] 1.3 `track_record/ingest.py`：ingest 按 `session_id` 反查 `pool_analysis_runs.pool` 定 lane，查不到/异常回退 main + WARN，失败测试先行

## 2. 小票池每日自动分析编排

- [ ] 2.1 `pool_analysis.py`：编排参数化（pool 维度读取 `latest_successful_pool(pool=...)`、source/标题后缀、独立 env 开关与上限 `AUTO_POOL_MICROCAP_*`），失败测试先行
- [ ] 2.2 跨池去重选样：按 rank 跳过当日已有 queued/completed 行的标的并顺延补位，补位不足如实披露，失败测试先行
- [ ] 2.3 时效性重校验与失败重派（全新会话）语义继承验证（既有测试扩展到 microcap 参数路径）
- [ ] 2.4 `outcome/scheduler.py` + `outcome/ops/jobs.py`：注册 `auto_pool_analysis_microcap`（16:47，windows 标注 + cron 配置 + 标签），含开关关闭时 skipped-disabled

## 3. 执行链 lane 隔离与第二账本

- [ ] 3.1 `execution/driver.py`：`run_execution_daily` 参数化 `lane`+`db_path`，主档调用处传 `lane='main'`（行为逐字节不变），失败测试先行
- [ ] 3.2 主驱动器观点扫描过滤 `lane='main'`（存量默认值行为不变回归测试）
- [ ] 3.3 `execution/matching.py`：`slippage_rate_for_amount` 加 `tier_bump` 参数（索引 +N clamp 末档），失败测试先行
- [ ] 3.4 小票驱动器封装 `run_execution_microcap`：单票 5% 上限、tier_bump=1、其余参数同主档，失败测试先行
- [ ] 3.5 `brokers/paper.py`：小票结算 `run_paper_settle_microcap`（写小票库 paper_equity），失败测试先行
- [ ] 3.6 `outcome/scheduler.py` + `ops/jobs.py`：注册 `paper_execute_microcap`（16:12）与 `paper_settle_microcap`（16:24）

## 4. 既有消费者兼容修复

- [ ] 4.1 `daily_brief.py`：自动选股行查询加 `pool='main'` 过滤，失败测试先行
- [ ] 4.2 `daily_brief.py`：新增小票行（池规模/分析数/跳过数/派发失败/小票净值，缺数据写「无数据」），失败测试先行
- [ ] 4.3 `report_trigger.py` `_today_used`：预算计数加 `pool='main'` 过滤，失败测试先行
- [ ] 4.4 回归确认 `_already_today` / timeout 披露 / reconcile 三处不动语义（快照测试钉住现行为）

## 5. 读侧 v1

- [ ] 5.1 `api.py`：execution 只读端点加 `book` 查询参数（main/microcap，缺省 main 不变，非法 422），失败测试先行
- [ ] 5.2 `screening/pool_analysis.py`（或 api 桥接）：按 pool 读运行行的查询参数化（供 book=统计与日报使用）

## 6. 验证与收口

- [ ] 6.1 全量 `uv run pytest`（主档回归红线：既有测试全绿）
- [ ] 6.2 `uv run ruff check` + `uv run mypy`
- [ ] 6.3 worktree 内 uv sync 环境自检（先 pwd 确认路径）
- [ ] 6.4 主档行为逐位比对：变更前后 screening_daily / paper_execute_daily 摘要字段一致（fixture 回放）
- [ ] 6.5 人工首跑观察报告落 `tests/validation/`（部署后次一交易日：16:47 首跑 / 16:12 空转披露 / 日报小票行 / microcap 库文件存在）
- [ ] 6.6 ops 巡检与备份脚本覆盖 `data/execution_microcap.db` 确认（不覆盖则补）
