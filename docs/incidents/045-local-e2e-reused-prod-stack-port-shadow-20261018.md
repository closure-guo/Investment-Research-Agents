# incident 045：本地 E2E 误复用生产栈 + [::1] 端口遮蔽（2026-10-18）

## 现象（2026-10-18 凌晨）

1. 本地全量 E2E 首跑（run #1）大面积失败：`/api/test/seed` 404、页面呈现旧代码、流式用例空文本——
   实为**对生产栈执行**（事故 038 同族复活）。
2. 生产 `data/sessions.db` 被写入 **24 条 E2E 测试会话**（00:18:59–00:22:07，测试命名，状态
   failed/clarifying/interrupted）。
3. 后续人工验证出现「`/factor-research` 页没有新开关」——排查发现浏览器 `localhost` 优先解析
   `::1`，而 `[::1]:5173` 由 **wslrelay**（容器发布端口残留转发）占据并返回**容器内旧前端**
   （主检出另一分支代码）；本地 vite 只被 IPv4 命中。

## 根因

- **外部拉起了生产栈**：docker events 显示 00:18:35 有一次 `compose up`（backend+frontend 从
  停止态被拉起；langfuse 等其余容器未动）。已排查：本机 Task Scheduler 无 docker/compose/wsl
  相关任务、仓库无保活脚本 → 归因于**并发会话/人工启动**（主检出被多会话共用属既有事实）。
- **Playwright `reuseExistingServer`（本地为 true）探活复用**：生产栈先行就绪 → 本地套件的
  后端探针命中生产 `/api/health` → 直接复用生产前后端，测试全部落生产。
- **[::1] 端口遮蔽**：Docker Desktop 的 `com.docker.backend`（0.0.0.0/[::]）与 `wslrelay`
  （[::1]）与本地 vite/uvicorn（IPv4）**同时持有 5173/8000**（Windows SO_REUSEADDR 语义）——
  即使本地服务在跑，`localhost` 请求也可能命中容器旧内容。incident 038 的「复用探活」与其后
  追加的「wslrelay 残留 [::1] 绑定」认知在此合并为一个可复现的**双栈争用**问题。

## 影响

- run #1 结果作废（不采信）；24 条测试会话污染生产库——**已精准清理**（删除前逐表核验无
  报告/predictions/decision_log 关联；清理后库复核 331 会话完好），无其他数据损伤（报告文件、
  预测、决策均零写入）。
- 时间成本：E2E 两轮返工 + 验证栈迁移。

## 处置与修复

- **本次（已在 add-factor-research-control 内落地）**：
  - E2E `factor-research-monitor.spec.ts` 增**前置守卫**：真写控制文件的用例在点击前必须证明
    后端读的是本 spec 的隔离 fixture（`collected_at` 逐字比对），否则拒绝继续——防「复用生产栈
    把 PUT 落到真控制文件、真停研发看护」这一类最高危路径；
  - 人工验证改用**专用端口对（8010/5175）+ `127.0.0.1`**，绕开被争用的 8000/5173 与 ::1 遮蔽。
- **建议后续（未在本 delta 实施，挂账）**：
  1. 本地跑默认套件前的端口归属自动化校验（谁在听 8000/5173、是不是 TESTING 栈），非空即拒绝
     启动；或为 spec 加统一的 TESTING 探针前置（本次仅覆盖了写控制文件的用例）。
  2. WSL relay/docker 代理残留 [::1] 绑定治理（重启 Docker Desktop / 停栈后复核 netstat）。
  3. 生产栈「谁在拉起」的溯源机制（compose 事件不含调用者，本次只能归因到「外部」）。

## 状态

**部分修复**：高危路径守卫 + 验证栈隔离已随 delta 落地；环境治理三项挂账待专项。
