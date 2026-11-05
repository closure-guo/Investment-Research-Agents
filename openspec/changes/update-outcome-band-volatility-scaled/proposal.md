# Proposal: update-outcome-band-volatility-scaled

> **状态：提案，待 owner 拍板。未拍板前不改 §1 口径定义、不改结算代码、不同步主规范库。**
> 出处：2026-11-06 零买入修复计划 Task 9；台账提案小节 `docs/evals/metrics.md` §1.24。

## Why

±2% 固定中性带对 20 日窗口 1σ=8–19% 的标的分辨率不足（带宽不到单日波动一半，
日内噪声即可把真实方向性判断打成 neutral）；二元 win/loss 把连续结果扔掉——检测
3% 的真实超额需要约 275 条样本，连续值口径只要约 88 条。同时 ±2% 带在决策链里被
当成风险预算使用（12/201 看多中 6/12 提及带宽、107 条 watch 中 63 条提及 σ），
是零买入锁死机制的组成环节之一。

## What Changes

1. **主指标改连续超额**：平均超额、超额胜率、超额 IR 成为主读数；resolution
   状态机照旧，resolved_* 继续产出（兼容）。
2. **±2% 降为辅助分类**：继续写库、可查，但降为分层切片，不再用于对外结论句。
3. **带宽改波动率自适应**：辅助分类带 = `max(1.5%, 0.5 × 20日波动率)`；低波动
   保底 1.5%，高波动随行。

切点：存量 resolved 分类不动、不回填；新指标从切点起算；主账本双轨展示 2 周后
由 owner 评估切换。

## Capabilities

- **Modified Capabilities**: `track-record`（判定规则（Outcome Resolution）：
  中性带计算式从全局常量 ±2% 改为波动率自适应；新增连续超额统计族）

## Impact

- `src/finance_agent/outcome/track_record/`：judgment（带宽计算 + 连续超额统计）、
  model（读数落库）、report/API（双轨展示）。
- 主账本页与 track-record API 需要同时输出旧带与新口径两套读数（2 周评估期）。
- 与零买入修复的解耦：本提案只动结算读数口径，**不动决策提示词**（后者由消融
  实验 Task 4/5/6 单变量处置）。
