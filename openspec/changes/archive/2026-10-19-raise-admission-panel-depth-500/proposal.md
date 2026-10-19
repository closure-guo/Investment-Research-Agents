# Proposal: raise-admission-panel-depth-500

## Why

issue #407：当前准入面板下限 250 交易日，闸② ICIR 估计标准误约 `1/√250 ≈ 0.063`，
0.3 阈值 ≈4.8 SE；同族因子读数跨度仍可能落在噪声带内。500 日面板下 SE ≈ `1/√500 ≈ 0.045`，
0.3 阈值 ≈6.7 SE，近门槛族排序更可信。

数据前提已就绪：2026-10-20 深历史回填补齐后，快照库 **512 个交易日**（2024-09-02 → 2026-10-19）、
5,617 个代码，其中 **5,049 个 ≥500 日**；回填区覆盖 **5,414 / 5,617 = 96.39%**（较 10-17 的
92.6% 提升，北交所 920xxx 已补齐；剩余缺失为无源新股/停牌窗口）。

闸①联动实测（250 → 500 日面板）：

| 因子形态 | 250 日 missing_rate | 500 日 missing_rate | 结论 |
|---|---|---|---|
| 5 日窗 | 0.0926 | 0.0763 | 通过 |
| 20 日窗 | 0.1540 | 0.1109 | 通过（更宽裕） |
| 60 日窗 | 0.2975（闸①拒） | 0.1838 | **转为通过**，进入闸②裁决 |

闸② 20 日窗波动因子 \|ICIR\| 0.378 → 0.457，n 229 → 479；5 日 overnight 族 0.217 → 0.150
（更长的面板改变了 IC 分布，属预期）。近门槛重放（39 条 gate②拒收、\|ICIR\| 0.2–0.35）：
500 日下 9 条越过 0.3（去重 7 个表达式），但用生产因子库副本跑完整五道闸 **0 入库**——
5 条闸④冗余（volratio5 族 vs 既有 volratio20，max_ret 族 vs range1）、1 条结构重复、
1 条闸⑤ OOS 失败、1 条闸④增量负。**深度提升改变的是拒收理由（闸② → 闸④/⑤），不产生新因子**。

## What Changes

- **MODIFIED「准入面板深度下限」（factor-library）**：250 → 500 交易日（独立准入面板常量；
  衰减监控 `DECAY_WINDOW + 30` 口径不动）；fail loud 语义不变（可用历史不足 500 → 任务 failed +
  补历史指引）。
- 实现面（非规范变更）：`constraints.ADMISSION_PANEL_LOOKBACK_DAYS = 500` + 口径注记更新；
  `monthly.py` 文档串同步；相关测试边界随动（260→520 种子、249→499 浅面板边界）。
- 闸① `MAX_MISSING_RATE=0.2` **保持不变**：500 日下 20 日窗 11.1%、60 日窗 18.4%，均在阈值内；
  本次只重算依据，不改阈值（一次一个变量）。
- 闸② 0.3 阈值不动；衰减监控面板口径不动；已入库因子不回溯重评（既有准入读数在其时面板下成立）。

## Capabilities

- **Modified Capabilities**: `factor-library`（准入面板深度下限）

## Impact

- 代码：`src/finance_agent/factors/constraints.py`、`src/finance_agent/factors/monthly.py`（文档串）。
- 三准入入口（提案月批 / builtin 注册 / 外部导入·收割）随常量自动生效；样本量变长，历史读数
  不可跨深度直接比较（属预期）。
- 测试：`tests/factors/test_monthly.py` 常量/边界随动；`test_external_import.py` 浅面板 fail loud
  语义不变（90 日回归）。
- 复跑证据：`tests/validation/2026-10-20-raise-admission-panel-depth-500-validation.md`。
