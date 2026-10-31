# 验证报告: update-pipeline-stall-watchdog（issue #521）

**日期**: 2026-10-31
**验证人**: agent（ZCode 会话）
**关联 delta**: openspec/changes/update-pipeline-stall-watchdog/
**E2E 门禁**: 不适用（纯后端管线语义，非交互类变更）

## 验证结果

| 项 | 证据 | 结果 |
|---|---|---|
| 阈值证据（STALL_TIMEOUT=600s 默认依据） | 生产库 60 个完成会话 / 43 万个管线内事件间隔：p99.99=17s、最大 124.4s（fetch 争用窗口，#491 同族）；>1000s 空洞全部为 `session_created→analysis_start` 队列等待（看门狗不计时） | ✅ 600s ≈ 5× 余量 |
| asyncio 路径停滞判死 | `test_asyncio_stall_marks_failed_with_stall_reason`：零事件 0.5s（阈值）→ failed + 「停滞」文案 + 无「管线执行超时」字样（TDD 先红后绿） | ✅ |
| thinking 流动不误判 | `test_thinking_flow_does_not_trigger_stall`：0.05s 间隔持续 token 不触发停滞 | ✅ |
| fast path 独立看门狗 | `test_fast_path_watchdog_marks_failed_while_blocked`：事件源阻塞（模拟 SSL 读挂死）→ 看门狗线程判死，failure_reason 带最后事件类型，is_running 归 False | ✅ |
| 迟到完成守卫 | 同上测试后半段：阻塞解除、管线复活后状态保持 failed（终态先到者为准） | ✅ |
| 总预算语义不变（回归） | `test_pipeline_timeout.py` + `test_pipeline_watchdog_graph_done.py` 全绿（含「图完成排空滞后不误判超时」拓荆回归锚）；steady chunk 墙钟超时用例照常 | ✅ 10 passed |
| 静态检查 | ruff check / ruff format --check 全绿；mypy 触碰文件与基线多重集差 = 0 新增错误 | ✅ |
| 全量回归 | tests/（除 e2e/screening）1581 passed；3 failed 均环境性（uv 缓存锁 os error 5，基线同红实证；fm_decision_live 自跳过） | ✅ 零回归 |

## 异常记录

1. **TDD red 阶段发现 liveness 语义缺陷**（已修）：初版按「拉取事件时刻」记 liveness，
   red 测试实证消费者处理耗时（慢 SQLite 写 0.59s > 测试阈值 0.5s）被误判停滞。
   机制修正为「队列空等窗计量」——停滞只计「等待事件到达」的时间，处理耗时只烧
   总预算。契约不变，tasks.md 实现注记披露。
2. **实机停滞样本未采集**：停滞判死的最终实证依赖真实 provider 挂死（生产复现
   需等待下一次 opencode/方舟级故障）。本次验证=阈值证据 + 双路径集成测试；
   下次真实故障发生时，核对 failure_reason 落库形态并在此报告补记。

## 结论

- [x] 全部验收项通过（tasks.md 6/6），可 archive。
- [ ] 实机样本补记（非阻塞项，随下次真实故障）。
