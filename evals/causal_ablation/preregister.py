"""预登记门禁：无有效预登记不得跑批（spec causal-ablation「预登记与阳性对照」）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FIELDS: tuple[str, ...] = (
    "主指标",
    "MDE",
    "决策阈值",
    "样本量依据",
    "停止规则",
    "rubric 版本",
)
# outcome 收益评估门禁字段（delta add-outcome-profitability-protocol，口径 metrics.md §1.8）：
# 与因果消融的差异——无 rubric 版本（判定零 LLM），增成本分型与泄漏控制（两腿读数特有）。
OUTCOME_REQUIRED_FIELDS: tuple[str, ...] = (
    "主指标",
    "MDE",
    "决策阈值",
    "样本量依据",
    "停止规则",
    "成本分型",
    "泄漏控制",
)
_FIELD_RE = re.compile(r"^-\s*(?P<key>[^:：]+)[:：]\s*(?P<value>.+)$")
_RATIONALE_MARKERS = ("依据", "换算", "换算式")


@dataclass
class Preregistration:
    path: Path
    fields: dict[str, str]
    raw: str
    required_fields: tuple[str, ...] = REQUIRED_FIELDS

    @property
    def issues(self) -> list[str]:
        out: list[str] = []
        for field in self.required_fields:
            if not self.fields.get(field, "").strip():
                out.append(f"缺字段: {field}")
        threshold = self.fields.get("决策阈值", "")
        if threshold and not any(m in threshold for m in _RATIONALE_MARKERS):
            out.append("决策阈值为裸数字，缺换算依据（须含「依据/换算」说明）")
        return out

    @property
    def valid(self) -> bool:
        return not self.issues


class MissingPreregistrationError(RuntimeError):
    """未找到有效预登记文档，拒绝跑批。"""


def parse_preregister(
    text: str, *, required_fields: tuple[str, ...] = REQUIRED_FIELDS
) -> Preregistration:
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = _FIELD_RE.match(line.strip())
        if m:
            fields[m.group("key").strip()] = m.group("value").strip()
    return Preregistration(
        path=Path("<inline>"), fields=fields, raw=text or "", required_fields=required_fields
    )


def find_latest_preregister(
    dir_path: Path,
    *,
    name_contains: str | None = None,
    required_fields: tuple[str, ...] = REQUIRED_FIELDS,
) -> Preregistration | None:
    """最新预登记文档；`name_contains` 限定为本实验自己的文档。

    多实验共用同一目录时，只取「目录里最新」会把跑 P1 的门禁读成别家（如 P2 族 B）的
    文档——门禁必须校验**本实验**的预登记（2026-09-17 叠加 P2 后实测踩到）。
    """
    if not dir_path.exists():
        return None
    candidates = sorted(dir_path.glob("*.md"))
    if name_contains:
        candidates = [c for c in candidates if name_contains in c.name]
    if not candidates:
        return None
    latest = candidates[-1]
    parsed = parse_preregister(latest.read_text(encoding="utf-8"), required_fields=required_fields)
    parsed.path = latest
    return parsed


def assert_preregistered(
    dir_path: Path,
    *,
    name_contains: str | None = None,
    required_fields: tuple[str, ...] = REQUIRED_FIELDS,
) -> Preregistration:
    found = find_latest_preregister(
        dir_path, name_contains=name_contains, required_fields=required_fields
    )
    if found is None:
        raise MissingPreregistrationError(f"未找到预登记文档（{dir_path}）：无预登记不得跑批")
    if not found.valid:
        raise MissingPreregistrationError(
            f"预登记文档无效（{found.path}）：{'; '.join(found.issues)}"
        )
    return found
