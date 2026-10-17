# 人工验证报告: fix-deploy-load-new-code（issue #343）

**日期**: 2026-10-17
**验证人**: ZCode 验证 runner（单测 + dry-run 实跑；生产实跑待下次真实部署命中）
**关联 issue**: #343（deploy.py：健康探针不覆盖「已拉取但代码未加载」）
**规范依据**: `openspec/specs/deploy` ——「拉取与健康探针」要求 MUST NOT 以「已 pull」冒充部署完成；「幂等与 dry-run」要求已最新时零副作用（本次行为变更不触碰该场景）
**E2E 门禁**: 不适用（运维脚本变更，无前端 UI / SSE / 会话切换 / 状态流转）
**执行环境**: worktree `.worktrees/fix-deploy-reload` @ fix/deploy-load-new-code（基于 main 4e95ebfc）；dry-run 对真实栈（后端可达、零 running 会话）

## 缺口与修复

**缺口**（issue #343 实证，2026-10-15 20:10）：`deploy.py` 探针只判「栈是否健康」——pull 后容器若仍健康（旧码照常服务 200），脚本判定「✓ 部署完成」退出 0，**但新代码从未加载**（宿主机改码不触发容器内 reload，WSL2 bind mount 的 inotify 不传播；部署 = 推进主检出 + restart）。实际部署靠人工补 restart 才完成。

**修复**（`scripts/deploy.py`）：

| 行为 | 修复前 | 修复后 |
|---|---|---|
| pull 推进且 incoming 含 `src/` | 不重启（旧码继续服务，谎报完成） | **无条件 `docker compose restart backend`** 后才探针（running 门禁①已过，无撞运行中分析风险） |
| incoming 含 `frontend/` | 同上（dev override 下前端亦为 bind mount） | 重启 frontend |
| 新码加载重启失败 | —（不存在该步） | 退出码 2，MUST NOT 声称完成 |
| incoming 含镜像类路径（`evals/`/依赖/构建文件） | 静默（restart 也加载不到） | 显式日志提示需 `docker compose up -d --build` |
| 已最新（无拉取） | 仅探针，零副作用 | 不变（幂等契约） |

判据纯函数 `plan_reload_services()` / `needs_image_rebuild()`；重启调用签名 `restart(repo, services)`（健康恢复路径仍 `backend frontend` 双服务）。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | `src/` incoming → restart backend 后探针 | `test_src_incoming_restarts_backend_before_probe`（real git sandbox + fake docker：restart_services == [("backend",)]、退出 0） | ✅ |
| 2 | `frontend/` incoming → restart frontend | `test_frontend_incoming_restarts_frontend` / `test_both_sources_restart_both_services` | ✅ |
| 3 | 非源码 incoming（docs/scripts）→ 零重启 | `test_docs_only_incoming_no_restart` | ✅ |
| 4 | 新码重启失败 → 退出 2、不进探针、不声称完成 | `test_reload_restart_failure_never_claims_success`（「✓ 部署完成」不出现在输出；backend 探针零调用） | ✅ |
| 5 | 新码重启后探针不健康 → 既有健康恢复路径仍生效（第二次双服务重启） | `test_reload_then_unhealthy_restarts_again_for_health` | ✅ |
| 6 | 幂等（已最新）零副作用 | `test_already_latest_zero_side_effects` + 原 `TestIdempotent` | ✅ |
| 7 | 镜像类路径提示 rebuild | `test_image_class_incoming_logs_rebuild_hint`（零重启 + 输出含 `up -d --build`） | ✅ |
| 8 | dry-run 打印新码加载计划且零变更 | `test_dry_run_prints_reload_plan` + 真实 dry-run 实跑（见下） | ✅ |

**回归**：`tests/test_deploy_script.py` + `tests/test_deploy_preflight.py` 67 项全绿；ruff、mypy 零告警。

**dry-run 真实实跑**（worktree，对真实栈）：

```
[deploy] ① running 门禁: 通过（后端可达，无 running 会话）
[deploy] ② 版本: HEAD=4e95ebf origin/main=4e95ebf（已是最新）
[deploy] ④ 运行时文件: 挪开 0 / 丢弃 0 / 阻断 0
[deploy] 拉取: 跳过（HEAD == origin/main 4e95ebf）  ← 已最新路径零副作用（新码加载计划不出现，正确）
```

## 未达成项 / 披露

1. **生产实跑未命中**：本日无新提交待部署（HEAD == origin/main），「pull 推进 → 重启 → 探针」全链的生产读数待下次真实部署补；单测已用真实 git sandbox + fake docker 钉死行为。
2. **已知边界（未纳入本次修复）**：若脚本在 pull 成功后、重载重启前中断（Ctrl-C/掉电），下次运行因「已最新」不会补重启——边缘窗口，如需覆盖需引入部署状态指纹（issue #343 建议方向 2/3，成本更高），本次按建议方向 1 最小修复面落地。
3. 前端 restart 的加载语义依赖 dev override（vite dev + bind mount）；纯镜像模式（`-f docker-compose.yml` 无 override）下前端 restart 不加载新码，需 rebuild——该模式不在本机标准部署路径（详见 issue #343 与 deploy-src-mount-mechanism 结论），脚本对 `frontend/` incoming 仍执行 restart（无害），偏差已记录。
