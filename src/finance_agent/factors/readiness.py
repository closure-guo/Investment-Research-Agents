"""因子 IC 就绪度（delta add-factor-ic-visibility）。

回答两个用户问题：**这个因子攒了几条 IC、离打分门槛还差几条、预计哪天恢复因子打分**。

口径（与 ``screening.ranker`` 的打分门槛同源，阈值由调用方传入，不在本模块复制常量）：

- 每因子每交易日最多新增 1 条 IC（t 日因子值 × t→t+1 收益，t+1 收盘后落库）；
- 最新一条 IC 的日期记为 ``latest_ic_date``（它是在其后第一个交易日 15:45 写入的）；
- 还需 ``needed = min_samples - samples`` 条 → 第 ``needed`` 条在 ``latest_ic_date`` 之后
  第 ``needed`` 个交易日收盘后写入；15:40 的选股批次要等**下一个**交易日才看到，
  故预计日 = 从 ``latest_ic_date`` 起前进 ``needed + 2`` 个工作日；
- 仅跳过周六/周日，**不含法定节假日**（调用方必须在文案中标注该口径）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# 近似交易日：周一至周五（不含法定节假日）。命名即口径，避免调用方误读为交易日历。
_WEEKDAY_COUNT = 5


def _next_weekday(current: date) -> date:
    candidate = current + timedelta(days=1)
    while candidate.weekday() >= _WEEKDAY_COUNT:
        candidate += timedelta(days=1)
    return candidate


def add_weekdays(start: date, count: int) -> date:
    """从 ``start`` 起前进 ``count`` 个工作日（不含 ``start`` 当天）。"""
    if count < 0:
        raise ValueError(f"count 必须是非负整数: {count!r}")
    current = start
    for _ in range(count):
        current = _next_weekday(current)
    return current


def expected_factor_mode_date(
    latest_ic_date: str | None, samples: int, min_samples: int
) -> str | None:
    """预计「该因子可参与打分」的首个选股批次日期；不可推算时返回 ``None``。

    - 样本已达门槛 → ``None``（已就绪，无需日期）；
    - 无任何 IC 记录 / 日期缺失 → ``None``（不臆造）；
    - 否则按工作日推算并返回 ``YYYY-MM-DD``。
    """
    if min_samples < 1:
        raise ValueError(f"min_samples 必须是正整数: {min_samples!r}")
    if samples < 0:
        raise ValueError(f"samples 必须是非负整数: {samples!r}")
    if latest_ic_date is None or samples >= min_samples:
        return None
    needed = min_samples - samples
    return add_weekdays(date.fromisoformat(latest_ic_date), needed + 2).isoformat()


def factor_signal_payload(
    factors: list[dict[str, Any]],
    ic_samples: dict[int, int],
    latest_ic_dates: dict[int, str],
    *,
    selection_mode: str | None,
    min_samples: int,
) -> dict[str, Any]:
    """池接口的 ``factor_signal`` 块（逐 active 因子进度 + 批次级最早恢复日）。

    ``factors`` 由调用方先过滤为 active 并按 id 升序；``ic_samples`` 缺失即 0 条，
    ``latest_ic_dates`` 缺失即不可推算（日期 ``None``，不臆造）。
    """
    rows: list[dict[str, Any]] = []
    expected_dates: list[str] = []
    for row in factors:
        factor_id = int(row["id"])
        samples = int(ic_samples.get(factor_id, 0))
        latest = latest_ic_dates.get(factor_id)
        expected = expected_factor_mode_date(latest, samples, min_samples)
        if expected is not None:
            expected_dates.append(expected)
        rows.append(
            {
                "id": factor_id,
                "ic_samples": samples,
                "eligible": samples >= min_samples,
                "expected_eligible_date": expected,
            }
        )
    if not rows:
        reason = "无 active 因子"
    elif all(item["eligible"] for item in rows):
        reason = "全部 active 因子 IC 样本达标"
    else:
        reason = f"IC 样本不足（门槛 {min_samples}）"
    return {
        "selection_mode": selection_mode,
        "reason": reason,
        "min_samples": min_samples,
        "expected_factor_mode_date": min(expected_dates) if expected_dates else None,
        "factors": rows,
    }
