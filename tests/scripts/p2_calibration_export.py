"""P2 校准材料导出（零 LLM）：把判定缓存重建为**可人工标注**的 CSV。

**为什么重建**：首版 `calibration_rows` 只导了单列材料（B1 只有风险点、B2 只有反驳行文），
而判定依据（决策理由/证据引用/FM 裁决、被反驳的论点）没进表 → 人工无从复核。
本脚本从 `reports/ablation/p2/judged-*.jsonl`（判定缓存，含判定时的完整输入）+ 材料重建，
**只读缓存不调模型**。

**人工列保留**：重跑本脚本不得覆盖已填的人工判定（按 unit_id 回读，同 `adjudication.py` 的
「重生成材料不得覆盖人工终裁」纪律）。

列语义（人工只看这几列）：
- B1：`风险点` + `决策动作` / `决策理由` / `证据引用` / `FM裁决` → 判「该风险点是否被决策吸收」；
  另附 `分析师发现摘要` 供判「是否新增（分析师没提过）」。
- B2：`被反驳论点` + `反驳行文` → 判「反驳是否修正了原论点」。

用法：
    uv run python tests/scripts/p2_calibration_export.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_materials as fb  # noqa: E402
from evals.causal_ablation import family_b_text as ft  # noqa: E402

JUDGED_DIR = Path("reports/ablation/p2")
MATERIALS_DIR = Path("reports/ablation/p2/materials")
OUT_DIR = Path("tests/validation")
TICKERS: tuple[str, ...] = (
    "600519",
    "000001",
    "002415",
    "300750",
    "601318",
    "002594",
    "600036",
    "000858",
    "601899",
    "002304",
    "601398",
    "600030",
    "000333",
    "002352",
    "601012",
    "600887",
    "000651",
    "600276",
    "601888",
    "002027",
)

B1_COLUMNS = (
    "unit_id",
    "ticker",
    "风险点",
    "角色/轮次",
    "原话上下文(解歧义用：该轮正文节选)",
    "分析师发现·最相似3条(附相似度,判「是否新增」用)",
    "分析师发现总条数",
    "决策动作",
    "决策理由",
    "证据引用",
    "FM裁决",
    "机器判定(被吸收?是/否)",
    "机器理由",
    "人工判定(被吸收?是/否)",
    "人工判定(是否新增?是/否)",
)
B2_COLUMNS = (
    "unit_id",
    "ticker",
    "被反驳论点",
    "反驳行文",
    "机器判定(被修正?是/否)",
    "机器理由",
    "人工判定(被修正?是/否)",
)
B5_COLUMNS = (
    "unit_id",
    "ticker",
    "展示顺序",
    "机器票型(3票)",
    "机器判定(胜方)",
    "人工判定(与机器一致?是/否)",
    "备注(B5 口径待重设计，本表可暂缓)",
)
_HUMAN_PREFIX = "人工判定"


def _current_material_ids(kind: str, tickers: Sequence[str]) -> set[str]:
    """当前材料重建出的判定行 id 集合（与读数用的行集一致）。"""
    states = {t: fb.load_material(MATERIALS_DIR, t)["state"] for t in tickers}
    if kind == "b1":
        rows = ft.judge_material_rows(
            [{"ticker": t, "_state": states[t], "b1": ft.b1_unit(states[t])} for t in tickers]
        )
    else:
        rows = ft.b2_material_rows([{"ticker": t, "_state": states[t]} for t in tickers])
    return {str(r.get("unit_id") or "") for r in rows}


def _load_judged(kind: str) -> list[dict]:
    path = JUDGED_DIR / f"judged-{kind}.jsonl"
    if not path.exists():
        raise SystemExit(f"缺判定缓存 {path.as_posix()}（先跑 p2_family_b_analysis.py）")
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _read_existing(path: Path, columns: Sequence[str]) -> dict[str, dict]:
    """已有的人工判定（按 unit_id 回读，重跑不覆盖）。"""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            kept = {
                col: row.get(col, "")
                for col in columns
                if col.startswith(_HUMAN_PREFIX) and str(row.get(col, "")).strip()
            }
            if kept:
                out[str(row.get("unit_id") or "")] = kept
    return out


def _write(path: Path, rows: Sequence[dict], columns: Sequence[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def _clip(text: object, limit: int = 600) -> str:
    value = str(text or "").replace("\n", " ")
    return value[:limit] + ("…" if len(value) > limit else "")


def _refs_text(refs: object) -> str:
    if not isinstance(refs, list):
        return ""
    return "；".join(
        f"{(_r or {}).get('claim', '')}（{(_r or {}).get('source', '')}）"
        for _r in refs
        if isinstance(_r, dict)
    )


def _context_lookup(state: dict) -> dict[str, dict]:
    """空方论点文本 → {角色/轮次, 该轮正文节选}（论点脱离上下文会歧义，如「不改变趋势」指哪个方向）。"""
    out: dict[str, dict] = {}
    for message in state.get("debate_history") or []:
        data = message.model_dump() if hasattr(message, "model_dump") else message
        excerpt = _clip(data.get("content"), 320)
        for argument in data.get("key_arguments") or []:
            arg = argument.model_dump() if hasattr(argument, "model_dump") else argument
            text = str(arg.get("text") or "")
            if text:
                out[text] = {
                    "角色/轮次": f"{data.get('role')} r{data.get('round')}",
                    "原话上下文(解歧义用：该轮正文节选)": excerpt,
                }
    return out


def _b1_rows(existing: dict[str, dict], *, tickers: Sequence[str]) -> list[dict]:
    findings_cache: dict[str, str] = {}  # 按 unit_id（同标的各风险点的 top3 不同）
    context_cache: dict[str, dict[str, dict]] = {}  # 按 ticker
    keep = _current_material_ids("b1", tickers)
    rows: list[dict] = []
    for row in _load_judged("b1"):
        if str(row.get("unit_id") or "") not in keep:
            continue
        ticker = str(row.get("ticker") or "")
        cache_key = str(row.get("unit_id") or ticker)
        if ticker not in context_cache:
            context_cache[ticker] = _context_lookup(
                fb.load_material(MATERIALS_DIR, ticker)["state"]
            )
        if cache_key not in findings_cache:
            state = fb.load_material(MATERIALS_DIR, ticker)["state"]
            refs = ft.analyst_reference_points(state)
            point = str(row.get("risk_point") or "")
            # 按与风险点的字面相似度排序取前 3（附相似度）：原来截断前 6 条会把真正相关的那条切掉
            ranked = sorted(((ft.text_similarity(point, ref), ref) for ref in refs), reverse=True)[
                :3
            ]
            top = " ｜ ".join(f"[{sim:.2f}] {_clip(ref, 80)}" for sim, ref in ranked)
            findings_cache[cache_key] = {"top": top, "total": len(refs)}
        item = {
            "unit_id": row.get("unit_id"),
            "ticker": ticker,
            "风险点": _clip(row.get("risk_point")),
            **context_cache[ticker].get(str(row.get("risk_point") or ""), {}),
            "分析师发现·最相似3条(附相似度,判「是否新增」用)": findings_cache[cache_key]["top"],
            "分析师发现总条数": findings_cache[cache_key]["total"],
            "决策动作": row.get("decision_action"),
            "决策理由": _clip(row.get("decision_reasoning"), 700),
            "证据引用": _clip(_refs_text(row.get("evidence_refs")), 300),
            "FM裁决": f"{row.get('fm_decision')}：{_clip(row.get('fm_reasoning'), 200)}",
            "机器判定(被吸收?是/否)": "是"
            if row.get("judge_label") is True
            else "否"
            if row.get("judge_label") is not None
            else "解析失败",
            "机器理由": _clip(row.get("judge_reason"), 300),
        }
        item.update(existing.get(str(row.get("unit_id")), {}))
        rows.append(item)
    return rows


def _b2_rows(existing: dict[str, dict], *, tickers: Sequence[str]) -> list[dict]:
    rows: list[dict] = []
    keep = _current_material_ids("b2", tickers)
    for row in _load_judged("b2"):
        if str(row.get("unit_id") or "") not in keep:
            continue
        item = {
            "unit_id": row.get("unit_id"),
            "ticker": row.get("ticker"),
            "被反驳论点": _clip(row.get("rebutted_point")),
            "反驳行文": _clip(row.get("rebuttal_text")),
            "机器判定(被修正?是/否)": "是"
            if row.get("judge_label") is True
            else "否"
            if row.get("judge_label") is not None
            else "解析失败",
            "机器理由": _clip(row.get("judge_reason"), 300),
        }
        item.update(existing.get(str(row.get("unit_id")), {}))
        rows.append(item)
    return rows


def _b5_rows(existing: dict[str, dict], *, tickers: Sequence[str]) -> list[dict]:
    rows: list[dict] = []
    for row in _load_judged("b5"):
        item = {
            "unit_id": row.get("unit_id"),
            "ticker": row.get("ticker"),
            "展示顺序": f"{row.get('position_first')} → {row.get('position_second')}",
            "机器票型(3票)": "｜".join(row.get("votes") or []),
            "机器判定(胜方)": row.get("verdict"),
            "备注(B5 口径待重设计，本表可暂缓)": "",
        }
        item.update(existing.get(str(row.get("unit_id")), {}))
        rows.append(item)
    return rows


def main() -> None:
    args = argparse.ArgumentParser(description="P2 校准材料导出（零 LLM）")
    args.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args.add_argument("--tickers", nargs="+", default=list(TICKERS))
    args.add_argument(
        "--fraction", type=float, default=0.20, help="抽样比例（spec 要求 ≥20%% 人工复核）"
    )
    args.add_argument("--all", action="store_true", help="导全量（默认抽 20%%）")
    ns = args.parse_args()
    out = Path(ns.out_dir)
    targets = (
        ("b1", B1_COLUMNS, _b1_rows),
        ("b2", B2_COLUMNS, _b2_rows),
        ("b5", B5_COLUMNS, _b5_rows),
    )
    locked: list[str] = []
    for kind, columns, builder in targets:
        path = out / f"2026-09-17-p2-calibration-{kind}.csv"
        existing = _read_existing(path, columns)
        rows = builder(existing, tickers=list(ns.tickers))
        total = len(rows)
        if not ns.all:
            rows = ft.calibration_sample(rows, fraction=ns.fraction)
        try:
            count = _write(path, rows, columns)
        except PermissionError:
            # 文件被占用（Excel 打开中）：不静默失败、也不硬写坏别人的表
            print(
                f"[拒绝写入] {path.name} 被占用（Excel？）：请关闭后重跑本脚本；"
                "已填的人工判定不会丢（按 unit_id 回读）",
                file=sys.stderr,
            )
            locked.append(path.name)
            continue
        kept = len(existing)
        print(
            f"[导出] {path.as_posix()}：{count}/{total} 行"
            f"（抽样 {ns.fraction:.0%}{'（全量）' if ns.all else ''}；保留已填人工判定 {kept} 行）"
        )
    if locked:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
