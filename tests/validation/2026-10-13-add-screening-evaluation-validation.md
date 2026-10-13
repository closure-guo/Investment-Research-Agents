# 人工验证报告: add-screening-evaluation

**日期**: 2026-10-13
**验证人**: ZCode 实施 runner（自动化执行；人工终审签字待补）
**关联 delta**: `openspec/changes/add-screening-evaluation/`
**E2E 门禁**: 不适用（纯计算/评估层，无前端/SSE/会话交互面）

## 验证结果

| # | Scenario | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 1 | 前向跟踪数据产出（spec：30 池日 → 5/10/20 日窗口 + 有效 n + CI） | 入选组/落选组各 horizon 的均值、胜率、配对 CI 与有效 n | 合成 30 池日端到端：`forward` 三 horizon 分组齐备、`unfilled_counts` 记录（LIMIT_UP_UNFILLABLE 1 例）、配对 CI 产出；11/11 checks PASS | ✅ |
| 2 | 收益口径（撮合 + 时点截断） | 入场 D+1 开盘 ± 滑点、一字涨停剔除记原因；只用 ≤ as_of | 单测（手算小面板逐数对照 + 未成交剔除 + 截断）全绿；复用 `match_order` 同源 | ✅ |
| 3 | 随机变体可复现（spec 场景：种子 42 两次一致） | 两次执行标的序列完全一致 | 单测钉死 | ✅ |
| 4 | 三变体 + 配对 bootstrap B=10000 | 因子打分/随机/等权指数同窗口；CI 含 0 仅「无显著差异」 | 单测 + 措辞守卫（渲染断言无「无效/无价值」）全绿 | ✅ |
| 5 | ±L3 粗筛价值判定（spec 场景 keep +2.1%/drop −3.4%/CI 不含 0） | 陈述「具有排雷价值」+ 披露窗口与 n；反向 → 无显著差异 | 单测两向钉死；合成跑批 keep n=219 均值 +0.58% vs drop n=60 均值 −1.92%（踩雷率 5.0% vs 13.3%） | ✅ |
| 6 | 因子来源分层（builtin vs LLM） | 两分层前向对照登记进报告 | `stratify` 两分层 + 配对 CI + 报告渲染单测 | ✅ |

命令证据：

```
$ uv run pytest tests/evals/screening -q
35 passed in 1.26s

$ uv run python tests/validation/2026-10-13-add-screening-evaluation-e2e.py
verdict = PASS   （checks 11/11：forward_selected_n / forward_rejected_n / forward_paired_ci /
                  unfilled_recorded / ablation_three_variants / ablation_paired_ci /
                  l3_both_groups / l3_paired_ci / stratify_both_strata / stratify_paired_ci / report_written）
```

## 异常记录

1. **live 数据报告为后续项（不伪造）**：spec 的「每 20 交易日前向报告」需要真实选股数据积累；
   本机受东财 IP 封禁（issue #325）且选股上线后需先积累 20 个交易日快照 → 本 change 交付
   **合成数据样本报告**（`docs/evals/2026-10-13-screening-forward-sample.md`，`data_source: synthetic` 明示）
   与可运行的跟踪/报告函数；真实 cadence 挂 issue。
2. **合成面板口径**：样本报告中的收益结构为合成（固定种子 20261013），**不构成真实市场有效性证据**；
   报告头部已明示。
3. **调度接线边界**：日批自动触发（与 outcome 日批对接）按计划列为 cadence 后续项，本 change 交付
   可直接调用的函数（合成端到端已证明可跑通）。
4. **既有测试**：全部新增文件，既有测试零修改（红线满足）。

## 结论

- [x] 全部通过（机器核对层面），可进入 archive 流程
- [ ] 存在失败项（无）
- 说明：live cadence 与人工终审签字待补；样本报告为合成数据，真实数据报告为显式后续项。
