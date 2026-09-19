"""B5 手术版校准对的「两版实际差异速览」（零 LLM）。

机器理由是评审的一句话感想，太空泛；手术版与 full 的差异是确定性可算的：
- 决策段逐字段 diff（方向/置信度/仓位/止损…由「- **字段**: 值」行解析）
- full 独有章节（风控辩论/基金经理决策）+ FM 裁决首句

用法：uv run python tests/scripts/p2_b5_pair_diff.py --tickers 601012 600276 002027 601888
输出 JSON 到 stdout（供写回预填表「两版实际差异速览」列）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_materials as fb  # noqa: E402

MATERIALS_DIR = _ROOT / "reports/ablation/p2/materials"
ARM_A = "full_no_riskfm"
ARM_B = "full"


def _section(text: str, start: str, end: str | None = None) -> str:
    pat = rf"{re.escape(start)}(.*?)" + (rf"(?={re.escape(end)})" if end else r"\Z")
    m = re.search(pat, text, re.S)
    return m.group(1).strip() if m else ""


def _fields(decision_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"^- \*\*(.+?)\*\*[：:]\s*(.*)$", decision_text, re.M):
        out[m.group(1).strip()] = m.group(2).strip()
    return out


def pair_diff(ticker: str) -> dict:
    a = str(fb.load_material(MATERIALS_DIR, ticker, ARM_A)["state"].get("final_report") or "")
    b = str(fb.load_material(MATERIALS_DIR, ticker, ARM_B)["state"].get("final_report") or "")
    fa = _fields(_section(a, "## 四、交易决策"))
    fbk = _fields(_section(b, "## 四、交易决策", "## 五、风控辩论"))
    changed = {
        k: {"手术版(trader原方案)": fa.get(k, "（无）"), "full(FM裁决)": fbk.get(k, "（无）")}
        for k in dict.fromkeys([*fa, *fbk])
        if fa.get(k) != fbk.get(k)
    }
    # 理由字段太长，压成头 80 字
    if "理由" in changed:
        for side in ("手术版(trader原方案)", "full(FM裁决)"):
            changed["理由"][side] = changed["理由"][side][:80] + "…"
    fm = _section(b, "## 六、基金经理决策")
    fm_first = re.search(r"\*\*(.+?)\*\*", fm)
    return {
        "ticker": ticker,
        "决策段字段差异": changed,
        "full独有章节": "风控辩论（三方两轮）+ 基金经理决策",
        "FM裁决首句": (fm_first.group(1)[:60] if fm_first else ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", required=True)
    args = ap.parse_args()
    print(json.dumps([pair_diff(t) for t in args.tickers], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
