"""单元级判定记录：claim / 风险点 / 执行参数 / 价位（spec causal-ablation）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

UNIT_TYPES: tuple[str, ...] = ("claim", "risk_point", "exec_param", "price_level")
VALID_METHODS: tuple[str, ...] = ("code", "nli", "judge")


@dataclass(frozen=True)
class UnitJudgment:
    unit_id: str
    ticker: str
    run: str
    variant: str
    unit_type: str
    judgment: str
    method: str
    confidence: float

    def __post_init__(self) -> None:
        if self.method not in VALID_METHODS:
            raise ValueError(f"method 非法: {self.method!r}（须为 {VALID_METHODS}）")
        if self.unit_type not in UNIT_TYPES:
            raise ValueError(f"unit_type 非法: {self.unit_type!r}（须为 {UNIT_TYPES}）")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 越界: {self.confidence}")
        if not self.judgment:
            raise ValueError("judgment 不得为空")


def write_units(path: Path, units: list[UnitJudgment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(asdict(u), ensure_ascii=False) for u in units]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_units(path: Path) -> list[UnitJudgment]:
    if not path.exists():
        return []
    out: list[UnitJudgment] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(UnitJudgment(**json.loads(line)))
    return out


def incomplete_reasons(units: list[UnitJudgment]) -> list[str]:
    reasons: list[str] = []
    for u in units:
        for field in ("unit_id", "ticker", "run", "variant", "unit_type", "judgment"):
            if not getattr(u, field):
                reasons.append(f"{u.unit_id or '<无 id>'}: 缺字段 {field}")
    return reasons
