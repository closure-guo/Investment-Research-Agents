# 因子工具消融报告（n=10 配对，技术分析师 ± query_factor_library）

**status**: active
**日期**: 2026-10-20（批次运行 12:57–14:05，约 68 分钟）
**来源**: `reports/ablation/ablation-20261020-140540.json`（20 run 聚合，本地产物）
**关联 issue**: #326 主项（delta `add-analyst-factor-tools` 闭环批次）

## 配置

- **配对**：`tech_bare`（裸技术分析师）→ `tech_factor_tool`（技术分析师注入因子库工具输出；单变量 = `_variant_technical_analyst(with_factor_tool=...)`，见 `evals/ablation.py`）
- **标的**（10 只，跨沪深主板/创业板/科创板）：600519 / 601398 / 600036 / 000001 / 000651 / 002415 / 002594 / 300750 / 300059 / 688981
- **规模**：10 标的 × 2 臂 × 1 重复 = 20 run；配对单元 = ticker（bootstrap 重采样）
- **判分**：judge = glm-5.3-flash@opencode Go（K=3 均值协议）；维度按变体适用性过滤为 `report_relevance`（两臂同为分析师层直出，无辩论/决策层）
- **被评对象**：deepseek-v4.1-flash@opencode（分析主链路）
- **prompt**：全部 `local`（E2E/eval 层 `LANGFUSE_*` 置空，回退仓库权威源）

## 结果

| 变体 | n_runs | report_relevance（中位） |
|---|---|---|
| tech_bare | 10 | 4.667 |
| tech_factor_tool | 10 | 5.0 |

**judge 维度增量（tech_bare → tech_factor_tool）**：`report_relevance` 点估计 **Δ = 0.0331，95% CI [−0.167, 0.1998] → 无显著差异**（CI 含 0）。

## 健康检查与前提验证（SOP 收口项）

| 检查 | 读数 | 判定 |
|---|---|---|
| run 齐全度 | 20/20（两臂各 10） | ✅ |
| judge 失败率 | 0（日志零 judge 异常 / RateLimit） | ✅ |
| **对照非空洞（关键）** | 10/10 标的因子库注入口非空；库内 4 个 active 因子，`as_of=2026-10-19`，每标的 4/4 具值（value/zscore/ic_mean_60d） | ✅ 处理臂材料真实在场 |
| 快照一致性 | 10/10 标的 `snapshot_digests` 落 JSON，两臂共享同一快照 | ✅ 单变量成立 |
| 解析降级 | 1/20 run 技术分析师 JSON 解析失败 → 降级原始文本（5%）；judge 仍有材料 | ⚠️ 已披露，不排除该 run 报告质量偏低 |

## 结论（CI 纪律措辞）

- 在本批次条件下（n=10 配对、judge=report_relevance、K=3 均值），**技术分析师 ± 因子库工具未获统计支持**——点估计微正（+0.0331）且 95% CI 含 0。
- **不得**据此引申为对因子库工具价值的否定（报告模板纪律）；本批次只测**分析师层上下文注入**（工具化先例：节点调用 + 输出注入），不测 LLM 自主多步工具调用（owner 裁决：真 ReAct 暂不实施）。
- 采纳/裁剪决策须有独立成本—收益证据，遵循评估 SOP（judge 校准先行、一次只动一个变量）。

## 适用边界披露

1. **有效 n=10**（配对单元=标的）；CI 宽度反映该分辨率。
2. **单维判分**：两臂无辩论/决策层 → 仅 `report_relevance` 适用（#112 口径）。
3. **因子库状态**：本批为 2026-10-19 落库的 4 个 active 因子（volratio20 / volcv20 / range1 / range1 的 20 日均值归一化变体，expression 见因子库）；此前 NO-GO 的「因子库 0 active → 两臂等价为空」前置已被满足并逐标的核验。
4. **数据面**：东财行情域封禁持续（`stock_zh_a_hist`/`stock_individual_info_em`/行业接口 ConnectionError），fetch 走既有回退/降级链；快照 digest 一致，但个别行业成分/指数数据为降级态（与两臂差异无关）。
5. 原始 JSON 落 `reports/ablation/`（本地，不入库）；判分处于 10-17 judge 端点切点之后，与切点后读数同口径。
