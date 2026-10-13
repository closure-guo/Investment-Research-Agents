# Tasks: add-analyst-factor-tools

## 1. 只读工具
- [x] 1.1 实现 `query_factor_library`（按标的返回 active 因子取值/z-score/近60日IC）+ 输出 schema 合同单测
      （提交 `a56374e9`；键逐字契约 + 退化输入（空库/缺 symbol/缺日期/窗口不足/std=0/坏表达式）
      + 严格 JSON，19 例）
- [x] 1.2 工具只读性保证（模块无写路径）+ 静态检查或单测
      （AST 静态扫描：无写库调用/写函数 import/`sqlite3`；工具不接受表达式入参）

## 2. 注册与追踪
- [x] 2.1 `agent_factory.py` 注册至技术分析师工具集 + Langfuse 用途标签 + wiring 单测
      （提交 `254bc5f1`；`ANALYST_TOOLSETS` 权威表 + `_trace_tool` span 落
      `purpose_tag=analyst_factor_tool` 与 `duration_ms`；技术分析师节点注入工具输出，
      其他三分析师零改动。**读法校正**：本仓库分析师层无 ReAct 工具集，按既有 toolize 先例
      接线（未新造执行机制）——偏差与理由见验证报告文首与 §2）

## 3. 消融变体
- [x] 3.1 `evals/ablation.py` 新增「±因子工具」变体 + 配对 bootstrap 跑批
      （提交 `529a6b92`；`tech_bare`/`tech_factor_tool` 单变量差异 + `_applicable_dims` 过滤 +
      `paired_bootstrap_ci` 聚合 + CI 含 0 措辞守卫；CLI `--suite factor-tool`）

## 4. 验证
- [x] 4.1 全量测试通过
      （`uv run pytest tests/ -q -m "not live"` = 1 failed, 5392 passed, 6 skipped, 12 deselected
      ——唯一失败 `tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default`
      为既有宿主 .env 泄漏 flake（隔离复跑 1 passed），与计划预告一致；门禁口径
      `tests/factors tests/evals -q -m "not live"` 全绿 1574 passed）
- [ ] 4.2 n=10 消融报告落 `docs/evals/`，结论按「CI 含 0 只写无显著差异」口径撰写
      ——**未完成（如实标注）**：按计划 A4（成本纪律 + 评估 SOP「judge 校准先行、一次只动一个变量」）
      本轮**不跑** n=10 批次，**未伪造任何批次结果**。已交付：可运行入口
      `run_factor_tool_ablation` / CLI `--suite factor-tool` + CI 含 0 措辞守卫（单测钉死）；
      1 对样本的最小实跑烟测**被供应商限流中断**（`RateLimitError`，第一臂
      `fundamental_analyst`，无配对 artifact），同链路单调用 live 探针跑通（因子库段
      value/z-score/IC 注入 + 16/16 claims PASS，见验证报告 §0.3）。后续项挂 issue #326。
      完成路径：judge 校准 → `uv run python -m evals.ablation --tickers <10 只> --repeats 1
      --suite factor-tool` → 报告落 `docs/evals/` + `metrics.md`/`runs.jsonl` 登记。
- [x] 4.3 既有测试无修改通过（红线）
      （diff 证据：`tests/test_agent_factory.py` 165 增 / 0 删（纯追加），其余测试文件均为新增；
      `git diff --name-only 3d12ba51..HEAD` = 8 文件，无既有测试文件被改）
