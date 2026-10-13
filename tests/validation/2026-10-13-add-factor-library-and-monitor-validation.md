# 人工验证报告: add-factor-library-and-monitor

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-factor-library-and-monitor/`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 1–7（求值引擎 → 库 schema/状态机 → 闸①② → 闸③置换/BH-FDR → 闸④⑤ → 衰减监控 + 日批接线 → 对照验收）
**全量回归时 HEAD**: `4514c173`（T1–T6 六个 feat 提交之上的工作区；此后仅新增本报告、验收脚本/artifact 与 `tasks.md` 回填，**零 src 改动**）
**评审修正后 HEAD**: `36105b9c`（fix 提交；修正 1–9 落地后重跑全部命令，§0 与 §1 数字已按修正版刷新；
其后的 docs 提交（本报告/计划/spec/tasks/artifact）零 src 改动）

> **边界声明（只记录实际发生）**：本 change 为**纯计算/存储层**，零网络、零 LLM 调用；
> 全部证据来自合成面板（固定种子）。§1 的对照验收是**合成世界**的管线验收（因子在生成过程中
> 真实预测），**不构成真实市场有效性的证据**——真实数据验收需 market-snapshot 实盘库接入后另行发起。

## 0. 命令证据（当场跑、逐字记录）

### 0.1 指定测试命令

```
$ uv run pytest tests/factors -q
252 passed in 8.18s

$ uv run pytest tests/factors tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py -q
303 passed in 9.78s

$ uv run pytest tests/ -q -m "not live"
1 failed, 5076 passed, 6 skipped, 12 deselected, 30 warnings in 782.16s (0:13:02)
（唯一失败：tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default
 ——既有环境性 flake，归因见 §3.6；评审修正仅触 factors 层，未重跑全量）

$ uv run pytest "tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default" -q
1 passed in 0.05s

$ uv run pytest tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py -q
51 passed in 2.38s

$ uv run python tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py
[verdict] {'classic_active': 5, 'classic_total': 5, 'noise_rejected': 5, 'noise_total': 5, 'passed': True}
（退出码 0；耗时约 3.3s；全量输出见 §1；评审修正后重跑，artifact 已刷新）

$ diff <(第 1 次 stdout) <(第 2 次 stdout) && diff <(第 1 次 artifact) <(第 2 次 artifact)
（两次运行 stdout 与 artifact 逐位相同 → 固定种子逐位可复现；artifact 不落临时 db 路径）
```

分文件计数（评审修正后）：`test_dsl` 86 / `test_eval_engine` 33 / `test_library` 25 / `test_propose` 46 /
`test_validate` 49（T3 编排 + T4 闸③ + T5 闸④⑤ + 评审修正 12 例）/ `test_monitor` 13。

### 0.2 Lint 与类型检查

```
$ uv run ruff check src/finance_agent/factors src/finance_agent/outcome tests/factors tests/outcome \
    tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py
All checks passed!

$ uv run mypy src/finance_agent/factors
Success: no issues found in 8 source files

$ uv run mypy src/finance_agent/outcome/scheduler.py src/finance_agent/outcome/ops/jobs.py \
    src/finance_agent/factors/monitor.py
Found 5 errors in 2 files (checked 3 source files)   # 全部位于 evals/extract.py、evals/judges.py
```

mypy 5 项报错**全部在 `evals/`**（`_summarize_debate` 重定义/多参、`judges.py` 赋值类型），
与本次改动无关：`git stash` 撤掉工作区改动后对同一目标跑 mypy 仍报同样 5 项（既有债务，
pyproject 亦注明「evals 另有 9 项存量债务」）。本 change 触碰的文件 mypy 全绿。

## 1. 对照验收（Task 7）

### 1.1 合成面板构造（固定种子、零网络）

`tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py`：
150 交易日 × 80 票，逐日迭代生成价格/成交过程，次日收益**显式依赖**五经典因子当日的
横截面 zscore（自满足构造）：

```
r_{t+1} = Σ_k β_k · z_k,t + σ · ε        β = (动量 0.006, 反转 0.009, 波动 0.006,
                                           流动性 0.006, 量价协同 0.006)，σ = 0.02
```

- 动量 `delta(close,20)`（1 月涨幅，Jegadeesh-Titman 口径）；
- 反转 `div(ts_mean(close,5),close)`（≈ 1 − (4r_t+3r_{t−1}+2r_{t−2}+r_{t−3})/5，周度反转）；
- 波动 `div(1,ts_std(delta(close,1),10))`（10 日已实现波动倒数，低波动异象）；
- 流动性 `div(1,ts_mean(amount,5))`（成交额水平倒数，非流动性溢价）；
- 量价协同 `ts_corr(close,volume,10)`（10 日价量滚动相关）。

噪声因子 = 固定种子随机表达式，**字段限 volume**、只用变异性/形态算子（`ts_std`/`ts_rank`/
`delta`/`abs`/`sub(volume,ts_mean(volume,N))`/两个 `ts_std` 之比）：volume 只通过「与价格的
滚动相关」进入收益过程，其水平与变异性与收益无关（实测 |ICIR| ≤ 0.16）→ 真噪声对照。

### 1.2 经典因子诊断（「真预测成分」的直接证据）

```
[经典因子诊断] 表达式 / IC 均值 / ICIR / 有效日
  动量     delta(close,20)                    ic_mean= 0.0638 icir= 0.5423 days=129
  反转     div(ts_mean(close,5),close)        ic_mean= 0.0854 icir= 0.6746 days=145
  波动     div(1,ts_std(delta(close,1),10))   ic_mean= 0.1229 icir= 1.0965 days=139
  流动性    div(1,ts_mean(amount,5))           ic_mean= 0.1434 icir= 1.2572 days=145
  量价协同   ts_corr(close,volume,10)           ic_mean= 0.1413 icir= 1.0112 days=140

[经典因子 IC 序列相关] {"动量~反转": -0.5677, "动量~波动": -0.036, "动量~流动性": 0.2001,
  "动量~量价协同": -0.0611, "反转~波动": -0.0667, "反转~流动性": -0.2109, "反转~量价协同": -0.0483,
  "波动~流动性": 0.1295, "波动~量价协同": 0.0757, "流动性~量价协同": 0.1103}
```

五因子 IC 同号为正（0.064–0.143，ICIR 0.54–1.26）；两两 IC 序列相关最大 0.568（动量~反转）
< 0.7 冗余线，其余 |ρ| ≤ 0.21 → 闸④冗余检查有真实发挥空间而非恰好擦线。

### 1.3 准入结论对照表（逐条记录被拒闸口与原因码）

```
[准入结论] 候选 / 状态 / 被拒闸口 / 原因码 / ICIR
  经典:动量      delta(close,20)                    active   gate=None reason=None icir=0.5423
  经典:反转      div(ts_mean(close,5),close)        active   gate=None reason=None icir=0.6746
  经典:波动      div(1,ts_std(delta(close,1),10))   active   gate=None reason=None icir=1.0965
  经典:流动性     div(1,ts_mean(amount,5))           active   gate=None reason=None icir=1.2572
  经典:量价协同    ts_corr(close,volume,10)           active   gate=None reason=None icir=1.0112
  噪声:1       delta(volume,5)                    rejected gate=2 reason=WEAK_ICIR icir=-0.0064
  噪声:2       sub(volume,ts_mean(volume,10))     rejected gate=2 reason=WEAK_ICIR icir=0.0028
  噪声:3       abs(delta(volume,3))               rejected gate=2 reason=WEAK_ICIR icir=-0.1427
  噪声:4       ts_std(volume,8)                   rejected gate=2 reason=WEAK_ICIR icir=-0.161
  噪声:5       ts_rank(volume,7)                  rejected gate=2 reason=WEAK_ICIR icir=-0.0356

[verdict] {'classic_active': 5, 'classic_total': 5, 'noise_rejected': 5, 'noise_total': 5, 'passed': True}
```

**验收判定：5 经典全部 `active`、5 噪声全部被拒（全部止步闸② `WEAK_ICIR`）——无不一致项。**
（spec 场景「噪声因子拦截」的另一形态由 T4 单测覆盖：50 条随机表达式注入纯噪声面板 →
实测 **0 条入库**，断言 `< 5` 通过。）

### 1.4 闸④/⑤ 明细（artifact 摘录）

入库后 `factors.pool_increment` 已由编排写入（= 闸④**验证段**增量，E8 保种排序的真实输入；
评审修正后为验证段口径）：

```
id=1 delta(close,20)                  pool_increment=0.5498
id=2 div(ts_mean(close,5),close)      pool_increment=0.9181
id=3 div(1,ts_std(delta(close,1),10)) pool_increment=0.8621
id=4 div(1,ts_mean(amount,5))         pool_increment=0.9885
id=5 ts_corr(close,volume,10)         pool_increment=1.0869
```

逐条闸④（冗余 → 验证段增量）与闸⑤（训验切分 + 边界日剔除）：

| 候选 | 闸④ 最大 \|ρ\|（对象） | IR(池) → IR(池+候选)（验证段） | 增量 | 训练段增量（审计） | 闸⑤ ICIR_val |
|---|---|---|---|---|---|
| 反转 | 0.568（动量） | 0.550 → 1.468 | **+0.918** | +0.969 | 0.640 |
| 波动 | 0.067（反转） | 1.468 → 2.330 | **+0.862** | +0.344 | 1.107 |
| 流动性 | 0.211（反转） | 2.330 → 3.318 | **+0.988** | +0.214 | 1.878 |
| 量价协同 | 0.110（流动性） | 3.318 → 4.405 | **+1.087** | +0.075 | 1.434 |

（|ρ| 仍为全样本 IC 序列相关，冗余口径不变；增量/IR 全部为验证段 44 个共同有效日；
第 1 条候选入空池，增量 = 验证段 IR(候选) = 0.5498。闸⑤ `purged_train_days = 1`
——训练段末端 1 日已剔除，见 §5 修正 4。）

闸③（置换 + BH-FDR）逐条 `p_value = p_adj = 0.001 = 1/(N+1)`（N=999；5 个候选全为
零超额 → step-up 后缀最小被末位项 `K·p/K = p` 压住），`n_hypotheses = 5`。

## 2. 单测覆盖（T1–T6 逐闸）

### 2.1 闸③ 置换检验 + BH-FDR（T4，`052b84f4`）

`tests/factors/test_validate.py` 新增 11 例：同种子两次运行 p 值**逐位相等**（`==`，非 approx）
且换种子确实改变抽样；强信号 p = 1/(1+N) 通过；纯噪声 p > 0.1 → `NOT_SIGNIFICANT`；全 NaN
序列 fail-closed（p = 1.0）；BH 手算小样本对照（K=4 后缀最小/单调不减，含「被后项压住」的
step-up 特征）；50 个 p 值批量单调不减、≥ 原值、≤ 1；50 随机表达式（互不同构，`is_subtree_isomorphic`
去重）注入纯噪声面板（120 日 × 60 票）→ 入库 0 < 5。

评审修正再增 6 例（§5 修正 1/8/9）：K=50 单强候选零超额 → `p_adj = 50/1000 = 0.05 ≤ q` 通过
（旧 N=200 时下界 1/201 → 必拒，回归钉）；K=50 全噪声全部拒收；K=25 闸②存活者批量
（24 条尖峰序列超额数 = N 全被拒 + 1 条强信号通过，K>20 端到端）；分辨率守卫 N=50：K=5 不触发 /
K=6 抛 `ValueError`（消息含不等式两侧 `1/(PERMUTATION_N+1)` 与 `BH_FDR_Q/K` + N/K/q 数值）；
默认 N=999：K=100 不触发 / K=101 触发；同批逐字重复表达式按位置各计假设（`n_hypotheses = 2`）。

### 2.2 闸④ 池级增量 + 闸⑤ 样本外（T5，`eca7f599`）

13 例：冗余 |ρ| = 0.92 → `REDUNDANT_WITH_POOL` 且 `detail.duplicate_of` 记相关对象 id；
池内因子**无落库 IC**（本批次刚入库）→ 由面板现算 IC 完成比对（ρ=1 命中）；噪声因子稀释
组合 → `NO_POOL_INCREMENT`（`increment ≤ 0` 且 `ir_pool > ir_pool_with_candidate`）；正增量
通过且 `detail.increment` 为 `float`；空池语义（增量 = IR(候选)，`ir_pool is None`；负 IR 候选被拒）；
闸⑤两段皆强通过 / 验证段无信号 `OOS_FAILED` / 符号相反 `OOS_FAILED`（|ICIR| 达标不等于通过）/
样本过短 fail-closed；切分点不跨日（train/val 无交集、并集 = 全区间、`max(train) < min(val)`）。

评审修正再增 5 例（§5 修正 2/4/6）：闸④判据 = 验证段增量（样本内强但样本外平坦 → 拒收，
`increment_train > 0` 记审计）；验证段有效日 < 10 → `NO_POOL_INCREMENT` +
`reason="insufficient_oos_dates"`；闸④验证段边界与闸⑤同一切分（`train_end`/`val_start`/
`val_end` 对齐）；冗余最少共同日阈值 = `MIN_REDUNDANCY_SAMPLES`（4 点跳过 / 5 点比对）；
闸⑤剔除训练段末端 horizon 日（秩平移构造：不剔除时训练均值被边界日翻转成正、剔除后为负 →
由拒收变通过）；剔除后训练段为空 → fail closed。

### 2.3 衰减监控 + 日批接线（T6，`4514c173`）

`tests/factors/test_monitor.py` 13 例（合成面板用**并列秩构造**把「最新可算 IC 日」的
RankIC 钉死为 −0.02，测试完全确定）：60 日窗口不足 → skipped；不足 20 个滚动点 → skipped；
20 个滚动点全负且 |ICIR| < 0.1 → `degraded` + 事件含滚动 IC/ICIR 快照（各 20 点）+ 
`list_active_factors` 立即剔除；最后 10 日 IC 转正 → 不降级（「连续 20 日」不成立）；滚动均值
恒负但 |ICIR| ≫ 0.1（强负 IC 非衰减）→ 不降级；同日重跑幂等（IC 行数不变、状态不二次迁移）；
非 active 因子不监控；`as_of` 越界 `ValueError`；空因子库 no-op；ops 注册表四处对齐 +
`scheduler` 注册 15:45 mon-fri（含 misfire 硬化）+ job 经 `_with_retry` → `run_job(job_id,
source="scheduled", func=run_factor_decay_monitor)` 且失败退避重试；`run_factor_decay_monitor`
从真实快照库（`write_snapshot_rows` 落 5 日 × 40 票）构造面板并跑通。

## 3. 异常与披露（诚实记录）

### 3.1 合成面板证据的边界（重要）

验收与全部单测均在**合成面板**上完成（本机无 market-snapshot 实盘库、零网络纪律）：
「5 经典因子入库」证明的是**管线判定正确性**（真预测 → 过五闸；无预测 → 被拦），
**不是**这些因子在真实 A 股上有效。真实数据验收需快照库接入后另行发起（本 change 非目标）。

### 3.2 闸③置换口径的实现披露（评审已批准，见 §5 修正 3）

计划 E4 写「逐日横截面重排因子值」，但接口（`check_significance(ic_series_map, *, seed)`，
编排契约逐字）只传 IC 序列——横截面数据不在闸③输入内。实现取该零分布的**条件随机化**
等价形式：横截面重排后当日 IC 在零假设下以 0 为中心对称，逐日独立随机化其符号即该零分布的
置换实现（保留观测幅度 → 对强信号保守：观测幅度越大越难被置换达到，不会放松判定）。
统计量、双侧 p 公式、BH-FDR、种子可复现均按 E4 逐字。**控制器已批准该替代**（H0 下逐日
Spearman IC 关于 0 对称 → 符号随机化是精确条件检验；对 H1 保守）；口径已写入
`validate.py` 模块 docstring「置换口径」段。若后续要求「真实横截面重排」，需扩展编排把因子值
传给闸③（接口变更，属行为变更，须走 OpenSpec）。

### 3.3 验收面板的调参过程（披露取舍，非事后挑数据）

初版（五因子同载荷 0.02、动量 10 日 vs 反转 1 日、σ = 0.01）实测：动量~反转 IC 序列相关
**−0.76 > 0.7**（反转被闸④按冗余拒收）、动量~流动性 0.64。根因：因子驱动收益 → 收益进价格 →
价格类因子互为镜像；信号越持续/信噪比越高，镜像越强。调整（**改生成过程，不改闸口径**）：
① 动量/反转窗口错开到经典口径（20 日 vs 5 日）；② 流动性水平改为逐日独立（不累积进价格路径）；
③ 降信噪比（σ 0.01 → 0.02）并给反转更高载荷（0.009）。调参后的实测指标见 §1.2（最大 |ρ| = 0.568，
五因子 ICIR ≥ 0.54）。**报告如实记录：这是为验收构造的合成世界参数，不是「闸④阈值太松」的证据。**

### 3.4 既有测试的最小更新（计划明文允许 + change 1 先例）

- `tests/outcome/test_ops_jobs.py`、`tests/outcome/test_scheduler.py`：注册表计数断言 **6 → 7**
  （新增 `factor_decay_monitor`），并给集合断言补一项、测试名与注释同步；**断言语义未变**。
  计划 Global Constraints 明文允许（「ops 注册表扩容的计数断言按 change 1 先例最小更新并在报告披露」）。
- `tests/factors/test_validate.py`：**模块 docstring 随 T4/T5 实装更新（纯文字）**；T3 既有断言零改动。
- 其余既有测试零改动；**T3 端到端 fixture 无需调整**（见 §3.7）。

### 3.5 `eval_engine.cross_section_zscore` 公开别名（小口径收敛）

闸④池组合需「逐日横截面 zscore」（E5 逐字），该口径原本是 `eval_engine` 的私有函数；
T5 把它改为公开 `cross_section_zscore`（DSL `zscore` 算子同一实现），避免在 `validate.py`
复制一份 zscore 口径。纯重命名 + 注释，行为零变化（eval_engine 33 例全绿）。

### 3.6 全量回归唯一失败：既有 llm env flake（非本 change 引入）

失败用例 `TestContextLengthOverride::test_empty_follows_registry_default`，归因链：
① 隔离复跑通过（0.05s）；② 宿主仓库 `.env` 第 40 行 `LLM_MAX_CONTEXT=256000`，
`finance_agent.api` 在 import 期 `load_dotenv()` 注入 `os.environ`，而该用例直读真实环境 →
套件内看到 256000 覆盖注册表默认 128000；③ `git stash` 撤掉本工作区全部改动后
`uv run pytest tests/llm -q` **同样失败**（1 failed, 358 passed）→ 与本次改动无关；
④ 同一 flake 已在上一 change 的报告（`2026-10-13-add-factor-proposal-agent-validation.md` §0.1）
记录并同样归因。控制台另有 Langfuse 离线导出噪音（`Failed to export span batch ...`），非用例失败。

### 3.7 T3 fixture 未被调整

计划要求「若真闸拒收 T3 端到端 fixture，调 fixture 强度而非契约」。实测：T3 的
`volume` 强因子在真实闸③④⑤ 下仍全闸通过（`test_admit_candidates_admits_strong_factor_end_to_end`
与 `mixed_batch` 两例零改动全绿）→ **无需调整 fixture**。

## 4. 结论

- T1–T7 全部实装并提交：`f4c621e0`（T1）→ `a61d98b7`（T2）→ `8ebc4858`（T3）→ `052b84f4`（T4）
  → `eca7f599`（T5）→ `4514c173`（T6）→ 本报告/脚本/tasks 回填（T7，docs 提交）。
- 对照验收通过：5 经典因子全部入库 `active`、5 噪声因子全部被拒（`WEAK_ICIR`），逐条闸口与
  原因码落 artifact JSON（`2026-10-13-add-factor-library-and-monitor-acceptance.json`）。
- 全量回归：`1 failed, 5076 passed, 6 skipped, 12 deselected`——唯一失败为既有 llm env flake（§3.6）。
- 红线核对：`openspec/specs/` 未手改（只写 change 内 `tasks.md`）；闸口判定均先写失败测试
  （T4/T5/T6 逐任务红→绿，逐次 commit）；本 change 无交互类变更 → 无 E2E 门禁要求。

### open items（未闭环，如实列出）

1. **真实数据验收未做**：需 market-snapshot 实盘库接入后跑同一验收脚本（脚本只需替换面板来源）。
2. ~~**闸③置换口径的最终裁决**~~：**已闭环**——控制器批准逐日符号随机化的条件检验替代
   （§3.2 / §5 修正 3）；若日后要求真实横截面重排，属接口/行为变更，须另行提案。
3. **衰减监控的落库面板窗口**：`_PANEL_LOOKBACK_DAYS = DECAY_WINDOW + 30 = 90` 交易日；
   窗口超过 90 日的因子在日批中会因值全 NaN 被 skipped（口径已写入模块 docstring），
   真实运行后若出现需评估是否放大窗口。
4. **`p_adj` 在存活候选上校正**（BH 只覆盖通过闸①②的候选，编排契约所致）——若评审认为应对
   全批次（含被闸①②拒收者）校正，属编排行为变更。评审修正后多重性已按候选**位置**计
   （逐字重复不塌缩），但覆盖范围仍是闸①②存活集。
5. 人工终审签字（自动化 runner 未代签）。

## 5. 评审修正（2026-10-13，review fixes）

评审对 T3–T7 实装提出 9 项修正，全部落地（TDD 红→绿；逐项有测试钉子；`tests/factors` 240 → **252 例**）：

| # | 项 | 严重度 | 修正内容 | 测试钉子 |
|---|---|---|---|---|
| 1 | BH-FDR 分辨率缺陷 | 重要 | `PERMUTATION_N` 200 → **999**（覆盖 `K ≤ q·(N+1) = 100`）；`check_significance` 加 fail-loud 守卫（`1/(N+1) > q/K` → `ValueError`，消息含不等式两侧数值与 N/K/q） | K=50 单强通过 / K=50 全噪声全拒 / 守卫边界 N=50·K=5↔6 / 默认 K=100↔101 |
| 2 | 闸④判据须样本外 | 重要 | 池增量改在**验证段**（与闸⑤同一 `TRAIN_FRACTION` 切分）计算，判定 `increment > 0`；`detail` 记 `increment_train`（审计）+ 两段 IR 与切分边界；验证段有效日 < `MIN_OOS_IR_SAMPLES=10` → `NO_POOL_INCREMENT` + `reason="insufficient_oos_dates"`；冗余口径（全样本 \|ρ\| > 0.7）不变 | 样本内强样本外平坦 → 拒收（旧实现放行）/ 验证段强 → 通过 / 验证段过短 fail closed / 边界与闸⑤同源 |
| 3 | 置换口径替代（ratification） | 重要（批准） | 逐日符号随机化 = H0 下 IC 关于 0 对称的**精确条件检验**，对 H1 保守——控制器批准；口径写入 `validate.py` 模块 docstring「置换口径」段 + 计划文件「评审修正记录」 | 既有置换用例（同种子逐位可复现、强信号/噪声）继续全绿 |
| 4 | 闸⑤边界泄漏 | 次要 | 训练段末端 `DEFAULT_IC_HORIZON` 日剔除后再算训练统计量（其远期收益跨入验证段）；`detail["purged_train_days"]` 记录 | 秩平移构造：剔除前训练均值被边界日翻转（旧实现错误拒收）、剔除后同号通过；训练段剔空 → fail closed |
| 5 | delta spec 查重措辞 | 次要 | 「与库内全部因子」→「与库内 candidate/active/degraded 因子（retired 允许重提）」（与 E7/实现一致）；`openspec validate add-factor-library-and-monitor --strict` 通过 | `openspec validate --strict` |
| 6 | `_MIN_REDUNDANCY_SAMPLES` 单一来源 | 次要 | 移至 `constraints.py` 为 `MIN_REDUNDANCY_SAMPLES=5`（validate 改为导入） | 4 点跳过 / 5 点比对 |
| 7 | 陈旧 docstring | 次要 | `test_validate.py` 中「闸③④⑤ 为占位桩」表述更新为真实闸（占位桩 T4/T5 已删除） | —（纯文字） |
| 8 | K>20 批量（闸③集成级） | 次要 | K=25 批量 = 闸②存活者（逐条先验 `check_single_factor` 通过）：24 条超额数 = N 的尖峰序列 + 1 条零超额强信号 → 仅强候选过闸③。**级别披露**：单一面板无法可控地造出 20+ 条过闸②且 IC 超额数受控的表达式（ICIR ≥ 0.3 与高超额数在σ面上互斥），故本用例在闸③集成级建模（输入 = 闸②存活集）；全管线视角另由既有 50 随机表达式 `admit_candidates` 用例（全部止步闸②）+ 修正 9 的重复表达式编排用例覆盖 | K=25 用例（修正 1 同节） |
| 9 | BH 多重性按位置 | 次要 | 编排闸③改以**零填充候选位置**为键（`_significance_key`），逐字重复表达式各计一条假设；`detail["n_hypotheses"]` 暴露 K | 同批两个逐字重复 volume：`n_hypotheses = 2`、仅 1 条入库 |
| 10 | 文档与验收刷新 | 次要 | 本报告 + 计划文件「评审修正记录」+ `tasks.md`；重跑验收脚本，artifact 已刷新（数字变化见下） | 本报告 §0/§1 |

**验收刷新（fix 10）**：重跑 `tests/validation/2026-10-13-add-factor-library-and-monitor-acceptance.py`，
verdict 不变（`classic_active=5/5, noise_rejected=5/5, passed=True`），artifact 数字变化：
① 闸③ `p_value = p_adj = 0.001`（N=999，原 0.004975 = 1/201）且 `n_hypotheses=5`；
② 闸④ `pool_increment` 由全样本增量改为**验证段**增量（0.5498 / 0.9181 / 0.8621 / 0.9885 /
1.0869，原 0.5423 / 0.9578 / 0.4329 / 0.3078 / 0.1563）；③ 闸⑤ `purged_train_days=1`
（训练段 ICIR 略变：动量 0.5352 → 0.5280 等）。两次运行 stdout/artifact 逐位相同（固定种子）。
另更正报告 §1.4 表一处转录笔误（波动闸④最大 |ρ|：0.053 → **0.067**，与 §1.2 两两相关 -0.0667 一致）。

**验收循环性告诫（已记录，沿用 §3.1/§3.3）**：本验收的合成世界是**为验收构造**的
（生成过程显式依赖五经典因子；§3.3 记录了调参取舍）——「5 经典入库 + 5 噪声拒收」证明的是
**管线判定正确性**（真预测成分 → 过五闸；无预测 → 被拦），**不是**这些因子在真实 A 股上有效，
也**不能**用于论证闸口阈值「太松/太紧」；真实数据验收（open item 1）仍是唯一有效性证据。
