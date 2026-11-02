# Tasks: update-pipeline-stall-watchdog

- [x] 共享纯函数模块（停滞阈值读取/failure_reason 文案/迟到完成守卫）+ 单测
- [x] agent_factory `_background_consume`：wait_for 超时改「停滞预算与总预算取小」，超时语义按先到期者分类产出对应文案（TDD 先红后绿）
- [x] pipeline_runner：看门狗线程 + 共享 liveness 状态（last_event_ts/type/node）+ 停滞处置（状态/终态/取消置位）（TDD 先红后绿）
- [x] 迟到完成不得覆盖停滞终态（fast path 迟到完成路径的状态守卫）
- [x] failure_reason 双文案可区分（"管线执行超时" vs "管线停滞"）
- [x] 验证报告落 tests/validation/（2026-10-31-update-pipeline-stall-watchdog-validation.md；非交互类变更：验证=阈值证据 + 双路径集成测试 + 静态检查，实机样本待下次真实 provider 故障回收）

> 实现注记：TDD red 阶段实证「消费者处理耗时（慢 SQLite 写 0.59s）会被基于
> 拉取时刻的 liveness 误判为停滞」，机制从「事件间隔时间戳差」修正为「队列
> 空等窗计量」（asyncio 路径：wait_for 超时即队列停滞停滞预算时长；fast path：
> 看门狗线程轮询事件源产出时刻）——契约不变（零事件超阈值判停滞），处理耗时
> 只烧总预算。
