"""闸门操作特性标定（真实面板注入）：已知强度因子族过真实五道闸（验证钉子 1）。

用途：把闸②阈值 0.3（文档背书值）变成**实测标定**——输出两条曲线：

- **功率曲线**：在真实准入面板（150 日）上注入不同信噪比的因子（信号 = 次日远期收益
  的横截面秩 + 噪声），实测其达成的 ICIR 与五道闸通过率/拒绝点；
- **H0 通过率**：纯噪声因子（多随机种子）经五道闸的误纳率（应 ≈ 0）。

方法：把面板 ``volume`` 帧替换为注入值（表达式 ``volume`` 为合法 DSL），经真实
``admit_candidates``（隔离临时库，零污染）逐条过闸；IC 由我们的 ``rank_ic_series``
实测（该实现已与独立 oracle 互证 1e-16）。

运行::

    uv run python tests/scripts/gate_calibration_injection.py --out reports/gate-calibration-20261017.json
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.monthly import _panel_from_snapshot
from finance_agent.factors.validate import admit_candidates

_HYPOTHESIS = "标定注入因子：信号强度受控的合成因子（验证钉子 1，非生产候选，≥30 字达标说明）"


def inject(
    base: MarketPanel, rng: np.random.Generator, noise_scale: float
) -> tuple[MarketPanel, dict]:
    """把 volume 帧替换为 秩(fwd) + noise_scale×噪声；返回（新面板, 实测统计）。"""
    close = base.fields["close"]
    fwd = forward_returns(close)  # h=1（闸门口径）
    fwd_rank = fwd.rank(axis=1, pct=True)
    noise = pd.DataFrame(rng.standard_normal(close.shape), index=close.index, columns=close.columns)
    values = fwd_rank + noise_scale * noise
    fields = dict(base.fields)
    fields["volume"] = values
    panel = MarketPanel(fields=fields)
    ic = rank_ic_series(evaluate(parse("volume"), panel), fwd)
    valid = ic.dropna()
    stats = {
        "ic_mean": float(valid.mean()) if len(valid) else float("nan"),
        "icir": float(valid.mean() / valid.std(ddof=1))
        if len(valid) > 1 and float(valid.max()) != float(valid.min())
        else 0.0,
        "ic_days": int(valid.size),
    }
    return panel, stats


def run_gates(panel: MarketPanel, *, seed: int, db_path: Path) -> dict[str, Any]:
    """真实 admit_candidates 过五道闸（隔离库）；返回裁决摘要。"""
    results = admit_candidates(
        [{"expression": "volume", "hypothesis": _HYPOTHESIS, "category": "calibration"}],
        panel,
        seed=seed,
        db_path=db_path,
    )
    result = results[0]
    detail = getattr(result, "detail", None) or {}
    return {
        "admitted": result.factor_id is not None,
        "rejected_gate": result.rejected_gate,
        "detail_keys": sorted(detail)[:6],
        "detail": {
            key: (float(value) if isinstance(value, (int, float)) else str(value)[:60])
            for key, value in list(detail.items())[:8]
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="闸门操作特性标定（真实面板注入）")
    parser.add_argument("--out", default=None, help="JSON 产物输出路径")
    parser.add_argument("--grid-seeds", type=int, default=3, help="每个噪声档位的随机种子数")
    parser.add_argument("--h0-runs", type=int, default=20, help="纯噪声 H0 重复次数")
    args = parser.parse_args(argv)

    base = _panel_from_snapshot(None)
    print(
        f"准入面板：{len(base.fields['close'].index)} 日 × {len(base.fields['close'].columns)} 票"
    )

    # 噪声档位：0 = 纯信号（ICIR 上界）→ 大噪声 = 近纯噪声
    grid = [0.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 256.0]
    runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="gate-calib-") as tmp:
        # 每次运行独立临时库：同表达式候选若共用库，先入库者会让后续运行撞
        # 闸 0 结构查重（STRUCTURAL_DUPLICATE）——运行必须相互独立
        for scale in grid:
            for seed_index in range(args.grid_seeds):
                rng = np.random.default_rng(1000 + seed_index)
                panel, stats = inject(base, rng, scale)
                outcome = run_gates(
                    panel, seed=42, db_path=Path(tmp) / f"grid-{scale}-{seed_index}.db"
                )
                row = {"kind": "grid", "noise_scale": scale, "seed": seed_index, **stats, **outcome}
                runs.append(row)
                print(
                    f"scale={scale:>6} seed={seed_index} | ICIR={stats['icir']:>7.3f} "
                    f"| admitted={outcome['admitted']} gate={outcome['rejected_gate']}"
                )
        print("--- H0（纯噪声）---")
        h0_pass = 0
        for seed_index in range(args.h0_runs):
            rng = np.random.default_rng(50_000 + seed_index)
            panel, stats = inject(base, rng, 10_000.0)
            outcome = run_gates(panel, seed=42, db_path=Path(tmp) / f"h0-{seed_index}.db")
            h0_pass += int(outcome["admitted"])
            runs.append(
                {"kind": "h0", "noise_scale": 10_000.0, "seed": seed_index, **stats, **outcome}
            )
        print(f"H0: {h0_pass}/{args.h0_runs} 误纳")

    artifact = {"panel_days": len(base.fields["close"].index), "runs": runs}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(artifact, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"产物：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
