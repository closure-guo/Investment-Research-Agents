## 1. judge 结构化枚举 + 程序封顶（TDD 先红后绿）

- [x] 1.1 红灯：`tests/evals/test_judges.py`——纯定性论点封顶 4 / 全数据不封顶 / 封顶不下压低于 4 / 枚举缺失 fail-open + `enumeration_missing` / 非 debate 维度结果形状不变 / v6 rubric 含 `points` 契约
- [x] 1.2 `RUBRICS["debate_quality"]` 输出契约加 `points`（含语义与格式示例）；`RUBRIC_VERSIONS` v5 → v6
- [x] 1.3 `run_judge` 归一化 `points` + 代码封顶 + 遥测字段；`evals/run.py` comment 附封顶/缺失证据
- [x] 1.4 `tests/evals/test_judges.py` 全绿（38 例）+ 既有用例不回归

## 2. 消融材料落盘 + 参数化（TDD 先红后绿）

- [x] 2.1 红灯：`tests/evals/test_ablation_pilot.py`——材料落盘路径与内容 / run 记录含路径与 judge 明细 / `--repeats`/`--tickers` 参数化写入 config / 断点续跑键不变（mock 掉管线与 judge，零 LLM）
- [x] 2.2 `ablation_pilot.py` 拆出 `parse_args` / `persist_materials` / `run_pilot(...)`（可注入依赖），`main()` 变薄驱动
- [x] 2.3 run 记录与 resume 落盘携带材料路径 + judge 明细；产物 config 记录实际 tickers/repeats
- [x] 2.4 新老用例全绿（含 `tests/evals/test_ablation.py` 回归）

## 3. 离线重判验证（真材料，不重跑管线）

- [x] 3.1 用 round10 同批 8 行材料跑 v6 重判（tests/scripts/rejudge_debate_v6.py，K=3 中位）：宁德 04baff5c 5→4（理由正引审计标头）、美的/平安等保持 4；批量单次调用出现 1 次翻转（详见验证报告）
- [x] 3.2 验证记录落 `tests/validation/2026-09-14-judge-enumeration-cap-and-ablation-materials-validation.md`
- [x] 3.3 `docs/evals/metrics.md`：§2.5 round11 预登记（v6 单变量离线重判）+ 时间线/待决策更新（debate 5 分边界条目收口）

## 3.5 judge 中位数协议（round11 实测单次调用 5/4 翻转后追加）

- [x] 3.5.1 红灯：`TestRunJudgeMedian`（奇/偶中位、None 计入失败、全失败 None、K=1 等价单次）
- [x] 3.5.2 `run_judge_median`（`median_low`：偶数对高分保守）+ 消融驱动 `--judge-repeats`（默认 3）+ run 记录 `scores`/`score_spread`
- [x] 3.5.3 驱动测试补中位协议断言（明细含每次分数与极差；CLI/config 记录 K）

## 4. 收口

- [x] 4.1 `openspec validate --strict` + 归档 + spec sync（主规范 54/54）
- [x] 4.2 全量 `uv run pytest` 绿 + ruff/mypy
- [x] 4.3（PR 见提交说明） PR + CI 绿
