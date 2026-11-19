# 人工验证报告: enforce-decision-contract-at-execution（T7 生产实跑）

**日期**: 2026-11-17（首夜，部分验证）/ 补验：2026-11-20（基于 11-18/11-19 生产批，见文末）
**验证人**: Closure（agent 辅助取证）
**关联 delta**: openspec/changes/enforce-decision-contract-at-execution/
**关联 issue**: #593
**生产版本**: 41587067（PR #595 squash，2026-11-14 部署）

## 背景

2026-11-14（周六）部署后，宿主停机吞掉 11-16（一）+11-17（二）两天日批。11-17 22:00 栈恢复，
手动补跑当日链（market_snapshot 22:05 ok → … → **paper_execute_daily 22:15 ok** → settle 等全绿；
screening 因 11-16 快照缺失失败，回填中）。本报告记录 22:15 执行批（新代码首个生产运行）的验证证据。

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果（生产 22:15 批） | 通过 |
|---|---|---|---|---|
| R5 摘要对账键在生产可用 | 是（test_driver） | summary 含 `skipped_details`/`plan_deviation` | 两键均出现，ops run summary 可查 | ✅ |
| R4 legacy 信号退化：schedule 不可解析 → 全额退出 + 留痕 | 是（test_schedule_unparsed_falls_back_full_exit） | 全额卖出 + `schedule_unparsed` | 600780.SH exit 信号（legacy 无 contract）→ 全额 34,400 股 @5.83 filled + `skipped_details=[{"symbol":"600780.SH","reason":"schedule_unparsed"}]`，与规格逐字一致 | ✅ |
| R2 触发门（生产，新契约决策） | 是（test_trigger_not_met/met） | 未触发零订单 + 留痕；触发放行 | **待验**：今日批仅有 legacy 信号（无 declared_contract）；今晚补跑池分析产出新决策后，11-18 16:10 批验证 | ⏳ |
| R3 档位权重（生产，新契约决策） | 是（test_trigger_met_enters_with_tier_weight） | light=10% 等 | **待验**（同上） | ⏳ |
| 摘要既有键兼容（closed-loop） | 是 | 既有键值不变 | `orders_placed=1/signals_created=10/skipped_symbols=[]`，格式与既有契约一致 | ✅ |

## 异常记录（非本 delta 缺陷，已立案）

- **#596 [P1] flat 信号无时效/supersede 感知**：本次批的 600780 卖单来自 12 天前（11-05 short，
  已 resolved_win）的遗留 flat 信号，击穿 11-12 buy 决策的新仓。存量信号选择语义缺陷，非 #595 引入。
  伴生观察：300927 收盘 20.32 已跌破其 buy 方案纪律线 20.50，reeval_triggers 无执行载体 + 无新决策，
  止损意图无主（依赖当晚补跑池分析出新高切决策）。

## 结论

- [x] 全部通过（R2/R3 按降档结论核关，见文末补验），可 archive
- [x] 部分通过（首夜）：R4/R5/兼容性 ✅；R2/R3 ⏳；伴生缺陷 #596 已立案

## 待办（2026-11-18）

1. 16:10 执行批后：查 summary——新契约信号是否按触发门/档位执行（核对 `skipped_details`/`plan_deviation` 与前夜决策的 declared_contract）
2. 补验通过 → 勾 delta tasks.md T7 → 核关 #593 → openspec sync + archive
3. 若新决策仍无 buy（全 sell/watch），R2/R3 无生产样本：以触发门/档位的单测 + legacy 退化路径生产行为为准，报告改判「R2/R3 生产样本未产生，单测覆盖 + 退化路径生产通过」，仍可核关（降档结论，如实标注）

---

## 补验（2026-11-20，基于 11-18/11-19 生产批）

**生产版本**: 41587067（#595）+ 6d963d34（#597 freshness，11-18 08:56 镜像重建入栈）；镜像含两者，11-19 22:07 重启复用同镜像。

**生产决策面核查**（execution.db / sessions.db 只读拷贝取证）：

| 核查项 | 证据 | 结果 |
|---|---|---|
| 部署后决策方向分布 | predictions `created_at>=2026-11-14`：neutral 16 + short 39，**buy = 0**（两 lane，含 11-18 与 11-19 两晚批） | R2/R3 无生产样本成立 |
| R2 触发门单测覆盖 | `tests/execution/test_driver.py` test_trigger_not_met / test_trigger_met 等 | ✅（2026-11-20 新鲜复跑 270 passed） |
| R3 档位锚单测覆盖 | test_trigger_met_enters_with_tier_weight（light=10% 下沿锚）等 | ✅（同上） |
| R4 legacy 退化生产 | 11-17 22:15 批 600780 全额退出 + `schedule_unparsed`（首夜已录） | ✅ |
| R5 对账键生产 | `plan_deviation` 键在 11-18 16:12 / 11-19 22:34 microcap 批 summary 中持续出现（值 `[]`，零偏差）；`skipped_details` 持续出现 | ✅ |
| 兼容性 | `skipped_symbols=[]` / `orders_placed` 键格式与既有契约一致（11-18/11-19 四个执行批） | ✅ |

**降档结论（预批准路径第 3 条）**：部署后生产决策面 11-18、11-19 两晚均零 buy（全 short/neutral），触发门与档位权重无生产样本产生。R2/R3 以单测覆盖 + legacy 退化路径生产通过为准核关；下一执行日批若出现 buy 决策，触发门/档位行为仍受 `skipped_details`（`TRIGGER_NOT_MET`）/`plan_deviation`（档位偏差）对账面持续监督。

**回归证据（2026-11-20 01:2x 新鲜）**：`uv run pytest tests/execution/` 270 passed；ruff check 全过；format 986 files 已格式化；mypy 98 errors in 30 files = 基线零新增。
