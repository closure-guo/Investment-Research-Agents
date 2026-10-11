# Tasks: fix-tool-schema-generics

## 1. 实现

- [x] 1.1 契约测试先行：`tests/test_tool_schema_generics.py`（list[str]/dict/Optional 剥壳/未来注解字符串/未知类型兜底/嵌套 items），红→绿
- [x] 1.2 `build_schema_from_function`：`get_type_hints` 解析 + `_annotation_to_json_schema` 递归解包
- [x] 1.3 既有套件回归：`pytest tests/test_agent_factory.py` + harness 相关全绿

## 2. 收口

- [x] 2.1 PR 合并后 sync + archive
- [ ] 2.2 issue #277 核销评论
