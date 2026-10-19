# 验证报告: extend-factor-dsl-sign-extremes

**日期**: 2026-10-19
**关联 delta**: `openspec/changes/extend-factor-dsl-sign-extremes/`
**E2E 门禁**: 不适用（纯后端 DSL/供给线变更）
**代码门禁**: `tests/factors` + api/screening 套件全绿（397/…）；ruff / ruff format / mypy 干净；`openspec validate --strict` 通过
**prompt 发布**: `factor_transcription.md` production **v2**（算子清单含 Max/Min/Sign、拒译反例已删）；`factor_proposal.md` 清单同步至 21 算子（review 修复项）

## 实测：40 条不可翻译候选重放（新算子清单，临时 state/库）

命令：`scripts/replay_factor_harvest.py --statuses untranslatable --limit 40`（生产状态副本；
零生产写入）。产物 `reports/replay_sign_extremes.json`。

### 结果：10 条恢复可测、0 入库——但拿到全链至今**最重要的两条读数**

**① 滚动极值族（本扩展的直接目标）成了最强的候选族**：

| 候选 | DSL | 裁决 | 读数 |
|---|---|---|---|
| **max_ret_5d** | `ts_max(sub(div(close,ref(close,1)),1),5)` | **闸④ NO_POOL_INCREMENT** | **闸②已通过**（首条！）；验证段 IR = **−0.383**（train 段 −0.279） |
| max_ret_10d | `ts_max(…,10)` | 闸② WEAK_ICIR | \|ICIR\| 0.281 |
| max_ret_20d | `ts_max(…,20)` | 闸② WEAK_ICIR | \|ICIR\| 0.269 |

**② sign 族恢复受阻于自由参数轴（差一个参数）**：转译 LLM 确实启用了新算子
（`Sum(Sign(Delta($close,1)) * $volume, 5) / (Mean($volume,5) + 1e-12)`），但
obv5 = 4 参数 > 3、pos20 = 4 参数 > 3、updays5 = 5 > 3、mfi14 三轴全超。
**自由参数上限（3）现在是新可表达族的唯一卡点**——pos20 的同族 klow_5d 读数 0.273，
本扩展的 max_ret 族读数 0.27–0.28+，说明该轴两侧都有接近门槛的候选。

### 其余 30 条的构成（更新后的损耗地图）

| 桶 | 条数 | 说明 |
|---|---|---|
| 自由参数轴 | ~14 | pos20/obv5 差 1 个参数；rsi/parkinson/mfi 等差距更大 |
| ε 未数值化（LLM 拒译） | ~9 | rsi_14d/vpt_5d/efficiency_ratio/boll_pos/signed_vol 等——**研究侧质量问题**（治 RD-Agent prompt） |
| 横截面/PCA/回归 | 4 | 计算模型级，不做 |
| 其它（语法/深度） | ~3 | — |

## 结论与决策项

1. **扩展有效但不足以入库**：10/40 恢复可测；9 条倒在闸②（|ICIR| 0.061–0.281）；
   **max_ret_5d 过闸②③、死在闸④样本外**（验证段带符号 IR 为负）。
2. **新决策项（本报告立项依据）**：
   - **镜像因子语义**：max_ret_5d 的 ICIR 为负且训练/验证两段一致负——按「符号翻转
     等价」（把稳定负相关因子当反转信号用）它是可用的；当前闸④按**带符号** IR 判池增量，
     会把这类因子全部拒掉。是否在闸门语义引入符号翻转等价（②已按 |ICIR|，④⑤未对齐），
     值得单独立项评估——这是全链至今第一条"可能被闸门口径误伤"的实证样本。
   - **自由参数 3→4**：pos20/obv5 恰差 1 个参数且同族读数 0.27+。属治理决策（过拟合轴），
     依据本报告交 owner；agent 立场维持 3 不动（闸②复审先例）。
   - **研究侧 ε 数值化**：LLM 拒译最大簇已从"缺算子"转为"ε 未数值化"——治 RD-Agent
     prompt（产出规范：常数全部数值化）。

## 结论

- [x] 全部通过（代码门禁全绿 / 复跑读数齐备 / prompt 双端发布），可进入 sync + archive
- [ ] 存在失败项，需修复后重新验证
