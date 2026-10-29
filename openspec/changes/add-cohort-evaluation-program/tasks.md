## 1. 口径登记（先行，硬依赖）

- [x] 1.1 `docs/evals/metrics.md` §1 登记四轨道全部指标口径：轨道 A（tokens_per_success / 成功率 / 预算占用）、轨道 B（citation 三项日均值）、轨道 C（neutral 反事实 r_e / short·long 结算胜负）、轨道 D（翻转率 / 保鲜期）；每个含定义 / 分母 / 窗口 / 剔除规则
- [x] 1.2 §1 登记窗口切分事件表（W1/W2/W3 边界 + 熔断日清单）与统计单位声明（交易日聚类、n<15 结论限级规则）
- [x] 1.3 §1 时间线预登记本轮 run 名（`cohort-eval-20261029`）与基线切点

## 2. 只读读数脚本

- [x] 2.1 新建 `scripts/cohort_eval_readout.py`（只读、零 LLM、参数 `--as-of` / `--universe-version`），按 D1-D4 口径实现四轨道取数；先写失败测试（fixture 造 2 票 × 3 日小面板，断言反事实 r_e / 聚合口径 / 缺日剔除三行为）再实现
- [x] 2.2 轨道 A：`cohort_runs` + `job_runs` 取分段成功率 / tokens_per_success / 预算占用，熔断日剔除并列示
- [x] 2.3 轨道 B：Langfuse Scores 按 cohort trace 拉 citation 三项，按日均值 + 分段对比 + 覆盖率披露
- [x] 2.4 轨道 C：neutral 反事实（快照收盘 + `index_closes` 基准，T+1 开盘~T+20 收盘，同票同日去重、缺日剔除计 `missing_bars`）；short/long 结算状态分布（本期只出 `preliminary` 行）
- [x] 2.5 轨道 D：方向翻转率（每票每周）+ 保鲜期曲线（T 日观点方向 vs T+k 实现超额符号一致率）
- [x] 2.6 输出双产物：JSON（机器可读，含每段 n）+ Markdown 报告骨架（轨道分节 + 三前置小节占位 + n 声明）
- [x] 2.7 `uv run pytest` 定向新测试全绿 + `uv run ruff check` / `uv run mypy` 通过

## 3. 首轮读数产出

- [x] 3.1 对生产库（v1 / as-of=读数日）跑首轮，人工核对三个抽查锚点：①轨道 A 单份成本与 job_runs 手算一致；②任选 3 条 neutral 反事实手算复核；③citation 覆盖率 = 183/183
- [x] 3.2 confidence 0.55 归因小节：分桶定位堆积起止日 + 抽 ≥5 条 trader/risk 原文人工判读，结论三态（prompt-anchor / default-fallback / genuine-mode）+ 处置去向；未归因前报告中不含任何 confidence 下游读数
- [x] 3.3 读数报告落 `docs/evals/cohort-eval-20261029.md`：四轨道结论（W3 段限量级描述）+ 三前置小节 + 「未达显著 ≠ 无差异」声明 + neutral 读数与全观望锁定的证据衔接段
- [x] 3.4 `docs/evals/metrics/runs.jsonl` 追加本轮行（run / HEAD@启动 / means）
- [x] 3.5 short/long 到期闸检查：确认 open 方向性观点数与最近到期潮日期，正式结算读数排期到 ≥30 条到期后（预计 11 月中）

## 4. 验证与归档

- [x] 4.1 只读验证：脚本运行前后 `predictions` / `cohort_runs` / 结算表行数与内容 hash 不变
- [x] 4.2 人工验证报告落 `tests/validation/`（抽查锚点记录 + 报告通读结论）
- [x] 4.3 `openspec validate add-cohort-evaluation-program --strict` 通过
- [x] 4.4 收口核对：runs.jsonl 行存在、metrics.md §1 口径与实现一致（无未登记指标）、报告无合成总分 / 无跨段混合均值 / 无未归因 confidence 读数
