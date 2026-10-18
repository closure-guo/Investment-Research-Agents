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
| 第一轮事件 | 自闭合流一次性投递 + `setTimeout(500)` | **受控 SSE**（`controlledSse()`）：事件按需 push、流闸门由测试持有 |
| 切到 s2 | `setTimeout(300)` | 等 s2 内容渲染（`findByText('你好！')`）为切完成条件 |
| 切回 s1 | `setTimeout(500)` | 切回前推完第一轮事件并等渲染；切回后等 token |
| 终态断言 | waitFor 5s | waitFor 10s + **钉续传游标 `streamAfterSeq === 3`**（证 resume 从断点续传） |
| CI 门禁 | `--exclude 'src/test/followup-resume-after-switch.test.tsx'` | 移除 exclude → 全量文件入门禁 |

### 二轮修正（首版条件等待合并前被 CI 证伪）

首版把三处 sleep 换成条件等待后在 CI 仍**两次重试全败**（`Unable to find text: 安孚科技分析完成`，21s）——证明不是窗口问题而是事件链真断。代码级根因定位：

1. 自闭合流下，「切走时 run 在途」这一前提**取决于机器速度**：慢机上流先自然关闭 →
   `pump` 防御性清理把相位落 `idle`；切回时 `selectSession` 的 `stillRunning`（streaming/
   connecting/resuming）不成立 → **不触发 resume** → chat_token 永不产生（确定性失败）。
   快机上切换先于流关闭（abort 路径保留 streaming 相位）→ resume 照常 → 通过。
2. 本地通过还叠加了挂载恢复路径的偶然命中（`loadWithRetry → selectSession(s1)` 在
   会话已持久化后解析 → 「刷新恢复」分支触发 resume）——同一竞态在 CI 的时序下不成立。

**修正**：受控流把「切走时任务在途」变成确定性状态（流不关，切换必走 abort 路径、
相位必保留）；同时消除对挂载恢复竞态的依赖（恢复分支在 reader 活跃时本就不 resume）。
逐机无关：本地 8/8 + `CI=1 --retry=1` 全量 96 文件绿。

**旁证排除**：失败日志里的 `HTTP 409 {"detail":"session_busy"}` 经溯源为**旁路测试文件**（`running-guard-toast.test.tsx` / `quickThreadGuards.test.tsx` 的 409 路径 console 输出，vitest 并行文件交错）——本用例 mock 对未匹配 URL 返回 404，不存在 409 通道，日志为噪声非本用例机制。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | 硬化后用例稳定 | 单文件连跑 **8/8 绿**（受控流，逐机无关）；`CI=1 --retry=1` 全量 **96 文件绿** | ✅ |
| 2 | 无跨文件回归 | 全量套件连跑 4 次全绿（96 文件/815 例；数量差 11 例=本 worktree 建自分支点 4e95ebfc，不含其后合并的图表契约测试） | ✅ |
| 3 | 事件链锚点真实（非恒真） | 探针实测三时点 DOM：轮内含「调用工具中·1 次/搜索了1个网页」；s2 含「你好！」；s1 回切含思考历史——锚点只在对应步骤后出现 | ✅ |
| 4 | CI 门禁覆盖恢复 | `ci.yml` vitest 步不再 exclude，全量文件纳入（PR 流水线首跑即验证） | ✅ |

## 未达成项 / 披露

1. **首版被 CI 证伪的记录保留**（上方「二轮修正」）：条件等待本身不足以保证
   「在途」前提，受控流才是根修——这是本 issue 的关键教训，已如实留档。
2. 用例内固定等待已全部清除（`grep setTimeout` 零命中）；同文件无其他用例。
3. 首版本地全量曾出现 1 次未留名的单文件失败（疑旁路文件抖动）：`CI=1 --retry=1`
   全量复跑绿；若 CI 复红以流水线产物定位。
