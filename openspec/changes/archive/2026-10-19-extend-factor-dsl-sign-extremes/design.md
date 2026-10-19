# Design: extend-factor-dsl-sign-extremes

## 语义决策

- `sign`：逐元素 `np.sign`——正 → 1、负 → −1、零 → 0、NaN → NaN（numpy 原生传播，
  无需额外掩码）。与 pyqlib `Sign`（`ops.py` NpUnaryOperator）严格等价。
- `ts_max`/`ts_min`：`rolling(window, min_periods=window).max()/.min()`——与 `ts_mean`/
  `ts_std`/`ts_sum` 完全同族口径（窗口含当日；窗口不足或窗口含 NaN → NaN，因
  min_periods=window 要求窗口内非 NaN 计数满窗）。与 pyqlib `Max`/`Min`（Rolling 子类）
  formulation 等价；实现级差异仅为 `_WARMUP`（pyqlib min_periods=1 热身期输出部分窗口值），
  在映射表逐条声明——同其他滚动算子先例。
- **映射翻转**：`OPERATOR_MAPPINGS` 中 `Max`/`Min` not_equivalent → equivalent
  （`ts_max`/`ts_min`）；`Sign` missing → equivalent（`sign`）。同名不同义拒收纪律不变
  ——本 DSL 逐元素二元 `max`/`min` 与 Qlib 滚动 `Max`/`Min` 的歧义由**映射目标名**消解
  （映射后不再有裸 `max(a,b)` ↔ `Max(x,N)` 混用面）。
- **复杂度账**：`sign` 无参数不计自由参数；`ts_max`/`ts_min` 的窗口计 1 个自由参数
  （与 ts_mean 同）；字段/深度口径自动适用。上限零改动。

## Prompt 同步（factor_transcription.md）

- 算子清单追加：`Max(x, N) / Min(x, N)`（滚动窗口最大/最小，窗口含当日）、`Sign(x)`。
- 拒译反例改写：删除「滚动最大值/最小值……本系统无对应」反例（现为可用算子）；
  保留滚动偏度/峰度/分位数/中位数、可变指数幂、窗口语义不符等反例。

## 实验设计（判据预设）

重放 = 生产候选文件 + 生产状态副本 + 临时库，`--statuses untranslatable --limit 40`
（重放占满批次）；读数两问：①新清单下 21 条 LLM 拒译族的转译恢复数；②恢复族的闸②
|ICIR| 分布。关闭条件：sign/极值族恢复后读数普遍 <0.15 → 该方向关闭，不续扩
（Skew/比较指示算子不再立项）。

## Alternatives Considered

- **加比较指示算子 `1{x>y}`（Gt/If）**：updays5/mfi14 的原生形态；但 NaN 语义
  （qlib 比较对 NaN 输出 0，本 DSL NaN 传播）有口径差，且可先试 `sign` 组合表达——
  先不加，若重放显示 LLM 仍拒译该族再评估。
- **加 Skew/Kurt**：skew20 的缺口；矩法展开天然超自由参数上限，需专用算子——
  一次一个变量，不与本扩展混批。
