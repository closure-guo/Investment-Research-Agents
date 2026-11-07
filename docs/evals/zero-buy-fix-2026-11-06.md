# 零买入修复 · 进度台账（2026-11-06）

> 计划：[docs/superpowers/plans/2026-11-06-zero-buy-decision-fix.md](../superpowers/plans/2026-11-06-zero-buy-decision-fix.md)
> 分支：`fix/zero-buy-decision-20261106`（rebase 至 origin/main 后推送）→ **PR #569**
> 本文件按小时更新，记录「做了什么 / 得到什么 / 下一步」。

## 诊断结论（已定稿）

| 事实 | 数值 |
|---|---|
| 全部分析会话中买入次数 | 0 / 581 |
| 研究经理「看多」占比 | 12 / 201（6%） |
| 看多之后变成买入的次数 | 0（12 条全部 watch） |
| 12 条看多中卡在「无催化剂/已定价」 | 12 / 12 |
| 12 条看多中提及「±2%/带宽/分辨率」 | 6 / 12 |
| 107 条 watch 中提及带宽/σ | 63（59%） |
| 已盯市 60 条看空的方向性收益 | -2.68%（超额 -3.56%，胜率 12%） |

**机制**：买入需证明「20 个交易日内有可兑现催化剂」＋ 财报真空期（11 月三季报已披露、下个窗口次年 1 月）＝ 结构性禁止买入；风险辩论用「止损窄于日波动」可否决任何方案；±2% 判分带宽被当成风险预算。

## 状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| 计划文档 | ✅ 完成 | `docs/superpowers/plans/2026-11-06-zero-buy-decision-fix.md` |
| Task 0 决策分布读数 | ✅ 完成 | `f754c109`；`tests/outcome/test_decision_mix.py` 3 passed |
| Task 1 日报告警 | ✅ 完成 | `436521fb`；6 新测试 + 6 既有测试全绿（12 passed） |
| Task 2 变体提示词 | ✅ 完成 | `6b67c5ed`；grep 校验 A 保留 ±2%/删催化剂、B 删契约（117→108 行） |
| Task 3 隔离运行器 | ✅ 完成 | `743d6309` + `77d29bcf`；6 测试绿；**--variant 改可重复（修 B 臂双变体缺口）** |
| Task 3 冒烟 | ✅ 通过 | 002916 watch(completed)，9 分钟，隔离库落库、生产库未动 |
| Task 4 消融实验 | ✅ 完成（判据落「两臂都 0 buy」格） | 报告：[zero-buy-ablation-2026-11-06.md](zero-buy-ablation-2026-11-06.md)；A 0/8 buy、B 0/4 buy（4 只 provider 过载未完） |
| Task 5 trader 提示词 | ⏭️ 按计划前置跳过 | 「两臂都 0」→ 不改生产提示词；归因修正见报告 §5（锁分布全链） |
| Task 6 risk_judge 提示词 | ⏭️ 按计划前置跳过 | 同上 |
| Task 7 回归验证 | ⏭️ 依赖 Task 5/6，一并跳过 | — |
| Task 8 账本语义提案 | ✅ 提案已交（待 owner 拍板） | `4c828d05`；`openspec validate --strict` 通过 |
| Task 9 评分口径提案 | ✅ 提案已交（待 owner 拍板） | `be500ec2`；metrics.md §1.24 + openspec validate 通过 |
| 收尾验证 | ✅ 全绿 | 目标面 21 passed；tests/outcome 全套件 999 passed + 2 skipped |

## 执行偏差记录（相对计划）

1. **Task 0 测试路径**：`tests/outcome/test_decision_mix.py`（flat，符合仓库 tests/outcome 惯例）而非计划的 `tests/outcome/ops/`；实现为加固版（缺表容错，多一个测试）。
2. **Task 1 接入点**：daily_brief 的 `warnings` 实为数据缺口专用 `list[dict]`——决策分布改为独立事实段 `facts["decision_mix"]`（含告警句），LLM 日报与降级渲染两条路都能看见；`zero_buy_warning` 契约与计划一致。
3. **Task 3 运行器**：`--variant` 改可重复 + `parse_variant_mapping` 独立函数——计划的草案无法表达 B 臂（trader+risk_judge 同时注入）；另补 scripts 引导（ops_api 依赖顶层 evals 包，`monitor_unverifiable_ratio.py` 同款先例）。
4. **Task 4 样本替换**：计划点名的 4 只看空票（002852/600780/600496/000019）在生产库无会话无 prediction（幽灵引用）；按「同批基线配对」意图替换为 10-30 批次的 000428(sell)、600916(sell)、002648(watch)、600015(watch)。**「本日 20 条决策」实为 10-30 批次**（生产库 predictions 按日恰好 20 条，11 月后新会话在容器卷，与本计划宿主侧库无关）。

## 停止点（触发即停，不硬做）

1. 生产提示词切换后、观察第一个交易日前 —— 需 owner 知晓回滚路径。
2. `metrics.md` §1 与结算代码改动 —— 只交提案。
3. 消融出现「两臂都 0 buy」—— 规则不是唯一原因，重新归因。
4. 需要判断「买入建议质量」—— 交人工终裁。

## 日志

- 2026-11-06 01:0x 完成机制诊断，落计划文档与台账。
- 2026-11-06 01:0x-01:40 Task 0-3 全绿 + 冒烟通过；Task 8/9 提案交付。分支 `fix/zero-buy-decision-20261106`。
- 2026-11-06 01:40 A 臂启动（8 只，顺序执行，预计 ~80 分钟）；B 臂候跑。
- 2026-11-06 01:53 **改双臂并行**：独立库独立进程符合计划风险设计，且两臂同刻拉同一行情快照可消除「B 臂晚数小时、数据窗口漂移」的混杂。
- 2026-11-06 02:26/02:40 两波 `sqlite3.OperationalError: unable to open database file`（session_store.append_session_event 重试耗尽）：A 臂 000428/002648 失败、B 臂 002916/601066 失败，两进程随后崩溃退出。**根因假设：实验期间的隔离库只读轮询**——Windows 上读连接 close 触发 WAL checkpoint + 删 -wal/-shm，与高频写 session_events 的管线进程发生 shm 映射竞态（session_store.py:36 已有同类瞬态错误先例注释；两波失败与轮询时刻重合）。磁盘未满（D: 剩 102G）、非独占锁。
- 2026-11-06 02:55 盘点：A 臂 5/8 completed（600026/300033/002916/601066/600916）+ B 臂 2/8（600026/300033）数据完好保留在隔离库；failed 会话无 prediction，不污染。**处置**：残余符号补跑（复用原隔离库），全程只用 Langfuse 只读监控、禁碰隔离库文件；顺序执行。
- 2026-11-06 02:57 A 臂补跑启动（000428,002648,600015 → tmp/ablation-A.db）。
- 2026-11-06 03:20 A 臂 8/8 全齐；B 臂二轮补跑（4 只）全灭于 provider 过载（RateLimitError）。
- 2026-11-06 03:2x-06:55 B 臂三轮补跑：运行器两轮加固（符号级重试 + RateLimit 纳入重试、6×180s）；002916 一只成功，其余持续过载。**成本纪律行使：停止补跑**——每次失败派单烧 ~100-150k token 零产出，已完成样本已足以落判据。
- 2026-11-06 07:0x **实验收口**：A 0/8 buy、B 0/4 buy → 判据「两臂都 0」格 → Task 5/6/7 按计划前置跳过。**归因修正（本实验最重要产出）**：锁分布在 fund_manager 完整契约拷贝 + 4 分析师 horizon_hint 条款 + 滞回机制——B 臂 300033 在变体删净「20 个交易日」文本后仍引用该逻辑，来源是上游材料而非模型自发；计划的「改 trader/risk_judge 即可解锁」假设不成立。第二轮消融设计建议见报告 §6，待 owner 拍板。
- 2026-11-06 07:1x 收尾验证全绿（目标面 21 passed + outcome 全套件 999 passed/2 skipped）；生产库 predictions=287/sessions=586 逐字未动。
