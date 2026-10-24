# Design: add-holdings-context

## Approach

单条语义主线：**让决策落到受众可执行的动作上**。三个改动点（默认值、受众约束、披露注）都由同一根因驱动——`sell` 同时承载 `exit`（可执行）与 `short`（A 股散户普遍不可执行），而系统不掌握受众持仓状态，于是分型只能靠 LLM 自述、默认值偏向 `short`、报告不披露执行前提。

因此本次先补「受众持仓」这条管道，再在管道上挂三个消费点。

### 1. 持仓框架的形状（预留管道的边界）

```
AnalyzeRequest.holdings (可选声明)          ← 当前唯一来源
        │
        ▼
HoldingsContext(holds: bool | None)         ← models.py，三态
        │
        ▼
state["holdings_context"]                   ← state.py 声明（未声明会被图静默丢弃）
        │
        ├──► Trader context 受众持仓行        ← nodes/trader.py 注入
        ├──► Risk Judge context 受众持仓行    ← nodes/risk.py 注入
        └──► 未持有受众的 exit 门禁           ← nodes/risk.py 消费（打回/降级）
```

**边界**：本次落地的是模型、状态键、解析、注入、消费契约五段。**不含**持仓持久化、账户接入、前端录入 UI、数量/成本/建仓日字段——这些字段在无消费方时引入就是死字段（本项目已吃过「Produces 接口必须有消费任务」的亏）。未来自动选股/交易接入真实账户时，`HoldingsContext` 增加字段与来源，管道其余各段不变。

### 2. 三态语义与默认值选择

| 状态 | 语义 | 看空表达 | 理由 |
|---|---|---|---|
| `holds=True` | 已申报持有 | `exit` | 有敞口可退，完全可执行 |
| `holds=False` | 已申报未持有 | `watch` | 无敞口可退，`exit` 语义矛盾；`short` 不可执行 |
| `holds=None` | 未申报 | `exit` + 说明未持有处置 | 可执行默认 |

未申报取 `exit` 而非 `watch`，依据是生产实证：13 条 sell 的裁决词全部是持有者减仓话术，「底仓」「重建仓」「收缩仓位」反复出现——LLM 一直在假定受众持仓。取 `watch` 会把这些结论整体降级为空转（watch 不进胜率分母，只算回避正确率），是永久的信息损失；取 `exit` 保留结论强度，并要求在理由里覆盖未持有情形。

### 3. 未申报 sell_type 默认 short → exit 的连锁改动

`risk.py` 现有默认路径（line 316）改 `exit` 后，`final_price_missing` 的 exit 分支会要求 `exit_schedule` 而非建仓价位。若沿用现有价位打回措辞（「请补全 entry_price/stop_loss/target_price」）会出现**要求补建仓价位来满足 exit 校验**的自相矛盾。因此打回措辞必须按缺失字段分支：

- 缺失含 `exit_schedule` → 要求申报减仓节奏（附「未持有则改为 watch」提示）
- 缺失为建仓价位 → 沿用现行措辞

滞回「维持前判」路径（line 206-207，prior_action 为 sell 时置 `sell_type`）同步改 `exit`，保持两处默认一致。

### 4. 门禁的定位

「未持有 + exit」门禁是本 change 唯一新增的确定性阻断点，直接复用了既有模式（打回重申一次 → 仍犯则降级 + 如实标注，与 `decision-hysteresis` 的「证据均衡无增量 → 降级观望」同构）。放它进来的理由是项目已确认的教训：**格式层 prompt 早做且静默失效过**——受众约束若只写在 prompt 里，就没有任何机制保证它生效；持仓状态是确定性输入，其矛盾也该由确定性门禁拦截，而非靠 LLM 自觉。

触发放大风险有限：`holds=False` 只在请求显式声明「未持有」时出现，未申报（当前生产默认）零触发。

## Alternatives Considered

- **只改 prompt、不加持仓框架**：成本最低，但受众约束失去确定性载体，且未来自动交易仍要从零补管道。舍弃。
- **禁止 `action=sell`（只允许 buy/watch）**：把可执行的 `exit` 与不可执行的 `short` 一起埋掉，并让看空论据被挤进 watch/hold，产生「论据全空、结论中性」的报告内部撕裂。舍弃。
- **从 `sell_type` 里删除 `short`**：波及 6+ 主规范 capability（方向映射、结算、胜率分母、校准、组合聚合、IC、回测、渲染），且抹掉看空论据的正式出口。改用「保留 + 收窄使用条件 + 披露」达成同样目的，成本低一个数量级。舍弃。
- **引入两融标的名单做精确可执行性判定**：需要新数据源且名单随时变化，收益不抵成本。`short` 本就只在受众明确要求时使用，披露注已足以诚实。推迟。
- **未申报时按「最保守解读」处理**：与生产实证相反（LLM 一直假定持仓），且把结论降级为空转。舍弃，取 `exit` 可执行默认。

## Risks

- **默认值改动影响面**：`exit` 要求 `exit_schedule` 必填，未申报路径的打回措辞若不同步分支，会让 LLM 去补建仓价位。对策：spec 场景 + 单测双覆盖措辞分支。
- **历史报告渲染变化**：`sell_type=None` 的历史决策在重渲染时会多出披露行。这是**期望行为**（历史形态同样按做空建仓模板呈现，不披露即不诚实），但会改动存量报告文本。对策：披露行只在「按做空模板呈现」时出现，exit 报告零变化。
- **`holds=False` 门禁过强**：若 LLM 完整输出 exit 并被降级 watch，可能损失一次有效研判。对策：降级时保留原 reasoning 并如实标注原因，state 可观测；且该路径当前生产零触发。
- **prompt 与 context 行措辞漂移**：两处描述同一纪律，可能不一致。对策：spec 里显式要求「context 行内纪律与 prompt 政策一致」，并由合同测试同时覆盖两处。
- **并行 change 冲突**：`add-horizon-semantics-to-decision-layer` 与 `add-watch-trigger-tracking` 各改本 change 触及 capability 的**不同 requirement**，依 §6 可并行；同改 `trader.md`/`risk_judge.md` 的不同章节，先到者合并后后者 rebase。

## Rollout

1. delta 校验 → 2. TDD 实施 → 3. 全量验证（pytest/ruff/mypy）→ 4. `scripts/deploy_prompts.py` 发布 prompt（prompt-deploy-consistency 门禁）→ 5. 人工验证报告落 `tests/validation/` → 6. sync + archive。
