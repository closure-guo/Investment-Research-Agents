"""tests/scripts/measure_dsl_unlock.py 单测（分类口径 + 读数结构）。

重点钉住一个易错点：``translate_qlib_expression`` 的成功条件是「可翻译 **且** 过
DSL 复杂度」——「复杂度超限」blocker 必须归入复杂度桶而非「翻译其他」。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).with_name("measure_dsl_unlock.py")


def _load() -> object:
    spec = importlib.util.spec_from_file_location("measure_dsl_unlock", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMeasure:
    def test_unlocked_and_complexity_buckets(self) -> None:
        module = _load()
        state = {
            # 可翻译：旧口径 6 个 Number 节点被拒，去重后 2 个 → 解锁
            "ok": {
                "status": "untranslatable",
                "stage": "translator",
                "qlib_expression": (
                    "Mean(($close / Ref($close, 1)) - 1, 20) / "
                    "Std(($close / Ref($close, 1)) - 1, 20)"
                ),
            },
            # 复杂度超限（自由参数 4 个互不相同）
            "cx": {
                "status": "untranslatable",
                "stage": "translator",
                "qlib_expression": "Mean($close, 5) + Mean($close, 10) + Mean($close, 20) + 0.5",
            },
            # 缺失算子（EMA）
            "missing": {
                "status": "untranslatable",
                "stage": "translator",
                "qlib_expression": "EMA($close, 20)",
            },
            # llm 阶段无 qlib 表达式
            "llm": {"status": "untranslatable", "stage": "llm", "detail": "缺横截面算子"},
            # 非 untranslatable 不参与
            "admitted": {"status": "admitted", "expression": "close"},
        }
        report = module.measure(state)  # type: ignore[attr-defined]
        assert report["untranslatable_total"] == 4
        assert report["with_qlib_expression"] == 3
        assert report["unlocked"] == 1
        assert report["unlocked_samples"][0]["name"] == "ok"
        blocked = report["still_blocked"]
        assert blocked["复杂度·自由参数"] == 1
        assert blocked["缺失算子/字段"] == 1
        assert blocked["无 qlib 表达式（llm 阶段拒译）"] == 1
        assert "翻译其他" not in blocked
