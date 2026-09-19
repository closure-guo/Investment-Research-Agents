"""族 B 编排层价值指标：B1 风险点增量、B3 出处率、B5 pairwise 盲评协议。

LLM/NLI 判定以 callable 注入（is_absorbed），使本模块可离线单测。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class GroundingParam:
    name: str
    value: float
    source_ref: str | None


def _norm(text: str) -> str:
    return "".join((text or "").split())


def risk_point_diff(prev_points: list[str], cur_points: list[str]) -> list[str]:
    """差集：cur 有而 prev 无的风险点（空白归一化后比对，保持 cur 顺序）。"""
    seen = {_norm(p) for p in prev_points}
    return [p for p in cur_points if _norm(p) not in seen]


def absorption_rate(new_points: list[str], is_absorbed: Callable[[str], bool]) -> float | None:
    """被决策吸收的新增风险点占比；无新增点返回 None（不得记为 0）。"""
    if not new_points:
        return None
    return sum(1 for p in new_points if is_absorbed(p)) / len(new_points)


def grounding_rate(params: Sequence[GroundingParam]) -> float | None:
    """B3：有出处（source_ref 非空）的执行参数占比。"""
    if not params:
        return None
    return sum(1 for p in params if p.source_ref) / len(params)


def pairwise_assign(left_id: str, right_id: str, *, pair_key: str) -> tuple[str, str]:
    """A/B 位置随机化：同一 pair_key 结果稳定（可复现），跨 key 位置分布均匀。"""
    digest = hashlib.sha256(pair_key.encode("utf-8")).digest()
    return (right_id, left_id) if digest[0] % 2 else (left_id, right_id)


def majority_verdict(votes: Sequence[str]) -> str:
    """K 次多数决；无多数（含空输入）返回 'tie'。"""
    counts: dict[str, int] = {}
    for v in votes:
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return "tie"
    top = max(counts.values())
    winners = [k for k, v in counts.items() if v == top]
    return winners[0] if len(winners) == 1 else "tie"
