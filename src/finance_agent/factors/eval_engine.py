"""因子 DSL 向量化求值引擎 + RankIC（add-factor-library-and-monitor Task 1）。

计划关键设计决策 E1/E2（实施者不得自行改口径）：

- **E1 求值语义**：时间序列算子（``ts_mean``/``ts_std``/``ts_corr``/``ts_rank``/
  ``delta``/``ref``/``ts_sum``）沿**日期轴按 code** 计算（``min_periods=window``，窗口不足或窗口含
  NaN → NaN；``ref`` 为纯 shift 无 min_periods 概念，前 N 行 NaN）；横截面算子
  （``rank``/``zscore``）沿 **code 轴按日期** 计算
  （NaN 不参与排序，``rank`` 用 ``pct=True`` 平均秩）；逐元素算子 NaN 传播；
  ``div`` 分母 0 → NaN（不抛）；``log`` 非正 → NaN、``sqrt`` 负值 → NaN。全部向量化——`rolling` /
  `corrwith` / `ufunc` 实现，**无逐 code Python 循环**。
- **E2 IC 定义**：``rank_ic_series`` 逐日对全市场做 Spearman 相关（= 因子值秩与
  远期收益秩的 Pearson）；任一日有效样本 < ``MIN_IC_SAMPLES`` → 该日 NaN。
  ``forward_returns(close_qfq, horizon)`` = ``close_qfq.shift(-horizon)/close_qfq − 1``
  （末日 NaN）。IC 序列仅取**两帧共同日期**（索引交集，升序）。

口径注记（本模块钉死、消费方不得再议）：

- 面板六字段 ``open/high/low/close/volume/amount`` 均为**前复权口径**；
  ``build_panel_from_rows`` 从 market_snapshot 行构造：``close`` = ``close_qfq``，
  ``open/high/low`` = 原值 × 当日复权因子 ``close_qfq/close_raw``（``close_raw`` 为
  0/NaN 时因子 NaN），``volume``/``amount`` 原样。
- ``MarketPanel.fields`` 各帧须共享同一 ``index``（日期字符串，升序）与
  ``columns``（code）——这是面板构造保证的不变量，算子按位置对齐。
- ``zscore`` 用**样本标准差**（``ddof=1``，pandas 默认）；横截面全相等 → std=0 →
  NaN（0/0，不产生 inf）。
- ``ts_std`` 同为样本标准差（ddof=1）；``ts_rank`` 为窗口内「当前值」的平均秩
  百分比（pandas ``rank(pct=True)`` 口径，含并列平均秩）。
- ``ts_corr(a,b,w)`` = 滚动皮尔逊相关（两帧逐格对齐）。
- **extend-factor-library-coverage 增量口径**：比较算子（``gt/lt/ge/le/eq/ne``）
  产出 1.0/0.0 浮点帧、任一侧 NaN → NaN；``if_else`` 条件非 0 取 a、为 0 取 b、
  NaN → NaN；``ts_median/ts_skew/ts_kurt/ts_quantile`` 直接委托 pandas rolling
  （热身期 NaN、窗口含 NaN → NaN；``ts_quantile`` 的 q 由解析期保证 ∈ [0,1] 且
  线性插值）；``ts_count`` 是唯一**容忍窗口内 NaN** 的时序算子（窗口内全 NaN →
  NaN、窗口不足 → NaN）；``ts_decay_linear`` 权重 1..N（最近最重、分母 N(N+1)/2）；
  ``ts_arg_max/ts_arg_min`` 为窗口内极值距当日期数（0 = 当日、并列取最近、窗口含
  NaN → NaN）；``ts_resid(y,x,w)`` 为滚动 OLS ``y = a + b·x`` 窗口末点残差
  （``b = cov/var`` ddof=0；x 方差 0 或窗口含 NaN → NaN）；``cs_mean`` 当日全市场
  等权均值广播（有效样本 < ``MIN_IC_SAMPLES`` → 当日 NaN）；``ind_neutralize`` 行业
  内去均值（标签缺失或组内有效样本 < ``MIN_GROUP_SAMPLES``=5 → NaN；标签须逐列
  恒定；面板未携带 ``groups["industry"]`` → ValueError）；``exp`` 结果非有限 → NaN。

复杂度：``ts_rank`` 经 ``rolling.apply`` 实现（窗口内排名无原生向量化算子）；
其余算子均为原生向量化。
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

from finance_agent.factors.constraints import DEFAULT_IC_HORIZON, MIN_IC_SAMPLES
from finance_agent.factors.dsl import WINDOW_ARG_INDEX, AstNode, Call, Field, Number

# 面板六字段（与 dsl.FIELDS 一致，逐字）
PANEL_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume", "amount")

# market_snapshot 行构造面板所需列（逐字）
_PANEL_ROW_COLUMNS: tuple[str, ...] = (
    "trade_date",
    "code",
    "open",
    "high",
    "low",
    "close_raw",
    "close_qfq",
    "volume",
    "amount",
)

# 行内做数值强转的列（与 data/snapshot.py::read_bars 同纪律：NULL 混入保持 float64）
_PANEL_ROW_NUMERIC: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close_raw",
    "close_qfq",
    "volume",
    "amount",
)


@dataclass(frozen=True)
class MarketPanel:
    """全市场面板：``fields`` 键 ∈ 六字段，值为 date(str)×code 的前复权口径帧。

    各帧须共享同一 index（规范日期字符串，升序）与 columns（code）；缺失格为
    NaN（例如某日某票无快照行、停牌缺格），算子对 NaN 的语义见模块 docstring。

    ``rows_present``（可选）：date×code 布尔帧，True = 该格有**非停牌**快照行
    （#512 闸①分母口径「当日有行」；停牌合成行与缺行同待遇）。``None`` = 未提供
    （直接构造的测试面板），消费方回退全面板口径。

    ``groups``（可选，extend-factor-library-coverage）：参考分组帧字典（键如
    ``"industry"``），值与 ``fields`` 同形（date×code 标签帧；缺失格 NaN）。
    ``None`` = 未携带——引用分组的算子（``ind_neutralize``）求值报错，
    MUST NOT 退化为全市场口径。
    """

    fields: dict[str, pd.DataFrame]
    rows_present: pd.DataFrame | None = None
    groups: dict[str, pd.DataFrame] | None = None


# ── 求值引擎 ────────────────────────────────────────────────────────────────


def evaluate(node: AstNode, panel: MarketPanel) -> pd.DataFrame:
    """AST → 面板因子值帧（向量化；语义见模块 docstring E1）。

    递归求值，无缓存（同一子树重复出现时重复计算——v1 复杂度上限内可接受）。
    Field 缺字段 / 空面板 → ``ValueError``（fail loud，不静默补 NaN）。
    """
    if isinstance(node, Field):
        frame = panel.fields.get(node.name)
        if frame is None:
            raise ValueError(f"面板缺少字段 {node.name!r}；现有字段: {sorted(panel.fields)}")
        return frame
    if isinstance(node, Number):
        reference = _reference_frame(panel)
        return pd.DataFrame(float(node.value), index=reference.index, columns=reference.columns)
    return _evaluate_call(node, panel)


def _evaluate_call(node: Call, panel: MarketPanel) -> pd.DataFrame:
    """算子调用求值：时序算子按窗口参数分派，其余为逐元素算子。"""
    if node.op == "ts_quantile":
        window = _window_of(node)
        q_node = node.args[2]
        if not isinstance(q_node, Number):
            raise ValueError("算子 'ts_quantile' 的 q 参数必须是数字字面量")
        return _ts_quantile(evaluate(node.args[0], panel), window, float(q_node.value))
    window_op = _WINDOW_OPS.get(node.op)
    if window_op is not None:
        window = _window_of(node)
        return window_op(evaluate(node.args[0], panel), window)
    if node.op == "ts_corr":
        window = _window_of(node)
        left = evaluate(node.args[0], panel)
        right = evaluate(node.args[1], panel)
        return left.rolling(window, min_periods=window).corr(right)
    if node.op == "ts_resid":
        window = _window_of(node)
        left = evaluate(node.args[0], panel)
        right = evaluate(node.args[1], panel)
        return _ts_resid(left, right, window)
    if node.op == "ind_neutralize":
        return _ind_neutralize(evaluate(node.args[0], panel), panel)
    elementwise = _ELEMENTWISE_OPS.get(node.op)
    if elementwise is None:
        raise ValueError(f"未知算子 {node.op!r}（evaluate 不支持）")
    return elementwise(*[evaluate(arg, panel) for arg in node.args])


def _reference_frame(panel: MarketPanel) -> pd.DataFrame:
    """Number 叶子广播的参照帧（任一字段即可；面板内各帧同形是构造不变量）。"""
    if not panel.fields:
        raise ValueError("空面板：fields 不含任何字段")
    return panel.fields[sorted(panel.fields)[0]]


def _window_of(node: Call) -> int:
    """取窗口参数（D2 保证 parse 产出正整数数字字面量；手工 AST 非法 → ValueError）。"""
    arg = node.args[WINDOW_ARG_INDEX[node.op]]
    if not isinstance(arg, Number):
        raise ValueError(f"算子 {node.op!r} 的 window 参数必须是数字字面量")
    return int(arg.value)


def _ts_mean(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """窗口均值（min_periods=window：窗口不足或含 NaN → NaN）。"""
    return frame.rolling(window, min_periods=window).mean()


def _ts_std(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """窗口样本标准差（ddof=1，pandas 默认）。"""
    return frame.rolling(window, min_periods=window).std()


def _ts_rank(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """窗口内当前值的平均秩百分比（pandas ``rank(pct=True)`` 口径，含并列）。

    向量化实现（issue #463，替代 ``rolling.apply`` 逐格 Python 回调）：当前值
    ``x_t`` 在窗口内的平均秩 = ``#{x_{t-j} < x_t} + (#{x_{t-j} = x_t} + 1)/2``
    （并列取平均，含自身），pct = 秩 / window——与 ``pd.Series(w).rank(pct=True)``
    的最后元素逐位一致。``min_periods=window`` 语义：窗口不足（前 window−1 行）
    或窗口内任一格（含 ``x_t`` 自身）NaN → NaN。按窗口偏移逐次移位比较（window
    次全帧向量化），内存有界（无 (日, 票, 窗) 三维展开）。
    """
    values = frame.to_numpy(dtype="float64")
    n_rows = values.shape[0]
    less = np.zeros_like(values)
    equal = np.zeros_like(values)
    valid = np.zeros(values.shape, dtype=bool)
    if n_rows >= window:
        valid[window - 1 :, :] = True
    for offset in range(window):
        # 行 i 处比较 x_i（窗口左端）与 x_{i+offset}（当前值），对齐到目标行 i+offset
        left = values[: n_rows - offset, :]
        current = values[offset:, :]
        less[offset:, :] += left < current
        equal[offset:, :] += left == current
        valid[offset:, :] &= ~np.isnan(left)
        valid[offset:, :] &= ~np.isnan(current)
    pct = (less + (equal + 1.0) / 2.0) / window
    return _wrap(np.where(valid, pct, np.nan), frame)


def _delta(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """``x − x.shift(window)``；前 window 行 NaN。"""
    return frame - frame.shift(window)


def _ref(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """``x.shift(window)``——沿日期轴后移 window 期（前 window 行 NaN）。

    与 ``_delta`` 同源 shift 机制（DataFrame 索引为日期 → 逐标的后移，不跨标的）；
    纯 shift 无 ``min_periods`` 概念（窗口参数为正整数由 D2 解析期保证）。
    """
    return frame.shift(window)


def _ts_sum(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """窗口求和（min_periods=window：窗口不足或含 NaN → NaN）。"""
    return frame.rolling(window, min_periods=window).sum()


def _ts_max(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """窗口最大值（窗口含当日；min_periods=window：窗口不足或含 NaN → NaN）。"""
    return frame.rolling(window, min_periods=window).max()


def _ts_min(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """窗口最小值（窗口含当日；min_periods=window：窗口不足或含 NaN → NaN）。"""
    return frame.rolling(window, min_periods=window).min()


# ── extend-factor-library-coverage：时序统计与计数 ──────────────────────────


def _ts_median(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """滚动中位数（窗口含当日；min_periods=window：窗口不足或含 NaN → NaN）。"""
    return frame.rolling(window, min_periods=window).median()


def _ts_skew(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """滚动样本偏度（与 pandas ``rolling(N, min_periods=N).skew()`` 逐值一致）。"""
    return frame.rolling(window, min_periods=window).skew()


def _ts_kurt(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """滚动超额峰度（与 pandas ``rolling(N, min_periods=N).kurt()`` 逐值一致；
    窗口内全相等 → NaN——pandas 口径）。"""
    return frame.rolling(window, min_periods=window).kurt()


def _ts_quantile(frame: pd.DataFrame, window: int, q: float) -> pd.DataFrame:
    """滚动分位数（窗口含当日；线性插值口径同 pandas 默认；q ∈ [0,1] 由解析期保证）。"""
    return frame.rolling(window, min_periods=window).quantile(q)


def _ts_count(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """窗口内非 NaN 值个数——唯一容忍窗口内 NaN 的时序算子。

    窗口不足（前 N−1 行）或窗口内全 NaN → NaN；其余为 1..N 的计数。
    """
    counts = frame.notna().astype("float64").rolling(window, min_periods=window).sum()
    return counts.where(counts > 0)


def _ts_decay_linear(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """线性加权移动平均：窗口内最早权重 1、最近权重 N（归一化分母 N(N+1)/2）。

    窗口含 NaN → NaN（逐偏移权重帧相加的 NaN 传播）。
    """
    if window < 1:
        raise ValueError(f"window 必须是正整数: {window!r}")
    total = frame.shift(window - 1)  # k=1：窗口最早一格权重 1
    for k in range(2, window + 1):
        total = total + frame.shift(window - k) * k
    return total / (window * (window + 1) / 2.0)


def _ts_arg_extreme(frame: pd.DataFrame, window: int, *, largest: bool) -> pd.DataFrame:
    """窗口内极值距当日的期数（0 = 当日；并列取最近一期；窗口含 NaN → NaN）。

    向量化实现（与 ts_rank 同款按偏移量全帧比较，无逐格 Python 回调）：
    ``extreme`` 为滚动极值帧；自 offset=0 起升序扫描，首个命中即最近一期。
    """
    values = frame.to_numpy(dtype="float64")
    n_rows = values.shape[0]
    rolling = frame.rolling(window, min_periods=window)
    extreme = (rolling.max() if largest else rolling.min()).to_numpy(dtype="float64")
    result = np.full(values.shape, np.nan)
    if n_rows >= window:
        any_nan = np.zeros(values.shape, dtype=bool)
        for offset in range(window):  # 窗口完整性：任一格 NaN → 该行 NaN（统一口径）
            any_nan[offset:, :] |= np.isnan(values[: n_rows - offset, :])
        for offset in range(window):  # offset 升序：首中即最近一期（并列口径）
            # 目标行 t 的窗口值在 t−offset 处；与 t 的滚动极值对齐比较
            candidate = values[: n_rows - offset, :]
            is_hit = (
                np.isnan(result[offset:, :])
                & (candidate == extreme[offset:, :])
                & ~any_nan[offset:, :]
            )
            result[offset:, :] = np.where(is_hit, float(offset), result[offset:, :])
    return _wrap(result, frame)


# ── extend-factor-library-coverage：回归与横截面 ─────────────────────────────

# ind_neutralize 组内有效样本下限（spec 逐字：< 5 → NaN）
MIN_GROUP_SAMPLES: int = 5


def _ts_resid(left: pd.DataFrame, right: pd.DataFrame, window: int) -> pd.DataFrame:
    """逐标的滚动 OLS ``y = a + b·x`` 在窗口末点的残差。

    闭式向量化：``b = cov/var``（ddof=0 两侧一致）、``a = ȳ − b·x̄``；
    任一序列窗口含 NaN 或 ``x`` 窗口内方差为 0 → NaN。
    """
    y, x = left.align(right)
    mean_y = y.rolling(window, min_periods=window).mean()
    mean_x = x.rolling(window, min_periods=window).mean()
    var_x = x.rolling(window, min_periods=window).var(ddof=0)
    mean_yx = (y * x).rolling(window, min_periods=window).mean()
    beta = (mean_yx - mean_y * mean_x) / var_x.where(var_x != 0)
    alpha = mean_y - beta * mean_x
    return y - (alpha + beta * x)


def cross_section_mean(frame: pd.DataFrame, *, min_samples: int = MIN_IC_SAMPLES) -> pd.DataFrame:
    """当日全市场等权均值，广播到每只标的（含当日值缺失者）。

    当日有效样本 < ``min_samples`` → 当日 NaN（防止稀疏字段的「全市场均值」
    被少数几只票决定）。公开命名（后续闸④组合构建可复用，与
    ``cross_section_zscore`` 同款）。
    """
    counts = frame.notna().sum(axis=1)
    means = frame.mean(axis=1)
    broadcast = pd.DataFrame(
        np.tile(means.to_numpy()[:, None], (1, frame.shape[1])),
        index=frame.index,
        columns=frame.columns,
    )
    return broadcast.where(counts >= min_samples)


def _ind_neutralize(values: pd.DataFrame, panel: MarketPanel) -> pd.DataFrame:
    """行业中性化：当日同行业（面板 ``groups["industry"]``）内去均值。

    行业标签缺失或组内有效样本 < ``MIN_GROUP_SAMPLES`` → NaN；标签须逐列恒定
    （随日期变化 → ValueError，MUST NOT 静默取首值）；面板未携带分组 → ValueError
    （MUST NOT 退化为全市场去均值）。
    """
    labels = (panel.groups or {}).get("industry")
    if labels is None:
        raise ValueError("面板缺少 industry 分组（ind_neutralize 需要行业标签帧）")
    label_by_col: dict[str, str] = {}
    for column in values.columns:
        series = labels[column] if column in labels.columns else None
        observed: list[object] = list(series.dropna().unique()) if series is not None else []
        if len(observed) == 0:
            continue
        if len(observed) > 1:
            raise ValueError(f"列 {column!r} 的 industry 标签随日期变化（须逐列恒定）")
        label_by_col[column] = str(observed[0])
    by_label: dict[str, list[str]] = {}
    for column, label in label_by_col.items():
        by_label.setdefault(label, []).append(column)
    out = pd.DataFrame(np.nan, index=values.index, columns=values.columns)
    for columns in by_label.values():
        sub = values[columns]
        counts = sub.notna().sum(axis=1)
        out[columns] = sub.sub(sub.mean(axis=1), axis=0).where(counts >= MIN_GROUP_SAMPLES)
    return out


def _exp(frame: pd.DataFrame) -> pd.DataFrame:
    """逐元素自然指数；结果非有限（溢出）→ NaN（与 div/log 的「不产 inf」同约定）。"""
    with np.errstate(over="ignore", invalid="ignore"):
        values = np.exp(frame.to_numpy(dtype="float64"))
    return _wrap(np.where(np.isfinite(values), values, np.nan), frame)


_WINDOW_OPS: dict[str, Callable[[pd.DataFrame, int], pd.DataFrame]] = {
    "ts_mean": _ts_mean,
    "ts_std": _ts_std,
    "ts_rank": _ts_rank,
    "delta": _delta,
    "ref": _ref,
    "ts_sum": _ts_sum,
    "ts_max": _ts_max,
    "ts_min": _ts_min,
    "ts_median": _ts_median,
    "ts_skew": _ts_skew,
    "ts_kurt": _ts_kurt,
    "ts_count": _ts_count,
    "ts_decay_linear": _ts_decay_linear,
    "ts_arg_max": lambda frame, window: _ts_arg_extreme(frame, window, largest=True),
    "ts_arg_min": lambda frame, window: _ts_arg_extreme(frame, window, largest=False),
}


def _cross_section_rank(frame: pd.DataFrame) -> pd.DataFrame:
    """逐日横截面 pct 排名（平均秩；NaN 不参与且结果保持 NaN）。"""
    return frame.rank(axis=1, pct=True)


def cross_section_zscore(frame: pd.DataFrame) -> pd.DataFrame:
    """逐日横截面 (x − mean)/std（样本标准差 ddof=1；std=0 → NaN）。

    公开别名（闸④池组合构建复用同一口径；DSL ``zscore`` 算子即本函数）。
    """
    return frame.sub(frame.mean(axis=1), axis=0).div(frame.std(axis=1, ddof=1), axis=0)


def _wrap(values: np.ndarray, reference: pd.DataFrame) -> pd.DataFrame:
    """numpy 结果 → 与参照帧同 index/columns 的 DataFrame。"""
    return pd.DataFrame(values, index=reference.index, columns=reference.columns)


def _div(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """除法：分母 0 → NaN（含 0/0 与 x/0）；非有限结果一律 NaN（不产 inf）。"""
    left_aligned, right_aligned = left.align(right)
    result = left_aligned / right_aligned
    result = result.mask(right_aligned == 0)
    return result.mask(~np.isfinite(result))


def _log(frame: pd.DataFrame) -> pd.DataFrame:
    """自然对数；非正 → NaN（NaN 传播）。"""
    return _wrap(np.log(frame.where(frame > 0)), frame)


def _sign(frame: pd.DataFrame) -> pd.DataFrame:
    """逐元素符号：正 1 / 负 −1 / 零 0；NaN 传播（np.sign 同）。"""
    return _wrap(np.sign(frame), frame)


def _sqrt(frame: pd.DataFrame) -> pd.DataFrame:
    """逐元素平方根；负值 → NaN（与 ``log`` 非正 → NaN 同约定；NaN 传播）。"""
    return _wrap(np.sqrt(frame.where(frame >= 0)), frame)


def _maximum(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """逐元素取大；任一侧 NaN → NaN。"""
    left_aligned, right_aligned = left.align(right)
    return _wrap(
        np.maximum(left_aligned.to_numpy(), right_aligned.to_numpy()),
        left_aligned,
    )


def _minimum(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """逐元素取小；任一侧 NaN → NaN。"""
    left_aligned, right_aligned = left.align(right)
    return _wrap(
        np.minimum(left_aligned.to_numpy(), right_aligned.to_numpy()),
        left_aligned,
    )


def _compare(
    op: Callable[[Any, Any], Any],
) -> Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame]:
    """比较算子工厂：成立 → 1.0、不成立 → 0.0；任一侧 NaN → NaN（pandas 原生
    比较把 NaN 当 False，须显式按有效格位掩码回 NaN）。"""

    def _cmp(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
        left_aligned, right_aligned = left.align(right)
        raw = cast(pd.DataFrame, op(left_aligned, right_aligned))
        valid = left_aligned.notna() & right_aligned.notna()
        return raw.astype("float64").where(valid)

    return _cmp


def _if_else(cond: pd.DataFrame, left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """条件选择：cond 非 0 取 left、为 0 取 right；cond 为 NaN → NaN（不猜分支）。"""
    cond_aligned, left_aligned = cond.align(left)
    cond_aligned, right_aligned = cond_aligned.align(right)
    mask = cond_aligned.fillna(0.0).to_numpy(dtype="float64") != 0.0
    picked = pd.DataFrame(
        np.where(
            mask, left_aligned.to_numpy(dtype="float64"), right_aligned.to_numpy(dtype="float64")
        ),
        index=cond_aligned.index,
        columns=cond_aligned.columns,
    )
    return picked.where(cond_aligned.notna())


_ELEMENTWISE_OPS: dict[str, Callable[..., pd.DataFrame]] = {
    "rank": _cross_section_rank,
    "zscore": cross_section_zscore,
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "div": _div,
    "max": _maximum,
    "min": _minimum,
    "abs": lambda frame: frame.abs(),
    "log": _log,
    "sqrt": _sqrt,
    "sign": _sign,
    # extend-factor-library-coverage：比较与条件
    "gt": _compare(operator.gt),
    "lt": _compare(operator.lt),
    "ge": _compare(operator.ge),
    "le": _compare(operator.le),
    "eq": _compare(operator.eq),
    "ne": _compare(operator.ne),
    "if_else": _if_else,
    # extend-factor-library-coverage：回归与横截面（ind_neutralize/ts_resid 走
    # _evaluate_call 特判分支——需要面板分组 / 双序列窗口，不进本表）
    "cs_mean": cross_section_mean,
    "exp": _exp,
}


# ── 快照行 → 面板 ───────────────────────────────────────────────────────────


def build_panel_from_rows(rows: pd.DataFrame) -> MarketPanel:
    """market_snapshot 行 DataFrame → ``MarketPanel``（前复权口径换算）。

    行须含 ``trade_date``/``code``/``open``/``high``/``low``/``close_raw``/
    ``close_qfq``/``volume``/``amount`` 列（缺列 → ``ValueError``）。
    数值列经 ``pd.to_numeric(errors="coerce")`` 强转；复权因子 =
    ``close_qfq / close_raw``（``close_raw`` 为 0/NaN → 因子 NaN → 价格字段 NaN）；
    ``close`` 取 ``close_qfq``；``volume``/``amount`` 原样。行按
    ``(trade_date, code)`` 透视，缺格 NaN；(trade_date, code) 重复 → ``ValueError``
    （``pivot`` 拒绝重复，fail loud——快照主键保证不会发生）。

    ``is_suspended`` 列可选（live 日批有、回填区显式 0；列缺失视为无停牌行）：
    停牌合成行（=1）六字段在输入侧置 NaN 且不计入 ``rows_present``——时序算子
    与闸①分母对停牌的语义与回填区「停牌=缺行」统一（issue #513 缺口 2 拍板）。
    """
    missing = [column for column in _PANEL_ROW_COLUMNS if column not in rows.columns]
    if missing:
        raise ValueError(f"market_snapshot 行缺少列: {', '.join(missing)}")
    frame = rows.copy()
    for column in _PANEL_ROW_NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "is_suspended" in frame.columns:
        suspended = pd.to_numeric(frame["is_suspended"], errors="coerce").fillna(0) == 1
    else:
        suspended = pd.Series(False, index=frame.index)
    present = ~suspended
    dates = frame["trade_date"].astype(str)
    codes = frame["code"].astype(str)
    # 停牌合成行六字段置 NaN（先取 presence 再抹值；close_raw 为 0 时把因子置
    # NaN（0 作分母非法），NaN 天然传播）
    frame.loc[suspended, list(_PANEL_ROW_NUMERIC)] = np.nan
    factor = frame["close_qfq"] / frame["close_raw"].where(frame["close_raw"] != 0)
    rows_present = _pivot(dates, codes, present.astype(float)).fillna(0.0).astype(bool)
    return MarketPanel(
        fields={
            "open": _pivot(dates, codes, frame["open"] * factor),
            "high": _pivot(dates, codes, frame["high"] * factor),
            "low": _pivot(dates, codes, frame["low"] * factor),
            "close": _pivot(dates, codes, frame["close_qfq"]),
            "volume": _pivot(dates, codes, frame["volume"]),
            "amount": _pivot(dates, codes, frame["amount"]),
        },
        rows_present=rows_present,
    )


def _pivot(dates: pd.Series, codes: pd.Series, values: pd.Series) -> pd.DataFrame:
    """长表三列 → date×code 宽表（日期/代码升序；缺格 NaN）。"""
    long = pd.DataFrame({"trade_date": dates, "code": codes, "value": values})
    wide = long.pivot(index="trade_date", columns="code", values="value")
    return wide.sort_index().sort_index(axis=1)


# ── RankIC（E2） ────────────────────────────────────────────────────────────


def forward_returns(close_qfq: pd.DataFrame, horizon: int = DEFAULT_IC_HORIZON) -> pd.DataFrame:
    """远期收益 ``close_qfq.shift(-horizon)/close_qfq − 1``（末 horizon 日 NaN）。

    ``horizon`` 必须为正整数（0/负数/非整数 → ``ValueError``）；非有限结果
    （价格含 0/∞ 的病态输入）一律 NaN，不让 inf 泄漏进统计量。
    """
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError(f"horizon 必须是正整数: {horizon!r}")
    returns = close_qfq.shift(-horizon) / close_qfq - 1.0
    return returns.where(np.isfinite(returns))


def rank_ic_series(
    factor_values: pd.DataFrame,
    fwd_returns: pd.DataFrame,
    *,
    min_samples: int = MIN_IC_SAMPLES,
) -> pd.Series:
    """逐日 RankIC 序列（E2）。

    对共同日期逐日计算因子值秩与远期收益秩的 Pearson 相关（= Spearman）；
    有效样本（两帧该格皆非 NaN）< ``min_samples`` 的日期 → NaN。返回序列
    index = 两帧索引交集（升序），name = ``"rank_ic"``；无共同日期 → 空浮点序列。
    两帧列集合须一致（同一面板派生，列集合由面板构造保证）；横截面秩方差为 0
    （全相等）→ NaN。
    """
    if min_samples < 1:
        raise ValueError(f"min_samples 必须是正整数: {min_samples!r}")
    common = factor_values.index.intersection(fwd_returns.index)
    if len(common) == 0:
        empty = pd.Series(dtype="float64", name="rank_ic")
        empty.index.name = factor_values.index.name
        return empty
    common = common.sort_values()
    left = factor_values.loc[common]
    right = fwd_returns.loc[common]
    # 逐日样本 = 该日「两帧皆非 NaN」的格位（pairwise-complete）：先掩码再排名，
    # 保证两帧秩在同一子样本上计算（否则各帧 NaN 数不同会错位秩编号）
    valid = left.notna() & right.notna()
    counts = valid.sum(axis=1)
    ic = left.where(valid).rank(axis=1).corrwith(right.where(valid).rank(axis=1), axis=1)
    ic = ic.where(counts >= min_samples)
    ic.name = "rank_ic"
    return ic
