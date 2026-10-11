"""工具 schema 泛型/Optional 解包契约（fix-tool-schema-generics / issue #277）。

本文件顶部 `from __future__ import annotations` 是有意的：让模块内函数的注解
以字符串形态存储，覆盖「字符串注解经 get_type_hints 解析」场景。

红→绿基线：修复前 list[str] | None 一律 fallback "string"（LLM 高频把列表
序列化成字符串传入的根因）。
"""

from __future__ import annotations

from typing import Any, Optional

from finance_agent.harness.llm_client import build_schema_from_function


def _props(func) -> dict:
    return build_schema_from_function(func).parameters["properties"]


def _required(func) -> list:
    return build_schema_from_function(func).parameters["required"]


def sample_generic_list(queries: list[str]) -> str:
    """批量搜索。

    queries: 查询列表
    """
    return ""


def sample_optional_list(peer_codes: list[str] | None = None) -> str:
    """深度分析。

    peer_codes: 对标股列表
    """
    return ""


def sample_old_optional(limit: Optional[int] = None) -> str:  # noqa: UP045 — 故意覆盖旧式 Optional 写法
    """旧式 Optional。"""
    return ""


def sample_dict_arg(payload: dict[str, Any]) -> str:
    """字典参数。"""
    return ""


def sample_tuple_arg(pair: tuple[int, ...]) -> str:
    """元组参数。"""
    return ""


class _Custom:
    pass


def sample_unknown(obj: _Custom) -> str:
    """未知类型。"""
    return ""


def sample_plain(path: str, limit: int = 100) -> str:
    """裸类型不回归。

    path: 路径
    limit: 上限
    """
    return ""


class TestGenericUnwrapping:
    def test_list_str_becomes_array_with_items(self):
        assert _props(sample_generic_list)["queries"] == {
            "type": "array",
            "items": {"type": "string"},
            "description": "查询列表",
        }

    def test_optional_list_peels_none_and_not_required(self):
        props = _props(sample_optional_list)
        assert props["peer_codes"]["type"] == "array"
        assert props["peer_codes"]["items"] == {"type": "string"}
        assert "peer_codes" not in _required(sample_optional_list)

    def test_old_style_optional_int_peels(self):
        props = _props(sample_old_optional)
        assert props["limit"]["type"] == "integer"
        assert "limit" not in _required(sample_old_optional)

    def test_dict_becomes_object(self):
        assert _props(sample_dict_arg)["payload"]["type"] == "object"

    def test_tuple_becomes_array(self):
        assert _props(sample_tuple_arg)["pair"]["type"] == "array"

    def test_unknown_type_falls_back_string(self):
        assert _props(sample_unknown)["obj"]["type"] == "string"

    def test_plain_builtins_unchanged(self):
        props = _props(sample_plain)
        assert props["path"]["type"] == "string"
        assert props["limit"]["type"] == "integer"
        assert _required(sample_plain) == ["path"]


class TestFutureAnnotationsResolved:
    """本测试模块整体处于 future annotations 之下——以上全部用例同时钉住
    「字符串注解经 get_type_hints 解析」场景；本类补一条显式语义断言。"""

    def test_string_annotation_resolved_to_generic(self):
        import inspect

        raw = inspect.signature(sample_generic_list).parameters["queries"].annotation
        assert isinstance(raw, str), "前提：future annotations 下注解为字符串形态"
        assert _props(sample_generic_list)["queries"]["type"] == "array"
