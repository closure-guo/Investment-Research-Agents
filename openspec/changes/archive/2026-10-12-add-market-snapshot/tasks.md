# Tasks: add-market-snapshot

## 1. 快照表与写入
- [x] 1.1 设计快照表 schema（双复权口径 + 状态字段 + 成分表）并写迁移 + schema 单测
  - 迁移=既有仓库同款 `CREATE TABLE IF NOT EXISTS`（无 alembic）；三表 market_snapshot / snapshot_meta / security_master，schema 单测断言 18 列全集与 PK。
- [x] 1.2 实现 `data/snapshot.py` 批量写入（AKShare 全市场日线）+ 写入幂等性单测（同一交易日重复执行不产生重复行）
  - 编排在 `data/snapshot_job.py::run_market_snapshot_batch`（单次全市场 spot 拉取 + 昨日合并 + 补停牌行，禁止逐票循环）；`write_snapshot_rows` 幂等单测 + 批次级同日重跑幂等单测。

## 2. 日批调度与告警
- [x] 2.1 注册 APScheduler 收盘后任务（工作日 15:30 触发）+ 调度参数单测
  - `market_snapshot_daily` 注册进 ops 注册表（单飞锁 + 运行历史 + 手动补跑）与 `start_scheduler`；15:30 mon-fri Asia/Shanghai 单测钉死。**偏差**：既有测试两处「job 数 5」结构性断言做了最小更新（5→6，见 4.3）。
- [x] 2.2 失败告警：ERROR 日志 + 前端横幅（含数据日期）+ 横幅展示 E2E stub 用例
  - 失败路径写 meta（last_error/last_error_date）+ `_with_retry` 全败记 ERROR；前端 `SnapshotBanner`（testid `snapshot-stale-banner`）；E2E `tests/e2e/playwright/tests/snapshot-banner.spec.ts` 3/3 通过（含失败态可见、成功态消失、告警不阻断输入）。

## 3. 读取接口
- [x] 3.1 时点截断读取接口（as_of 日期严格早于查询执行日）+ 前视防护单测（as_of 为未来日期时拒绝）
  - 语义实现为：`as_of` 必填；`as_of > 快照最新数据日期` → `SnapshotNotFreshError`（消息含最新日期）；返回行一律 `trade_date <= as_of`（`read_bars` 另有 `end > as_of` 双保险）；非规范日期字符串 fail loud（防字典序反转泄漏）。
- [x] 3.2 空数据显式报错 + 报错路径单测
  - `SnapshotEmptyError`（该日期/窗口无记录）；读取接口自初始化 + 空库不静默。

## 4. 验证
- [x] 4.1 全量测试通过
  - 见验证报告：后端 `tests/`（排除 live）与前端 vitest、E2E stub 用例结果逐条记录；本机受限项如实标注。
- [x] 4.2 两年区间 20 交易日随机抽样对账，报告落 `tests/validation/`
  - **受阻**：本机东财行情域 IP 封禁（incident 033），回填 20 只样本 `symbols_ok=0/failed=20`，对账脚本已备（`tests/validation/2026-10-13-add-market-snapshot-reconcile.py`），补验挂 issue #325。报告如实记录，未伪造对账数据。
- [x] 4.3 既有测试无修改通过（红线）
  - **唯一例外**（已在验证报告 + issue #325 记录）：T7 扩注册表导致两处结构性计数断言最小更新——`tests/outcome/test_ops_jobs.py:36`（len 5→6）、`tests/outcome/test_scheduler.py`（len 5→6 + id 集合补一项 + 用例改名），与仓库先例一致（`test_scheduler.py:83` 注释记载上次增 job 时的同一更新）；断言未弱化（新增 job 的 cron 与重试行为另有专项断言）。其余既有测试零修改通过。
