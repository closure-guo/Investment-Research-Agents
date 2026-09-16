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

---

## 7. Round 2（重校轮，2026-09-16，commit `63e6347`）

**重校项与效果**（按预登记停止规则的逐型判定执行）：

| 型 | round 1 | round 2 | 变化说明 |
|---|---|---|---|
| period_shift | 1.000 too_hard | **0.500 ok** | 双形态实例（偶=仅标签；奇=标签+field_ref 段位移）→ 一半实例被值检查在**双态**拦下 |
| direction_error | 0.500 ok | 0.500 ok | 不变 |
| unit_error | 0.900 ok | 0.900 ok | 不变 |
| fabricated_event / stale_macro | 0.000 | 0.000 | 盲区不变（A6/A7 无 FAIL 面，结构性） |
| **合计** | 0.485 ok | **0.374 ok** | 仍在健康区间 |

**真跑腿 round 2**（12 单元 / **157 LLM 调用**，meter 交叉核对一致）：

- `value_error` ×4 仍 void：「污染未到达产物」——分析师不引用被注入字段（`derived_series.chg_5d` / income_statement 营收格）→ **下一轮靶点：注入到产物 claim 实际引用的字段**（recompute 注册根优先，使 A3 值检查可拦）
- `mirror_narrative` ×3 ok + 1 void：MA.5 反转**首次进入产物**（de-void 成功），但两态均 caught（一致对，无机制信号）——A2 开关效应未显现，需结合逐态证据读
- `illegal_price` ×4 void：跑图产出的决策为 `action='watch'`（hold/watch 豁免价位要求）→ 注入不可施加 → **下一轮：筛选可执行决策（buy/sell）的标的/实例**

阳性对照：显著（n=76，b=56，c=0，exact p=2.8e-17）→ 本轮阴性结论不作废。
停止规则：总体不一致比 **0.374** 落健康区间；终裁 **180 条 < 300** 上限。
终裁清单已更新为 **round 2 证据增补版**（新增逐态 `polluted_present` / `flagged` 列，便于逐条裁定）。

## 8. 下一轮（round 3 候选）

1. `value_error` 靶点改为「产物 claim 实际引用字段」（保证到达 + A3 可拦）——**真跑型，待批**（§10 已把离线侧做完）
2. `illegal_price` 可执行决策（buy/sell）筛选——**真跑型，待批**
3. A6/A7 可测面定义 ——**已完成**（§10：可追溯性面 + 输入侧面）
4. 终裁回填 → `escape_rate` 出数（当前 `rate=None`，口径纪律；清单已按 round 3 重生成）

## 9. 终裁材料补齐（2026-09-16，round-2 证据审读）

round-2 交付的终裁清单四列逐态证据**交付时全空**（`6f3fcc9` 只加了列头未填值）——人工
无从判读。本轮补齐为可判材料：库侧 `evals/causal_ablation/adjudication.py` + 薄壳
`tests/scripts/p1_adjudication_material.py`（20 条单测，`tests/evals/causal_ablation/test_adjudication.py`）。

- **单元级清单**（180 行，`2026-09-16-p1-adjudication-worklist.csv`）：四列证据回填，
  新增 `on_flag_bucket` 列（真拦 vs 伪拦截可分辨）；已填的人工裁定按 `unit_id` 继承，
  重生成不覆盖人工终裁。
- **案例级清单**（50 行，`2026-09-16-p1-adjudication-cases.csv`）：同标的同型的确定性实例
  **完全相同**（注入载荷与污染 claim 一致）→ 折叠为案例：**判 50 次而非 180 次**
  （period_shift 每标的 2 实例，其余 4 实例；实例数之和 == 清单行数，有单测守口径）。
- **机器预读列**（证据整理，SHALL NOT 读作终裁）：23 真逃逸候选（反事实型）/ 10 无拦截面 /
  10 自证出处 / 5 未产生有效差异 / 1 伪拦截 / 1 覆盖缺口。

### 9.1 证据审读新发现的注入构造缺陷（登记为 round-3 修复项）

| 缺陷 | 实证 | 影响 |
|---|---|---|
| `direction_error` 半数翻转是**空操作** | stated 自带符号（如 -1.21），翻成 positive 后 `eff = stated×(+1)` 与原来相同 → A3 PASS（delta≈0）；只有 stated 为正、翻成 negative 才产生真矛盾 | 20/40 单元不携带机制信息 |
| `unit_error` 的 ×1e8 在部分标的上**恰是合法换算** | 002415 原值 141.95（亿元）×1e8 = 1.4195e10（元），与真值相对差 2.6e-5（容差内）→ PASS | 4/40 单元是无效污染（终裁应判「误报」） |
| `fabricated_event` **自证出处** | 假新闻与假引用同源注入（`mutated_fields` 含 `snapshot.news_list`）→ 回声匹配必然 PASS | 10/10 案例不携带 A6 信息（A6 可测面须与注入改造同做） |
| **伪拦截** | 002594 的 on 态 FAIL 桶为 `path_unresolvable`（field_ref 本就解析不出），与翻转无关 | 1 案例不得算机制功劳（`on_flag_bucket` 列即为可分辨而加） |

**这些缺陷不改变 round-2 的停止规则判词**（0.374 仍落健康区间），但改变分型的可读性：
按可归因子集重算，`direction_error` = 20/20、`unit_error` = 36/36（均贴 too_hard 边界）——
即这两型的可检出污染**全靠 A3 单点**（同 period_shift 之于 A2），round-3 需按「双形态」思路
补非 A3 可拦形态，而不是硬扩样本。

## 10. Round 3（注入构造重校，2026-09-16，离线腿零 LLM）

§9.1 的四项构造缺陷已修复（库侧 `evals/causal_ablation/pilot_runner.py` + `injection.py`，
单测 316 条），离线腿重跑：`reports/ablation/p1-round3/`（200 单元 / **0 LLM 调用** /
0 void / 0 error；`--legs offline`，未动真跑腿）。

| 型 | round 2 | round 3 | 变化说明 |
|---|---|---|---|
| direction_error | 0.500（半数是空操作） | **1.000** | 只选「翻转真改变有效值」的 claim（`_effective_direction_flip`，判据走校验器单一实现） |
| unit_error | 0.900（4 例是合法换算） | **1.000** | 倍率按「是否被归一化吸收」挑选（`_effective_unit_error`）：1e8→恰等于真值、1e4→「万」、1e6→percent(×100) 均被吸收 → 退到 1e3 |
| fabricated_event | 0.000（无面 + 自证出处） | **1.000** | 改为**只注入 claim**（不写 news_list）+ **可追溯性面**（拦截 = 未静默放行，判定非 PASS） |
| period_shift | 0.500 | 0.500 | 不变（双形态仍有效） |
| stale_macro | 0.000（无面） | 0.000（校验面） | 改读**输入侧面**：presence 检查 40/40 ON 有「数据滞后」告警、0/40 OFF → 判「机制生效」 |
| **合计** | 0.374 | **0.700** | 落健康区间；阳性对照显著（b=80 c=0 p=1.65e-24）；0 void / 0 error |

**读法（重要）**：direction/unit/fabricated 三型的 1.000 = **单点依赖**（该污染只有唯一机制能
拦下），是结构事实而非测量缺陷——**不得按「too_hard → 再降强度」处理**：污染现在已是有效的，
再降就退回空操作（round-2 的教训）。stale_macro 的 0.000 只反映「校验链上无此面」，其读数由
`input_side_evidence` 取代。

**可测面分型**（新增 `FLAG_SURFACE_KIND`，报告 `by_type[*].flag_surface`）：`fail`（A2/A3/A5 型）/
`traceability`（A6：PASS↔UNVERIFIABLE）/ `input_side`（A7：context presence）/ `none`（无面 → 盲区）。
`blind_spots` 现只列 `none` 型（round-3 离线批次为空）。

**清单变化**：终裁清单 180 → **140 条**（stale_macro 40 条移出清单：输入侧面无「拦截/逃逸」
语义，判读走 presence 证据；`worklist_excluded` 显式披露，不静默减条）；案例级 **40 条**
（10 标的 × 4 型），机器预读**全部为「真逃逸候选（反事实型）」**——不再有空操作/无面/自证出处单元。
两份 CSV 已按 round-3 台账重生成（`--resume reports/ablation/p1-round3/resume.json`）。

## 11. 人工终裁结果与主指标出数（2026-09-16）

**终裁结论**：40 个案例（140 个单元）**全部确认为真逃逸**（人工裁定，按 §10.1 三问逐条口径；
两份清单的 `human_verdict` 列已回填）。stale_macro 40 单元不进清单（输入侧面，走 presence 证据）。

**逃逸率出数**（`rate` 从 `None` → 数字；`adjudicated=140`）：

| 读法 | 数 | 含义与信息量 |
|---|---|---|
| ① 关态逃逸率（**现行实现**：已终裁单元中关态逃逸占比） | **140/140 = 1.000** | **退化读数**——清单定义即「关态逃逸」，分母全是逃逸候选；它读的是「关掉机制后污染全漏」这一反事实 |
| ② 机制开启时的残留逃逸（开态逃逸 / 全部 ok 单元） | **0/200 = 0.000** | **信息量所在**：每个污染型在机制开启时**无一逃逸**（与三型不一致比 1.000「单点依赖」互证） |
| ③ 跑批口径（关态逃逸 / 全部 ok 单元） | **140/200 = 0.700** | 与本批总体不一致比 0.700 同源（两态恒为 caught/escaped 时二者相等） |

**待 owner 决策（第 3 项：分母语义）**：三种读法对应三个分母，**建议以读法 ② 作主指标**
（它回答"防线有效性"），把读法 ① 降为机制必要性的辅证——现行实现（①）在本批为退化值（=1.000），
不改口径则主指标无信息量。选定后改 `escape.py::escape_rate` 的分子分母语义 + 复现测试。

### 10.1 终裁清单读法（图例）

**三问**：① 污染真进产物了吗（`polluted_field` + `polluted_values` 非空、`off_state=escaped`）；
② 开态那次拦下是真拦吗（`on_flag_bucket` ∈ {`direction_mismatch`/`value_mismatch`/
`semantic_period_mismatch`/`internal_inconsistency`}；**`path_unresolvable` 是伪拦截**——FAIL 来自
claim 本就解析不出，与污染无关）；③ 关态真逃逸吗。三条成立 → 填「真逃逸」。
`instances`/`unit_ids`：同一标的同一型的实例**完全相同**，判一次、组内套用。

**看起来奇怪但正常的格子**（不是漏填，是结构如此）：

| 现象 | 原因 |
|---|---|
| `period_shift` / `002415-unit_error` 的 `ground_truth`/`delta` 为空 | **语义类检查**（A2 期次检查「申报期次在数据里存在吗」、内部回声检查「值与 claim 正文自相矛盾吗」）只报「不匹配」，不做数值比对 → 无真值/差值可报；拦截证据看桶名（`semantic_period_mismatch` / `internal_inconsistency`） |
| `fabricated_event` 的 `on_verdict=UNVERIFIABLE/-` | A6 可追溯性面：该机制没有判 FAIL 的能力，"无出处的 claim 不静默放行"即算拦下（见 §10 可测面分型） |
| `direction_error` 的 `delta` ≈ 2×`ground_truth` | 污染是方向标签翻面，有效值变号 → 原始差 = 2 倍真值（正常算术，非异常） |
| `002415-direction_error` 的 `delta=18.71` 与 `ground_truth=0.1852` 不同单位 | claim 面值是**百分数**（18.52）而真值是**小数**（0.1852）——方向冲突分支在校验器里**先于单位/百分比归一**返回原始差（`abs(gt - eff)`），故该 delta 只能读作「方向冲突成立」，**不能读作偏差幅度**（按同一单位算应为 0.37）。判词不受影响（桶 `direction_mismatch` 正确）；此为校验器侧观测量缺陷，已登记待处置 |
| `unit_error` 中 `002415` 的桶是 `internal_inconsistency` | 值被放大到与 claim 正文自相矛盾，先被内部一致性抓到（同属 A3 校验链，仍算真拦） |
| `polluted_values` 曾是方向型的申报对照值 | round-3 实测缺陷（commit `875b6c5`）：方向型漏写真实标记 → 已修为翻转后的方向词，并加 `polluted_field` 列 |

**spec 更新（已落 delta）**：新增 `### Requirement: 可测面分型与注入有效性`——四类可测面
（`fail`/`traceability`/`input_side`/`none`）的定义与读法边界、注入有效性要求（空操作/等价换算/
自证出处三类形态 SHALL NOT 作为实验单元）、输入侧面不冒充拦截；`openspec validate --strict` 通过。
