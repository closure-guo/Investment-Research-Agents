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


def test_template_contract_block_renders_summary(tmp_path: Path) -> None:
    """任务指令层契约段：规则/复杂度/字段全量；算子词表 ≤32 时也全量列出（第 2 轮起是唯一算子指引）。"""
    mod = _load_consumer()
    path = _write_payload(tmp_path, {"contract": _CONTRACT, "explored": []})
    block = mod.render_contract_block(path)
    assert "BINDING" in block
    for token in (
        "epsilon",
        "cross-sectional",
        "Complexity limits: max_ast_depth <= 8",
        "$close",
    ):
        assert token in block
    # 全量算子，而非样例：Ref/Rank 等主力算子不得因摘要被遗漏
    assert "Abs, Mean, Ref" in block
    assert "any operator outside this set is inexpressible" in block
    assert "gate2" not in block  # 拒收计数只属上下文段，模板段不带


def test_template_contract_block_fails_safe(tmp_path: Path) -> None:
    mod = _load_consumer()
    p = tmp_path / "explored.json"
    p.write_text("{bad json", encoding="utf-8")
    assert mod.render_contract_block(str(p)) == ""  # 损坏 → 空串
    assert (
        mod.render_contract_block(_write_payload(tmp_path, {"explored": []})) == ""
    )  # 无契约 → 空串
    assert mod.render_contract_block(str(tmp_path / "missing.json")) == ""  # 缺失 → 空串


def test_template_contract_block_header_only_returns_empty(tmp_path: Path) -> None:
    """契约非空但无任何可用条款（rules 空/字段损坏）→ 视为无契约，不注入空抬头。"""
    mod = _load_consumer()
    path = _write_payload(tmp_path, {"contract": {"rules": []}, "explored": []})
    assert mod.render_contract_block(path) == ""
    path = _write_payload(
        tmp_path,
        {"contract": {"rules": [1, None], "complexity": {"max_fields": None}}, "explored": []},
    )
    assert mod.render_contract_block(path) == ""


def test_template_contract_complexity_bad_values_filtered(tmp_path: Path) -> None:
    """复杂度损坏值不得原样渲染成 'max_fields <= None'（MUST NOT 虚构契约数值）。"""
    mod = _load_consumer()
    contract = dict(_CONTRACT, complexity={"max_ast_depth": 8, "max_fields": None})
    path = _write_payload(tmp_path, {"contract": contract, "explored": []})
    block = mod.render_contract_block(path)
    assert "max_ast_depth <= 8" in block
    assert "None" not in block


def test_template_contract_block_budget_bounded(tmp_path: Path) -> None:
    mod = _load_consumer()
    huge = dict(_CONTRACT, rules=["x" * 5000])
    path = _write_payload(tmp_path, {"contract": huge, "explored": []})
    block = mod.render_contract_block(path)
    assert len(block) <= mod.TEMPLATE_CONTRACT_MAX_CHARS + 15
    assert "(truncated)" in block


def test_template_contract_consistent_with_context_contract(tmp_path: Path) -> None:
    """同源一致性：两段复杂度条款逐项相等、算子总数 == 记忆块算子列表长度。"""
    mod = _load_consumer()
    path = _write_payload(
        tmp_path, {"contract": _CONTRACT, "gate_rejections": {"2": 322}, "explored": []}
    )
    tpl = mod.render_contract_block(path)
    ctx = mod.render_explored_block(path)

    def _complexity_items(block: str) -> set[str]:
        line = next(ln for ln in block.splitlines() if "Complexity limits:" in ln)
        return {p.strip() for p in line.split("Complexity limits:", 1)[1].rstrip(".").split(",")}

    assert _complexity_items(tpl) == _complexity_items(ctx)
    tpl_count_line = next(ln for ln in tpl.splitlines() if "Expressible operators:" in ln)
    assert "3 in total" in tpl_count_line
    ctx_ops_line = next(ln for ln in ctx.splitlines() if "Expressible operators (Qlib form):" in ln)
    ctx_ops = ctx_ops_line.split(":", 1)[1].rstrip(".").split(", ")
    assert len(ctx_ops) == 3


def _style_gap_payload() -> dict:
    return {
        "contract": _CONTRACT,
        "gate_rejections": {"2": 322},
        "style_gap": {
            "pool_by_category": {"量与流动性": 8, "外部": 2},
            "rejections_by_category": {"动量反转": 36, "量与流动性": 49},
            "rejections_by_category_gate": {
                "动量反转": {"2": 30, "4": 6},
                "量与流动性": {"2": 41, "4": 8},
            },
        },
        "explored": [{"name": "mom20", "status": "rejected", "gate": 2, "reason": "WEAK_ICIR"}],
    }


def test_style_gap_rendered_between_contract_and_explored(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(tmp_path, _style_gap_payload())
    block = mod.render_explored_block(path)
    assert "Style gap" in block
    assert "量与流动性×8" in block
    assert "gate2×30" in block
    assert block.index("Hard contract") < block.index("Style gap") < block.index("mom20")


def test_style_gap_absent_keeps_legacy_output(tmp_path: Path) -> None:
    mod = _load_consumer()
    base_payload = {k: v for k, v in _style_gap_payload().items() if k != "style_gap"}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    base = mod.render_explored_block(_write_payload(tmp_path / "a", base_payload))
    styled = mod.render_explored_block(_write_payload(tmp_path / "b", _style_gap_payload()))
    marker = "# Cross-session memory"
    assert base[base.index(marker) :] == styled[styled.index(marker) :]
    assert base.splitlines()[0] == styled.splitlines()[0]
    assert "Style gap" not in base


def test_style_gap_malformed_fields_fail_safe(tmp_path: Path) -> None:
    mod = _load_consumer()
    payload = _style_gap_payload()
    payload["style_gap"] = {
        "pool_by_category": {"x": "not-int", 3: None},
        "rejections_by_category_gate": {"fam": "not-a-dict", "ok": {"2": "bad", "3": 1}},
    }
    path = _write_payload(tmp_path, payload)
    block = mod.render_explored_block(path)
    assert "mom20" in block  # 损坏字段不致命，清单照常
    assert "Style gap" not in block  # 字段全不可用 → 整段省略（不渲染空抬头）


def test_style_gap_only_payload_renders_style_gap(tmp_path: Path) -> None:
    mod = _load_consumer()
    path = _write_payload(
        tmp_path,
        {"style_gap": {"pool_by_category": {"量与流动性": 8}}},
    )
    block = mod.render_explored_block(path)
    assert "Style gap" in block
    assert "Hard contract" not in block
    assert "量与流动性×8" in block
