"""含退市标的的池可见性（delta add-microcap-lane-guardrails T3，防幸存者偏差）。

口径（docs/evals/metrics.md §1.16，binding；实施者不得自行改）：

- **时点可见性**：对时点 ``t``，标的可见当且仅当 ``list_date ≤ t ≤ delist_date``
  （含边界）。这是「含退市股池」的全部判据——池构建不得以「不在当前上市名单」剔除
  历史标的（幸存者偏差，2026-10-15 对标备忘录 ❌ 项）。
- **缺失不编造、不静默剔除**：``delist_date`` 缺失/不可解析 → 按「未退市」处理
  （上界不约束）；``list_date`` 缺失/不可解析 → 下界不约束。两处缺失都不作为剔除理由
  （与 spec「无价格历史的退市标的 SHALL 如实标注缺失，MUST NOT 静默剔除」同纪律）；
  已知退市但日期缺失的标的由调用方按「如实标注」披露（沪源语义见 §1.16）。
- **主表无该 code**：无退市信息 → 可见（不为缺数据惩罚标的；上游如构建退市池则另行登记）。
- **t 严格规范** ``YYYY-MM-DD``：非规范/不可解析 → ``SnapshotError``（与
  ``data/snapshot.py`` 日期入参同纪律——非规范形态不参与字典序比较）。
- 本模块**只判时点窗口**，与「当日有无行情行」解耦：退市标的有历史行即可入池，
  无历史行由补采通道如实标注 ``history_missing``，不得据此剔除。
"""

from __future__ import annotations

from datetime import date

from finance_agent.data.snapshot import _parse_date_param


def _date_or_none(value: object) -> str | None:
    """主表日期值 → 规范 ``YYYY-MM-DD``；缺失/不可解析/非规范 → None（视为无约束）。

    只接受 ``date.fromisoformat`` 可解析且 ``isoformat()`` 回写一致的形态——外部数据
    （交易所清单）里的变体（``2020/06/30``、``"20200630"``）一律按缺失处理，不让非规范
    形态在字典序比较中静默漂移。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return text if parsed.isoformat() == text else None


def universe_visible(master_row: dict | None, t: str) -> bool:
    """``t`` 时点该标的是否可见（``list_date ≤ t ≤ delist_date``；缺失字段不约束）。

    ``master_row`` = ``get_security_master()`` 的单行（``{"name","list_date","delist_date"}``）
    或 None（主表无该 code → 可见）。``t`` 非规范日期 → ``SnapshotError``（fail loud，
    不静默判可见/不可见）。
    """
    _parse_date_param(t, param="t")
    row = master_row or {}
    list_date = _date_or_none(row.get("list_date"))
    delist_date = _date_or_none(row.get("delist_date"))
    if list_date is not None and t < list_date:
        return False
    return not (delist_date is not None and t > delist_date)


def filter_snapshot_rows_for_date(rows: list[dict], master: dict[str, dict], t: str) -> list[dict]:
    """``t`` 当日快照行按主表上市—退市窗口过滤（含退市标的；保持输入顺序）。

    ``rows`` = 该日快照行（``read_universe`` 输出形状，消费 ``code``）；``master`` =
    ``get_security_master()`` 输出。主表无该 code 或行 ``code`` 缺失 → 保留（不静默剔除，
    见模块 docstring）；``t`` 非规范 → ``SnapshotError``（与 ``universe_visible`` 同源）。
    """
    return [
        row for row in rows if universe_visible(master.get(str(row.get("code") or "").strip()), t)
    ]
