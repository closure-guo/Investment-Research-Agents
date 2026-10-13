# 人工验证报告: add-screening-ranker

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-screening-ranker/`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 1–3（L2 横截面打分纯函数 → builtin 手工因子基线包 → 对照验收）
**全量回归时 HEAD**: `05a26261`（T1/T2 两个 feat 提交之上的工作区；此后仅新增验收脚本/artifact/本报告与 `tasks.md` 回填，**零 src 改动**）

> **硬约束声明（只记录实际发生）**：spec 要求「对 2026-09 每月首交易日产出 Top-100 名单，人工抽
> 10 只核对手算综合分一致」，但本机东财行情域 IP 封禁持续生效（incident 033 / issue #325），
> 真实 2026-09 快照数据不可得。§1 为**合成面板（固定种子）+ 独立手算**的替代证据：手算不使用
> 被测打分实现（口径见 §1.3），证明「打分/排序/勾稽机制正确」，**不构成真实市场有效性的
> 证据**；live 全月核对挂 follow-up（issue #325）。

## 0. 命令证据（当场跑、逐字记录）

### 0.1 指定测试命令与全量回归

```
$ uv run pytest tests/screening tests/factors -q
337 passed in 11.18s

$ uv run pytest tests/screening -q
76 passed in 1.54s

$ uv run pytest tests/factors -q
261 passed in 10.22s

$ uv run pytest tests/ -q -m "not live"
1 failed, 5118 passed, 6 skipped, 12 deselected, 30 warnings in 869.20s (0:14:29)
（唯一失败：tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default
 ——既有环境性 flake，归因见 §2）

$ uv run pytest "tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default" -q
1 passed in 0.05s
```

失败归因（如实记录，不掩饰）：① 单独运行该用例通过（上）；② 同一失败在上一轮全量回归
（`2026-10-13-add-factor-library-and-monitor-validation.md`，5076 passed 那轮）已出现并归因为
宿主 `.env` 泄漏 `LLM_MAX_CONTEXT`（既有 llm env flake）；③ 本 change 的 src diff
（`git diff --name-only 4f7f7fe8..HEAD`）仅含 `src/finance_agent/screening/ranker.py` 与
`src/finance_agent/factors/builtin.py`，**不触 `llm/` 任何文件**；④ 交叉验证：
5118 − 5076 = 42 = 12（既有 `36105b9c` 评审修正新增用例）+ 30（本 change 新增用例）。

新增用例数：`tests/screening/test_ranker.py` 21 例（rank_universe 打分/勾稽/确定性/边界 +
factor_weights/recent_ic_stats）、`tests/factors/test_builtin.py` 9 例（目录逐字/complexity
逐条/注册路径/闸②同阈值/幂等）——既有测试**零修改**（`git status` 可证：T1/T2 为纯新增文件）。

### 0.2 Lint 与类型检查

```
$ uv run ruff check src/finance_agent/screening src/finance_agent/factors tests/screening \
    tests/factors tests/validation/2026-10-13-add-screening-ranker-acceptance.py
All checks passed!

$ uv run mypy src/finance_agent/screening src/finance_agent/factors
Success: no issues found in 13 source files
```

### 0.3 验收脚本执行（逐字尾部输出）

```
$ uv run python tests/validation/2026-10-13-add-screening-ranker-acceptance.py
[builtin 准入结论] 名称 / 状态 / 被拒闸口 / 原因码
  momentum_20     rejected gate=2 reason=WEAK_ICIR factor_id=None
  reversal_5      active   gate=None reason=None factor_id=1
  turnover_proxy  active   gate=None reason=None factor_id=2
  volatility_20   active   gate=None reason=None factor_id=3
  size_proxy      active   gate=None reason=None factor_id=4

[监控日批] calls=100 degraded=[]

[rank 日 2026-05-15] 候选 100 条（rank1=+2.1289 / rank100=-0.4402）
  手算 ic_stats 与 recent_ic_stats 交叉核对差: 0.000e+00
    id=1   div(ts_mean(close,5),close)     ic_mean=+0.164519 icir=+1.8676 weight=+0.378160
    id=2   div(volume,ts_mean(volume,20))  ic_mean=+0.121352 icir=+1.4182 weight=+0.278938
    id=3   ts_std(delta(close,1),20)       ic_mean=+0.038057 icir=+0.4216 weight=+0.087478
    id=4   ts_mean(amount,20)              ic_mean=+0.111123 icir=+1.2839 weight=+0.255425
  （10 只抽样手算逐项对照全部 OK，最大逐项差 6.217e-15）
  全量勾稽（Σcontribution == composite）最大差: 0.000e+00
[rank 日 2026-06-04] ……（10 只全部 OK，最大逐项差 4.163e-17；全量勾稽最大差 0.000e+00）
[rank 日 2026-06-23] ……（10 只全部 OK，最大逐项差 2.220e-16；全量勾稽最大差 0.000e+00）

[verdict] {'sampled_total': 30, 'sampled_matched': 30, 'sampled_days': 3,
           'reconciliation_max_abs_diff': 0.0, 'active_factor_ids': [1, 2, 3, 4], 'passed': True}
[artifact] tests/validation/2026-10-13-add-screening-ranker-acceptance.json
（退出码 0；耗时约 7.6s）

$ diff <(第 1 次 stdout) <(第 2 次 stdout) && diff <(第 1 次 artifact) <(第 2 次 artifact)
（两次运行 stdout 与 artifact 逐位相同 → 固定种子逐位可复现；artifact 不落临时 db 路径）
```

## 1. 对照验收（Task 3）

### 1.1 合成面板构造（固定种子、零网络、零 LLM）

`tests/validation/2026-10-13-add-screening-ranker-acceptance.py`：170 交易日 × 120 标的，
逐日迭代生成价格/成交量/成交额，次日收益**显式依赖五 builtin 因子当日横截面 zscore**
（自满足构造——因子对下一期收益有真实预测成分）：

```
r_{t+1} = Σ_k β_k · z_k,t + σ · θ_i · ε_t
β = (动量 0.006, 反转 0.009, 量比代理 0.006, 波动 0.006, 成交额代理 0.006)，σ = 0.03
θ_i：持久异质波动（exp(0.3·N(0,1)) 逐标的固定）——供 volatility_20 有横截面结构
```

载荷与噪声的选取（实测调参，非拍脑袋）：载荷沿用 add-factor-library 验收的同量级经典载荷；
σ 从 0.02 上调至 0.03 并加入持久异质波动，是在本面板上实测「σ=0.02 无 / 有异质波动」两种配置
时 `volatility_20` 分别在闸④出现 `NO_POOL_INCREMENT` 后的修正（σ=0.02 + 异质波动时波动率因子
被闸④拒收）：σ=0.03 配置下五因子先验全部为正 IC 通路且两两 |IC 序列相关| ≤ 0.38。

### 1.2 builtin 准入（同五道闸）与 IC 累计

准入结论（逐条闸口 detail 见 artifact `admission` 字段）：`reversal_5` / `turnover_proxy` /
`volatility_20` / `size_proxy` 过五道闸置 active（闸③ p_adj=0.001、闸④ 验证段增量
+1.403 / +0.665 / +0.434 / +0.784、闸⑤ 验证段 |ICIR| 1.403 / 1.446 / 0.346 / 1.435 ≥ 0.3）；
`momentum_20` 被闸②拒收（ICIR = 0.227 < 0.3）——**免提案不等于免闸**的直接证据：被拒者不落库
（`factor_id=None`）。四个 active 因子随后经 100 次 `run_factor_monitor`（面板逐日截断至
≤ as_of，只记录 ≤ as_of−1 的 IC，无前视）累计 100 个 IC 交易日，`degraded=[]`。

### 1.3 独立手算口径（与打分实现解耦）

打分链路的每一步都在脚本内**重写**（仅把结果作为接口输入传入被测函数）：

- IC 统计：`list_ic_series(fid, limit=60)` → 手写 `fsum/count` 均值与 ddof=1 ICIR；
- 截面 z-score：手写教科书公式（ddof=1 样本方差），非有限值记 0、样本 <2 或 std=0 全 0；
- 权重：手写 `ic_mean_i / Σ|ic_mean_j|`；综合分：手写同序加权和。

被测打分实现只被调用一次（`rank_universe`），其产物 `factor_contribs`/`composite_score` 与
手算逐项对照。脚本另有弱一致性检查：`recent_ic_stats`（交付物之一）输出与手算 ic_stats
逐位相同（最大差 0.000e+00）——该检查口径同源、强度弱，仅作交叉印证；强证据是综合分对照。

### 1.4 三日 Top-100 抽样手算核对：30/30 一致

3 个 rank 日（面板末端倒数 40 / 20 / 1，间隔约 20 个交易日，对应 spec「按月抽查」节奏；
合成日历为连续日期轴，无真实交易日历的「每月首日」语义——如实记录）各产出 Top-100，
按 rank 升序等比间隔抽 10 只（rank #1 / #12 / #23 / #34 / #45 / #56 / #67 / #78 / #89 / #100）：

| rank 日 | 抽样数 | 一致数 | 最大逐项差（z / w / contribution / composite） | 容差 |
|---|---|---|---|---|
| 2026-05-15 | 10 | 10 | 6.217e-15 | 1e-9 |
| 2026-06-04 | 10 | 10 | 4.163e-17 | 1e-9 |
| 2026-06-23 | 10 | 10 | 2.220e-16 | 1e-9 |

差异来源：pandas 向量化均值/标准差与手写 `fsum` 教科书公式的浮点末位差（量级 1e-15 以内，
远小于 1e-9 容差）；权重与 IC 均值逐位相同。**30/30 全部一致**（超计划要求 10 只/总样本，
按父任务要求 3 日 × 10 只执行）。

### 1.5 勾稽 `Σ contribution == composite_score`（spec 明文，容差 1e-9）

除 30 只抽样外，对 3 日**全部 300 条**返回候选逐条核对：最大绝对差 **0.000e+00**（逐位相等）
——实现按 `factors` 传入顺序累加 composite、`factor_contribs` 保持同序，勾稽在浮点上逐位成立。
单测另覆盖全 NaN 因子 / std=0 截面 / 负权重 / 缺失单元 / 因子缺 IC 信息等路径的同式勾稽。

## 2. 代理口径与边界（诚实披露）

- **换手率代理**：DSL 只有价量六字段（无股本/流通股数据），`turnover_proxy` 以**量比**
  `div(volume, ts_mean(volume, 20))` 代理换手率；**市值代理**：`size_proxy` 以 20 日平均
  成交额 `ts_mean(amount, 20)` 代理市值（成交额与市值正相关，但受换手率影响，非市值本身）。
  两者已在 `factors/builtin.py` 模块 docstring 与各因子 hypothesis 中写明，报告/评估不得按
  真实换手率、真实市值解释。其余三个（`delta(close,20)` / `div(ts_mean(close,5),close)` /
  `ts_std(delta(close,1),20)`）为经典形态直译。
- **本验收不证明什么**：合成面板的预测结构是构造出来的（自满足），本验收只证明打分、权重、
  排序、贡献分解与勾稽的**机制正确性**；因子在真实市场的有效性须待快照数据可用后另行评估。
- **builtin 闸②同阈值（spec 场景）**：`tests/factors/test_builtin.py` 以纯噪声面板（固定种子）
  实测反转因子 ICIR ≈ 0.119 ≈ spec 场景 0.12 → 被拒于闸②（`WEAK_ICIR`），同一表达式的
  builtin 与 proposal 两条候选统计量/阈值逐位相同（`min_abs_icir=0.3`）。
- **既有测试零修改**：本 change 全部为新增文件（T1/T2 提交 diff 仅含新增），符合红线。
- **全量回归唯一失败的归因**：见 §0.1；`tests/llm/test_resolver.py` 的既有环境性 flake
  （隔离复跑 1 passed；本 change 不触 `llm/` 任何文件；passed 增量 42 = 12 既有 + 30 本 change）
  ——与上一轮 `add-factor-library` 全量回归同一失败点。

## 3. 复现方法

```bash
uv run pytest tests/screening tests/factors -q
uv run python tests/validation/2026-10-13-add-screening-ranker-acceptance.py   # 退出码 0 即 30/30
uv run pytest tests/ -q -m "not live"      # 全量（约 13 分钟）
uv run ruff check src/finance_agent/screening src/finance_agent/factors tests/screening tests/factors
uv run mypy src/finance_agent/screening src/finance_agent/factors
```

artifact：`tests/validation/2026-10-13-add-screening-ranker-acceptance.json`（逐条准入闸口
detail、IC 统计/权重、30 只抽样逐因子对照、勾稽差值；同种子逐位可复现）。
