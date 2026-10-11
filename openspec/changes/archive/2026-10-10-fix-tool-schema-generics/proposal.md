# Proposal: fix-tool-schema-generics

## Why

`build_schema_from_function`（harness/llm_client.py）的类型内省只认裸类型名（`getattr(annotation, "__name__")`），泛型/Optional 注解（如 `list[str] | None`）一律 `str(annotation)` 查表失败 → fallback `"string"`。LLM 看到的 schema 类型与 docstring 描述（「列表」）矛盾，高频把列表序列化成字符串传入（PR #275 实证两种形态：中英文逗号串、JSON 数组字符串），下游被迫堆容错分支（`_resolve_peer_codes` 逗号串/JSON 串双容错）。同坑存量：`batch_web_search(queries: list[str])` 等（issue #277）。

## What Changes

- `build_schema_from_function` 改用 `typing.get_origin`/`get_args` 解包：`list[X]` → `{"type":"array","items":…}`，`dict[...]` → `object`，`X | None`/`Optional[X]` 剥壳取 X，`tuple/set` → `array`；未识别注解保持 `"string"` 兜底不变
- 字符串注解（`from __future__ import annotations` 模块）经 `typing.get_type_hints` 解析，解析失败回退现有裸名逻辑
- `_resolve_peer_codes` 的逗号串/JSON 串容错分支保留（防御层冗余无害，回收与否留后续）

## Capabilities

### New Capabilities

- `tool-schema-generation`: 工具 schema 类型内省契约——泛型/Optional 解包规则与兜底语义

### Modified Capabilities

- （无）

## Impact

- `src/finance_agent/harness/llm_client.py`（`build_schema_from_function` 类型解析）
- 新契约测试 `tests/test_tool_schema_generics.py`
- 纯后端非交互变更，无 E2E 门禁
