# Design: recover-factor-supply-losses

## 1. 负数常量重排（T1）

**原则**：重排必须**严格等价**（不得改变任何输入的输出值，含 NaN/0 边界），且不引入新语义。

重排表（`_NegNumber(-k)` 记作 `Neg(k)`，k>0；`0` 为 DSL 数字字面量 0）：

| 原形态 | 重排 | 等价性 |
|---|---|---|
| `Sub(A, Neg(k))` | `Add(A, k)` | A−(−k)=A+k |
| `Add(A, Neg(k))` / `Add(Neg(k), A)` | `Sub(A, k)` | 加法交换 + A+(−k)=A−k |
| `Mul(Neg(k), B)` / `Mul(A, Neg(k))`，k=1 | `Sub(0, B)` / `Sub(0, A)` | −1·x=−x；**特例省 1 个自由参数** |
| `Mul(Neg(k), B)`，k≠1 | `Sub(0, Mul(k, B))` | −k·x=−(k·x) |
| `Div(A, Neg(k))` | `Sub(0, Div(A, k))` | A/(−k)=−(A/k)；**不做 k=1 特例**——`sub(0,x)` 会传播 ±inf 而引擎 `div` 把非有限掩为 NaN，`sub(0,div(A,1))` 才是同语义形式 |
| `Div(Neg(k), A)` | `Sub(0, Div(k, A))` | 同上 |
| `Sub(Neg(k), A)` | `Sub(0, Add(k, A))` | −k−A=−(k+A)；k=1 → `Sub(0,Add(1,A))` 保持一般式 |
| `Add(Neg(a), Neg(b))` | `Sub(0, Add(a, b))` | 负+负 |
| `Mul(Neg(a), Neg(b))` / `Div(Neg(a), Neg(b))` | `Mul(a, b)` / `Div(a, b)` | 负负得正 |
| `Sub(Neg(a), Neg(b))` | `Sub(b, a)` | −a+b |
| 独立 `Neg(k)`（根/非窗口实参） | `Sub(0, k)` | −k=0−k |
| `Neg(k)` 出现在 window 参数位 | **保持拒收** | pyqlib 负窗口=前瞻引用（红线） |

实现落点：`qlib_translate._build` 的 `_BinOp` 分支与 2 元 `_CallNode`（Add/Sub/Mul/Div）
分支**共用**同一张重排表（`_rewrite_negation`，`_QLIB_TO_BINOP` 归一）——review 实证：
生产候选 `parkinson20` 以 `Mul(-1, Power(...))` **调用形态**出现，仅盖中缀会让它绕过
重排、多计 1 个自由参数；未命中时走原路径。`_NegNumber` 叶子返回 `Sub(0, k)`。
`_build_call` 的 window 校验（要求 `_Num`）不动——负窗口仍在拒收面内。

**数值等价口径**：全部规则为代数恒等式，数值逐格一致（含 NaN 传播与 ±inf）；唯一位级
差异为符号零（`±0.0`），本 DSL 除零一律 NaN、排序按数值比较，不可观测。

**自由参数预算**：`Sub(0, X)` 比原 `Neg(1)` 多 1 个 Number（0），部分候选会因此触发
自由参数上限——这是**正确行为**（上限是既有治理约束，本 change 不动）；复跑读数按实记录。

## 2. 深度上限 6 → 8（T2）

- 证据：12 条复杂度拒收中「仅深度超限」3 条（illiq20 深度 7 / illiq_20d 深度 7 /
  klow_5d 深度 8），自由参数与字段种类均在限内；其余 9 条受自由参数轴约束，**本 change 不解决**。
- 先例：深度上限 4→5（raise-factor-dsl-depth-cap）、5→6（extend-factor-dsl-coverage），
  本次 6→8 同款「有具体解锁对象」判据。
- 联动：`factor_proposal.md` 的「AST 深度 ≤ 6」→「≤ 8」+ Langfuse 发布；
  `dsl.py` 常量注释同步（「7+ 当前无解锁对象」一句据实改写）。

## 3. 转译重试（T3）

- **重试面**：仅 `TranscriptionResponseError`（非法 JSON；载荷合同缺字段在
  `transcribe_candidate` 边界归一为该异常）——即「模型输出不合合同」一类。
- **不重试面**：`complete_text` 抛出的网关级异常（`LLMError` 族）与配置类
  `ValueError` 子类（resolver 的 `IncompleteLLMConfigError` / `UnknownProviderPrefixError`）
  ——维持现 fail loud 上抛语义（spec「网关不可用 fail loud」场景逐字不变）。
  *review 修复*：初版误以 `ValueError` 为泛捕获面，会把部署配置错误降级为逐条
  `transcription_error` 并落状态（实证复现）；现以专用异常收窄，并有
  `IncompleteLLMConfigError` 用例钉死。
- **上限**：3 次尝试（1 次初始 + 2 次重试），与「提案输出合同」修复重试上限逐字一致；
  耗尽抛 `_ContractExhaustedError`（携带**实际**尝试次数）。
- **留痕**：verdict 增加 `attempts` 字段（审计：几次尝试才拿到合法响应）。

## 4. 按状态重放（T4）

- **API**：`run_factor_harvest(..., retry_statuses: Collection[str] | None = None)`。
- **候选选择**：`pending = 命中 retry_statuses 的已处理候选（保持候选文件次序） +
  未处理候选`，再截断到 `limit`——**重放优先**，保证限批下重放不被 300+ 未处理候选挤压。
- **状态写入**：新裁决覆盖旧记录；新记录携带 `retried_from`（旧 status）供审计。
  `retry_statuses=None`（日批默认）时逐字维持现行为。
- **ops 入口**：`scripts/replay_factor_harvest.py`（复用 `run_factor_harvest`；默认生产路径，
  `--db/--state/--candidates` 可覆盖为临时库；打印重放前后状态迁移对照）。
- **不做**：日批 job 不带 retry_statuses（重放是显式运维动作，避免日批反复重试已知拒收、
  避免污染闸③ BH-FDR 批分辨率）。

## Alternatives Considered

- **重放走「清 state 条目」**：把重放语义做进状态文件的手工编辑——不可审计、易误删其他裁决，拒绝。
- **重放走独立脚本直连 external import（绕过转译）**：对「翻译器修复后重试」恰好够用，但对
  「转译重试」无效（transcription_error 没有 qlib_expression），且绕过生产链路裁决不可信，拒绝。
- **负数常量用 `sub(x, x)` 之类的零参数技巧**：省自由参数但可读性差、且与 normalize/同构查重
  交互难验证；`sub(0,x)` 清晰且复杂度账可解释，采用后者。
- **深度上限一步到 10/15**：超范围（当前无 9+ 深度解锁对象；一次只动一个变量），拒绝。

## Risks

- **重排的等价性风险** → 逐条重排规则单测（含 NaN 传播与 k=1 特例）；整数/浮点判定沿用
  `Number` 非负约束不变。
- **深度 8 增加求值递归开销** → 面板求值为向量化 pandas 运算，深度只影响 AST 递归层数（≤8），
  可忽略。
- **重放写生产状态** → 默认路径 + 显式参数；测量复跑用临时 `--state/--db`（纪律同「复跑务必临时库」）。
- **prompt 部署预检拦截** → 按既有处置（指纹取证 + `--exclude` 定向发布，不触碰非本 delta prompt）。
