# 验证报告: fix-pool-increment-orientation

**日期**: 2026-10-19
**关联 delta**: `openspec/changes/fix-pool-increment-orientation/`
**E2E 门禁**: 不适用（纯后端闸门语义修复）
**代码门禁**: `tests/factors` 全绿（403）；ruff / mypy 干净；`openspec validate --strict` 通过
**复跑脚本**: `scripts/replay_factor_harvest.py --statuses rejected,untranslatable --limit 78`（生产状态副本 + 临时库，两轮独立重放）

## 结论

| 项 | 读数 | 判定 |
|---|---|---|
| T1 方向归一化 | 负向候选（空池）定向后 `orientation=-1` 且 increment > 0；镜像对称增量精确相等；方向不可定 fail closed | ✅ |
| 回归 | 既有闸④/闸⑤/准入用例全绿（review 复核：既有正向用例增量逐位相同） | ✅ |
| 池因子定向 | `pool_orientations` 审计字段；**review C1 修复**（落库 IC 只覆盖验证段 → 改面板现算）+ 陈旧落库 IC 回归用例 | ✅ |
| 复跑（两轮） | **均入 3 条**：`volratio20`（`volume/Mean(volume,20)−1`）、`volcv20`（量波动比）、`range1`（`(high−low)/close`） | ✅ |

## 复跑读数（78 条已拒+不可翻译，临时库，两轮独立重放）

**入库 3 条（两轮一致）**——均为镜像定向受益者（旧闸④下 increment 为负被拒）：

| 候选 | 表达式 | 说明 |
|---|---|---|
| volratio20 | `sub(div(volume,ts_mean(volume,20)),1)` | 量比偏离 |
| volcv20 | `div(ts_std(volume,20),ts_mean(volume,20))` | 量变异系数 |
| range1 | `div(sub(high,low),close)` | 日内振幅 |

**`max_ret_5d`（上轮首个过闸②者）本轮过②③、倒在闸④——但原因是 `REDUNDANT_WITH_POOL`**
（与 `range1` 的 IC 相关 **0.7008 > 0.7**，同一信息簇只收一个代表）——闸门栈行为符合设计：
镜像因子能进，冗余仍被拦。闸④另三条拒收同为冗余（`vol_ratio_20d`/`vol_ratio_10d`/
`vol_mom_10d`，dup_of = 已入库的对应族代表），detail 带 `orientation=-1` 审计。

其余：44 条倒闸②（读数不变）、29 条仍不可翻译、2 条转译失败（LLM JSON 抖动，重试耗尽留痕）。

## review 修复记录（general-purpose 审查者，结论 Needs fixes → 逐条处置）

| # | 分级 | 发现 | 处置 |
|---|---|---|---|
| C1 | **Critical** | 池因子定向取落库 IC：近日入库因子落库 IC 只覆盖验证段（monitor 每日只写最新一天），与训练段交集为空 → 被静默踢出组合、闸④退化为单因子判定（实跑复现「同一候选随落库覆盖不同而一次拒一次收」） | 定向改为**与候选同规则的面板现算**（同一面板/训练段取 IC 符号；训练段有效样本 <　MIN_REDUNDANCY_SAMPLES 记 0）；陈旧落库 IC 回归用例钉死 |
| I1 | Important | delta 新场景 AND 子句与实现不符（反向号时空池下闸④先拒、闸⑤根本不会执行） | 场景文案修正为「闸④以 NO_POOL_INCREMENT 先拒；⑤门槛对经④者继续兜底」 |
| I2 | Important | 噪声安全性过度声明：旧闸④的「验证段 IR>0」实际是单边方向过滤，归一化后消失——孤立链（弱③、K=1）噪声入库率 ≈1.8×；既有「50 随机因子」用例从未触达闸④，不能作证据 | proposal 如实声明该效应与边界（生产批次 BH-FDR 更严）；列为口径决策项（恢复过滤强度应显式调②，不靠④副作用）；镜像中性/对称由用例钉死 |
| Minor | — | 方向可由 1 个交易日决定 / 死分支（`stored_ic is not None`）/ 两路径数据源不一致 / 冗余路径缺审计字段 / 池全不可定分支无用例 | 全部处置：最小样本下限、删除死分支、docstring 声明输入契约、冗余 detail 补定向审计、补两条回归用例 |

**复核（同一审查者基于 e5268385 复跑）：Approved**——C1 四场景逐位一致且强探针（训练段落库错误符号）仍取面板方向；回归用例在 pre-fix 提交上实证红、修复后绿；I1 流水线 rejected_gate=4 与文案逐字对上；残留 4 处 Minor（措辞/声明收窄）已再处置。镜像对称：空池由套件用例钉死，**非空池由复核者外部实跑验证**（旧/新增量均 −70.020992696，差值 0）。

## 异常记录

- 两轮重放的 LLM 转译存在抖动（第二轮 2 条 JSON 失败→重试耗尽；上轮 0 条）——重试机制（#405）
  按设计工作；入库清单两轮一致，说明该测量对转译抖动不敏感。
- 临时库/临时状态，生产因子库与状态文件全程只读；未重启容器、主检出未改。

## 结论

- [x] 全部通过（代码门禁全绿 / 两轮复跑读数一致 / review 三级发现全处置），可进入 sync + archive
- [ ] 存在失败项，需修复后重新验证
