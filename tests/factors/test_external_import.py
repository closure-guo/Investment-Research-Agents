"""外部研发供给导入（unify-quant-research-architecture P0-3）单测。

覆盖 spec factor-proposal「外部研发供给接入」场景：过闸入库（source=external）/
不可表达拒收（errors 桶）/ 外部结论不替代闸口读数；另钉通道强制来源标注与
浅面板 fail loud（准入面板深度下限共用路径）。

合成强信号面板复用 ``tests/factors/test_builtin.py``（同种子同结构，准入结论
逐位可复现：``delta(close,20)`` 过闸、``div(ts_mean(close,5),close)`` 拒于闸②）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance_agent.factors.external import run_factor_external_import
from finance_agent.factors.library import list_factors
from tests.factors.test_builtin import _STRONG_SEED, _strong_panel
from tests.factors.test_monthly import _seed_snapshot_dates

_ADMITTED_EXPRESSION = "delta(close,20)"
_REJECTED_EXPRESSION = "div(ts_mean(close,5),close)"
_HYPOTHESIS = "外部研究员候选：测试用经济学假设文本，长度达标并描述与下一期收益的关系。"


def _candidate(expression: str, **overrides: object) -> dict:
    base: dict = {"expression": expression, "hypothesis": _HYPOTHESIS, "category": "外部动量"}
    base.update(overrides)
    return base


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "factor_library.db")


def test_admitted_and_rejected_with_external_source(tmp_path):
    """过闸入库 source=external；闸②拒收者留原因码——外部结论字段不参与裁决。"""
    db = _db(tmp_path)

    summary = run_factor_external_import(
        [
            _candidate(_ADMITTED_EXPRESSION),
            _candidate(_REJECTED_EXPRESSION, external_ic=0.9, external_note="外部回测 IC 显著"),
        ],
        panel=_strong_panel(),
        seed=_STRONG_SEED,
        db_path=db,
    )

    assert summary["admitted"] == 1
    assert summary["rejected"] == 1
    assert summary["errors"] == 0
    rejection = next(item for item in summary["rejections"] if item["gate"] is not None)
    assert rejection["expression"] == _REJECTED_EXPRESSION
    assert rejection["reason"] == "WEAK_ICIR"  # 外部 IC 0.9 不替代闸②读数
    rows = list_factors(db_path=db)
    assert len(rows) == 1
    assert rows[0]["source"] == "external"
    assert rows[0]["category"] == "外部动量"
    assert rows[0]["expression"] == _ADMITTED_EXPRESSION


def test_unparseable_counts_errors_without_blocking_batch(tmp_path):
    """不可解析候选计入 errors（gate=None），整批其余照常裁决。"""
    db = _db(tmp_path)

    summary = run_factor_external_import(
        [_candidate("foo(close,3)"), _candidate(_ADMITTED_EXPRESSION)],
        panel=_strong_panel(),
        seed=_STRONG_SEED,
        db_path=db,
    )

    assert summary["errors"] == 1
    assert summary["admitted"] == 1
    failure = next(item for item in summary["rejections"] if item["gate"] is None)
    assert failure["expression"] == "foo(close,3)"
    assert len(list_factors(db_path=db)) == 1


def test_source_is_forced_to_external(tmp_path):
    """通道强制来源标注：文件自称 proposal 也一律改写为 external（MUST NOT 伪造来源）。"""
    db = _db(tmp_path)

    run_factor_external_import(
        [_candidate(_ADMITTED_EXPRESSION, source="proposal")],
        panel=_strong_panel(),
        seed=_STRONG_SEED,
        db_path=db,
    )

    assert list_factors(db_path=db)[0]["source"] == "external"


def test_shallow_snapshot_panel_fails_loud(tmp_path):
    """无注入面板且快照深度不足 → ValueError（准入面板深度下限共用路径）。"""
    db = _db(tmp_path)
    snap = tmp_path / "shallow_snapshot.db"
    _seed_snapshot_dates(snap, n=90)

    with pytest.raises(ValueError, match="深度不足"):
        run_factor_external_import(
            [_candidate(_ADMITTED_EXPRESSION)], db_path=db, snapshot_db_path=snap
        )
