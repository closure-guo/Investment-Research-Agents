## Context

小票通道日批（16:45）已在生产出池（2026-11-09：40 只、`frontier_gate=ready`），但
observation_only：不进分析链、决策零产出、两个执行记录字段
（`MICROCAP_SUGGESTED_SINGLE_CAP` / `MICROCAP_SLIPPAGE_TIER_BUMP`）闲置。主档自动分析
（16:22 `auto_pool_analysis_daily`）只读 main 池；执行链单账本五表单驱动器
（`paper_execute_daily` 16:10 吃全部 open 观点）。完整设计见
`docs/superpowers/specs/2026-11-10-microcap-lane-daily-analysis-design.md`（用户逐节批准）。

关键约束：

- 主档路径行为逐字节不变（回归红线）；主档 track-record 历史连续不得污染；
- `pool_analysis_runs` 幂等键 `(trade_date, symbol)` 全局语义与 `duplicate_of_day`
  日主判定 lane 无感语义均为刻意设计，不得破坏；
- 实施在 worktree `feat-microcap-lane-analysis` 分支进行（共享检出上有并行会话）。

## Goals / Non-Goals

**Goals:**

- 小票池每日自动分析（前 10、跨池去重跳过+顺延、16:47 job）
- 决策 lane 归属与两账本执行隔离（predictions.lane + 第二执行 DB）
- 小票账本执行经济学转正（5% 单票、滑点 tier_bump）
- 读侧 v1（execution 读端点 `book` 参数 + 日报小票行）与既有消费者兼容修复

**Non-Goals:**

- lane 维度前端 UI（双曲线/页签/横幅文案更新）——后续 delta
- 小票 lane 独立预算/限流机制——每日 10 只即兜底
- 重叠标的复用主档分析结果——被否决（见 Decisions D2）
- 小票通道读运行时配置——spec 明令禁止
- frontier_gate 判据扩展——维持现状

## Decisions

**D1 第二执行 DB 文件（方案 A）**：`EXECUTION_MICROCAP_DB_PATH` 平行库，驱动器/撮合/
门禁代码参数化复用。备选 B（同库 book 列）被否：五表全加列 + 全部读者加过滤，迁移面
大、漏一处即串账；备选 C（分期记分卡先行）被否：丢失滑点现实性这一核心观察点。
主档库逐字节不动是决定性理由。

**D2 重叠标的跳过+顺延补位，不复用主档结果**：三重否决依据——① 16:47 时主档批次分析
多在途（共用队列），无结果可复，复用须深夜二段派发；② `job.py` 日主判定对同
`(symbol, 归属日)` 只留 created_at 最晚者、其余 `duplicate_of_day`（lane 无感），复用行
被随机误杀或反向污染主档记录；③ 偏倚方向保守（低估不高估）且忠实 top-10 影子组合可
用主档决策结算数据事后离线重算。重开条件：主档批次可保证 16:47 前完成 + 成本成主要
矛盾 + 观察通道升级实盘级模拟（届时一并 lane 感知化日主判定）。

**D3 归属列加 pool/lane 而非改主键**：两列均 `ADD COLUMN ... DEFAULT 'main'` 幂等加列；
`(trade_date, symbol)` 全局键保留即天然实现跨池去重。lane 归属在 ingest 时按
`session_id` 反查 `pool_analysis_runs.pool`，反查失败回退 main 不阻断。

**D4 主档兼容修复点收敛为两处**：`daily_brief.py` 自动选股行查询与
`report_trigger._today_used` 预算计数加 `pool='main'` 过滤（不改则主档口径被小票行
稀释）；`_already_today` 同日去重、`api.py` timeout 披露、`reconcile` 对账**不动**
（理由：全局去重语义 / 时序上无交集 / 对账应覆盖两池）。

**D5 滑点上移在撮合层实现**：`slippage_rate_for_amount` 加 `tier_bump` 参数（选中档位
索引 +N 再 clamp 末档），门禁/撮合全程纯代码不变；主档调用 tier_bump=0 行为不变。

## Risks / Trade-offs

- [主档回归] 仅有的主档触碰点 = 两列加列 + 主驱动器 lane 过滤 + 两处 pool 过滤修复
  → 回归证据 = 既有测试全绿 + 主档摘要逐字段比对
- [小票批时序] 16:47 入队排在主档批次后，结果晚间才齐 → 观察期预期行为，摘要如实
  披露；不设独立并发
- [日主判定与新 lane 的边界] 同日同 symbol 出现两池观点时（人工触发）仍按旧规则去重
  → 规格已声明 lane 无感；跨池去重使自动链路不会自造该场景
- [第二库运维盲区] 既有 ops 巡检/备份脚本可能只认 execution.db → 上线后首个交易日
  巡检 checklist 增加 microcap 库文件存在性检查；backup 脚本确认覆盖（实施时核）
- [worktree 测试环境] worktree 无 .venv，`uv run pytest` 首跑需建环境 → 计划内任务
  显式包含 uv sync；验证前先 pwd 确认 worktree 路径（CWD 重置坑）

## Migration Plan

1. 合并部署窗口遵守日批红线（先查 `/api/sessions` 与 `/api/v1/ops/jobs` 无 running）；
2. 两列幂等 ALTER 随应用启动迁移自动执行（无独立回填步骤）；
3. `data/execution_microcap.db` 首连自建，无预置数据；
4. 首跑观察：部署后次一交易日 16:47 小票批首跑 + 16:12 空转披露 + 20:30 日报小票行，
   人工观察报告落 `tests/validation/`；
5. 回滚 = 回退镜像（两列保留无害：DEFAULT 'main' + 主驱动器过滤随旧代码消失，行为
   回到变更前；microcap 库文件成为孤儿数据，可留档删除）。

## Open Questions

（无——用户已裁决目标定位 / 规模 / 去重策略 / 账本形态四项分叉。）
