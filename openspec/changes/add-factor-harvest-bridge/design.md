# Design: add-factor-harvest-bridge

## D1 两段式转译：LLM 只写 Qlib 语法，DSL 侧保持确定性可审计

- **为什么不让 LLM 直接输出 DSL**：现有「外部表达式翻译契约」的核心价值是**逐算子映射表可审计**
  （43 算子按 pyqlib 源码逐条核对）。LLM 直接写 DSL 会把「等价性」变成不可核对的模型自述；
  拆成「LLM 转译（formulation → Qlib 语法，窄任务）+ 确定性翻译（Qlib → DSL，映射表裁决）」后，
  审计面完整保留：LLM 的错误会在确定性翻译层以 blocker 暴露（如 `-1 *` 负数常量、滚动 Max）。
- 载荷合同 = 二值：`{"qlib_expression": …}` XOR `{"untranslatable": true, "reason": …}`
  （缺 reason/双空 → 转译失败计 errors）。prompt（`factor_transcription.md`）显式列可用算子清单
  与「严格等价优先、MUST NOT 猜测」纪律，并把已知不可表达形态（滚动极值/偏度/峰度/分位数、
  可变指数）写成拒译示例。

## D2 候选源：采集器顺带产出（零新增桥）

- 采集器已在增量解析会话日志；本 delta 只把同一份新字节里的**因子块**（name/description/
  formulation 三元组，ANSI 清理后正则）并入全局候选文件 `data/factor_research/candidates.json`
  （按 name 全局去重、原子写、跨会话不丢）。半块不产出（MUST NOT 以残缺元数据冒充）。
- 增量重叠窗 4KB：防因子块跨 chunk 边界截断（去重使重复解析无害）。

## D3 幂等与 fail loud 的分界

- **幂等**：状态文件 `harvest_state.json` 记 processed（终态含 admitted/rejected/untranslatable/
  transcription_error 与读数）；重复运行零 LLM 调用。
- **fail loud**：LLM 网关异常/面板不可得 → 上抛（job 记 failed），**不写状态**——候选下次重试；
  转译**载荷不合合同**（模型输出非法 JSON）→ 计 `transcription_error` 并记状态（不无限重试，
  状态可人工清理）。两者的区别 = 「基础设施故障（重试）」vs「单条产物缺陷（留痕终态）」。

## D4 全自动 + 日频：闸门即裁判；频率由注册表与 FDR 预算决定

- 全自动过闸 = 既有设计立场（外部结论不替代闸口读数；月批提案本就是全自动）。收割只是把
  「手工转写 + 手工跑导入」替换为「自动转译 + 定时跑导入」——裁判未换、阈值未动。
- 频率取**工作日 15:55 日批**（注册表既有粒度：快照 15:30 → 选股 15:40 → 衰减 15:45 →
  内置注册 15:50 → 收割 15:55 → 结算链 16:00 起）：① 闸③ BH-FDR 的批内分辨率为 K ≤ 100，
  日频单批（当日新候选）远低于上限；② 闸②/⑤ 的 IC 读数以交易日为单位变化，小时级重跑不产生
  新信息；③ 沿用注册表 = 运行历史/补跑/状态页零新增基建。

## Risks / Trade-offs

- LLM 转译的语义漂移无法被闸门直接识别（闸门测的是表达式本身的 IC）——缓解：原始 formulation
  与转译链随 hypothesis 留档（人工可抽查）；prompt 的拒译纪律 + 确定性翻译层的 blocker 双重拦截。
- 已知保守面（不隐瞒）：`-1 * X` 负数常量形态被确定性翻译层拒绝（blocker 已注明可用 `sub` 重排），
  实测 4 条候选仅因此被拒——改写规则属独立后续项（一次一个变量）。
