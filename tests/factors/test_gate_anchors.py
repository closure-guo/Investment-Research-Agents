"""闸门回归锚点（验证钉子 3）：冻结真实面板子集上复现闸口读数。

夹具：``tests/fixtures/gate_anchor_panel.npz``（200 票 × 150 日六字段，float32）
+ ``gate_anchor_expected.json``（锚点读数，由 ``tests/scripts/make_gate_anchor_fixture.py``
生成）。闸门机器（面板构造→求值→IC→统计）的任何重构都必须在**冻结数据**上复现
锚点读数——捕捉「改一处、悄悄漂一点」的静默漂移。

口径注记：锚点在 float32 冻结值上自洽计算（脚本与测试同一数据同一代码路径），
容差 rel=1e-6 吸收跨平台浮点噪声、远低于任何真实语义漂移的量级（min_periods /
shift / horizon 类改动是 1e-2+ 级）。**子集读数 ≠ 生产全市场读数**——锚点是内部
一致性回归，不是生产口径承诺。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def frozen() -> tuple[MarketPanel, dict]:
    data = np.load(_FIXTURES / "gate_anchor_panel.npz")
    dates = [str(value) for value in data["dates"]]
    codes = [str(value) for value in data["codes"]]
    panel = MarketPanel(
        fields={
            name: pd.DataFrame(data[name].astype("float64"), index=dates, columns=codes)
            for name in ("open", "high", "low", "close", "volume", "amount")
        }
    )
    expected = json.loads((_FIXTURES / "gate_anchor_expected.json").read_text(encoding="utf-8"))
    return panel, expected


def test_anchor_readings_reproduce_on_frozen_panel(frozen) -> None:
    panel, expected = frozen
    fwd = forward_returns(panel.fields["close"], int(expected["h"]))
    assert len(expected["expected"]) >= 3  # 锚点集非空（防夹具被误清空）
    for expression, anchor in expected["expected"].items():
        ic = rank_ic_series(evaluate(parse(expression), panel), fwd).dropna()
        assert ic.size == anchor["ic_days"], expression
        assert float(ic.mean()) == pytest.approx(anchor["ic_mean"], rel=1e-6, abs=1e-9), expression
        assert float(ic.mean() / ic.std(ddof=1)) == pytest.approx(
            anchor["icir"], rel=1e-6, abs=1e-9
        ), expression
