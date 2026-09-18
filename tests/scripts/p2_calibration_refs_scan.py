"""B1 校准辅助：风险点 × 决策证据引用 命中扫描（零 LLM）。

**为什么需要**：校准表里「证据引用」列是全量 refs 拼接后 300 字截断的，而
debate/risk 标签的条目排在末尾——肉眼在表里查「决策有没有引用辩论层」天然不可靠。
本脚本对每行 B1 判定行，把风险点与**完整** evidence_refs 逐条做字面相似度，
导出 top2 命中（含通道标签），供人工判「被吸收」时核对。

**通道分账规则**（与人工口径一致）：
- analyst = fundamental/technical/macro/sentiment（四分析师）
- 其余标签（debate_*/risk_*/research_manager…）= 辩论下游通道：内容只能起源于辩论，
  增量经这些通道进决策即算吸收
- 新增行的「新增截」不可能出现在 analyst 标签条目里；若发现，说明「是否新增」判错了，
  应翻成否（自洽校验）。

用法：
    uv run python tests/scripts/p2_calibration_refs_scan.py
产物：tests/validation/2026-09-17-p2-calibration-b1-refs.csv（全部 196 行；Excel 占用时 [拒绝写入] 退出 3）
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_text as ft  # noqa: E402

JUDGED_B1 = _ROOT / "reports/ablation/p2/judged-b1.jsonl"
OUT_PATH = _ROOT / "tests/validation/2026-09-17-p2-calibration-b1-refs.csv"

ANALYST_SOURCES = {"fundamental", "technical", "macro", "sentiment"}
COLUMNS = (
    "unit_id",
    "风险点(截80)",
    "表内引用是否被截断",
    "命中1·相似度",
    "命中1·通道",
    "命中1·条目(截100)",
    "命中2·相似度",
    "命中2·通道",
    "命中2·条目(截100)",
    "非分析师通道条目数",
)


def _channel(source: str) -> str:
    """analyst（四分析师）或 非分析师(具体标签)——后者内容只能起源于辩论层。"""
    s = str(source or "").strip().lower()
    if s in ANALYST_SOURCES:
        return "analyst"
    return f"非分析师({s})" if s else "非分析师(无标签)"


def _clip(text: object, limit: int) -> str:
    value = str(text or "").replace("\n", " ")
    return value[:limit] + ("…" if len(value) > limit else "")


def scan() -> int:
    judged = [
        json.loads(x) for x in JUDGED_B1.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    out: list[dict] = []
    for row in judged:
        refs = [r for r in (row.get("evidence_refs") or []) if isinstance(r, dict)]
        full_len = sum(
            len(str(r.get("claim") or "")) + len(str(r.get("source") or "")) + 4 for r in refs
        )
        ranked = sorted(
            (
                (
                    ft.text_similarity(str(row.get("risk_point") or ""), str(r.get("claim") or "")),
                    i,
                    r,
                )
                for i, r in enumerate(refs)
            ),
            reverse=True,
        )
        item: dict[str, object] = {
            "unit_id": row.get("unit_id"),
            "风险点(截80)": _clip(row.get("risk_point"), 80),
            "表内引用是否被截断": "是" if full_len > 300 else "否",
        }
        for k, (sim, _i, ref) in enumerate(ranked[:2], start=1):
            item[f"命中{k}·相似度"] = f"{sim:.2f}"
            item[f"命中{k}·通道"] = _channel(ref.get("source"))
            item[f"命中{k}·条目(截100)"] = f"{_clip(ref.get('claim'), 100)}（{ref.get('source')}）"
        item["非分析师通道条目数"] = sum(1 for r in refs if _channel(r.get("source")) != "analyst")
        out.append(item)
    try:
        with OUT_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(out)
    except PermissionError:
        print(f"[拒绝写入] {OUT_PATH}（Excel 占用？关掉再跑）")
        return 3
    print(f"{len(out)} 行 → {OUT_PATH.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(scan())
