"""B1 校准辅助：风险点 × 决策证据引用 对照表（零 LLM）。

**为什么需要**：校准表里「证据引用」列是全量 refs 拼接后 300 字整体截断，而
辩论侧标签（debate_*/risk_*/research_manager）的条目排在末尾——全部 196 行都被截，
人工在表里看不到「决策到底引用了辩论层的哪些话」，无法按通道判「被吸收」。

本表**与校准主表同行、同序**（默认只出你要标的那 40 行，按 unit_id 对齐），
每行列出决策的完整证据引用里与该风险点最像的两条：相似度 / 来源侧 / 原文。

**看法**（吸收列的通道分账）：
- 来源侧 = 分析师(四个标签之一)：只说明决策用了分析师的信息，不算辩论的功劳
- 来源侧 = 辩论侧(debate_*/risk_*/research_manager)：内容只能来自辩论层，算辩论进了决策
- 你判了「新增=是」的行：看新增那截有没有出现在辩论侧引用（或决策理由正文）里——有→被吸收=是，
  没有→否；若新增截出现在分析师侧引用里，说明「新增」判错了，翻成否

用法：
    uv run python tests/scripts/p2_calibration_refs_scan.py          # 与主表对齐的 40 行
    uv run python tests/scripts/p2_calibration_refs_scan.py --all    # 全部 196 行（分析用）
产物：tests/validation/2026-09-17-p2-calibration-b1-refs.csv（--all 时为 -refs-all.csv）
Excel 占用时 [拒绝写入] 退出 3。
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
MAIN_TABLE = _ROOT / "tests/validation/2026-09-17-p2-calibration-b1.csv"
OUT_ALIGNED = _ROOT / "tests/validation/2026-09-17-p2-calibration-b1-refs.csv"
OUT_ALL = _ROOT / "tests/validation/2026-09-17-p2-calibration-b1-refs-all.csv"

ANALYST_SOURCES = {"fundamental", "technical", "macro", "sentiment"}
COLUMNS = (
    "unit_id",
    "风险点(前60字)",
    "最像的引用1·相似度",
    "最像的引用1·来源侧",
    "最像的引用1·原文",
    "最像的引用2·相似度",
    "最像的引用2·来源侧",
    "最像的引用2·原文",
    "决策共引用辩论侧几条",
)


def _side(source: object) -> str:
    """分析师侧 / 辩论侧——后者内容只能起源于辩论层。"""
    s = str(source or "").strip()
    if s.lower() in ANALYST_SOURCES:
        return f"分析师({s})"
    return f"辩论侧({s})" if s else "辩论侧(无标签)"


def _clip(text: object, limit: int) -> str:
    value = str(text or "").replace("\n", " ")
    return value[:limit] + ("…" if len(value) > limit else "")


def _row(judged: dict) -> dict:
    refs = [
        r
        for r in (judged.get("evidence_refs") or [])
        if isinstance(r, dict) and str(r.get("claim") or "").strip()
    ]
    point = str(judged.get("risk_point") or "")
    ranked = sorted(
        ((ft.text_similarity(point, str(r.get("claim") or "")), i, r) for i, r in enumerate(refs)),
        key=lambda x: (x[0], -x[1]),
        reverse=True,
    )
    item: dict[str, object] = {
        "unit_id": judged.get("unit_id"),
        "风险点(前60字)": _clip(point, 60),
    }
    for k, (sim, _i, ref) in enumerate(ranked[:2], start=1):
        item[f"最像的引用{k}·相似度"] = f"{sim:.2f}"
        item[f"最像的引用{k}·来源侧"] = _side(ref.get("source"))
        item[f"最像的引用{k}·原文"] = _clip(ref.get("claim"), 120)
    item["决策共引用辩论侧几条"] = sum(
        1 for r in refs if not _side(r.get("source")).startswith("分析师")
    )
    return item


def scan(*, all_rows: bool) -> int:
    judged = {
        str(j.get("unit_id") or ""): j
        for j in (
            json.loads(x) for x in JUDGED_B1.read_text(encoding="utf-8").splitlines() if x.strip()
        )
    }
    if all_rows:
        order = list(judged)
        out_path = OUT_ALL
    else:
        with MAIN_TABLE.open(encoding="utf-8-sig", newline="") as fh:
            order = [str(r.get("unit_id") or "") for r in csv.DictReader(fh)]
        out_path = OUT_ALIGNED
    missing = [u for u in order if u not in judged]
    if missing:
        print(f"[警告] 主表有 {len(missing)} 行不在判定缓存里，已跳过：{missing[:5]}")
    rows = [_row(judged[u]) for u in order if u in judged]
    try:
        with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(COLUMNS), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError:
        print(f"[拒绝写入] {out_path}（Excel 占用？关掉再跑）")
        return 3
    print(f"{len(rows)} 行 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(scan(all_rows="--all" in sys.argv))
