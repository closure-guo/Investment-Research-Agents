"""distro 消费端渲染块单测（源码按文件加载，与部署件同源）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

_CONSUMER_SRC = (
    Path(__file__).resolve().parents[2] / "tests" / "scripts" / "spike" / "explored_feedback.py"
)


def _load_consumer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("explored_feedback_under_test", _CONSUMER_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_payload(tmp_path: Path, payload: dict) -> str:
    p = tmp_path / "explored.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


_CONTRACT = {
    "complexity": {"max_ast_depth": 8, "max_free_params": 3, "max_fields": 3},
    "operators": ["Abs", "Mean", "Ref"],
    "fields": ["$close", "$volume"],
    "rules": [
        "Numeric constants must be concrete numbers (e.g. write 1e-12); symbolic epsilon is rejected.",
        "No cross-sectional operations.",
        "No machine-learning pipelines.",
    ],
}


def test_contract_rendered_before_explored(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(
        tmp_path,
        {
            "contract": _CONTRACT,
            "gate_rejections": {"2": 322, "4": 84},
            "explored": [{"name": "mom20", "status": "rejected", "gate": 2, "reason": "WEAK_ICIR"}],
        },
    )
    block = mod.render_explored_block(path)
    assert "Hard contract" in block
    assert block.index("Hard contract") < block.index("mom20")
    for token in (
        "epsilon",
        "cross-sectional",
        "machine-learning",
        "max_ast_depth <= 8",
        "Mean",
        "$close",
        "gate2=322",
    ):
        assert token in block


def test_missing_contract_falls_back_to_explored_only(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(
        tmp_path, {"explored": [{"name": "mom20", "status": "rejected", "gate": 2}]}
    )
    block = mod.render_explored_block(path)
    assert "Hard contract" not in block
    assert "mom20" in block


def test_contract_only_payload_renders_contract(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(tmp_path, {"contract": _CONTRACT, "explored": []})
    block = mod.render_explored_block(path)
    assert "Hard contract" in block


def test_corrupted_file_renders_empty(tmp_path: Path) -> None:
    mod = _load_consumer()
    p = tmp_path / "explored.json"
    p.write_text("{not json", encoding="utf-8")
    assert mod.render_explored_block(str(p)) == ""


def test_total_length_bounded_with_truncation_marker(tmp_path: Path) -> None:
    mod = _load_consumer()
    items = [
        {"name": f"f{i}", "status": "rejected", "gate": 2, "desc": "x" * 200} for i in range(500)
    ]
    path = _write_payload(tmp_path, {"contract": _CONTRACT, "explored": items})
    block = mod.render_explored_block(path)
    assert len(block) <= mod.MAX_CHARS + 200
    assert "(truncated)" in block
