# incident 047：WSL 内存耗尽 → 生产前后端容器 ENOMEM 退出；后端重启首尝试失败（2026-10-21）

## 现象

1. 2026-10-21 约 23:00，生产前端容器退出（`Exited (1)`）。日志：

   ```
   Error: ENOMEM: not enough memory, scandir '/app/src/test/settings'
   Emitted 'error' event on FSWatcher instance
   ```

   Vite dev 的文件监听器在扫描 `frontend/src` 时分配失败——不是代码/配置错误。

2. 约 23:19 为加载新增的并发设置代码重启后端容器（宿主 `src/` 以 dev override 挂载；
   WSL2 bind mount 的 inotify 不传播，宿主改码不会触发容器内 `--reload`）。首次启动失败：

   ```
   Error in sitecustomize; set PYTHONVERBOSE for traceback:
   OSError: [Errno 12] Cannot allocate memory: '/app/src'
   ...
   OSError: [Errno 12] Cannot allocate memory: '/app/src'
   ```

   容器 `Exited (1)`，无 OOMKilled、无 cgroup 内存上限。

3. WSL（`docker-desktop` distro）读数：`MemTotal 11960MB / used 8.2–10GB / Swap 4096MB 全满 /
   MemAvailable ~1.5GB`；`Committed_AS 62GB`。宿主 Windows 侧仍有 19.3GB 空闲——
   瓶颈是 WSL VM 的 12GB 上限，不是宿主总内存。

4. 同期 `rdagent` distro 内多个 `rdagent` 进程 RSS 各 2.5–3.3GB（因子研发常驻代理），
   是 VM 内存的主要占用者；前端/后端 dev watcher 进一步放大压力。

## 根因

- WSL VM 被 `.wslconfig` 限制为 12GB + 4GB swap（2026-10-18 为防 WSL 吃满宿主内存而设置）。
- 因子研发常驻进程（rdagent distro）长期占用大部分 VM 内存；当日 swap 已耗尽。
- 内存紧张时，任何新进程/文件扫描（Vite `scandir`、Python import `_fill_cache`）都可能
  直接返回 `ENOMEM`——受害者是「谁在启动」，不是「谁有 bug」。
- 容器未设 `mem_limit`，因此不会 OOMKilled，只表现为启动期随机 ENOMEM。

## 处置

1. `docker compose stop`（全部项目容器，先释放内存）。
2. `docker desktop stop` → `docker desktop start`（回收 Docker VM；**未执行 `wsl --shutdown`**，
   以免连带杀死 `rdagent` 因子研发进程）。
3. `docker compose up -d backend frontend`（依赖容器随起；Langfuse 容器也恢复）。
4. 验证：`/api/health` 200；`GET /api/settings/analysis-concurrency` 返回 `value=10`；
   `http://127.0.0.1:5173/settings → 会话管理` 只读抽查显示「批量分析最大并发数 10（1–20，默认值）」；
   截图见 `tests/validation/2026-10-21-analysis-concurrency-prod.png`。
5. 启动时仍出现一次非致命 `sitecustomize` ENOMEM 告警，但 uvicorn 随后正常
   `Application startup complete`，健康检查通过。

## 教训与防护

- **重启生产容器前看内存，不只看 running 会话**：`wsl -d docker-desktop -- free -m`；
  `SwapFree` 接近 0 时应先回收 VM（Docker Desktop restart），不要直接重启容器。
- **宿主改 `src/` 不会热加载进容器**；需要重启才能生效时，重启动作本身也可能被 ENOMEM 拒绝，
  必须预留恢复步骤（本次：停栈 → 重启 Docker Desktop → 起栈）。
- **不要用 `wsl --shutdown` 清理 docker VM**：会同时杀掉 `rdagent` 因子研发常驻代理，
  需要手工 `start-factor-research.sh` 恢复。
- 因子研发代理的并发/内存配额（例如限制 RD-Agent worker 数）是待办项；12GB 上限与
  常驻研发并存时，生产栈启动没有余量。
- 与 incident 044 / 046 同属 WSL/Windows 挂载环境族：环境资源耗尽的表现会伪装成
  「服务代码启动失败」。

## 关联

- 本次变更验证：`tests/validation/2026-10-21-add-analysis-concurrency-setting-validation.md`
- 相关 incident：044（E2E 配置缺隔离 → 生产 API 卡死）、046（WAL 句柄/索引损坏）
