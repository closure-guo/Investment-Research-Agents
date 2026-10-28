## Context

详情接口 `_factor_detail_diagnostics`（api.py:4407）在 HTTP 请求内现算 500 日准入面板 + 因子求值 + 5 个 builtin 代理求值 + 单调性检验，唯一护栏（窗口 IC < 30 短路）被 IC 历史回填绕过后，active 因子详情全部挂死（探针 71 分钟未完成）。系统已有「日批算、接口只读」的成熟模式（IC 缺口回填在 `factor_decay_monitor` 15:45；收割 15:55；builtin 注册 15:50），诊断读数本质上也是收盘后数据派生的日内不变量——适合同一模式。

## Goals / Non-Goals

**Goals**
- HTTP 详情路径零面板构建（无条件不变量，写进 spec）
- 读数由独立日批预计算落库，T-0 当日（17:15）即新鲜
- 逐因子失败隔离、幂等重跑、增量跳过（无新数据日近零成本）

**Non-Goals**
- 不改前端展示（`null` → 「数据不足」文案语义沿用）
- 不改诊断口径本身（check_monotonicity / compute_style_exposure 原样复用）
- 不回溯翻案存量因子状态（读数仍是观测项）

## Decisions

1. **独立日批作业，不挂衰减监控**。衰减监控面板只有 `DECAY_WINDOW+30`（≈90 日）深，诊断读数须用 500 日准入面板——若让监控建大面板，会顺带改变其 IC 缺口回填窗口语义（现在「超出面板窗口的缺口诚实留洞」是明文契约）。也不挂收割（收割仅在有待处理候选时建面板，诊断不能依赖候选队列）。备选被否：进程级面板缓存（484MB 常驻内存 + 失效复杂度，单 worker 下收益不抵成本）。

2. **存储进 factor_library.db 新表 `factor_diagnostics`**（`factor_id` PK、`as_of`、`style_exposure`/`monotonicity` JSON、`computed_at`）。库 schema 单一归属 library.py（与 factors/factor_ic/factor_events/factor_admission_ledger 同 pattern）；不另建库文件。

3. **增量判定 = 读数缺失 或 `as_of` < 面板最新交易日**。周末快照无新交易日 → 全部已新鲜 → 零面板构建近零成本；周末收割新准入的因子（收割 `day_of_week="*"`）无读数 → 当日补算。待算集合为空时不建面板（与收割「无候选不建面板」语义一致）。

4. **调度 17:15、`day_of_week="*"`**。在日批窗口（15:30-17:10）结束之后、cohort 窗口（17:55）开始之前；收割实测最晚 17:56 结束，可能并发——两者均进程内 CPU 任务、SQLite 写短暂，无正确性影响（记入 Risk）。

5. **失败隔离**：逐因子 try/except（OSError/ValueError/DSLParseError/FactorComplexityError），失败因子保留旧读数、计 `failed`；批不抛错（作业记 ok）。与详情路径旧行为对齐（旧路径失败 = 返回 null；新路径失败 = 旧读数或无读数，均诚实）。

6. **API 只读改造**：`_factor_detail_diagnostics` 变纯读库 + 附 `diagnostics_as_of`；删除面板/DSL/IC 相关 import。IC 点数 ≥ 30 的门槛从 API 护栏移到日批准入条件。

## Risks / Trade-offs

- [首跑回填成本：一局面板 + 12 因子求值 ≈ 10~20 分钟] → 与 builtin_register 同量级，部署后手动补跑一次即收敛，此后增量运行分钟级内。
- [17:15 与过晚收割并发] → CPU 争抢使两者变慢但均能完成；无库级冲突（不同表、短事务）。
- [读数 T-0 17:15 前的窗口（如上午查看新入库因子）显示「数据不足」] → 文案语义可接受（spec 明文），次日批自动补齐；收益是详情接口毫秒级。
- [手工 seed（API 直投）的因子无读数直至下次日批] → 与收割路径一致，可接受。

## Migration Plan

1. 合并后 `docker compose up -d --build backend`（红线前置检查照常）。
2. 手动触发 `factor_diagnostics_daily`（ops API）为存量 12 因子补算读数（预计 10~20 分钟）。
3. 验证：详情接口毫秒级返回且带读数；`/api/v1/ops/jobs` 出现新作业且次日 17:15 自动运行。
4. 回滚：revert 部署即可（新表冗余无害，旧代码不读它）。
