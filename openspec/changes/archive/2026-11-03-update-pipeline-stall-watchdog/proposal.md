# Proposal: update-pipeline-stall-watchdog

## Why

2026-10-28 opencode.ai 全站宕机（SSL 层挂死）时，16 条在跑池分析管线**全部吊死**：
零新事件、零新 trace，唯一防线是 `PIPELINE_TIMEOUT_SECONDS=2400s` 总墙钟——provider
断流后系统空转最长 40 分钟才判死（issue #521）。总墙钟语义把「stalled」与
「slow-but-progressing」混为一谈，故障发现延迟等于整个预算。

## What Changes

- 新增**管线停滞看门狗**：管线启动后若连续 `PIPELINE_STALL_TIMEOUT_SECONDS`
  （默认 600s）没有任何事件（含 thinking_token / SSE 心跳）且图未完成，判定停滞：
  会话标 failed、failure_reason 携带停滞时长与最后事件类型/节点、发布 error 终态
- 总墙钟（2400s，`PIPELINE_TIMEOUT_SECONDS`）语义**不变**——两者互补：
  停滞看门狗抓「零事件挂死」（provider 断流），总预算抓「有进度但跑飞」
  （601700 类 71 分钟 runaway）
- 双执行路径同语义：asyncio 消费路径（agent_factory `_background_consume`）以
  `min(总预算剩余, 停滞预算剩余)` 作为 `wait_for` 超时；fast path
  （pipeline_runner 阻塞生成器，循环内检查在挂死读期间不可达）由独立看门狗线程执行
- 阈值证据：生产库 60 个完成会话 43 万个管线内事件间隔，p99.99=17s、**最大 124s**
  （#491 观察到的 fetch 争用窗口），600s ≈ 5× 余量；环境变量可覆盖
- 队列等待不构成停滞：池批预建会话 → starter 拉起前的等待窗口在管线启动前，
  看门狗不计时（10-31 实测：已完成会话 journal 中 >1000s 空洞全部是
  `session_created→analysis_start` 排队窗，管线内无此形态）

**Non-goals**（另案）：LLM 流式读的 per-chunk 超时（litellm 层，方向 2——看门狗
落地后吊死对用户可见性已修复，线程级挂死解除属加固）；DB 观测指标（方向 3，
挂 harden-sqlite-availability 观测面）。

## Capabilities

- **Modified Capabilities**: pipeline-events（管线超时与中断检测——补停滞看门狗）

## Impact

- `src/finance_agent/pipeline_runner.py`：新增看门狗线程 + 共享 liveness 状态
- `src/finance_agent/agent_factory.py`：`_background_consume` 的 wait_for 超时改双预算
- 新增共享纯函数模块（超时计算/过期分类/停滞文案），两路径共用、单测覆盖
- 部署：新增环境变量 `PIPELINE_STALL_TIMEOUT_SECONDS`（默认 600）
