# 人工验证报告: add-feedback-quota-fairness

**日期**: 2026-10-30
**验证人**: agent（生产载荷核证；自动化证据见 delta tasks 与独立审查轮记录）
**关联 delta**: openspec/changes/add-feedback-quota-fairness/
**E2E 门禁**: 不适用（纯后端数据管道，非交互类变更）

## 部署在位证据

生产后端容器（10-28 晚宕机排障重建，与 #502/#510 同批上生产）于 **2026-10-30 16:11**
由 15:55 收割批自动刷新 `data/factor_research/explored_factors.json`，载荷含
`quota_allocation` 字段——该字段仅存在于本 delta 引入的代码路径，即新代码已在
生产收割链路运行。

## 生产读数（2026-10-30T16:11:13+08:00 载荷）

| 读数 | 值 | 判定 |
|---|---|---|
| total_explored | 2105 | — |
| counts | admitted 12 / rejected 302 / untranslatable 1772 / transcription_error 19 | 与 [[factor-supply-audit-20261027]] 漏斗形态一致 |
| returned | 80（= DEFAULT_LIMIT） | ✓ |
| rejected_by_gate | 0:5, 1:6, 2:24, 4:8, 5:5 | **per-gate 保底生效**：闸0/1/4/5 全 ≥ floor=5，闸2 大桶按占比拿 24 |
| untranslatable_by_stage | llm:10, translator:10 | **stage 交织生效**：quota 20 平分双 stage |
| style_gap / contract | 在场 | 同批 #523/#482 能力共存正常 |

## 验证结果

| Scenario | 覆盖方式 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| 次日 15:55 后生产载荷含 quota_allocation | 10-30 16:11 自动导出实读 | 字段在位且 rejected_by_gate/untranslatable_by_stage 双键齐 | 双键齐，见上表 | ✅ |
| 各闸保底（闸④ 不再 0 出场） | 同上 | 小闸 ≥5 | 0:5 / 1:6 / 4:8 / 5:5 | ✅ |
| stage 交织代表性 | 同上 | 双 stage 非单一独占 | llm:10 / translator:10 | ✅ |
| 读数与清单一致性 | 既有单测（M2 全量对账）+ 独立审查轮 500 轮模糊测试 | 出场统计=清单实际 | 测试绿 | ✅ |
| 既有语义零回退 | 既有回归（admitted 全量/去重/limit/contract/gate_rejections） | 行为不变 | 测试绿 | ✅ |

## 归因口径备忘

10-28 晚容器重建同批上生产 #502/#509/#510——10-29 起的收割读数是**叠加变量**，
判效归因（A2，门 10-31）须按叠加批记录，不得把 untranslatable 下降单独归因本 delta
（本 delta 只改变**出场构成**的公平性，不改变转译成功率）。

## 结论

- [x] 生产核证通过：per-gate 保底 + stage 交织在生产载荷如实生效
- [x] 可进 sync + archive
