# 人工验证报告: fix-followup-resume-race（issue #331）

**日期**: 2026-10-17
**验证人**: ZCode 验证 runner（本地压测；CI 复跑待随 PR 流水线）
**关联 issue**: #331（test(frontend): followup-resume-after-switch 时序硬化，CI 慢机竞态）
**E2E 门禁**: 不适用（单测硬化 + CI 门禁配置，无产品行为变更）
**执行环境**: worktree `.worktrees/fix-followup-race` @ 基于 main 4e95ebfc（前端 node_modules 以 junction 复用主检出）

## 缺口与修复

**缺口**：PR #330 给 CI 补前端单测门禁后首跑即抓到 `followup-resume-after-switch` CI 专属失败（waitFor 超时 ~6.9s）——用例内三处固定 `setTimeout(300/500ms)` 赌事件链时点 + 最后一处 5s 窗口；慢机（2 vCPU Linux）下切回会话时上一轮 stub run 尚未收尾，事件链断。门禁以 `--exclude` 临时隔离，issue 明确「修复后移除本 exclude」。

**修复**：

| 处 | 修复前 | 修复后 |
|---|---|---|
| 第一轮事件消费 | `setTimeout(500)` | 条件等待：`findByText('让我搜索安孚科技')` + `findByText(/调用工具中/)` |
| 切到 s2 | `setTimeout(300)` | 等 s2 内容渲染（`findByText('你好！')`）为切完成条件 |
| 切回 s1 | `setTimeout(500)` | 等 s1 历史恢复渲染（`findByText('让我搜索安孚科技')`） |
| 终态断言 | waitFor 5s | waitFor 10s（纯余量，条件本身不依赖窗口） |
| CI 门禁 | `--exclude 'src/test/followup-resume-after-switch.test.tsx'` | 移除 exclude → 全量文件入门禁 |

**旁证排除**：失败日志里的 `HTTP 409 {"detail":"session_busy"}` 经溯源为**旁路测试文件**（`running-guard-toast.test.tsx` / `quickThreadGuards.test.tsx` 的 409 路径 console 输出，vitest 并行文件交错）——本用例 mock 对未匹配 URL 返回 404，不存在 409 通道，日志为噪声非本用例机制。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | 硬化后用例稳定 | 单文件连跑 **8/8 绿**（每跑 ~0.5s，远低于 5s 旧窗口） | ✅ |
| 2 | 无跨文件回归 | 全量套件连跑 4 次全绿（96 文件/815 例；数量差 11 例=本 worktree 建自分支点 4e95ebfc，不含其后合并的图表契约测试） | ✅ |
| 3 | 事件链锚点真实（非恒真） | 探针实测三时点 DOM：轮内含「调用工具中·1 次/搜索了1个网页」；s2 含「你好！」；s1 回切含思考历史——锚点只在对应步骤后出现 | ✅ |
| 4 | CI 门禁覆盖恢复 | `ci.yml` vitest 步不再 exclude，全量文件纳入（PR 流水线首跑即验证） | ✅ |

## 未达成项 / 披露

1. **CI 复跑观察**：本地 4 次全量中曾出现 1 次单文件失败（未留存文件名的历史输出，其后 4 次全量绿）；该失败与本变更文件无耦合（vitest 文件隔离 worker，本变更仅动 followup 文件与 ci.yml）——按既有「运行器级劣化」族（#328/#312）对待，若 CI 复红以流水线产物定位。
2. 用例内其余固定等待已全部清除（`grep setTimeout` 零命中）；同文件无其他用例。
