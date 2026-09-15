"""结论注册表状态解析：报告结论有生命周期（spec causal-ablation「结论注册表生命周期」）。"""

from __future__ import annotations

import re

STATUSES: tuple[str, ...] = ("active", "superseded-by")
# 行内间隙一律用 [ \t]（不得用 \s）——`\s` 会跨行，使本行无路径的
# `**status**: superseded-by` 把下一行首个 token（如 `**日期**:`）当 target，
# 从而绕过「superseded-by 必须带目标路径」校验（G7/⑤a 修复）。
_STATUS_RE = re.compile(r"\*\*status\*\*:[ \t]*(?P<status>[A-Za-z-]+)[ \t]*:?[ \t]*(?P<target>\S*)")


def parse_status(text: str) -> tuple[str, str | None]:
    m = _STATUS_RE.search(text or "")
    if not m:
        raise ValueError("报告缺 status 头（期望 `**status**: active | superseded-by: <path>`）")
    status = m.group("status")
    if status not in STATUSES:
        raise ValueError(f"未知 status: {status!r}（须为 {STATUSES}）")
    target = m.group("target") or None
    return status, target


def assert_status_valid(text: str) -> None:
    status, target = parse_status(text)
    if status == "superseded-by" and not target:
        raise ValueError("superseded-by 必须带目标路径（取代者报告）")


def status_badge(status: str, target: str | None = None) -> str:
    if status == "active":
        return "![active](badge:active)"
    return f"![superseded](badge:superseded) → {target or '<缺目标>'}"
