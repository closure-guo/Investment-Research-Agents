# tool-schema-generation Specification

## Purpose

harness 从 Python 函数签名自动生成工具 JSON Schema 的类型内省契约（fix-tool-schema-generics，issue #277）：泛型/Optional 注解必须如实解包，避免 LLM 看到的 schema 类型与 docstring 描述矛盾（实证：list 被声明为 string 时 LLM 高频把列表序列化成字符串传入）。
## Requirements
### Requirement: 工具 schema 类型内省

harness 从 Python 函数签名生成 JSON Schema 时 SHALL 如实解包类型注解：`list[X]` MUST 生成 `{"type":"array","items":<X 的映射>}`；`dict[...]`/裸 `dict` MUST 生成 `{"type":"object"}`；`Optional[X]`/`X | None` MUST 剥壳取 `X` 的映射（可选性由 `required` 表达，不由类型表达）；`tuple`/`set` MUST 映射为 `array`。未识别的注解 MUST 回退 `{"type":"string"}`（现状兜底语义保留）。

字符串形态注解（`from __future__ import annotations` 模块）SHALL 先经 `typing.get_type_hints` 解析；解析失败 SHALL 回退裸名内省而非报错。

#### Scenario: 泛型列表生成 array 带 items

- **GIVEN** 工具函数注解 `queries: list[str]`
- **WHEN** 生成 JSON Schema
- **THEN** 参数类型为 `{"type":"array","items":{"type":"string"}}` 而非 `"string"`

#### Scenario: Optional 剥壳

- **GIVEN** 工具函数注解 `peer_codes: list[str] | None = None`
- **WHEN** 生成 JSON Schema
- **THEN** 参数类型为 array（items string）且不在 `required` 中

#### Scenario: 字符串注解经 get_type_hints 解析

- **GIVEN** 工具定义于 `from __future__ import annotations` 模块，注解以字符串存储
- **WHEN** 生成 JSON Schema
- **THEN** 泛型解包照常生效

#### Scenario: 未知注解兜底 string

- **GIVEN** 工具函数注解为自定义类或未导入前向引用（get_type_hints 解析失败）
- **WHEN** 生成 JSON Schema
- **THEN** 参数类型回退 `"string"`，schema 生成不报错

