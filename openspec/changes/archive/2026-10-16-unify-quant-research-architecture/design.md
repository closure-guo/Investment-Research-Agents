# Design: unify-quant-research-architecture

## Context

目标架构（对标分析 + 架构图，2026-10-15）把量化研发层定为**三供给、一裁判**：

```
数据地基 ──→ 供给线①本地月批（LLM 提案，库分桶上下文）──┐
   │        供给线②内置基线包（手工 5 条，注册日批）────┼─→ 五道闸 ──→ 因子库 ──→ 选股 L2
   └──────→ 供给线③外部研究员（RD-Agent/Qlib，待 spike）┘    （裁决权唯一）      （衰减监控→重提回流）
```

裁决与执行侧（闸门、因子库、选股、决策、paper 执行、结算回流）不因外部供给变化而变化——供给线可插拔是架构原则，本 delta 只补齐供给线③的进闸契约与两条数据前置。

## Goals / Non-Goals

- Goals：准入面板深度口径独立化（≥150 交易日）；回填历史补成交额；外部候选进闸契约（source=external、同一五道闸、真源唯一）。
- Non-Goals：不实现 Qlib/RD-Agent 本体（spike 验证另行执行）；不做翻译器进生产（翻译属 spike 工具，生产通道接受 **DSL 表达式**候选）；Deferred 项（PIT/退市股池/文本快照库/G2/G4 闸/决策层消融）各自另立 delta。

## Decisions

### D1 准入面板深度独立常量，下限 150 交易日（fail loud）

- 算术依据：闸①缺失率含热身期 NaN（`check_computability` 口径，含算子不足窗口）。20 日窗因子：19/90≈21.1% + 北交所历史缺席≈6% ≈ 27% > 20% **结构性恒拒**；19/150≈12.7% + 6% ≈ 18.7% < 20% 可过。验收先例用 170 日合成面板（`tests/validation/2026-10-13-add-screening-ranker-acceptance.py` PANEL_DATES=170）。
- 衰减监控面板（`DECAY_WINDOW+30`）**口径不动**（其消费的是 IC 序列不是闸①缺失率）；准入（本地提案 / builtin 注册 / 外部导入）共用新的 `ADMISSION_PANEL_LOOKBACK_DAYS`（默认 150）。
- 深度不足 → fail loud（同月批「快照库为空」先例）：浅面板上的准入结论结构性有偏，宁拒不评。
- 备选：改闸①缺失率口径（剔除热身期）——否决：动的是已验证的统计判定语义，且会放松对所有长窗因子的约束；补深度是数据问题数据解。

### D2 回填补齐成交额（amount）

- 实测根因：`fetch_symbol_history_raw_qfq` 输出 `[日期, 开盘, 收盘_raw, 收盘_qfq, 成交量]` 五列，回填行 amount=NULL → `size_proxy` 100% 缺失；新浪 `stock_zh_a_daily` 原生返回 amount 列（9 列之一），可归一接入。
- 口径：与日批一致（元）；源缺 amount → 该票按取数失败处理（不产出半口径行，同「不产出半口径数据」先例）。
- 既有回填区（约 5270 票 × 187 日）需补跑 amount（任务项），在此之前 amount 类因子在旧区间仍不可算——闸口按真实覆盖裁决，不特殊豁免。

### D3 外部供给接入契约：DSL 表达式候选 + 同一五道闸 + 真源唯一

- 通道形态：导入入口（ops 任务或脚本）接受**合法 DSL 表达式候选**（`source="external"`，与 `proposal`/`builtin` 并列）；Qlib 表达式→DSL 的翻译属 spike 工具链，不进生产代码（翻译产出即 DSL 候选）。
- 同一闸门：外部候选走 `admit_candidates` 完整五道闸，逐条事件史留痕；不可解析/超 DSL 能力边界 → 拒收留痕（`gate=None` 预检失败语义，同月批 `errors` 桶）。
- 真源唯一：外部因子库（RD-Agent SOTA 库等）MUST NOT 成为生产因子来源；外部回测结论（IC/收益）MUST NOT 替代闸口读数——依据：其择优标准（IC≥0.99 冗余 + SOTA 比较）无多重检验校正，试数百个必有运气赢家（对标分析 §4）。
- 备选：把 RD-Agent 直接写进生产链路——否决：spike 未验证（产出质量/翻译损耗/运维成本未知），且违反供给线可插拔原则。

### D4 上线顺序（数据先行）

`ADMISSION_PANEL_LOOKBACK_DAYS=150` 生效即要求快照库 ≥150 交易日历史——**先完成扩展回填与 amount 补齐，再切深度常量**，否则三条供给线同时 fail loud（冷启动顺序错误会把系统打回无因子状态）。任务顺序即上线顺序。

## Risks / Trade-offs

- 深度 fail loud 使准入强依赖回填完备性：回填中断（如 WAL 互斥 #344）期间供给线停摆——可观测（job failed），运维可判读。
- 外部供给质量未知：spike 是投用前置（任务项）；不达标则供给线③不投用，契约本身无成本。
- 150 是「闸①热身期算术 + 验收先例」的工程下限，非统计最优；后续若改闸①口径需与该常量联动重算（记入 constraints 文档）。

## 关联工件

- 对标备忘录：`docs/evals/2026-10-15-量化投研设计文档对标与差距分析.md`
- 目标架构图（交互 HTML）：`C:\Users\Administrator\AppData\Local\Temp\archify-target\target-architecture.html`（规格 JSON 随档 `candidate.json`）
- 实测证据：`tests/validation/2026-10-15-wire-builtin-factor-registration-validation.md`（0/5 读数 + 深面板 ② 证据）
