# 验证报告: /api/health DB 探针 + integrity_check 热库页级校验（harden-sqlite-availability task 5-8）

**日期**: 2026-11-01
**执行人**: agent（ZCode 会话，用户「继续」指令授权）
**关联 delta**: openspec/changes/harden-sqlite-availability/（task 5/6/7/8）

## 变更定性

后端可观测性改造（无交互面）：探针模块 + 健康端点语义升级 + integrity_check 作业扩展。
E2E 门禁不适用（无 UI/SSE/会话切换变更）；task 8 以 TestClient 集成层覆盖（env 注入
tmp 库路径，不 mock 被测系统）。

## 验证结果

| 项 | 证据 | 结果 |
|---|---|---|
| TDD 先红后绿 | 探针 10 例 + 端点集成 3 例 + 页级校验 3 例 + 对账补例 2 例（收集期 ImportError/断言失败为红）→ 实现后全绿 | 通过 |
| 探针契约 | 全 ok → 每库 ok；目录冒充库文件/垃圾文件/父目录缺失 → unavailable + 短错误类（OperationalError/DatabaseError）；慢库 → 总闸 2s 内返回 TimeoutError（实测 elapsed≈0.2s） | 通过 |
| 探针查询语义 | `SELECT 1` 不触库文件会假绿（Windows 实证 connect(目录) 成功）——实现期细化为读 sqlite_master 页 1，design §2 已记修订 | 通过 |
| 端点契约（task 8） | 200 `{status: ok, databases×8 全 ok}` / 503 `{status: degraded, databases: {screening: {unavailable, OperationalError}}}` / 修复后回 200 | 通过 |
| 页级校验契约（task 6） | summary `databases` 全 ok；regime 库损坏 → RuntimeError（消息含库名 + "not a database" 截断 detail）→ run_job 落 failed；predictions 哈希 mismatch 行为保留（bogus_hash → mismatch_count=1） | 通过 |
| 对账补例（task 7） | interrupted → failed（error 取 failure_reason）；会话消失 → 行保持 queued 不误回写 | 通过 |
| 回归 | 受影响面 138 passed（含 test_pipeline_write_blocking / ops_jobs / ops_model / track_record_job） | 通过 |
| lint/format/mypy | ruff check + format --check 全过；mypy 98 = 既有基线（223 files，+1 新模块） | 通过 |

## 迁移前后数据行数对照

task 9 要求的行数对照由迁移环节承担并已存档：**2026-11-01 迁移执行八库对账 ALL-OK**
（sessions/screening/execution/events/regime/research_reports/factor_library/market_snapshot
逐库 quick_check 双侧 ok + 全表行数逐一致），见
`tests/validation/2026-11-01-hot-db-named-volume-migration.md` §迁移结果。本报告不重复该对照。

## 生产语义说明

- `/api/health` 503 语义上线后，外部监控需区分 200/503（compose healthcheck 走
  `/api/sessions` 不受影响）。
- 探针读 env 为调用期口径；迁卷后容器内 env 指向卷内库，宿主不可直读（护栏纪律不变）。

## 结论

- [x] 全部通过，task 5-8 完成
