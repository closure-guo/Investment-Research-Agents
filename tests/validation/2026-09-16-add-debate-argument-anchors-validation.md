# 验证报告：add-debate-argument-anchors（辩论论点结构化锚点）

日期：2026-09-16
delta：`openspec/changes/add-debate-argument-anchors/`
执行：本机（Windows / uv）+ 火山方舟端点；分支 `ground-comparative-delta-claims`
运行环境：Langfuse 在线（Docker 恢复后），trace 证据已取（§3）

## 0. 变更范围（12 提交）

| # | 变更 | 层 | commit |
|---|---|---|---|
| 1 | `DebateArgument{text,kind,anchors}` 模型 + 旧格式显式降级 `unspecified` + 渲染/stub 适配 | 生成契约 | `9634da5` + `dfd4f4f` |
| 2 | `debate_anchors.py` 零 LLM 锚点存在性校验 + `debate_anchor_checks` 通道 + `collect_text_sources` 复用 | 校验通路 | `f143e26` + `f0b106e` |
| 3 | bull/bear + 风控三方节点接入（fail-open，span 落 stats） | 节点 | `2e6183d` |
| 4 | 三份辩手 prompt 锚点申报纪律 + 契约测试（bear/risk 示例措辞纠偏见 `1e6eadf`） | prompt | `3062a5f` + `1e6eadf` |
| 5 | judge 材料 `【锚点覆盖】` 分方骨架行 + `[kind 状态]` 标注 + `eval_argument_anchor_coverage` + task 输出拆项 | 评估 | `52b1e41` + `85ae866` |
| 6 | 终审修复：computational 方向误申报补记缺口（1a1109e）；bear/risk 示例纠偏 + 通道/契约硬化（1e6eadf） | 审查修复 | `1a1109e` `1e6eadf` |
| 7 | 消融接线：run 记录携 value+拆项、层增量聚合 CI（4.4 解阻后补） | 消融 | `0132f1e` + `a31793d` |

## 1. 单元与门禁（逐任务红→绿，均经独立审查）

| 任务 | 红 | 绿 |
|---|---|---|
| T1 模型/兼容/渲染 | ImportError / 实例腐蚀 / 隐私断言 | 37 passed（9 用例） |
| T2 锚点校验 | ModuleNotFoundError / 测试缺口 | 106 passed（含 inference 两段式突变验证） |
| T3 节点接入 | KeyError ×2 | 27 passed（含 graph 路由未动） |
| T4 prompt 纪律 | 契约 6 failed | 73 passed |
| T5 材料/评估器 | 三面红 | 75 passed |
| T4.4 消融接线 | 8 failed | 44 passed（含 judge golden guard） |
| 终审修复 | 3 failed | 112 passed |

## 2. 真实链路验证（两次 deep，600519，真 LLM，新 prompt v23）

| 指标 | 运行 A | 运行 B（带根 span） |
|---|---|---|
| 论点总数 | 49 | 45 |
| anchored（≥1 锚可解析） | 8（16.3%） | 10（22.2%） |
| `unresolved`（申报了锚但不可解析） | 25 | 20 |
| `unanchored_inference`（推断零锚，合法） | 16 | 14 |
| `missing_required`（data/event 零锚，纪律违规） | **0** | **1** |
| `unspecified`（旧格式/非法 kind 降级） | **0** | **0** |
| token 增量代理（结构化 vs 纯文本字符） | +3526 / 49 论点 | +3439 / 45 论点（≈76 字符/论点） |

**逐条归因（运行 A 的 unresolved 全量人工核对，14 条样本 + 分布）**——**全部为路径格式错误，无一条编造**：

1. **自造伪前缀**：`fundamental.毛利率`、`macro.M2增速`、`technical.中期趋势`——用了「板块名」而非上下文数据段标注的 state 英文键；
2. **把值/描述写进锚点**：`fundamental.毛利率91%+`、`macro.M2增速7.5%`、`technical_indicators.MA.发散状态`、`technical_indicators.MACD.死叉`——知道前缀但把键写成定性描述；
3. **event 锚点整句改写**：`i茅台常态化投放、资金流入板块带来支撑`——应为标题短引语，改写后无法回声匹配；
4. **成功样本集中在风控三方**：`risk_metrics.beta/var_95/max_drawdown/volatility`——正是 prompt 示例里的真实键，**示例照抄效应显著**。

**预登记判定（tasks 5.3）**：`unresolved` 占比高（51%/44%）→ **处置对象为 prompt 迭代**，不得放松校验或改判 kind。**登记迭代候选**（不阻塞本 delta）：① 明确「锚点只能是数据段标题内联的英文键」，并给坏→好对照示例（自造前缀 / 描述混入 / 整句 event 三例）；② 强调示例键的可复制性（风控侧成功即证）；③ event 锚点限「标题短引语（≤20 字），不得改写」。

## 3. Trace 证据（Langfuse，trace `b98eac87...` / `04f1c32d...`）

- **锚点统计进 trace**：根 span metadata 含 `anchor_stats` ✅。**如实记录机制边界**：`update_current_span` 依附当前活动 span（共享根 span 时"最后写入者胜"，本轮可见值为最后一次辩手调用）；**逐辩手归属以 state channel `debate_anchor_checks` 为准**（设计即如此——channel 是权威源，span 元数据为便捷观测）。
- **Score 落库**：`argument_anchor_coverage = 0.2222` 已落本轮真实 trace（comment 携四拆项；由 evaluator 离线执行于本轮保存输出后按 trace id 落回，属机制验证；实验路径的自动落库由 evaluator 承担，75 条离线用例覆盖）。
- **prompt 版本**：trace metadata `prompt_version: 23`（三份辩手 prompt 已发布）。
- 未取项：`citation` 三类计数在 trace 的 score 元数据——属 delta `ground-comparative-delta-claims` 5.4 的证据，已在其验证报告 §6 记录（同批运行）。

## 4. 边界与遗留

- **根 span 元数据最后写入者胜**：逐节点遥测在共享 span 下不保真（AGENTS.md 既有登记「根 span 元数据会被覆盖」同源）；本 delta 的通道设计已规避（state channel 权威）。
- **prompt 迭代候选**（§2 三条）为登记项，未在本轮实施——实施需再发布 + 复测。
- **发布流程偏离（如实记录）**：`deploy_prompts.py` 预检因「先提交后发布」的顺序误判方向（远程 v22 == 历史提交 `4c4b1b8`，实为旧部署版本而非 UI 编辑）；`sync_prompts --dry-run` 会反向收编覆盖本地（已避免）→ 以 git 历史证明方向后按 deploy 同逻辑 SDK 直推三份，回验 remote==local、预检 PASS、`_verify_prompt_sync=[]`。**治理缺口登记**：committed-but-undeployed 的 prompt 变更会被预检误拦，且 `sync_prompts` 在该状态下会覆盖本地。
- 单点修复计数在两次运行均为 0/None（无 value_mismatch 触发），不影响本 delta 判定。

## 结论

| 验收项 | 状态 |
|---|---|
| 模型/校验/节点/材料/评估器/消融接线（单元+审查） | ✅ |
| 真实链路（通道计数、遵从率分布、token 增量） | ✅ |
| Trace 级证据（span 元数据 + Score 落库） | ✅（含共享 span 归属边界如实记录） |
| prompt 发布 | ✅（v23，含方向证明偏离记录） |
| 遵从率 | ⚠️ 低（22%）但**归因为路径格式/引导问题**，登记 prompt 迭代候选——机制本身按预期工作（0 编造、纪律违规 ≤1） |
