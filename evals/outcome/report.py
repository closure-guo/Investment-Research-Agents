"""Outcome 收口报告契约：生命周期 status 头 + 取代者指针可解析。"""

from __future__ import annotations

from pathlib import Path

from evals.causal_ablation.report_status import parse_status


def assert_outcome_report(text: str, *, root: Path | None = None) -> tuple[str, str | None]:
    """校验报告 status 头；superseded-by 指针在给定 root 时按 root 相对解析并须真实存在。"""
    status, target = parse_status(text)
    if status == "superseded-by":
        if not target:
            raise ValueError("superseded-by 必须带目标路径（取代者报告）")
        if root is not None and not (root / target).exists():
            raise ValueError(f"取代者指针不存在：{target}")
    return status, target
