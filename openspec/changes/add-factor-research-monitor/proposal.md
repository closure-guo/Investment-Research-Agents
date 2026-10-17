# 因子研发监控（add-factor-research-monitor）

## Why

持续因子研发（RD-Agent `fin_factor` 无界循环，看护脚本见 #362）当前在 WSL `rdagent` distro
后台长期运行（「不说停就一直研发」语义），但唯一观测手段是 CLI 状态脚本——终端输出、需人工
执行、无历史视图。用户无法在系统界面里回答三个基本问题：研发还在跑吗？跑到哪个循环、什么
步骤、还剩多久？产出了什么、日志尾部是什么？研发是长期过程，需要常驻可见性；本页与因子库页
互补：库页看已入库资产，本页看**供给侧的研发过程**。

## What Changes

- **新 capability「factor-research-monitor」**：
  - **状态快照采集契约**：distro 侧采集器周期（≤60s）把研发看护的运行状态写为 JSON 快照至宿主
    可见路径（默认 `data/factor_research/status.json`，env 可覆盖），原子写；快照含看护进程、
    当前会话（循环序号/步骤/进度/本会话因子/日志尾）、会话历史、累计计数；采集器只读观测
    （不干预研发进程）；停止时写末次快照（`running=false`）后退出；未知字段如实置空。
  - **只读查询端点**：`GET /api/factor-research/status` 调用期读快照文件（`FACTOR_RESEARCH_STATUS_PATH`
    env，默认同上）；文件缺失/损坏 → 200 + `available=false` + 原因码（`missing`/`corrupt`，如实二分）；
    存在 → 原样透传 + 派生 `age_seconds`/`stale`（>180s 判陈旧）。
- **新 Requirement「因子研发监控页」（frontend ADDED）**：`/factor-research` 路由页（只读）——
  看护状态区（运行中/已停止徽章 + pid + 启动时刻 + 已运行时长 + 累计会话/候选数）、当前会话
  进度区（循环序号 · 当前步骤 · 步骤进度 x/5 · 本会话因子列表 · 日志尾）、会话历史表；轮询
  自动刷新；陈旧快照明示「数据已陈旧」、缺失/损坏如实展示对应空态；MUST NOT 提供启停控制；
  侧边栏与命令面板同源入口。

## Impact

- 代码：`tests/scripts/spike/collect_factor_status.py`（新增，distro 侧采集器）+
  `start/stop-factor-research.sh` 集成（start 拉起采集器、stop 写末次快照后收采集器）；
  `src/finance_agent/api.py`（新端点）；`frontend`（新页 + 路由 + 侧边栏/命令面板入口 + types）。
- 数据：`data/factor_research/status.json`（运行时产物，gitignore 补条目）。
- E2E：新 spec 入默认套件（fixture 快照文件种子 + 隔离路径 env 注入，不 mock 业务接口）。
- 不做：不做启停控制入口（后端容器无法执行 distro 命令，控制面另议）；不改 RD-Agent / 五道闸 /
  因子库；不做历史趋势图（v1 只呈现当前快照与会话列表）。
