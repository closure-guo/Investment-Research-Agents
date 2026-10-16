# Tasks: unify-quant-research-architecture

> 上线顺序 = 任务顺序（D4：数据先行——先回填后切深度常量，否则三条供给线同时 fail loud）。

## P0-1 回填补齐成交额（market-snapshot ADDED）

- [x] 先红后绿：`fetch_symbol_history_raw_qfq` 输出 +amount 列（新浪日线原生 amount，单位归一与日批一致）；源缺 amount → 取数失败不落半口径行（单测：回填行 amount 可算 `ts_mean(amount,20)` / 缺 amount 票计失败）
- [x] 回填脚本兼容（列契约六列 + `write_snapshot_rows_bulk` 每票单事务——逐日提交在 16 进程并行下提交放大，实测吞吐降一个量级）+ 对既有回填区补 amount（齐备判据=amount 全覆盖，缺者重取 upsert）
- [x] 完成扩展回填：[2026-01-05..10-14] 全量重取（amount 齐备判据自动重取缺 amount 既有行），5617 票全覆盖尝试（5265 票有数据 / 352 失败=北交所+特殊票）；库终态 977,327 行 / 187 交易日 / quick_check ok（incident 043 恢复后）

## P0-2 准入面板深度下限（factor-library ADDED）

- [x] 先红后绿：`ADMISSION_PANEL_LOOKBACK_DAYS`（默认 150，`factors/constraints.py` 单一来源）+ 三入口（提案月批 / 基线注册 / 外部导入）面板构造切换；深度不足 fail loud（错误含补历史指引）
- [x] 面板构造口径拆分：准入面板（新常量）与衰减监控面板（`DECAY_WINDOW+30` 不动）单测钉死互不影响
- [x] 生产实跑：基线注册补跑（run 93，150 日深面板）→ 0/5 入库但拒绝原因全部为真实读数（turnover_proxy 过①②③倒在闸④样本外；详见验证报告 §7）

## P0-3 外部供给接入（factor-proposal ADDED×2）

- [x] 先红后绿：外部候选导入入口（脚本 `scripts/import_external_factors.py` + `factors/external.py` 通道——事件驱动无排程，审计走 factor_events + 脚本摘要）→ `source="external"` 过同一五道闸；不可解析计 `errors` 桶；摘要同本地口径
- [x] 拒收原因回喂：深度改进提案上下文注入本批+近 3 次运行历史 gate/reason 聚合分布 + degraded 复盘（空时如实标注）；**核对结果：深度路径（`propose_deep_improvement`）此前无生产调用方（休眠）——已接线入月批（依序广度→深度，两批各自过闸），spec「提案月批排程」MODIFIED**
- [x] RD-Agent spike（投用前置，六步走，报告落 `tests/validation/2026-10-16-rdagent-spike-validation.md`）：
      ① 环境：WSL2 Ubuntu 发行版 + Docker Desktop 集成（GUI 勾选解锁 socket）+ 独立 conda env（py3.10 / pyqlib 0.9.7；**不进**项目 3.14 环境）
      ② 数据：Qlib 侧先用社区数据集 `chenditc/investment_data`（A 股 qlib_bin，850MB）——**不先做自有导出器**
      ③ 运行：`rdagent fin_factor` 单循环真实跑 5/5 步（LLM 走 LiteLLM→bigmodel glm-5.3-flash；Volatility_20 判 SUCCESS、47,378 非空因子值）
      ④ 翻译：按「外部表达式翻译契约」TDD 实现翻译器（spike 工具 `tests/scripts/spike/qlib_translate.py`，23/23 绿；映射表 43 算子+7 字段，pyqlib 0.9.7 源码逐条核对；**规格场景例修正**：`Rank` 经源码核对为滚动秩（等价 `ts_rank`）→ 不可翻译例改 `Quantile`）
      ⑤ 导入过闸：4/10 可翻译 → **0/4 过闸**（gate4 NO_POOL_INCREMENT×2 / gate2 WEAK_ICIR×1 / gate1 LOW_COVERAGE×1=回填缺 high/low 归因）；6 条不可翻译按设计入 errors 桶（gate=None）
      ⑥ 结论：**供给线③不投用（当前形态）**——翻译覆盖 4/10（缺 `ref`）+ 可翻译者 0/4 过闸；导入通道保留、架构零改动；复跑条件=扩 DSL `ref` 后重跑本批输入

## 收口

- [x] `uv run pytest tests/factors tests/data tests/outcome -q` 全绿；ruff / mypy 干净
- [x] `openspec validate unify-quant-research-architecture --strict` 通过
- [x] 验证报告落 `tests/validation/2026-10-16-unify-quant-research-architecture-p0-validation.md`（P0 阶段报告；深面板 builtin 读数已录）+ 实施已合并（PR #349）；**sync+archive 待 spike 完成后执行**（tasks 唯一未勾项=spike）

## Deferred（本 delta 不做，另行立项）

- 财务披露日 PIT（报告期→披露日）· 回测用含退市股池 · 文本 available_at 快照库（数据地基三件）
- G2 分层回测闸 · G4 扣费回测闸（闸门扩容）
- 决策层消融实验（多 Agent vs 自一致性，用数据裁决辩论层价值）
