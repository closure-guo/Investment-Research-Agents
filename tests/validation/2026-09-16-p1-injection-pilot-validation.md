# P1 注入法反幻觉消融 pilot 校准报告（首次启动）

日期：2026-09-16
delta：`revamp-ablation-v2-causal-claims`（worktree `.worktrees/ablation-v2`）
预登记：`evals/ablation/preregister/2026-09-16-p1-injection-pilot.md`（门禁通过，valid=True）
执行环境：本机 + 火山方舟（glm-5.3）；Langfuse 在线；10 标的基准材料为 analysts 变体真跑

## 0. 执行摘要

| 项 | 结果 |
|---|---|
| 单元 | 213 = **ok 202 / void 10 / error 1** |
| 离线腿 | **200 单元，0 LLM 调用（已核验）** |
| 真跑腿（限量 12） | 13 单元记录 / **204 LLM 调用**（修正后账目，见 §4） |
| 阳性对照 | **显著**（n=82，b=57，c=1，exact p=4.09e-16）→ 管线灵敏度证实，本轮阴性结论不作废 |
| 停止规则 | 总体不一致比 **0.485** 落健康区间 → 可进入人工终裁；终裁 **201 条 < 300 上限** |
| 逃逸率 | `rate=None`（201 条 pending，未终裁不报数——口径纪律） |

## 1. 分型校准（离线腿，n=40/型，零 LLM）

| 污染型 | 机制 | n | b | c | 不一致比 | 校准 | 读法 |
|---|---|---|---|---|---|---|---|
| direction_error | A3 | 40 | 20 | 0 | **0.500** | **ok** | A3 拦截约一半方向错误；另一半未到达/未触发，强度可维持 |
| unit_error | A3 | 40 | 36 | 0 | **0.900** | **ok**（贴 too_hard 边界） | 单位归一化拦住绝大多数；再强一点就只剩该机制独扛 |
| period_shift | A2 | 40 | 40 | 0 | **1.000** | **too_hard** | 仅期次检查能拦——其余层零冗余；按停止规则**降强度/换注入点**，不硬扩样本 |
| fabricated_event | A6 | 40 | 0 | 0 | 0.000 | too_easy | **结构性盲区**：A6 无 FAIL 面（只产 PASS/UNVERIFIABLE） |
| stale_macro | A7 | 40 | 0 | 0 | 0.000 | too_easy | **结构性盲区**：A7 的新鲜度标记只进 context，不进校验链 |
| 合计 | — | 202 | 97 | 1 | **0.485** | **ok** | — |

**盲区（如实登记，拦截率 0 不可读作机制无价值）**：A6/A7 在校验链上无 FAIL 面——须按成本分型改读输入侧/决策层证据，或在后续批次为其定义可测面（登记候选）。

## 2. 真跑腿（限量 12，逐条如实记录）

| 型 | 单元 | 结果 |
|---|---|---|
| value_error（context→income_statement） | 5 | **2 ok**：`600519-value_error-0` 开态=escaped/关态=caught（**反向 c**，单例）；`000001-value_error-0` 开态=caught/关态=escaped（机制方向正确）。n=2 → **insufficient** |
| value_error / mirror_narrative | 3 + 4 | **7/8 void**：「两态终态产物均无污染值且无告警（污染未到达产物）」——**context 级注入未传导到终态**：营收格 ±5–50% 与 K 线反转均未被分析师写进产物。→ **真跑型注入强度/靶点需重校**（停止规则精神：不硬扩样本） |
| illegal_price（decision） | 4 | 3 void + 1 error：产物快照缺 `trader_plan`/`final_trade_decision`（材料步只跑 analysts 变体）→ **决策层注入钩子待建**；错误单元见 §3 |

## 3. P1 命中的真实缺陷（均已修复）

1. **`anchors` 字符串形态致 DebateMessage 校验崩溃**（`600519-illegal_price-2`，全图运行中）：LLM 把 `anchors` 返回为字符串 → pydantic 抛错 → 图运行失败。这是 adba 收紧契约后引入的崩溃路径，**野外首次实证**。修复：`09a5029`（形态归一：str→单元素列表、不可解释→[]，text 空仍拒），32 passed。
2. **预算漏计**：真跑腿实际 204 次 LLM 调用，报告仅计 20（只算 ok 单元）——违反「预算分型申报」的诚实要求。修复：`fdfeb9f`（全量计账 + 按状态拆项 + 计量器交叉核对）。
3. **void 单元先跑后废**：illegal_price 4 单元在 `applied=False` 已知的情况下仍各烧 38 次调用（114 次）才判 void。修复：`fdfeb9f`（不可施加注入**预判于图运行之前**，0 次图运行/0 次 LLM；按现材料，全部 10 标的的 illegal_price 每轮可省 ~1520 次调用）。

## 4. 预算（修正后，分型申报）

| 腿 | 单元 | LLM 调用 |
|---|---|---|
| offline_replay | 200 | **0**（`zero_llm_verified=true`） |
| real_run | 13（ok 2 / void 10 / error 1） | **204**（含 void 114、error 38；`llm_meter_matches_unit_sum` 可核） |
| 另计：基准材料生成 | 10 标的 × analysts 变体 | 未并入跑批账（材料步独立，产物路径见 `reports/ablation/p1/materials/`） |

两类成本量级不同，SHALL NOT 合并为单一 run 等效数（spec 注入成本结构二分）。

## 5. 终裁工作清单

- **201 条待人工终裁** → `tests/validation/2026-09-16-p1-adjudication-worklist.csv`（列：unit_id / 型 / 机制 / 腿 / 开态 / 关态 / 污染值 / 证据路径 / 人工裁定留空列）
- 终裁口径（spec）：**污染值出现在终态产物且未被拦截 = 真逃逸**；逐条确认后回填，`escape_rate` 才会从 `rate=None` 出数
- 工作量：201 < 预登记上限 300 ✓

## 6. 结论与下一步

**达标**：direction_error（0.50）、unit_error（0.90）落健康区间；阳性对照显著；离线腿零 LLM 通路成立（spec「离线重放不消耗 LLM」实证）。
**待重校（pilot 目的本身）**：period_shift（too_hard，降强度/换点）；context 级真跑注入（7/8 未到达产物，需强化靶点——建议改为「注入到分析师必然引用的字段」或经 claim 级注入）。
**结构性缺口（登记）**：A6/A7 校验面无 FAIL 面；illegal_price 决策层钩子（材料步需捕获 decision dict）；`anchors` 形态归一入 spec（候选 delta）。
**本轮不作为正式结论**：pilot 为强度校准轮；逃逸率待终裁。
