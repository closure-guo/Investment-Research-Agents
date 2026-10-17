# Tasks: add-factor-research-monitor

> 上线顺序 = 任务顺序。TDD：先红后绿；每层测试先行。

## T1 采集器（distro 侧 Python，tests/scripts/spike/）

- [ ] 先红后绿：解析函数单测——ANSI/`\r` 清理、末次 `loop_index/step_index/step_name` 与
      `[MM:SS<MM:SS` 提取、`factor_name` 按序去重、supervisor.log 事件解析、会话文件名时间戳解析、
      未知字段如实置空（`tests/scripts/spike/test_collect_factor_status.py`）
- [ ] 先红后绿：快照组装（fixture 工作目录：pid 文件 + supervisor.log + 会话日志 → 完整快照；
      增量状态文件跨周期累积因子名、会话切换重置）
- [ ] `--once` 与守护循环两模式；原子写（tmp + `os.replace`）；单字段失败不影响整体
- [ ] start/stop 脚本集成：start 拉起采集器（setsid 脱离）、stop 写末次快照后收采集器

## T2 后端端点（api.py）

- [ ] 先红后绿（pytest）：`GET /api/factor-research/status`——缺失 → `available=false/missing`；
      损坏 → `corrupt`；合法 → 透传 + `age_seconds`/`stale`（180s 阈值两侧）；调用期读 env 路径
- [ ] 只读：零写入、零业务复制；同步 IO 走 `asyncio.to_thread`

## T3 前端监控页

- [ ] 先红后绿（vitest）：运行中渲染（徽章/pid/时长/循环序号/步骤/进度 x/5/因子列表/日志尾）；
      陈旧提示；缺失与损坏空态（文案区分）；轮询刷新（间隔 ≤15s）与 unmount 清理
- [ ] `/factor-research` 路由 + 侧边栏（展开/折叠）与命令面板入口；types.ts 类型扩展

## T4 E2E（默认套件，fixture 快照种子，红线：不 mock 业务接口）

- [ ] 新 spec：运行中三态渲染 + 陈旧提示 + 缺失空态 + 侧边栏导航（fixture 写入隔离路径
      `data/test-e2e-factor-research-status.json`，env 注入）
- [ ] 默认 config env 注入 `FACTOR_RESEARCH_STATUS_PATH` + `.gitignore` 补两条（快照目录与 E2E fixture）

## 收口

- [ ] `uv run pytest tests/test_api_factor_research.py -q` + `uv run pytest tests/scripts/spike/test_collect_factor_status.py -q`
      全绿；`ruff check` / `mypy` 干净；前端 `tsc --noEmit` + `vitest` 绿
- [ ] `openspec validate add-factor-research-monitor --strict` 通过
- [ ] 部署：采集器推入 distro 并启动（页面可见真实数据）+ 后端重启（先查无 running 会话）+
      前端镜像重建；页面实跑人工验证（报告落 `tests/validation/`）
- [ ] 验证报告 + PR；**sync + archive 在实跑验证通过后执行**
