## Why

cohort 日批（固定 10 只、每交易日一次，9-25 上线）已积累 245 次运行 / 183 次成功分析 / 每票 16~21 个有效样本，但从未做过系统性评估。当前数据形态已支持第一轮评估，且有三个已发现的问题需要读数量化：①10-13 分析 LLM 切换后单份成本 0.8M→2.1M token（约 2.6×），cohort 一项占 300 万日预算约 73% 并于 10-29 触发 budget_stopped；②近 10 日决策 0 条 long（全观望锁定的延续，10-10 已诊断、处置未拍板）；③confidence 38% 恰为 0.55（SOP 点名的「整齐得可疑」形态，未归因）。评估本身还承担一个双重角色：neutral 反事实读数同时是全观望锁定处置的证据材料。

## What Changes

建立 cohort 评估程序：**四轨道分离评估，不合成总分**，口径先入台账再取数。

- **轨道 A（供给与成本）**：按「每成功一份报告」归一的成功率 / token 成本 / 预算占用读数；结论面向「日频 vs 隔日」的运营决策。
- **轨道 B（过程质量）**：citation 三项（Langfuse Scores 已有，183 条 trace 全覆盖）分段时间序列；judge 四维批**不在本 delta**（花 judge 配额，单独拍板，保持一次一个变量）。
- **轨道 C（方向能力）**：neutral 反事实读数（观望后 T+20 相对基准超额分布，评估侧只读自算，不依赖结算调度）+ short/long 结算胜负读数；正式读数以结算到位为准（预计 11 月中），此前只出标注 preliminary 的中期读数。
- **轨道 D（日频价值）**：方向翻转率 + 观点保鲜期，仅描述统计。

三个横切前置：①窗口切分（9-25~10-10 / 10-13~10-16 / 10-17 后，段内结论、段间差异只作换模型专案记录）；②confidence 0.55 归因闸（未归因前禁入任何校准曲线）；③统计单位 = 交易日（同日 10 只横截面均值后再跨日检验，应对市场共同冲击导致的伪独立样本）。

产出物：`docs/evals/` 读数报告 + `docs/evals/metrics/runs.jsonl` 记行 + 指标口径登记 `docs/evals/metrics.md` §1。全部只读，不改任何观点 / 记账 / 结算行。

## Capabilities

### New Capabilities
- `cohort-evaluation`: cohort 评估程序——四轨道指标口径、窗口切分、聚类统计单位、neutral 反事实读法、confidence 归因闸、读数产出与台账记录。

### Modified Capabilities

（无——复用 `paper-trading-cohort` 既有「Cohort 读数导出」能力取数，本 delta 不改其任何 Requirement；`evaluation` 能力的 judge 批扩展留待后续 change。）

## Impact

- 新增评估读数脚本（只读）：`scripts/`（部署/分析类），从 `sessions.db`（cohort_runs / predictions / job_runs）+ `market_snapshot.db`（行情）+ Langfuse Scores API 取数，不写任何业务库。
- `docs/evals/metrics.md` §1 新增口径条目（先行）；`docs/evals/` 新增读数报告；`docs/evals/metrics/runs.jsonl` 追加。
- 不触碰：分析链路、结算链路、执行链、prompt、前端。
- 依赖：Langfuse 在线（Scores 拉取）；opencode/bigmodel 配额（无——本 delta 不跑 judge）。
