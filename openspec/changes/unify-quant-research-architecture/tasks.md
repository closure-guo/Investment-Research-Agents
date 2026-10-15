# Tasks: unify-quant-research-architecture

> 上线顺序 = 任务顺序（D4：数据先行——先回填后切深度常量，否则三条供给线同时 fail loud）。

## P0-1 回填补齐成交额（market-snapshot ADDED）

- [ ] 先红后绿：`fetch_symbol_history_raw_qfq` 输出 +amount 列（新浪日线原生 amount，单位归一与日批一致）；源缺 amount → 取数失败不落半口径行（单测：回填行 amount 可算 `ts_mean(amount,20)` / 缺 amount 票计失败）
- [ ] 回填脚本兼容（列契约六列）+ 对既有回填区补 amount（断点续跑：仅重取 amount 空的日期段或全票重取 upsert）
- [ ] 完成扩展回填（2026-01-05..07-09 剩余 ~2400 票 + ≤16 票 partial 修复），快照库 ≥150 交易日全票覆盖核验

## P0-2 准入面板深度下限（factor-library ADDED）

- [ ] 先红后绿：`ADMISSION_PANEL_LOOKBACK_DAYS`（默认 150，`factors/constraints.py` 单一来源）+ 三入口（提案月批 / 基线注册 / 外部导入）面板构造切换；深度不足 fail loud（错误含补历史指引）
- [ ] 面板构造口径拆分：准入面板（新常量）与衰减监控面板（`DECAY_WINDOW+30` 不动）单测钉死互不影响
- [ ] 生产实跑：基线注册补跑 → 深面板五条 builtin 过闸读数（预期 turnover_proxy 过闸①②，读数落验证报告）

## P0-3 外部供给接入（factor-proposal ADDED×2）

- [ ] 先红后绿：外部候选导入入口（ops 任务 `factor_external_import` 或脚本，接受 DSL 表达式候选文件）→ `source="external"` 过同一五道闸；不可解析计 `errors` 桶；摘要同本地口径
- [ ] 拒收原因回喂：深度改进提案上下文注入近期 gate/reason 聚合分布（空时诚实标注）；核对「双模型分工」深度路径接线（spec 已有、实现是否在跑）
- [ ] RD-Agent spike（投用前置，六步走，报告落 `tests/validation/`）：
      ① 环境：WSL2 Ubuntu 发行版 + Docker Desktop 集成 + 独立 venv（Python 3.10–3.12；**不进**项目 3.14 环境）
      ② 数据：Qlib 侧先用社区数据集 `chenditc/investment_data`（A 股 qlib_bin）——**不先做自有导出器**；社区数据质量/更新节奏不足时再另立「SQLite→Qlib 导出器」delta
      ③ 运行：`rdagent fin_factor` 单循环真实跑（LLM 走 LiteLLM，接现有网关/DeepSeek 配置）
      ④ 翻译：按「外部表达式翻译契约」TDD 实现翻译器（二值裁决/映射表可审计/拒绝猜测翻译；算子覆盖表与不可翻译清单随报告）
      ⑤ 导入过闸：翻译产出经导入通道过五道闸，逐条读数（过闸率/拒收原因分布）落报告
      ⑥ 结论：供给线③是否投用——不达标则导入通道保留、供给线不投用（架构零改动）

## 收口

- [ ] `uv run pytest tests/factors tests/data tests/outcome -q` 全绿；ruff / mypy 干净
- [ ] `openspec validate unify-quant-research-architecture --strict` 通过
- [ ] 验证报告落 `tests/validation/`（深面板 builtin 读数 + spike 报告引用）+ sync + archive

## Deferred（本 delta 不做，另行立项）

- 财务披露日 PIT（报告期→披露日）· 回测用含退市股池 · 文本 available_at 快照库（数据地基三件）
- G2 分层回测闸 · G4 扣费回测闸（闸门扩容）
- 决策层消融实验（多 Agent vs 自一致性，用数据裁决辩论层价值）
