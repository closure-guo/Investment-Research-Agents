# Proposal: add-fdr-candidate-ledger

## Why

issue #355（因子治理缺口）：五道闸的显著性控制（闸③置换检验 + BH-FDR q=0.1）按**同批次候选数 K**做多重检验校正，但因子库是跨批次累积的（月批广度+深度、builtin 注册、外部导入各自成批）——批次流没有全局账本，长期累积的假发现率无控制。结构性问题、尚未在生产暴露（因子库当前为空）。

使能层的具体缺口：**闸③的 p 值没有落点**。`factor_events.factor_id` 为外键，被拒候选无 factors 行 → 拒收事实只留在批次日批摘要（run summary），跨批次不可查询；被采纳候选的 `p_value/p_adj` 也只存在于内存中的 `GateResult.detail`。没有可查询的族定义，方向 1（全局账本）在数据面不可实施。

本变更只补使能层（记录 + 读数），**不改闸门口径、不在准入时执行全局校正**——执行层（在库级家族上强制 BH / 批内更严 q' / 显式接受残余风险）属 issue 的「供裁决」三方向，待 owner 决策后另立 delta。

## What Changes

- 因子库库表新增 `factor_admission_ledger`（候选检验账本）：逐候选记录批次（batch_id/source）、表达式、闸③ `p_value/p_adj/q/n_hypotheses`（未达闸③或未测 → NULL）、准入结果（admitted/factor_id/rejected_gate）、时刻；
- `admit_candidates` 单点写入（所有入口——月批/收割/builtin/外部导入——自动获得账本，无需各自接线）；
- 只读读数：`ledger_summary` + 全局 BH 反事实预览（按记录的全部 p 值做一次跨批次 BH，回答「若执行全局账本，今日会拒掉多少」）——经脚本 `scripts/factor_fdr_report.py` 输出；
- 台账与数据面口径登记到 `docs/evals/metrics.md`? **否**——非评估指标（因子治理数据面），登 `docs/` 因子治理相关文档（本 delta 的 tasks 中说明）。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: `factor-library`（ADDED 跨批次候选检验账本 requirement）

## Impact

- 代码：`factors/library.py`（DDL + 记录/查询/复位）、`factors/validate.py`（`admit_candidates` 单点写入 + `ledger_source` 参数）、四入口传 source（`monthly.py`/`harvest.py`/`builtin.py`/`external.py`）、新脚本 `scripts/factor_fdr_report.py`；
- 数据：新增表（幂等 DDL；存量库首连自动建表）；
- 交互类判定：纯后端数据面，前端零变更 → 非交互类，免 E2E 门禁；真实链路由因子日批/脚本实跑验证。
