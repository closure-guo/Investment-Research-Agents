"""factors/review.py 单测（add-factor-post-admission-review）。

四用例：恒定序列水位失守降级 / 真波动序列+账本显著通过 / 全局反事实降级 /
样本不足跳过。库全落 tmp_path。
"""

from __future__ import annotations

import math
import sqlite3

from finance_agent.factors.library import add_factor, record_admission_ledger, record_ic
from finance_agent.factors.review import run_post_admission_review


def _seed_factor(db, *, n_samples: int, ic_series=None) -> int:
    fid = add_factor(
        expression=f"ts_mean(close,{5 + n_samples})", hypothesis="h", category="测试",
        status="active", db_path=db,
    )
    if ic_series is None:
        ic_series = [0.04] * n_samples
    for i, value in enumerate(ic_series):
        record_ic(fid, f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", value, db_path=db)
    return fid


def _ledger_row(*, fid: int | None, p_value: float) -> dict:
    return {
        "batch_id": "b1", "source": "test", "expression": "ts_mean(close,5)",
        "p_value": p_value, "p_adj": None, "q": None, "n_hypotheses": 1,
        "admitted": 1, "factor_id": fid, "rejected_gate": None,
    }


class TestPostAdmissionReview:
    def test_watermark_degrades_constant_series(self, tmp_path):
        # 恒定 IC → std=0 → ICIR=0（与闸②同口径）→ 水位失守降级
        db = str(tmp_path / "factor_library.db")
        f_weak = _seed_factor(db, n_samples=80)
        result = run_post_admission_review(db_path=db)
        assert result["reviewed"] == 1
        assert f_weak in result["degraded"]
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM factors WHERE id=?", (f_weak,)).fetchone()[0]
        conn.close()
        assert status == "degraded"

    def test_pass_with_real_series_and_ledger_significant(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        series = [0.4 + 0.1 * math.sin(i) for i in range(80)]
        fid = _seed_factor(db, n_samples=80, ic_series=series)
        record_admission_ledger([_ledger_row(fid=fid, p_value=0.001)], db_path=db)
        result = run_post_admission_review(db_path=db)
        assert result["reviewed"] == 1
        assert result["degraded"] == []
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM factors WHERE id=?", (fid,)).fetchone()[0]
        conn.close()
        assert status == "active"

    def test_ledger_counterfactual_degrades(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        series = [0.4 + 0.1 * math.sin(i) for i in range(80)]
        fid = _seed_factor(db, n_samples=80, ic_series=series)
        # 家族 {0.06, 0.5}：BH 后 0.06 → 0.12 > 0.1（批内显著、全局不显著）
        record_admission_ledger(
            [_ledger_row(fid=fid, p_value=0.06), _ledger_row(fid=None, p_value=0.5)],
            db_path=db,
        )
        result = run_post_admission_review(db_path=db)
        assert fid in result["degraded"]
        conn = sqlite3.connect(db)
        detail = conn.execute(
            "SELECT detail FROM factor_events WHERE factor_id=? AND event='status_change'",
            (fid,),
        ).fetchone()[0]
        conn.close()
        assert "POST_ADMISSION_REVIEW" in detail
        assert "counterfactual_hit" in detail

    def test_insufficient_samples_skipped(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        fid = _seed_factor(db, n_samples=30)
        result = run_post_admission_review(db_path=db)
        assert result["reviewed"] == 0
        assert fid in result["skipped"]
        assert result["degraded"] == []
