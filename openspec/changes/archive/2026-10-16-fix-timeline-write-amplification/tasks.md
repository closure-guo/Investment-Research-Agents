# Tasks: fix-timeline-write-amplification

## 1. 可变累加器（D1，TDD 先红）

- [x] 1.1 失败测试：`NodeTimelineAccumulator` 与纯函数 fold 等价——脚本化交错序列（多节点洪峰/切回/tool/search 穿插/node_complete/洪峰后 token）逐事件对照 materialize()
- [x] 1.2 失败测试：O(1) 行为代理——thinking item 内部以 parts 分片暂存（len(parts) 随 token +1，不在 add 时整串拼接）；materialize() 输出无 parts 残留键
- [x] 1.3 实现 `NodeTimelineAccumulator`（add_thinking_token / apply_search_event / apply_tool_event / apply_node_complete / materialize）
- [x] 1.4 既有 timeline_builder 测试全绿（纯函数族保持不动，作为语义锚）

## 2. 自适应中间写间隔（D2，TDD 先红）

- [x] 2.1 失败测试：`timeline_persist_interval` 边界——0 字节→0.5s 下限；128KB→0.5s；1MB→4s；5MB→20s；≥7.5MB→30s 封顶；单调不减
- [x] 2.2 实现 `timeline_persist_interval` + 常量三件套

## 3. 两条消费路径切换

- [x] 3.1 失败测试（agent_factory）：洪峰场景（3000 thinking token，假 store 计写次数）写次数有界（≪ 洪峰条数×0.5s  naive 上界），结束 flush 写全量内容
- [x] 3.2 agent_factory `_background_consume` 切换累加器 + 自适应间隔（节点边界即时冲刷保留）
- [x] 3.3 失败测试（pipeline_runner）：同 3.1 口径（fast path 洪峰写有界 + 终态全量）
- [x] 3.4 pipeline_runner 切换（含 search/tool 事件路径）
- [x] 3.5 既有回归：`test_pipeline_write_blocking.py` / `test_pipeline_watchdog_graph_done.py` / `test_agent_factory_blocked_terminal.py` 全绿

## 4. 验证与回归

- [x] 4.1 全量 pytest -m not live 0 失败；ruff/mypy 任务范围零错误
- [x] 4.2 openspec validate --strict 通过
- [x] 4.3 性能实证（本地微基准）：6 万 token 洪峰的累加耗时对比（纯函数 fold vs 累加器）落测试输出，验证平方消除
