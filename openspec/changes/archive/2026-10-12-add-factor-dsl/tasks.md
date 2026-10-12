# Tasks: add-factor-dsl

## 1. 解析器
- [x] 1.1 实现 DSL 词法/语法解析产出 AST + 全算子解析单测
      —— 新增 `src/finance_agent/factors/{__init__,dsl}.py`（纯函数零 IO、零第三方依赖）；
      手写词法器（带字符偏移 token）+ 递归下降解析器（无中缀运算符，D1）；
      15 个算子逐一解析 + 元数表（D2）逐条断言 + 6 字段叶子 + 整数/小数 + 空白自由 +
      round-trip（spec 场景 `rank(ts_corr(close, volume, 10))` 的 serialize→parse AST 相等）
      —— `tests/factors/test_dsl.py::test_every_op_parses_with_expected_arity`（15 例）、
      `test_spec_scenario_rank_ts_corr_structure`、`test_spec_scenario_round_trip_ast_equality`
- [x] 1.2 非法表达式（未知算子/括号不配对/非数值参数）显式报错 + 错误消息含位置信息的单测
      —— `DSLParseError` 消息末尾固定 `"，位置 <pos>"` 并暴露 `.pos`；
      覆盖：未知算子（含名与位置）、未知字段/未定义标识符（含 window 位 `ts_mean(close, x)`）、
      括号不配对/多余记号/缺逗号、空参数 `rank()`、尾逗号、元数错误、window 非法
      （`10.5`/`0`/`1.0` 小数形态/`ts_corr` 3 号位）、中缀式 `close + volume`、空表达式
      —— 15 例错误路径单测（含位置号逐字断言）

## 2. 复杂度约束
- [x] 2.1 深度 ≤4 / 自由参数 ≤3 / 原始字段 ≤3 的解析期拒收 + 三条约束各自的边界单测（深度 4 通过、深度 5 拒收）
      —— `MAX_AST_DEPTH=4` / `MAX_FREE_PARAMS=3` / `MAX_FIELDS=3` 逐字；
      `ast_depth`（D3 叶子=1）/ `count_free_params`（D4 Number 总数）/ `count_fields`（去重字段名）
      + `parse(enforce_complexity=True)` 默认执行 `_check_complexity`；
      边界：深度 4 通过 / 5 拒收（消息含「深度 5 > 上限 4」逐字）、自由参数 3/4、
      字段种类 3/4（含同名字段去重不触限）、三条同时违反时消息**全部列出**（不短路）；
      `enforce_complexity=False` 旁路（D7，仅测试/验证调用）—— 15 例单测

## 3. AST 工具
- [x] 3.1 AST 规范化（交换律/结合律排序）+ 规范化幂等单测
      —— `normalize`：add/mul/max/min 结合律展平（`add(add(a,b),c)` → 三参）+ 交换律排序
      （按 serialize 规范串）+ 幂等（`normalize(normalize(x)) == normalize(x)`）；
      非交换/非结合算子（sub、ts_corr 含 window 位次）保持参数顺序 —— 8 例单测
- [x] 3.2 子树同构检测 + 正反例单测（含仅参数不同的同构用例）
      —— `is_subtree_isomorphic`：规范化后比较、数字只按「有」匹配（取值忽略）；
      spec 情景 `ts_mean(close,10)` ≍ `ts_mean(close,11)` → True；
      反例：字段不同、op 不同、元数不同、深度不同、数字 vs 字段位置错配；
      正例：交换律等价、结合律分组差异、深树数字全忽略 —— 8 例单测

## 4. 验证
- [x] 4.1 全量测试通过
      —— `uv run pytest tests/factors -q` 82 passed；`uv run pytest tests/factors tests/screening -q`
      137 passed；全量 `uv run pytest tests/ -q -m "not live"` → 1 failed / 4914 passed / 6 skipped
      （唯一失败为既有环境 flake `tests/llm/test_resolver.py::TestContextLengthOverride::
      test_empty_follows_registry_default`：单跑 1 passed，宿主 .env 泄漏 `LLM_MAX_CONTEXT`，
      非本 change 引入；归因四步见 `tests/validation/2026-10-13-add-factor-dsl-validation.md` §0.2）；
      ruff（check + format）与 `uv run mypy src/finance_agent/factors src/finance_agent/screening` 全绿
- [x] 4.2 Alpha101 十公式 round-trip 核对，报告落 `tests/validation/`
      —— 脚本 `tests/validation/2026-10-13-add-factor-dsl-alpha101.py`：10 条公开公式
      **函数式转写**（逐条内嵌原式 + 映射；DSL 无 `sum/covariance/sign/returns/adv20/vwap`
      等，按注释做结构性替换），`parse(enforce_complexity=False)` → serialize → 再 parse →
      AST 相等断言，**round-trip 10/10**；另报告默认上限判定：**拒收 8/10、通过 2/10**
      （通过均为单序列简单式 Alpha#4/#6，与计划「预期全部或绝大多数超限」一致）；
      深度/参数/字段手算内嵌并断言全符（初版手算漏计 `sub(0,·)` 的 0，被脚本抓出后修正——
      证明断言有判别力）—— 报告 `tests/validation/2026-10-13-add-factor-dsl-validation.md`
- [x] 4.3 既有测试无修改通过（红线）
      —— 本 change 全部文件为新增（`src/finance_agent/factors/`、`tests/factors/`、
      tests/validation 两个新文件）；未修改任何既有 src/测试文件（唯一既有文件改动为本 delta
      自身的 `tasks.md` 回填：条目原文保留，仅勾选 + 注记）；全量套件既有用例零修改，
      运行结果与既有 flake 归因见 validation 报告 §0.2
