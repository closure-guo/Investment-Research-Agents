"""factor_diagnostics 存储层单测（add-factor-diagnostics-batch Task 1）。

契约：roundtrip（JSON 载荷原样往返）/ upsert 覆盖（幂等重写）/ 无记录 → None /
reset 清空。零网络：tmp 库（FACTOR_LIBRARY_DB_PATH 调用期读 env）。
"""

from __future__ import annotations

import pytest

from finance_agent.factors.library import (
    get_factor_diagnostics,
    init_library,
    reset_factor_library,
    set_factor_diagnostics,
)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(tmp_path / "factor_library.db"))
    init_library()
    return tmp_path


def test_roundtrip_and_upsert() -> None:
    set_factor_diagnostics(
        3,
        "2026-10-24",
        style_exposure={"dominant": "momentum_20", "n_days": 41},
        monotonicity={"passed": True, "spearman": 0.71},
    )
    stored = get_factor_diagnostics(3)
    assert stored is not None
    assert stored["as_of"] == "2026-10-24"
    assert stored["style_exposure"] == {"dominant": "momentum_20", "n_days": 41}
    assert stored["monotonicity"] == {"passed": True, "spearman": 0.71}
    assert stored["computed_at"]

    # 幂等覆盖：同因子重写同 as_of → 新值生效、行数仍为 1
    set_factor_diagnostics(3, "2026-10-24", style_exposure=None, monotonicity=None)
    stored = get_factor_diagnostics(3)
    assert stored["style_exposure"] is None
    assert stored["monotonicity"] is None


def test_missing_factor_returns_none() -> None:
    assert get_factor_diagnostics(999) is None


def test_reset_clears_diagnostics() -> None:
    set_factor_diagnostics(1, "2026-10-24", style_exposure={}, monotonicity={})
    removed = reset_factor_library()
    assert removed["factor_diagnostics"] == 1
    assert get_factor_diagnostics(1) is None
