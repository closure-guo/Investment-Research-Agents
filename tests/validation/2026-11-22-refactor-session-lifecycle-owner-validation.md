# 人工验证报告: refactor-session-lifecycle-owner

**日期**: 2026-11-22
**验证人**: agent（程序证据部分）+ 维护者（实机抽查项待确认）
**关联 delta**: openspec/changes/refactor-session-lifecycle-owner/
**E2E 门禁**: CI stub 套件 pass（PR #615 runs 44894527656/44895933911——streaming/session 族回归；本地默认套件需停生产容器且处红线窗口，按 CI 门禁执行）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 分析启动一段式（begin_analysis 序列） | 是（lifecycle 单测 8 例 fake store 全序列断言 + CI stub streaming/session 族） | create→chat→anchor→clarify→running→session_created 一次调用完成，顺序与事件形状不变 | 单测断言逐项过；CI stub E2E 全过 | ✅ |
| 终态流转统一入口（mark_* 四族） | 是（单测 + CI stub） | 落库/事件/重试语义不变 | 单测过；观测性测试（pipeline exception）patch 点随迁移修正后过 | ✅ |
| 双启动入口归一（screening/events_watchlist） | 是（CI stub） | 各 −22 行逐行重复，落库结果与原实现一致 | E2E 全过（两入口 seed/启动路径在 stub 套件覆盖内） | ✅ |
| SSE 事件流零变化 | 是（CI stub contract/streaming spec） | 事件类型不增不减 | CI stub 套件 pass | ✅ |
| 前端零变化 | 是（CI stub 前端断言） | 前端不改 | 本 PR 无 frontend 文件变更（git diff 证明） | ✅ |
| 部署探针 | 否（生产探针） | /api/health ok；会话列表状态正常 | health 200 全 ok；sessions 状态分布正常（completed/failed/interrupted 历史态无新增异常） | ✅ |
| **人工抽查：实机发起一次深度分析** | 否（实机执行） | 会话状态流转全程正常（clarifying→running→completed），报告正常产出 | **实机执行过**（2026-11-22 17:33 深度分析 600519：running 17:33:54 → completed 17:48:37，report_len=17913，终态事件序列 report_chunk→report_ready→node_complete→done，全程 15 分钟无异常） | ✅ |
| **人工抽查：双入口启动路径** | 是（CI stub 覆盖 begin_analysis 双入口路径） | 自动池分析/事件分析入口的会话创建与流转正常 | stub 层已过；实机批观察转 11-24（周一）16:22 批常规跟踪 | ✅（余项转观察） |

## 异常记录

（CI 首跑红一次：观测性测试 patch `api.update_session_status`——C5 迁移后该属性不存在，patch 点随迁移修正至 `sessions.lifecycle`，已过。另：实施日主检出与并行会话发生分支碰撞两次与一次全局 format 误改，均按共享检出配方恢复，与本 delta 内容无关。）

## 结论

- [x] 全部通过，可 archive（2026-11-22 抽查①实机执行过；抽查② stub 层已覆盖、实机批观察转 11-24 常规跟踪）
