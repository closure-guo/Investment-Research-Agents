## 1. 数据与配置

- [x] 1.1 `screening.db` 新增 `report_analysis_runs`（主键 `(doc_key, symbol)`，含 status/reason/session_id/时间）
- [x] 1.2 新增配置读取：`REPORT_TRIGGER_ENABLED`（默认 0）、`REPORT_TRIGGER_SCOPE`、`REPORT_TRIGGER_MAX_SYMBOLS`（3）、`REPORT_TRIGGER_MAX_AGE_DAYS`（7）、`REPORT_TRIGGER_DAILY_MAX`
- [x] 1.3 单元测试：配置默认值与非法值处理、旧库迁移幂等

## 2. 编排

- [x] 2.1 实现范围解析：pool（最新成功批次）、watchlist（event_watchlist）、holdings（positions qty>0）；单来源失败降级为空并记 WARNING
- [x] 2.2 实现 `prepare_report_analysis_batch(doc_keys, dry_run)`：stocks 过滤、时效校验、范围求交、单篇/日预算、幂等与当日去重
- [x] 2.3 实现会话创建与 `report_analysis_runs` 写入；复用 `create_session/append_chat/set_pipeline_anchor/update_session_for_clarify`
- [x] 2.4 复用主循环桥接入队（`_enqueue_analysis`），`dispatch_failed` 复用原 session_id 重试
- [x] 2.5 单元测试：空 stocks 不触发、过期不触发、范围外跳过、单篇截断、日预算共享、当日去重、投递失败复用会话

## 3. 入口与可观测

- [x] 3.1 投喂扫描 job 在抽取完成后调用编排；默认关闭时只记 would-trigger 摘要
- [x] 3.2 新增 `POST /api/research/reports/{doc_key}/trigger-analysis`（支持 `?dry_run=1`）
- [x] 3.3 批次摘要记录 docs_scanned/candidates/triggered/skipped_*/failed，ops 可见
- [x] 3.4 复用信号时效性校验；停牌/一字涨跌停不入队并留审计
- [x] 3.5 测试：dry-run 零副作用、失败留痕、摘要计数完整

## 4. 验收

- [x] 4.1 `openspec validate add-report-triggered-analysis --strict` 通过
- [x] 4.2 `uv run pytest` 定向测试全绿；`uv run ruff check` 通过
- [x] 4.3 真实场景验证：用一份 `stocks=[]` 的策略报告跑触发 → 0 会话；用一份含范围内标的的个股研报 → 按上限触发
- [x] 4.4 并发验证：触发 3 只时运行中分析数不超过 `SCREENING_ANALYZE_CONCURRENCY`
- [x] 4.5 确认触发不改变执行链与审批口径（不自动下单）
