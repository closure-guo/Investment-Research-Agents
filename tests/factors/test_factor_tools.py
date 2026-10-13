"""factors/tools.py 只读因子库工具单测（add-analyst-factor-tools Task 1）。

覆盖计划关键设计决策 A1（输出 schema 键逐字；value/zscore/IC 口径；缺 symbol
列 / 缺日期 / 求值失败 → null 不编造；无 active 因子 → 空列表 + note 不抛错）
与 Global Constraints（只读：模块内无任何因子库写路径——AST 静态扫描钉死；
输出为 ``json.dumps(ensure_ascii=False)`` 严格 JSON）。

零网络、零共享状态：面板由 ``build_panel_from_rows`` 合成，因子库落 ``tmp_path``。
期望值逐条独立手写（不复用被测模块生成期望）。
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from finance_agent.factors.dsl import parse, serialize
from finance_agent.factors.eval_engine import build_panel_from_rows
from finance_agent.factors.library import add_factor, init_library, record_ic
from finance_agent.factors.tools import query_factor_library

# ── 合成面板（3 标的 × 25 日；列名逐字 = market_snapshot 行契约） ──────────────

_DATES = [f"2026-01-{day:02d}" for day in range(1, 26)]  # 25 个规范交易日
_SYMBOL = "300750"
_OTHER_SYMBOLS = ("600519", "000858")

# 逐标的收盘（close_qfq = close_raw，复权因子 1.0）：线性序列便于手算 delta
_CLOSES: dict[str, list[float]] = {
    _SYMBOL: [100.0 + i for i in range(25)],  # 100..124
    "600519": [200.0 + 2 * i for i in range(25)],  # 200..248
    "000858": [50.0 + 0.5 * i for i in range(25)],  # 50..62
}
_AS_OF = _DATES[-1]
# 20 日动量 delta(close,20) 在 as_of 行的取值 = close[24] − close[4]
_MOMENTUM_20D: dict[str, float] = {
    symbol: closes[24] - closes[4] for symbol, closes in _CLOSES.items()
}


def _panel_rows() -> pd.DataFrame:
    """长表：三标的 × 25 日行情（复权因子 1.0，OHLC 同价，量额固定）。"""
    rows = []
    for symbol, closes in _CLOSES.items():
        for day, close in zip(_DATES, closes, strict=True):
            rows.append(
                {
                    "trade_date": day,
                    "code": symbol,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close_raw": close,
                    "close_qfq": close,
                    "volume": 1000.0,
                    "amount": 1000.0 * close,
                }
            )
    return pd.DataFrame(rows)


def _hand_zscores(values: dict[str, float]) -> dict[str, float]:
    """手算横截面 z（样本标准差 ddof=1，与 eval_engine.cross_section_zscore 同口径）。"""
    numbers = list(values.values())
    mean = sum(numbers) / len(numbers)
    variance = sum((value - mean) ** 2 for value in numbers) / (len(numbers) - 1)
    std = math.sqrt(variance)
    return {symbol: (value - mean) / std for symbol, value in values.items()}


_EXPECTED_ZSCORE = _hand_zscores(_MOMENTUM_20D)

_EXPRESSION_MOMENTUM = "delta(close,20)"


def _db(tmp_path) -> str:
    return str(tmp_path / "factor_library.db")


def _add_active(db: str, expression: str = _EXPRESSION_MOMENTUM, *, category: str = "动量") -> int:
    return add_factor(
        expression=expression,
        hypothesis="测试用假设文本，描述该因子与未来收益的关系。",
        category=category,
        status="active",
        ast_json=serialize(parse(expression)),
        db_path=db,
    )


def _query(**overrides) -> dict:
    """调用工具并 json.loads（默认：合成面板 + 库内 active 动量因子）。"""
    kwargs = {
        "panel": build_panel_from_rows(_panel_rows()),
        "as_of": _AS_OF,
        "symbol": _SYMBOL,
    }
    kwargs.update(overrides)
    symbol = kwargs.pop("symbol")
    return json.loads(query_factor_library(symbol, **kwargs))


# ── 主路径：schema 合同 + 取值口径（spec 场景） ─────────────────────────────


class TestSchemaAndValues:
    def test_schema_keys_verbatim(self, tmp_path):
        """键逐字：{"symbol","as_of","factors":[{"factor_id","expression","category",
        "value","zscore","ic_mean_60d","icir_60d"}]}。"""
        db = _db(tmp_path)
        factor_id = _add_active(db)
        payload = _query(db_path=db)

        assert set(payload) == {"symbol", "as_of", "factors"}
        assert payload["symbol"] == _SYMBOL
        assert payload["as_of"] == _AS_OF
        assert len(payload["factors"]) == 1
        entry = payload["factors"][0]
        assert set(entry) == {
            "factor_id",
            "expression",
            "category",
            "value",
            "zscore",
            "ic_mean_60d",
            "icir_60d",
        }
        assert entry["factor_id"] == factor_id
        assert entry["expression"] == _EXPRESSION_MOMENTUM
        assert entry["category"] == "动量"

    def test_value_is_as_of_day_factor_value(self, tmp_path):
        """value = 该因子在 as_of 行的标的列值（20 日动量手算：close[-1] − close[-21]）。"""
        db = _db(tmp_path)
        _add_active(db)
        entry = _query(db_path=db)["factors"][0]
        assert entry["value"] == pytest.approx(_MOMENTUM_20D[_SYMBOL], rel=1e-12)

    def test_zscore_is_full_market_cross_section(self, tmp_path):
        """zscore = 该日**全市场**截面的 z（样本标准差 ddof=1，与 ranker 同口径）——
        用三标的截面手算值对拍（只取该标的会得 z=0，故本断言区分两口径）。"""
        db = _db(tmp_path)
        _add_active(db)
        entry = _query(db_path=db)["factors"][0]
        assert entry["zscore"] == pytest.approx(_EXPECTED_ZSCORE[_SYMBOL], rel=1e-9)

    def test_ic_stats_read_from_library_60d_window(self, tmp_path):
        """ic_mean_60d / icir_60d 缺省取库内近 60 日 IC（均值 / mean÷std ddof=1）。"""
        db = _db(tmp_path)
        factor_id = _add_active(db)
        for day, value in zip(_DATES[:3], (0.1, 0.2, 0.3), strict=True):
            record_ic(factor_id, day, value, db_path=db)
        entry = _query(db_path=db)["factors"][0]
        assert entry["ic_mean_60d"] == pytest.approx(0.2, rel=1e-9)
        assert entry["icir_60d"] == pytest.approx(2.0, rel=1e-9)

    def test_explicit_inputs_override_defaults(self, tmp_path):
        """显式 factors / ic_stats 注入优先（免库读取路径）。"""
        db = _db(tmp_path)
        factor_id = _add_active(db)
        rows = [
            {
                "id": factor_id,
                "expression": _EXPRESSION_MOMENTUM,
                "category": "动量",
            }
        ]
        entry = _query(db_path=db, factors=rows, ic_stats={factor_id: (0.5, 1.5)})["factors"][0]
        assert entry["ic_mean_60d"] == 0.5
        assert entry["icir_60d"] == 1.5

    def test_no_ic_history_reports_zero_pair(self, tmp_path):
        """无 IC 记录 → (0.0, 0.0)（与 recent_ic_stats 口径一致，不产 NaN/inf）。"""
        db = _db(tmp_path)
        _add_active(db)
        entry = _query(db_path=db)["factors"][0]
        assert entry["ic_mean_60d"] == 0.0
        assert entry["icir_60d"] == 0.0

    def test_output_is_strict_json_string(self, tmp_path):
        """输出为严格 JSON 字符串（无 NaN/Infinity 字面量，可 json.loads 往返）。"""
        db = _db(tmp_path)
        _add_active(db)
        raw = query_factor_library(
            _SYMBOL, panel=build_panel_from_rows(_panel_rows()), as_of=_AS_OF, db_path=db
        )
        assert isinstance(raw, str)
        assert "NaN" not in raw and "Infinity" not in raw
        assert json.loads(raw)["symbol"] == _SYMBOL

    def test_ensure_ascii_false_keeps_chinese(self, tmp_path):
        """确保 ``ensure_ascii=False``：中文类别原样呈现（不转 \\uXXXX）。"""
        db = _db(tmp_path)
        _add_active(db)
        raw = query_factor_library(
            _SYMBOL, panel=build_panel_from_rows(_panel_rows()), as_of=_AS_OF, db_path=db
        )
        assert "动量" in raw


# ── 边界：空库 / 缺标的 / 缺日期 / 坏表达式（一律 null 或空，不编造） ────────


class TestDegenerateInputs:
    def test_empty_library_returns_empty_list_with_note(self, tmp_path):
        """库无 active 因子 → {"symbol","as_of","factors": []} + note，不抛错。"""
        db = _db(tmp_path)
        init_library(db)  # 建表但无任何因子
        payload = _query(db_path=db)
        assert payload["factors"] == []
        assert payload["symbol"] == _SYMBOL and payload["as_of"] == _AS_OF
        assert payload["note"]
        assert "active" in payload["note"]

    def test_library_file_missing_returns_empty_list(self, tmp_path):
        """库文件不存在（从未初始化）→ 同样按空库处理（读路径自建空表，不编造因子）。"""
        payload = _query(db_path=_db(tmp_path))
        assert payload["factors"] == []
        assert payload["note"]

    def test_candidate_factors_excluded(self, tmp_path):
        """只取 active：candidate/degraded/retired 不入输出（在册口径）。"""
        db = _db(tmp_path)
        add_factor(
            expression=_EXPRESSION_MOMENTUM,
            hypothesis="测试用假设文本，描述该因子与未来收益的关系。",
            category="动量",
            status="candidate",
            ast_json=serialize(parse(_EXPRESSION_MOMENTUM)),
            db_path=db,
        )
        payload = _query(db_path=db)
        assert payload["factors"] == []

    def test_symbol_absent_from_panel_value_and_zscore_null(self, tmp_path):
        """面板无该标的列 → 该因子 value/zscore 为 null（不编造 0）。"""
        db = _db(tmp_path)
        _add_active(db)
        payload = _query(db_path=db, symbol="999999")
        entry = payload["factors"][0]
        assert entry["value"] is None
        assert entry["zscore"] is None

    def test_missing_as_of_date_nulls_values_with_explicit_note(self, tmp_path):
        """面板缺 as_of 行 → value/zscore 为 null，且 note 显式指出缺失日期与可用范围
        （A1：不编造；不静默）。"""
        db = _db(tmp_path)
        _add_active(db)
        payload = _query(db_path=db, as_of="2025-12-31")
        entry = payload["factors"][0]
        assert entry["value"] is None
        assert entry["zscore"] is None
        assert "2025-12-31" in payload["note"]
        assert _DATES[0] in payload["note"] and _DATES[-1] in payload["note"]

    def test_insufficient_window_nulls_value(self, tmp_path):
        """窗口不足（as_of 行早于 21 日）→ 求值 NaN → value/zscore null（非 0）。"""
        db = _db(tmp_path)
        _add_active(db)
        payload = _query(db_path=db, as_of=_DATES[2])
        entry = payload["factors"][0]
        assert entry["value"] is None
        assert entry["zscore"] is None

    def test_corrupt_expression_nulls_value_and_notes_defect(self, tmp_path):
        """库内坏表达式 → 该因子 null + note 显式记录求值失败（可见不静默）。"""
        db = _db(tmp_path)
        factor_id = add_factor(
            expression="bogus_op(close)",
            hypothesis="测试用假设文本，描述该因子与未来收益的关系。",
            category="动量",
            status="active",
            ast_json="bogus_op(close)",
            db_path=db,
        )
        payload = _query(db_path=db)
        entry = payload["factors"][0]
        assert entry["factor_id"] == factor_id
        assert entry["value"] is None and entry["zscore"] is None
        assert str(factor_id) in payload["note"]

    def test_empty_symbol_rejected(self, tmp_path):
        """空 symbol 是调用契约错 → ValueError（fail loud，不返回空榜）。"""
        db = _db(tmp_path)
        with pytest.raises(ValueError):
            query_factor_library(
                "  ", panel=build_panel_from_rows(_panel_rows()), as_of=_AS_OF, db_path=db
            )

    def test_std_zero_cross_section_nulls_zscore(self, tmp_path):
        """全市场该因子取值全相等（std=0）→ zscore null（0/0 不产 inf/伪 0）。"""
        db = _db(tmp_path)
        _add_active(db)
        # 常量面板：三标的收盘恒为 100 → delta(close,20) 截面全 0 → std=0
        rows = _panel_rows()
        rows["close_qfq"] = 100.0
        rows["close_raw"] = 100.0
        payload = _query(db_path=db, panel=build_panel_from_rows(rows))
        entry = payload["factors"][0]
        assert entry["value"] == 0.0
        assert entry["zscore"] is None


# ── 只读保证：AST 静态扫描（spec「工具模块内不存在任何写库函数」） ──────────


_FORBIDDEN_CALL_NAMES = frozenset(
    {
        "add_factor",
        "set_status",
        "set_pool_increment",
        "record_ic",
        "apply_bucket_quota",
        "init_library",
        "write_snapshot_rows",
        "execute",
        "executescript",
        "commit",
        "rollback",
    }
)
_FORBIDDEN_CALL_PREFIXES = ("write_", "update_", "record_", "delete_", "insert_", "remove_")
_FORBIDDEN_IMPORT_NAMES = frozenset(
    {
        "add_factor",
        "set_status",
        "set_pool_increment",
        "record_ic",
        "apply_bucket_quota",
        "init_library",
        "sqlite3",
    }
)


def _tool_module_tree() -> ast.Module:
    import finance_agent.factors.tools as tools_module

    source = Path(tools_module.__file__).read_text(encoding="utf-8")
    return ast.parse(source)


def test_module_has_no_write_calls():
    """静态扫描：无因子库写函数调用、无 ``write_*``/``update_*``/``record_*`` 类调用。"""
    offenders: list[str] = []
    for node in ast.walk(_tool_module_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _FORBIDDEN_CALL_NAMES or name.startswith(_FORBIDDEN_CALL_PREFIXES):
            offenders.append(f"{name}@{node.lineno}")
    assert offenders == [], f"工具模块出现写路径调用: {offenders}"


def test_module_has_no_write_imports():
    """静态扫描：不 import 因子库写函数、不 import sqlite3（无任何直连 SQL 能力）。"""
    offenders: list[str] = []
    for node in ast.walk(_tool_module_tree()):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            offenders += [
                alias.name for alias in node.names if alias.name in _FORBIDDEN_IMPORT_NAMES
            ]
    assert offenders == [], f"工具模块出现写路径 import: {offenders}"
