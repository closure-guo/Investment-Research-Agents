## Why

消融实验（`docs/evals/metrics.md` §2.4 round10 预登记）暴露两处**测量层缺陷**，不修就没法得到可信的层增量结论：

1. **debate_quality 5 分档漏判（prompt 机制已到顶）**：round9 审计实测 4 分档判例执行严格，但 5 分档对论点标头的纯定性表述视而不见（宁德/美的/平安银行三行标头含纯定性论点仍给满分）；v5 改为 prompt 内强制逐条枚举后离线重判 8 行，准确率 2/8 → 6/8，但仍有漏判（宁德）且出现回归（招行）——**LLM 自我枚举是随机的，不是确定性的**。结论：把判据从「LLM 自律」搬到「结构化输出 + 程序强制」。
2. **消融材料不落盘、重复次数不可参数化**：`tests/scripts/ablation_pilot.py` 的 `TICKERS` / `REPEATS` 是模块常量（3 标的 × 3 重复），且每轮只落 judge **分数**、不落 judge 输入材料——rubric 变更后无法离线重判（round10 只能绕道 Langfuse 拉观测反解材料），也无法直接跑 10 重复的 90 条权威轮。

这两项都是「影响消融」的前置：判据不稳 → 分数不可信；材料不落盘 → 改 rubric 就要重烧管线 token。

## What Changes

- **debate_quality 结构化枚举 + 程序封顶**：judge 输出契约新增 `points` 字段（逐条列出论点标头及类型 `data`/`qualitative`）；`run_judge` 解析后由**代码**判定——存在任一 `qualitative` 即把分数压到 ≤4（`min(score, 4)`），与 LLM 是否自觉扣分无关。枚举缺失/非法时**不擅自封顶**，但标记 `enumeration_missing` 并把该标记随分数落库（fail-open + 可审计，杜绝「静默当作无纯定性论点」）。rubric 版本 debate_quality v5 → v6。
- **消融材料落盘 + 参数化**：`ablation_pilot.py` 的标的与重复次数改为 CLI 参数（默认值不变），并把每次 run 的 judge 输入材料（`judge_vars`）按 `(variant, ticker, repeat)` 落盘到 `reports/ablation/judge_vars/`，run 记录携带材料路径与 judge 明细（分数 + 纯定性条数 + 是否封顶 + 枚举是否缺失）。断点续跑语义不变（done key 仍为 `(variant, ticker, repeat)`）。
- **判分协议改 K 次均值**（实现中实测追加）：单次 judge 调用在 5/4 边界**双峰翻转**（宁德 04baff5c 同材料 n=13 次调用：4 分 7 次 / 5 分 6 次，σ≈0.5），与消融待测层级增量（0.25–0.5）同阶。消融 judge 分数改为 K 次均值（`run_judge_mean`，`--judge-repeats` 默认 3），每次分数与极差随 run 落盘；SHALL NOT 用中位——p≈0.5 双峰下中位不降翻转概率。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `evaluation`：「LLM-as-Judge 评估器与 rubric 标准」增加 debate_quality 结构化枚举与程序封顶条款（含枚举缺失的可审计降级）；「数据对齐消融实验」增加 judge 材料落盘与重复次数参数化条款。

## Impact

无产品行为变化（不触业务链路）。影响面：
- debate_quality 分数口径变更（v5 → v6）——**跨此切点的 debate 分不可直接比较**；测量侧：5 分档漏判由程序兜底，封顶证据随分数落库。
- 消融可离线重判（不重跑管线、不依赖 Langfuse 拉取），可参数化跑 10 重复权威轮。

回归：`tests/evals/test_judges.py`（新增封顶/降级用例）、`tests/evals/test_ablation_pilot.py`（材料落盘 + 参数化 + 断点续跑）、既有消融编排用例（`tests/evals/test_ablation.py`）全绿。
