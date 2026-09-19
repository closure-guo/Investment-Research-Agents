# 验证报告：judge 结构化枚举 + 程序封顶 / 消融材料落盘 + 参数化

日期：2026-09-14　delta：`judge-enumeration-cap-and-ablation-materials`（round11 预登记）
执行：本机（Windows / uv）+ 火山方舟 judge 端点（`openai/deepseek-v4-flash`）

## 0. 变更范围

| # | 变更 | 层 | 触发证据 |
|---|---|---|---|
| 1 | debate_quality 输出契约加 `points` 结构化枚举 + `run_judge` 按枚举由代码封顶 ≤4；枚举缺失 fail-open 但标 `enumeration_missing` | judge 判据 | round10 收口：prompt 内强制枚举（v5）到顶，8 行仍有漏判（宁德）与回归（招行） |
| 2 | 消融每次 run 的 judge 材料（`judge_vars`）按 `(variant, ticker, repeat)` 落盘；run 记录携材料路径 + judge 明细；标的/重复次数参数化 | 消融设施 | 材料不落盘 → 改 rubric 只能绕道 Langfuse 反解材料；`TICKERS/REPEATS` 为模块常量无法跑 10 重复权威轮 |
| 3 | 判分协议改 **K 次均值**（`run_judge_mean`，`--judge-repeats` 默认 3），每次分数与极差落盘 | 测量协议 | round11 实测单次调用 5/4 双峰翻转（宁德 n=13：4 分 7 次 / 5 分 6 次，σ≈0.5） |

## 1. 单元与门禁（TDD 先红后绿）

```
uv run pytest tests/evals/test_judges.py tests/evals/test_ablation_pilot.py tests/evals/test_ablation.py tests/evals/test_run.py -q
→ 78 passed
uv run pytest tests/evals/ -q
→ 468 passed
```

- **红灯记录**：`TestDebateEnumerationCap` 首轮 6 failed（`KeyError: 'points'` 等）；
  `TestRunJudgeMean` 首轮 5 failed（`ImportError: run_judge_mean`）；
  `tests/evals/test_ablation_pilot.py` 对**改动前**脚本整体 10 failed（无
  `parse_args`/`persist_materials`/`run_pilot`），恢复改动后 11 passed。
- 覆盖点：纯定性封顶 4 / 全数据不封顶 / 封顶不下压低于 4 / 枚举缺失 fail-open + 标记 /
  非法 points（非 list、缺 type）按缺失处理 / 非 debate 维度结果形状不变（既有精确断言契约）；
  材料落盘路径与内容 / run 记录材料路径与明细 / 参数化入 config / 断点续跑键不变 /
  续跑不重复消耗 / 扩 K 只跑新增；均值语义（奇偶/None 计入失败/全失败 None/K=1）。

## 2. 真实重判（offline，不重跑管线；同批材料纯归因）

`tests/scripts/rejudge_debate_v6.py`（round9 8 条 deep trace 的 debate 材料，与 round10 同源；
K=3 均值）→ `evals/judge_calibration/data/judge-sample-round11-debate-v6.jsonl`

| trace | 查询 | v5 | v6（K=3 均值） | scores | 纯定性条数 | 极差 |
|---|---|---|---|---|---|---|
| 04baff5c | 宁德时代 | 5 | **4.333** | [4,5,4] | 1 | 1 |
| 20712f2a | 美的集团 | 4 | 4.0 | [4,4,4] | 1 | 0 |
| 4fda4b19 | 平安 | 4 | 4.0 | [4,4,4] | 5 | 0 |
| 53448e4b | 招商银行 | 5 | **4.333** | [4,5,4] | 4 | 1 |
| 9f4d576d | 比亚迪 | 4 | 4.333 | [4,4,5] | 0 | 1 |
| a163494e | 平安银行 | 4 | 4.0 | [4,4,4] | 2 | 0 |
| b344d937 | 中芯国际 | 4 | 4.333 | [5,4,4] | 2 | 1 |
| d46e0942 | 贵州茅台 | 4 | 4.333 | [4,5,4] | 2 | 1 |

**预登记判定：达标**——① 满分（5 分）行数 v5 2 → v6 **0**（≤1/8 ✓）；② 漏判样本宁德
5 → 4.333 且理由原文引用审计指认标头「价格战加速产能出清利好龙头份额集中」判为纯定性 ✓；
③ `enumeration_missing` **0/8**（judge 遵守 points 契约，机制生效）✓。对照审计 ground truth
（8 行全应得 4）：v6 均值 4.17，平均偏差 ≈0.17；v5 均值 4.38（含 2 行 5）。

**副作用（如实记录）**：
- **取值域压缩**：v6 后 8 行全部落在 4.0–4.333 → 5 分档在实际样本中近乎不可达
  （真实辩论几乎总有个别纯定性标头）。对消融的含义：debate 维度的**分辨力受限**，
  该层增量预期≈0，读数须与「纯定性条数 + 封顶理由」并读（已记 §3 待决策候选）。
- **调用级噪声普遍**：K=3 中极差>0 的行 **5/8**（不止宁德）→ 单次调用的 5/4 抽样翻转
  是普遍现象，K 次均值是必要而非保险。

## 3. 程序封顶是否真的触发过？

本轮 8 行 `cap_applied` 均为 false——因为**最低分那次调用的枚举里已含纯定性，LLM 自己也给了 4**，
代码无需再压。封顶逻辑本身由单测钉死（`test_qualitative_point_caps_score_to_4`：score=5 +
qualitative → 4，cap_applied=True）。即：v6 的双保险是「LLM 按枚举扣分（本轮生效）」+
「代码兜底（本轮未需）」，兜底路径由单测保证。

## 4. 消融设施验证（零 LLM）

- 材料落盘：3 变体 × 2 标的 × 2 重复 → 12 个 `judge_vars/<variant>-<ticker>-<i>.json`，
  内容即 `judge_vars`；run 记录的 `materials_path` 与文件一一对应。
- 参数化：`--tickers 600519 --repeats 2` → 产物 `config.tickers/repeats/judge_repeats`
  如实记录，runs 条数 = 标的 × 3 变体 × 重复。
- 断点续跑：同 resume 二次运行零新增调用；`--repeats 1 → 2` 只新增 repeat=1 的三条。
- 聚合口径不变：`aggregate_results` 输入形状未变（新增字段为附加信息），
  `tests/evals/test_ablation.py` 既有 CI 用例全绿。

## 5. 全量回归与静态检查

- 全量：`uv run pytest -q --ignore=tests/e2e` → **2371 passed, 2 skipped**（789.85s；含本轮全部新增用例）
- `uv run pytest tests/evals/ -q` → **469 passed**（judge/pilot/ablation/run 全绿；含库侧均值协议回归用例）
- ruff check + format：`evals/`、`tests/evals/`、`tests/scripts/` 全绿
- mypy：`evals/judges.py`、`evals/ablation.py` → Success, no issues

## 6. 边界与遗留

- **程序封顶的兜底路径本轮未实际触发**（LLM 自扣已足够）——由单测钉死，非死代码。
- **枚举粒度不稳**：judge 有时列子句、有时列整行（如招行行整行含数据+定性混合），
  故「纯定性条数」暂不作为消融指标直接入账（需先在 rubric 里钉死粒度，见 §3 待决策）。
- **取值域压缩**：v6 下 debate 分数压缩到 4.0–4.333，层增量分辨力受限——已在
  `docs/evals/metrics.md` §3 登记三个候选（连续计数 / pairwise / 输入侧确定性信号）。
- 单次调用 σ≈0.5 的噪声只在本轮 8 行材料上测过；更大样本的噪声下界留待 27/90 跑批次核对。
