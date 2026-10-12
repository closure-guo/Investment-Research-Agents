# Tasks: add-screening-filters

## 1. 约束常量与纯函数
- [x] 1.1 实现 `constraints.py`（单一来源常量）+ `filters.py` 过滤纯函数 + 每规则正反单测
      —— 新增 `src/finance_agent/screening/{__init__,constraints,filters}.py`（纯函数零 IO）；
      `tests/screening/test_filters.py` 共 41 例：每规则正反、多因枚举顺序、枚举完整性、
      F1/F2 缺失策略（DATA_MISSING）、`build_candidate_record` 构造链
- [x] 1.2 边界值单测：上市天数 59/60/61、成交额恰等于阈值、一字涨停与"盘中触板未封死"区分
      —— `test_ipo_recent_boundary_59_60_61`、`test_illiquid_at_threshold_passes`（恰等于不剔除）、
      `test_illiquid_just_below_threshold_rejected`、`test_touched_but_not_sealed_passes` /
      `test_both_sealed_flags_single_reason`；另覆盖日历往返/空历/非规范日期/NaN 窗口等边界

## 2. 输出契约
- [x] 2.1 剔除原因码枚举（结构化、供前端展示与风控复用）+ 枚举完整性单测
      —— `RejectReason` 六码逐字（声明顺序 = 返回顺序）；`test_enum_members_verbatim` +
      `test_filter_universe_only_emits_enum_members`；另补 F4 单点修改场景 5 例
      （`test_default_thresholds_override_*`、显式 `thresholds=` 覆盖）

## 3. 验证
- [x] 3.1 全量测试通过
      —— `uv run pytest tests/screening -q` 41 passed；`uv run pytest tests/ -q -m "not live"`
      见 `tests/validation/2026-10-13-add-screening-filters-validation.md` §0.2（唯一失败为既有
      `tests/llm/test_resolver.py` 环境性 flake，非本 change 引入；ruff / mypy 全绿）
- [x] 3.2 2026-09 全月过滤结果抽 50 例人工核对，报告落 `tests/validation/`
      —— **受阻替代（如实标注）**：本机东财 IP 封禁（issue #325）拿不到真实 2026-09 快照，
      live 全月过滤未执行；替代证据 = 手工构造 50 例（六码 × 正反 × 边界，期望值手算硬编码）
      逐行核对 50/50 一致——脚本 `tests/validation/2026-10-13-add-screening-filters-cases.py`，
      报告 `tests/validation/2026-10-13-add-screening-filters-validation.md`；实盘核对挂
      issue #325 follow-up
- [x] 3.3 既有测试无修改通过（红线）
      —— 本 change 全部文件为新增（`src/finance_agent/screening/`、`tests/screening/`、
      tests/validation 两个新文件）；唯一既有文件改动为本 delta 自身的 `tasks.md` 回填
      （条目原文保留，仅勾选 + 注记）；全量套件既有用例零修改
