# Design: add-factor-research-monitor

## D1 桥：distro 采集器 → /mnt/d JSON → 后端调用期读取 → 前端轮询

- 研发进程在 WSL `rdagent` distro（root 用户），后端在 Docker 容器——两者无直接文件互通。
  唯一现成的共享面 = 宿主仓库 `data/`（compose 挂载 `./data:/app/data`；distro 经 `/mnt/d`
  9p 挂载可写，已实测 rw）。故桥 = distro 侧采集器写 JSON 至
  `/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/status.json`。
- 备选 A（后端直连 distro）：容器内无法执行 `wsl` 命令，否决。
- 备选 B（SQLite 库）：快照是单条滚动状态而非事务数据；JSON 单文件 + 原子替换更简单、
  天然可审计，SQLite 的迁移/并发成本无收益，否决。
- 备选 C（宿主侧采集进程）：宿主无常驻调度器，临时进程与开发会话耦合、随会话死；
  distro 侧采集器与看护同生命周期更稳，否决。
- 备选 D（前端直读文件）：前端无文件系统访问且绕过 API 契约，否决。

## D2 采集器实现：Python 纯 stdlib、增量解析、原子写

- distro 有 `/usr/bin/python3` 3.12；解析日志（ANSI 转义、`\r` 进度条、结构化标记）与 JSON
  组装在 bash 里不可靠（转义地狱），Python 一次到位。
- 数据源（全部只读）：
  - `factor_research.pid` + `kill -0` → 看护存活；`supervisor.log` → 启动时刻与会话结束事件；
  - `session-*.log`（文件名时间戳 = 开始时刻）→ 当前会话：**末次** `loop_index=…, step_index=…,
    step_name=…` 进度标记（tqdm 进度条内），`[MM:SS<MM:SS` 步内已用/剩余；
  - `factor_name: X` 提取 → 本会话因子名列表（按出现顺序去重）；
  - workspace `find -name factor.py` 计数 + 任务目录数 → 累计候选。
- **增量解析**：会话日志无上界（实测 ~11MB/h，会话可跑数天），全量重扫每周期不可取。采集器
  维护状态文件（`factor_research_logs/.collector_state.json`）：记 `{log_file, offset, factors[]}`，
  每周期只解析新增字节并追加因子名；会话切换（最新日志文件名变化）→ 重置。日志尾与进度标记
  只读尾部固定窗口（64KB/1MB），成本有界。
- 写入 = 临时文件 + `os.replace`（同目录原子替换），读取方任一时刻只见完整快照；写失败保留
  旧快照（陈旧机制兜底可见）。
- 时间口径：`collected_at_epoch`（Unix 秒，age 计算真源）+ `collected_at`（ISO8601 带时区，
  供展示）；采集器显式用 Asia/Shanghai 渲染，免疫 distro 默认 UTC 的时区歧义。
- 模式：默认守护循环（30s 周期）；`--once` 单次采集（供 stop 流程写末次快照）。
- 解析健壮性：一切以「末次匹配」为准；无匹配 → null/空数组（如实未知）；单字段解析失败
  不影响快照整体（try/except 全包），MUST NOT 崩掉采集循环。

## D3 端点：调用期读 env、派生 age/stale、缺失/损坏二分

- `FACTOR_RESEARCH_STATUS_PATH` 调用期读取（E2E env 注入即生效，与 `SNAPSHOT_DB_PATH`
  同纪律）；默认 `data/factor_research/status.json`（相对仓库根，容器内 cwd=/app 下即
  `/app/data/...`）。
- 派生字段：`age_seconds = now_epoch - collected_at_epoch`；`stale = age_seconds > 180`
  （3× 采集周期，阈值常量单源）。`collected_at_epoch` 缺失时回退解析 ISO；两者皆不可解析
  → 视为 `corrupt`。
- 缺失/损坏 → 200 + `available=false` + reason（`missing`/`corrupt`）：监控页需要第一类
  「未接入」状态，MUST NOT 用 500 或伪快照冒充。同步文件 IO 走 `asyncio.to_thread`
  （与既有端点一致）；端点零写入。

## D4 前端：轮询 10s、陈旧横幅、三态空态、只读

- 轮询 10s（ScreeningPage 轮询模式复用：单轮失败静默重试、unmount 清理）。
- 陈旧（端点 `stale=true`）→ 顶部提示条「数据已陈旧（最近采集 HH:MM:SS）」；内容照常展示
  （陈旧 ≠ 不可用）。
- 缺失 → 空态「采集器未接入——因子研发看护未运行或采集器未部署」；损坏 → 空态「快照损坏」；
  二者 MUST NOT 与加载中混淆。
- 页面形态：状态指标卡 + 当前会话进度卡（进度条 x/5 + 因子 chips + 等宽日志尾）+ 会话历史表；
  复用 StatusBadge 五态与设计令牌，未知步骤名原值如实（不臆造中文映射，映射表内命中才译）。
- 入口：侧边栏（展开 + 折叠）与命令面板，同源 navigate('/factor-research')。
- 只读纪律：页面零写入口（启停控制面另议——后端容器无法执行 distro 命令，需控制通道设计）。

## D5 E2E：fixture 快照种子（真实文件，非 mock）

- 默认套件新 spec；config env 注入 `FACTOR_RESEARCH_STATUS_PATH=data/test-e2e-factor-research-status.json`；
  spec 以 Node fs 写入 fixture 快照（运行中 / 陈旧 / 缺失三态），断言渲染、陈旧提示、空态与
  侧边栏导航；不 `route.fulfill`（红线）。
- 本地跑默认套件前停 Docker 栈（防 `reuseExistingServer` 复用生产后端，incident 038/044
  家族纪律）；CI 无生产端口冲突，天然隔离。

## Risks / Trade-offs

- 日志格式漂移（RD-Agent 升级）→ 解析失败即如实置空（页面显示「无进度信息」），不崩不伪；
  验证报告记录当轮实测的日志格式版本。
- 9p 写延迟/锁 → 原子写 + 旧快照保留；采集器自身崩溃 → 快照陈旧（stale 提示）可判读。
- distro 重启（WSL 关机）→ 采集器与看护同死，需重跑 start 脚本；快照转陈旧而非误报运行中。
  已知边界，记入 validation 报告。
- 增量状态文件损坏 → 因子列表重建（从头扫一次），进度不受影响；极端下仅本会话因子列表
  回退为「尾窗可见集」，如实呈现不虚构。
