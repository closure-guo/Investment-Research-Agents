# Design: ground-comparative-delta-claims

## Approach

三步按认识论顺序排列：**可测（计数）→ 接回已有预防（derived 通路）→ 扩展预防（均线差幅）**。全部为确定性后端逻辑，零 LLM 新调用，不改 prompt 文件。

## 派生值存放位置：独立 channel `derived_series`，不嵌入 `technical_indicators`

曾考虑把派生值挂到 `technical_indicators.derived` 下（规范原文「随 technical_indicators 注入」、免新增 channel、`_resolve_field_ref` 天然可解析）。**否决**，因为三处消费者假定 `technical_indicators` 的子项是与 K 线等长的时间序列：

1. 重算注册表 `"technical_indicators": lambda s: calc_technical(s["kline"])`（`citation.py:292`）——计算型 claim 按根键整体重算，`calc_technical` 不产 `derived`，嵌入即不可重算；
2. 期次检查对根键 `technical_indicators` 特化为 `X.Y.<idx> → kline 日期列同索引`（`citation.py:775, 808-812`）——标量派生值没有索引，会被误判期次；
3. `_trim_technical_indicators`（`analysts.py:308`）按序列窗口裁剪——标量子字典形态不符。

独立 channel 的代价只是一行 `AnalysisState` 声明 + 一条通道契约断言，而且顺手让 `derived_series` 成为重算注册表里的独立根键（同一份 `calc_derived_series(kline)` 重算）——派生值上的计算型 claim 从此可验，与 `garp_result` / `anomalies` 的注册模式完全一致。

规范原文「随 technical_indicators 注入技术面分析师 context」指的是 context 层面并列注入，与独立 state 键不冲突；本 delta 把可引用前缀钉死为 `derived_series.`。

## 均线差幅定义

| 字段 | 定义 | 单位 / 符号 |
|---|---|---|
| `ma_spread_5_20_pct` | `(MA5[-1] − MA20[-1]) / MA20[-1] × 100` | %；正 = MA5 在 MA20 上方 |
| `ma_spread_20_60_pct` | `(MA20[-1] − MA60[-1]) / MA60[-1] × 100` | %；正 = MA20 在 MA60 上方 |
| `close_vs_ma20_pct` | `(close[-1] − MA20[-1]) / MA20[-1] × 100` | %；正 = 收盘价在 MA20 上方 |
| `close_vs_ma60_pct` | `(close[-1] − MA60[-1]) / MA60[-1] × 100` | %；正 = 收盘价在 MA60 上方 |

- 任一操作数缺失（K 线不足 60 期 / MA 为 None / 分母为 0）→ None，context 渲染「数据不足」（沿既有 `derived_view` 口径），SHALL NOT 伪造。
- 全部为有符号量：`derived_series` 加入 `_SIGNED_ROOTS`，使 `direction` 申报参与符号比对（「MA5 较 MA20 低约 2.3%」应申报 `stated=2.3, direction=negative` 或 `stated=-2.3, direction=positive`，与增长率类语义一致）。
- 复用既有 `calc_technical` 的 MA 计算（同一 pandas rolling 口径），SHALL NOT 在 `calc_derived_series` 里另算一份均线——两份口径会成为新的校验器债。

## 比较型差值的显式降级

`_verify_comparative` 在 `direction` 不属三枚举时：

```python
return CitationResult(status="UNVERIFIABLE", claim=claim, bucket="comparative_delta_unregistered")
```

并由缺口计数路径把它算进 `coverage_gap`（与「未注册根键的计算型 claim」同一计数通道），拆报输出 `citation_unverifiable_comparative_delta`（`citation_unverifiable_text` / `citation_unverifiable_unregistered` 之外的第三类）。

**明确不做**：不在此路径重算差值、不判 PASS/FAIL、不改 D3 基期校验的执行顺序（现状是非枚举时早返回、不核基期——保持，避免把今天 UNVERIFIABLE 的行改判 FAIL 造成阻断层突变；若路线 2 立项再一并调整）。

## 路线 2 的触发条件（写进 metrics.md，不写进代码）

首轮实验收口后统计 `citation_unverifiable_comparative_delta / UNVERIFIABLE 总数`。若该比例可观且其中多数不是均线 / 涨跌幅这类可枚举组合（逐条人工核对），则立项路线 2（comparative 加 `formula ∈ {diff, ratio, pct_change}` + `stated_delta`，校验器按 formula 重算）；否则继续扩 `calc_derived_series` 清单。**不预先立项**——AGENTS.md「先分桶归因再处置」。

## Alternatives Considered

- **别名映射 `derived → derived_series`**：能救旧 claim，但 context 文案本就该写真实键；别名是第二套词表（违反「上下文与校验单一词表」要求）。改文案 + 声明键，不加别名。
- **只修断点、不扩差幅**：修完只是把 5 个既有字段接回去，MA 比较仍要心算；差幅是同一函数里加四行，边际成本极低，且正是登记问题的直接解。
- **路线 2 同步落地**：见 proposal「Why」与上节触发条件——需求未定量、契约与语义成本高，先测量。

## Risks

- **UNVERIFIABLE 比例上升被误读为退化**：切点登记 + 拆报单列 + 收口时按桶解读。
- **技术分析师引用行为漂移**：context 首次真正含派生值，claim 分布会变——预期方向是更多 `derived_series.*` 引用与更少心算数字；收口时抽验 3 条 deep 的技术面 claim。
- **重算 fixture 数值口径**：`calc_derived_series` 重算依赖 `kline` 完整，fixture 须含 ≥60 期；数据不足场景单测覆盖 None 路径。
