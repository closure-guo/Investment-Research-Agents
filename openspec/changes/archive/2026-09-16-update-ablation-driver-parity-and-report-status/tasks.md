# Tasks: update-ablation-driver-parity-and-report-status

> **非交互类变更**（文档 + 评估口径实现），不触发 E2E 门禁。
> 全部改动不重跑跑批、不烧 token——n10 数字继续作为权威（补披露后）。

## 1. 止血：对外材料真实性修正（纯文档，先做）

- [x] 1.1 `evals/ablation/results/pilot.md` 头部加 `**status**: superseded-by: docs/evals/2026-09-03-消融n10权威结果.md`；「简历素材对照」段保留原文 + 撤回说明（结论已被 #109/#111/#112 全盘推翻；成本口径 41% 为 pilot 的 full-vs-analysts 口径、n10 为 28.7%；「显著退步」在 n10 不成立）
- [x] 1.2 `docs/evals/2026-09-03-消融n10权威结果.md` 补四处适用口径披露：① plus_debate 的 grounding/consistency 为 #112 伪影存活（该变体无对应层），full−plus_debate 两条 Δ 标为无效比较（表格删除线 + 结论区标注）；② 本批次（09-03）早于 judge 校准 round7 达标（09-13），judge 未校准；③ 层增量配对单元为标的（3 个）而非 run（30 条）；④ citation_pass 契约噪声（#105）不作为层增量解读。同时修正结尾「本报告为修复后 n=10 权威版」的自我定位
- [x] 1.3 `README.md` 消融节改写：「judge 维度按变体适用性过滤」限定为库侧/新批次（n10 批次未过滤）；删除「据此裁剪管线省 ≈29% token」，改为「增量价值未获统计支持（有效 n=3，评估分辨率不足）」+ 成本梯度实测（辩论层 +7.9% / 决策+风控层 +30.1% / 相对 analysts 整体 +40.3%，裁到 analysts 省 28.7%）+「是否裁剪待 v2 消融裁决」
- [x] 1.4 `README.md` 诚实边界补 n10 自身两处（#112 存活进 90 批次 + judge 未校准），与 pilot 的 #111/#112 披露并列

## 2. 堵漏：驱动接回库侧（TDD 先红后绿）

- [x] 2.1 红灯：`tests/evals/test_ablation_pilot.py::TestApplicableDimsParity`——假件提供 `_applicable_dims`，驱动对 `plus_debate` 的 `decision_grounding`/`consistency` 记 `None` 且不产生 judge 调用（判分次数 7 vs 硬编码形态的 9）
- [x] 2.2 红灯：假件 vs 库侧属性面对照（`test_fake_matches_library_attribute_surface`）+ 库侧不暴露 `_applicable_dims` 时驱动必须炸（防 a86986a 式 mock 缺口复发）
- [x] 2.3 红灯：`TestSnapshotDigestVerification`——台账 digest 与重建 digest 不一致时抛错（含标的与两侧 digest）且失败前不消耗 run；一致时正常续跑
- [x] 2.4 实现：`tests/scripts/ablation_pilot.py` 删硬编码维度分支改用 `abl._applicable_dims`；每条 run 前核验 digest（首次登记、之后比对，不一致 `RuntimeError`）
- [x] 2.5 既有 `tests/evals/test_ablation_pilot.py` 其余用例全绿（18 passed）
- [x] 2.6 突变验证：把过滤改回硬编码 analysts 分支（原始 bug 形态）→ 3 条用例转红；还原后全绿

## 3. 立规：聚合字段正名与配对单元披露（TDD 先红后绿）

- [x] 3.1 红灯：`tests/evals/test_ablation.py::TestLayerIncrementFieldName`——层增量含 `diff_mean`、不含 `diff_median`；值等于逐标的差值的均值（构造均值 3.0 vs 中位 0.0 的分离序列）；`pairing_unit`/`pairing_units` 披露
- [x] 3.2 实现：`evals/ablation.py::aggregate_results` 字段改名 + 层条目携配对单元与数量
- [x] 3.3 `tests/scripts/ablation_aggregate_90.py` 两处消费点同步改名（未顺手改路径与阈值——该脚本参数化是 metrics.md 已登记的独立遗留项）

## 4. 立规：报告状态契约（TDD 先红后绿）

- [x] 4.1 新增 `tests/evals/test_report_status.py`——`evals/ablation/results/*.md` 每份须含合法 `**status**:` 头；`superseded-by` 路径须可解析；被标 superseded 的报告须带就地撤回说明；空目录即失败
- [x] 4.2 `pilot.md` 头部满足契约（与 1.1 同批落地）
- [x] 4.3 突变验证：指针指向不存在文件 → 转红；删除 status 头 → 转红；还原后全绿

## 5. 口径登记

- [x] 5.1 `docs/evals/metrics.md` 新增 §1.5「消融层增量」（点估计 `diff_mean` / 配对单元=标的 / 有效 n=标的数 / 维度适用性 / 快照一致性 / 报告状态头）+ §1.6「报告引用纪律」
- [x] 5.2 `docs/evals/metrics.md` §2 时间线补「消融口径切点（2026-09-15）」条目：字段改名 / 驱动接回库侧 + digest 核验 / pilot 标记 / n10 补披露 / 成本数字仍有效但不足以支撑裁剪

## 6. 验证与收口

- [x] 6.1 `uv run pytest tests/evals/` 全绿（新增用例转绿；live 用例的 3 项失败为干净工作区既有状态，需真实 API key，不进 PR 门禁）
- [x] 6.2 全量 `uv run pytest -m "not live"` 绿 + `uv run ruff check` / `ruff format --check` 通过 + `uv run mypy`（改动文件：驱动 15 → 0，`evals/ablation.py` 0；`tests/evals/test_ablation.py:16` 为既有的故意负例）
- [x] 6.3 `openspec validate update-ablation-driver-parity-and-report-status --strict` 通过
- [x] 6.4 归档前置：本文件全勾 + spec sync（evaluation 主规范 1 条 MODIFIED + 1 条 ADDED，`openspec/specs/evaluation/spec.md` +58 行）+ `openspec validate --all --strict` 通过（57/57）
