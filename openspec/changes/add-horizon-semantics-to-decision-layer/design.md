# Design: add-horizon-semantics-to-decision-layer

## Approach

**周期契约段抽为共享片段，三层同源注入。** trader / risk_judge / fund_manager 三个 prompt 的周期语义必须逐字一致，否则会出现 sell_type 治理之前的层间语义漂移。实现上把周期契约写成一份共享片段（`prompts/_horizon_contract.md` 或 loader 层的共享常量），三处注入同一来源；契约测试断言三个 prompt 渲染后含同一片段。文案要点：

1. 考核语义：本决策按 T+20 个交易日相对沪深300 超额收益考核（±2% 中性带，与 decision-outcome spec 判定口径一致）；
2. buy 约束：买入决策 MUST 说明催化剂/兑现路径为何在 20 个交易日内兑现（财报披露、解禁落地、政策窗口、技术位触发等）；
3. 长线论据降级：兑现期 >20 个交易日的论据（估值回归、产能释放、长期市占率逻辑）MUST NOT 作为买入主因，仅可作为安全垫（佐证下行风险有限）或否决项（推翻买入）。

**horizon_hint 借鉴「可空申报」先例但校验器完全不消费。** Claim 模型已有 metric_name / period / direction 三个可选申报字段，语义为「None = 未申报 → 校验器跳过对应检查」。horizon_hint 同为可空申报：`horizon_hint: Literal["short", "mid", "long"] | None = None`（short=≤5 交易日 / mid=6–20 / long=>20），语义为「该论据预期兑现窗口」。pydantic 默认丢弃未建模的 extra 字段——模型不加字段则 LLM 申报静默丢失，因此模型字段与 prompt 说明必须同 delta 落地。与三个先例的关键差异：horizon_hint 是论据元数据、不是可验证引用维度，校验器**完全不读取**该字段——不校验、不新增 FAIL 桶、**不计入 coverage_gap**（direction 未申报会计 coverage_gap，但 horizon_hint 并入该 bool 会把全部存量 claim 翻成缺口，破坏既有语义）；未申报率由 eval 侧从 claims 统计。

**覆盖范围实现期核实修正**：direction 的申报说明实际存在于全部四个分析师 prompt（fundamental/technical/sentiment/macro，后两者内联在要求条目中），辩论者 prompt 无 claim 登记章节。horizon_hint 覆盖同样取四个分析师，且不限数值型——entity/event 型 claim（舆情事件）正是 short 档主力。

## Alternatives Considered

- **MODIFIED 既有「Trader 决策语义契约」Requirement 而非 ADDED 新 Requirement**：被否。MODIFIED 在 sync 时整段替换，既有需求段长且含 sell_type 等活跃场景，历史上撞过「delta 演进期间主规范被其他 change 修改导致 archive 漂移」的坑；新关切用 ADDED 追加，archive 守卫对 ADDED 无既有场景比对负担。
- **分析师直接按周期分组输出论据（报告结构改动）**：被否。改报告结构牵动 citation 校验、报告渲染、前端展示一整条链；horizon_hint 元数据方案只加一个可空字段，达到同样的「Trader 可机器化匹配」目的。
- **硬规则拦截「长线论据买入」**：被否。价格校验器误拦七连发的事故教训——确定性拦截器对 LLM 文本的误判率很高。本 delta 只做 prompt 层语义对齐 + 结构化元数据，执行约束交给后续 eval 观测数据说话。
- **horizon_hint 强制必填**：被否。先例（metric_name 词表外置 null）表明强制必填会诱发编造；可空 + eval 侧申报率统计让未申报显性化而非静默。
- **校验器消费 horizon_hint（null 计 coverage_gap，照抄 direction 先例）**：被否。coverage_gap 是 CitationResult 上的既有 bool（术语/期次/方向未申报），把 horizon_hint 并入会把全部存量 claim 翻成 coverage_gap=True，破坏既有缺口语义与依赖它的测试/报表；horizon_hint 是论据元数据而非可验证引用维度，本就不该由引用校验器裁决。

## Risks

- **周期语义挤占 prompt 预算、稀释既有指令**：三个决策层 prompt 已较长。对策：契约段控制在 5 行以内，放「输出格式」之前（决策语义区），实测后若 eval 关键指标漂移则回滚（prompt 有 Langfuse 版本管理，回滚成本已知）。
- **horizon_hint 申报率低（大量 null）**：需观察申报率水位。对策：实施后首个 eval 批次从 claims 统计 horizon_hint 申报率（校验器不承担统计），低于可用水位再考虑加强 prompt 示例（不改契约）。
- **「20 个交易日」字面值与配置项漂移**：考核窗是 `OUTCOME_DEFAULT_HORIZON_DAYS`（当前 20），prompt 写死 20 会在配置变更时漂移。对策：共享片段注明「与 OUTCOME_DEFAULT_HORIZON_DAYS 联动」，配置变更时同步改 prompt（契约测试断言字面 20 存在，配置改动会被测试拦下要求同步）。
