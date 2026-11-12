# Design: add-pool-analysis-defer-guard

## Approach

护栏放日批入口（`run_pool_analysis_daily` / `run_pool_analysis_microcap_daily`），不放
`api.submit_pool_analysis_batch`——手动补跑不应被静默阻塞 15 分钟；深夜手动/补跑场景前置
当日无 `running` 行，护栏自然零等待，不影响 02:47 类补跑时延（issue #578 的 38 秒边际正是
这类补跑路径，护栏不改变其行为）。

「还在跑」信号读 `job_runs`（sessions 库，`SESSIONS_DB_PATH` env，与 daily brief 直读同款
先例）：`SELECT status FROM job_runs WHERE job_id=? AND substr(started_at,1,10)=? ORDER BY
run_id DESC LIMIT 1`，status∈{running, ok, failed, skipped-disabled}。取最新行：scheduler
重试（5s/20s 退避）间隙最新行是 `failed` → 视为不在跑、立即放行——可接受（选股失败当日
fresh 批本就不存在，等待无意义）。

主档 3 轮 / 小票档 8 轮的依据：主档选股收尾 16:05–16:21（16:22+3×5min=16:37 覆盖
P99+15min）；小票档 `microcap_screening_daily` 连续 8 日 17:04–17:17 收尾
（16:47+8×5min=17:27 覆盖 P99+10min）。间隔/轮数均可 env 覆盖，护栏可一键关闭。

阻塞面：等待发生在 APScheduler 默认线程池（max_workers=10）的 job 线程内，主档最坏占线到
16:37、小票档到 17:27；相邻作业（16:45 microcap screening / 16:50 cb_daily）各自独立线程
与独立单飞锁，无冲突。池分析 job 自身的 `job_runs` 行在等待期间保持 `running`——如实反映
执行时长，与 screening 行为一致。

## Alternatives Considered

- 调度层依赖声明（前置未完成则推迟触发）：APScheduler 无原生依赖编排，需自建 DAG 或改用
  chain 触发器，改动面大且把「依赖知识」从业务层抽走；否。
- 小票档仅平移排程（16:47→17:25）：治标不治本（前置时长漂移即复发），且主档薄余量问题
  依旧需要护栏；护栏落地后小票档排程维持 16:47（护栏承担等待），平移与否留部署后观察数据
  再裁。
- 轮询 screening 库的成功批次行而非 job_runs：`latest_successful_pool` 只能表达「成功
  与否」不能表达「还在跑」，且跨库耦合选股表结构；否。

## Risks

- 前置 job 进程崩溃后 `running` 行残留（无终态回写）→ 护栏白等 15/40 分钟后照常走 stale
  路径，行为退化为现状，不劣化。
- `job_runs` 读失败（库忙等）→ 护栏按「读不到 = 不在跑」放行（fail-open），并在 defer 摘要
  记 `outcome=not_running`；不阻断编排。
