# 029: 单点修复的收益在遥测里不可见——记账要「该分析师全 PASS」，改对也不计入（2026-09-17）

## 症状

P1-A4 补测（20 标的 × 1 注入单元 + 2 自然命中单元 = 22，`tests/validation/2026-09-16-p1-injection-pilot-validation.md` §17）
出现一组自相矛盾的读数：

- 修好的：改写值经校验器单条核对 **20/20 全部改对**（GLM 回填到真值），**终稿留错 0/20**；
- 记账的：`value_mismatch_repaired` 口径只认 **4/22**；
- 更极端的对照：把仲裁范围换回**全库**（本批观测装置的第一版），记账 **0/22**。

## 根因

`citation_node.py` 的 sparse 修复分支：

```python
re_results = verify_claims(_extract_claims(reports[agent]), state)
re_report = CitationReport.from_results(re_results)
if re_report.all_passed:           # ← 该分析师「全部 claim」都过才算修复成功
    value_mismatch_repaired += len(records)
    retry_targets.remove(agent)
```

`all_passed` 的分母是**该分析师的全部 claim**，而稀疏 value_mismatch 只是其中一处。
分析师同一轮还有别的 FAIL（术语/期次/语言覆盖等——本批 20 个产物里 14 个至少有一处）时：

1. 修复调用发生了、正文也回填了（`reports[agent] = _with_markdown(...)` 在重校验**之前**，
   仲裁失败**不回滚**）；
2. 但 `value_mismatch_repaired` 不 +1，`retry_targets` 也不移除该分析师。

于是**「改对了」与「记上了」脱钩**：正文里的错数字已被改掉，遥测里却只有「该分析师仍有 FAIL」。
在 `CITATION_AUTO_RETRY_ENABLED=False`（incident 026 阶段 0，当前默认）下也没有全量重试兜底，
该分析师的这一处修复就**在任何观测面上都不留痕**。

## 影响面

- **本批实测**：注入腿 20 例中 **16 例**属于此形态（80%），`value_mismatch_repaired` 低估 16/20；
- **生产侧**：任何「稀疏 value_mismatch + 同分析师另有 FAIL」的轮次，修复的收益都记不上；
  决策（是否回退重试）不受影响（该分析师本来就该重试），**受影响的是遥测与据此做的机制价值判断**；
- **消融侧（本次发现它的地方）**：A4 的登记主指标「修复前后真 FAIL 率差」若直接取
  `value_mismatch_repaired` 口径，会把「改对但未记账」误读成「机制不工作」——本批 delta 0.182
  远低于实际（终稿留错 0/20）。**这就是「指标低 ≠ 能力差」的又一实例：先分桶归因，再谈处置。**

## 判定

**真问题（记账口径 / 遥测缺陷）**，非误报、非能力不足。修复回路本身在本批被证明有效
（改对 20/20、终稿留错 0/20）；缺陷在于**收益的记账范围**。

## 处置建议（未实施，须走 delta）

候选（按侵入性排序，供 owner 选择，本 incident 不自行改产品行为）：

1. **拆报遥测**：新增 `value_mismatch_repaired_local`（本次调用改对且单条重校验 PASS 的数），
   保留现有 `value_mismatch_repaired`（该分析师全 PASS 口径）——两个口径都留，别互相顶替；
2. **仲裁范围分级**：`all_passed` 只作「移除 retry_target」的条件（现状正确），
   **不再兼任**修复收益的记账条件；
3. 若采纳 1/2，同步更新 `citation-verification` 规范与 `metrics.md` 口径（口径变更先改 §1 再动代码）。

## 复现

`uv run python tests/scripts/p1_a4_repair.py`（22 次 LLM 调用）；对照单测
`tests/evals/causal_ablation/test_repair_a4.py::TestValueCorrectness`（零 LLM，验证
「值改对」与「是否记账」是两件事）。
