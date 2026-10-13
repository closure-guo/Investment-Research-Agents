# add-analyst-factor-tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 技术分析师只读因子库工具 `query_factor_library`（结构化 JSON：active 因子当前值/z-score/近 60 日 IC，无任何写路径）+ agent_factory 注册与 trace 用途标签 + 消融变体「±因子工具」接入既有配对 bootstrap 框架。

**Architecture:** `factors/tools.py` 纯只读工具（消费 `eval_engine`/`library`/`ranker` 的既有原语，零写路径——静态扫描钉死）；工具注册进 `agent_factory` 的技术分析师 ReAct 工具集；消融变体在 `evals/ablation.py` 按既有变体机制新增一对（`tech_with_factor_tool` / `tech_bare`）。

**Tech Stack:** Python 3.14 / pytest；复用 `evals/ablation.py` 变体与 bootstrap 框架。

## Global Constraints

- 测试：`uv run pytest tests/factors tests/evals -q`；ruff/mypy 同仓库纪律；TDD 红→绿。
- 既有测试零修改（消融变体为新增；agent_factory 注册测试只追加）。
- 工具输出 = 结构化 JSON 字符串（`json.dumps(ensure_ascii=False)`），键逐字：`{"symbol", "as_of", "factors": [{"factor_id","expression","category","value","zscore","ic_mean_60d","icir_60d"}]}`；无 active 因子 → `{"symbol", "as_of", "factors": []}`（不抛错，附 `note`）。
- 只读保证：`factors/tools.py` 静态扫描（AST）无 `add_factor`/`set_status`/`record_*`/`write_*`/`update_*` 调用与 `library` 写函数 import；spec 明文「工具模块内不存在任何写库函数（静态扫描可验证）」。
- 用途标签逐字：`analyst_factor_tool`（工具调用计入节点耗时追踪与 Langfuse trace）。
- 消融报告口径（spec 场景）：judge 维度增量 95% CI 含 0 → 只写「无显著差异」，禁止「因子工具无效」类表述（在报告模板断言）。
- live 纪律：n=10 消融批次需要真实 LLM + judge（成本/时长极高，且项目评估 SOP 要求一次只动一个变量 + 校准先行）→ 本 change 交付**可运行的变体与最小实跑烟测（1 对样本，验证 wiring 端到端）**；n=10 批次与报告落 `docs/evals/` 作为显式后续项（挂 issue），验证报告如实记录该边界，不得伪造批次结果。

## 关键设计决策

**A1 — 工具签名与语义**：`query_factor_library(symbol: str, *, panel: MarketPanel, as_of: str, factors: list[dict] | None = None, ic_stats: dict[int, tuple[float, float]] | None = None, db_path=None) -> str`：`factors` 缺省 = `list_active_factors()`；逐因子 `evaluate(ast, panel)` 取 `as_of` 行的 symbol 列值；z-score = 该日全市场截面的 z（`cross_section_zscore`，与 ranker 同口径）；IC 统计缺省 `recent_ic_stats(window=60)`；面板缺 symbol 列/日期 → 该因子 `value/zscore` 为 null（不编造）。工具供 LLM 调用的包装（ReAct function 形态）另在 `agent_factory` 侧定义（薄壳：解析 JSON 入参 → 调 `query_factor_library` → 返回字符串）。

**A2 — agent_factory 注册**：技术分析师工具集追加 `query_factor_library`（入参 `{"symbol": str}`）；工具调用记录耗时与 `purpose_tag="analyst_factor_tool"`（复用既有工具追踪机制——实施者读 `agent_factory.py` 的技术分析师工具集定义与 `harness/` 的工具执行路径后按既有模式接线，禁止新造机制）；测试：技术分析师工具清单含该工具、其他三个分析师不含（非目标：不改动其他分析师）。

**A3 — 消融变体**：`evals/ablation.py` 现有变体机制（`build_variant_graph(name)`）新增 `tech_factor_tool`（技术分析师带工具）与 `tech_bare`（裸技术分析师，去掉该工具）——两变体共享同一快照（既有配对机制）；`evals/` 报告模板：CI 含 0 → 「无显著差异」措辞断言（单测钉模板渲染，不跑真 LLM）。

**A4 — 最小实跑烟测**：用既有 eval 跑批入口跑 1 对样本（2 次完整分析）验证 wiring 端到端（有 LLM 凭据才跑；失败/超时如实记录）。n=10 批次不跑（成本纪律 + 评估 SOP 校准前置），挂 issue。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/factors/tools.py`（新） | 只读工具 + JSON 输出 |
| `src/finance_agent/agent_factory.py`（改） | 技术分析师工具集注册 + 用途标签 |
| `evals/ablation.py`（改） | 新增两变体 |
| `tests/factors/test_factor_tools.py`、`tests/evals/test_ablation_factor_tool.py`（新） | 单测 |
| `tests/validation/2026-10-13-add-analyst-factor-tools-validation.md`（新） | 报告（含烟测/后续项边界） |

---

### Task 1: 只读工具

- [ ] **Step 1: 写失败测试**：spec 场景（active 因子 F=20 日动量、标的 300750 → JSON 含 value/zscore/ic_mean_60d/icir_60d 且可 `json.loads`）；无 active 因子 → 空列表 + note；symbol 不在面板 → null 字段；面板缺日期 → 明确错误；**静态扫描**（AST）：模块无 library 写函数调用/import。
- [ ] **Step 2–4: 红 → 实现 → 绿**（A1）。
- [ ] **Step 5: Commit** `feat(factors): read-only query_factor_library tool (T1)`。

### Task 2: 注册与追踪

- [ ] **Step 1: 写失败测试**：技术分析师工具集含 `query_factor_library`；其他三分析师不含（零改动断言）；工具执行路径产生 `analyst_factor_tool` 用途标签与耗时记录（按既有机制 stub）。
- [ ] **Step 2–4: 红 → 实现 → 绿**（A2）。
- [ ] **Step 5: Commit** `feat(agents): register factor-library tool for technical analyst with trace tag (T2)`。

### Task 3: 消融变体 + 烟测 + 收口

- [ ] **Step 1: 写失败测试**：`build_variant_graph("tech_factor_tool")` 与 `("tech_bare")` 均可构建且工具集差异正确（带/不带工具）；报告模板渲染：CI 含 0 → 文本「无显著差异」且**不含**「无效」字样。
- [ ] **Step 2–4: 红 → 实现 → 绿**（A3）。
- [ ] **Step 5: 最小实跑烟测（1 对样本）**（有凭据才跑；如实记录）→ Commit `feat(evals): factor-tool ablation variants + CI-zero wording guard (T3)`。
- [ ] **Step 6: 收口**：`uv run pytest tests/factors tests/evals -q` + 全量（记录）；报告 `tests/validation/2026-10-13-add-analyst-factor-tools-validation.md`（含 n=10 批次后续项边界 + 挂 issue）；`tasks.md` 全勾（4.2 如实标注）→（控制器 archive）。
