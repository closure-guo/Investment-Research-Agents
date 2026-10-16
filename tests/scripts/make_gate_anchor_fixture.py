"""回归锚点夹具生成（验证钉子 3；一次性运行，产物入库）。

从**真实准入面板**（150 日 × 全市场）确定性抽取子集（按代码升序前 200 只），
冻结六字段为 ``tests/fixtures/gate_anchor_panel.npz``（float32 存储；锚点由冻结
值自洽计算，精度不依赖原始 float64），并计算锚点表达式读数
（IC/ICIR，h=1）落 ``tests/fixtures/gate_anchor_expected.json``。

回归锚点的用途：闸门代码任何重构都必须在这份**冻结数据**上复现锚点读数
（``tests/factors/test_gate_anchors.py``）——捕捉「改一处、悄悄漂一点」的静默漂移。
注意：子集面板读数 ≠ 生产全市场读数（锚点是内部一致性回归，不是生产口径承诺）。

运行::

    uv run python tests/scripts/make_gate_anchor_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import evaluate, forward_returns, rank_ic_series
from finance_agent.factors.monthly import _panel_from_snapshot

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _ROOT / "tests" / "fixtures"
_CODES = 200
_ANCHOR_EXPRESSIONS = [
    "div(close,ts_mean(close,20))",
    "sub(div(close,ts_mean(close,20)),1)",
    "delta(close,5)",
    "div(volume,ts_mean(volume,20))",
    "ts_std(close,20)",
]


def main() -> int:
    from finance_agent.factors.eval_engine import MarketPanel

    panel = _panel_from_snapshot(None)
    close = panel.fields["close"]
    codes = sorted(close.columns)[:_CODES]
    dates = list(close.index)
    fields = {name: frame.loc[:, codes] for name, frame in panel.fields.items()}

    arrays = {name: fields[name].to_numpy(dtype="float32") for name in sorted(fields)}
    _FIXTURES.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        _FIXTURES / "gate_anchor_panel.npz",
        dates=np.array(dates, dtype="U10"),
        codes=np.array(codes, dtype="U16"),
        **arrays,
    )
    size_kb = (_FIXTURES / "gate_anchor_panel.npz").stat().st_size // 1024

    # 用**存储值**（float32→float64 升格）自洽计算锚点
    frozen = MarketPanel(
        fields={name: _to_frame(arrays[name], dates, codes) for name in sorted(arrays)}
    )
    fwd = forward_returns(frozen.fields["close"], 1)
    expected = {}
    for expression in _ANCHOR_EXPRESSIONS:
        ic = rank_ic_series(evaluate(parse(expression), frozen), fwd).dropna()
        expected[expression] = {
            "ic_mean": float(ic.mean()),
            "icir": float(ic.mean() / ic.std(ddof=1)),
            "ic_days": int(ic.size),
        }
        print(
            f"{expression}: IC mean={expected[expression]['ic_mean']:.10f} ICIR={expected[expression]['icir']:.10f}"
        )
    (_FIXTURES / "gate_anchor_expected.json").write_text(
        json.dumps(
            {"codes": len(codes), "dates": len(dates), "h": 1, "expected": expected},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"fixture: {size_kb} KB / {len(codes)} codes × {len(dates)} days")
    return 0


def _to_frame(values: np.ndarray, dates: list[str], codes: list[str]):
    import pandas as pd

    return pd.DataFrame(values.astype("float64"), index=dates, columns=codes)


if __name__ == "__main__":
    raise SystemExit(main())
