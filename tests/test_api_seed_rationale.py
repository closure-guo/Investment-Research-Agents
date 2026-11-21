"""seed predictions 造数的快照透传（add-price-polarity-contract，#606）。

E2E 极性标注需要造带 declared_prices.polarity 的 predictions 行——seed 通道
原硬编码 ``rationale_snapshot: {}``，透传后 E2E 可复现 #606 两类数据态：
带极性声明（新契约）与无声明（存量）。
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("finance_agent.api")


@pytest.fixture
def testing_client(monkeypatch, tmp_path):
    """TESTING=1 reload 出 /api/test/seed；SESSIONS_DB_PATH 指向 tmp 测试库。"""
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "sessions.db"))
    import finance_agent.api as api_module

    importlib.reload(api_module)
    try:
        yield TestClient(api_module.app)
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


def _seed_payload() -> dict[str, Any]:
    return {
        "track_record": {
            "predictions": [
                {
                    "symbol": "600029.SH",
                    "symbol_name": "南方航空",
                    "direction": "short",
                    "status": "open",
                    "entry_price": 5.12,
                    "target_price": 5.3971,
                    "created_at": "2026-11-20T15:00:00",
                    "rationale_snapshot": {
                        "action": "sell",
                        "sell_type": "exit",
                        "declared_prices": {
                            "entry_price": 5.12,
                            "stop_loss": 4.9,
                            "target_price": 5.3971,
                            "polarity": "exit",
                        },
                    },
                },
                {
                    "symbol": "600771.SH",
                    "direction": "short",
                    "status": "open",
                    "entry_price": 11.8,
                    "created_at": "2026-11-20T15:05:00",
                },
            ]
        }
    }


def test_seed_passes_rationale_snapshot_and_detail_exposes_polarity(testing_client):
    """seed 行透传 rationale_snapshot；详情端点读侧提取 declared_polarity。"""
    resp = testing_client.post("/api/test/seed", json=_seed_payload())
    assert resp.status_code == 200, resp.text

    detail = testing_client.get("/api/v1/track-record/predictions").json()
    rows = {r["symbol"]: r for r in detail["predictions"]}
    assert rows["600029.SH"]["declared_polarity"] == "exit"
    # 存量形态（seed 行未带快照）→ 口径未声明
    assert rows["600771.SH"]["declared_polarity"] is None
    snap = json.loads(rows["600029.SH"]["rationale_snapshot"])
    assert snap["declared_prices"]["polarity"] == "exit"
