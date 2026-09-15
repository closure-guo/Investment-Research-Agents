# Tasks: revamp-ablation-v2-causal-claims

> 顺序约束：1.x 依赖 `update-ablation-driver-parity-and-report-status` 落库（见执行计划 GATED 前置检查）。
> 本 delta 不含 P1/P2 正式跑批——跑批按预登记另行启动，本清单止于框架收口与通路验证。
> 进度记录（2026-09-16）见 `tests/validation/2026-09-16-causal-ablation-framework-validation.md`。

## 1. P0 工程收口（零 LLM 成本）

- [ ] 1.1 确认 `update-ablation-driver-parity-and-report-status` 已收口合并，本 delta rebase 到其合并结果（project-workflow §6 同 Requirement 冲突处理）
      **阻塞**：该 delta 的改动（`diff_mean` 重命名 / 驱动接回 `_applicable_dims` / 驱动侧 digest 核验）在 2026-09-16 夜晚仍为未提交状态，无法 rebase。
- [ ] 1.2 F0 后半：`tests/scripts/ablation_pilot.py` 的 judge 判分与 `judge_vars` 落盘逻辑移入库侧 `evals/ablation.py`，驱动只保留续跑/记账/coverage 包装；驱动侧不再存在判分/过滤/落盘重复实现（先写失败测试：驱动薄壳化后现有跑批行为不变）
      **阻塞**：同 1.1（编辑该文件会覆盖未提交改动）。
- [ ] 1.3 F3：消融聚合的 citation 腿接入四桶拆报口径（blocked / analyst_true_fail / surgical_repaired / verifier_normalized），`citation_pass` 标量退出层增量比较路径（带回归测试：旧标量字段仍产出但不进层增量结论）
      **阻塞**：同 1.1（`evals/ablation.py` 聚合路径 + `evals/run.py` 拆报输出均被他方持有）。
      **部分前置已就绪**：四桶常量与拆报聚合已在 `evals/causal_ablation/escape.py::split_verifier_buckets` 落地（Task 6）。
- [ ] 1.4 F8：结论注册表——evals 报告头部 `status: active | superseded-by` 字段约定 + docs/evals 索引状态渲染；存量报告（pilot.md / n10 报告）作为首批注册对象核对一遍
      **代码部分已完成**：`report_status.py`（解析/校验/徽章）+ `status_index.py`（索引渲染）已落地并测试（Task 9 / Task 13）。
      **文档部分阻塞**：`docs/evals/` 索引与存量报告核对须在 parity 落库后执行（该目录正被改动）。
- [ ] 1.5 薄壳化后 3 标的通路验证（复用 resume.json 断点续跑设施，TESTING=1 stub 模式跑通全链路，digest 核验生效）
      **阻塞**：依赖 1.2。

## 2. 因果主张登记与指标管线（框架代码）

- [x] 2.1 因果主张登记表落库（A1–A7 / B1–B6 数据结构 + 无主张对象进矩阵时的拒绝校验 + 单元测试）——`evals/causal_ablation/claims.py`
- [x] 2.2 预登记机制：文档 schema（主指标/MDE/决策阈值含换算依据/样本量依据/停止规则/rubric 版本）+ 「无有效预登记拒绝跑批」门禁（单元测试覆盖拒绝路径）——`evals/causal_ablation/preregister.py`
- [x] 2.3 单元级判定落库：`unit_id, ticker, run, variant, unit_type, judgment, method(code|nli|judge), confidence` 记录结构与写入（单元测试）——`evals/causal_ablation/units.py`
- [x] 2.4 聚类 bootstrap 聚合：以标的为重采样簇的 bootstrap CI + 设计效应（ICC 折算有效 n）披露（单元测试：簇结构正确、设计效应计算正确）——`evals/causal_ablation/aggregate.py`
- [x] 2.5 注入矩阵基础设施：8 类污染的确定性 fixture 构造器 + 机制开关点注册 + 离线重放型/真跑型分型标记（单元测试：确定性复现、成本分型、非变异注入）——`evals/causal_ablation/injection.py`
- [x] 2.6 逃逸率统计与四桶误报分离的聚合逻辑（单元测试：误报不进分母、未终裁不得报 0%）——`evals/causal_ablation/escape.py`
- [x] 2.7 族 B 指标管线：B1 差集提取 + 吸收判定接口、B3 出处率代码化检查、B5 pairwise 盲评协议（位置随机化 + K 次多数决）——`evals/causal_ablation/family_b.py`
      （LLM/NLI 判定以 callable 注入，真实模型接线随 P2 跑批接入）
- [x] 2.8 LLM 判定校准门控：凡 method=nli|judge 的维度挂一致率 ≥80% 门控（单元测试：未达标阻断结论）——`evals/causal_ablation/calibration_gate.py`
- [x] 2.9（追加）结论句式纪律：两句式 + MDE 强制、裸「未获统计支持」拒绝——`evals/causal_ablation/conclusion.py`

## 3. 验证与文档

- [x] 3.1 阳性对照验证：已知劣化变体（关 verify_citations）的差异可被逃逸率管线检出（不一致对子比例 ≥50%）——`tests/evals/causal_ablation/test_positive_control.py`
- [x] 3.2 注入强度 pilot 校准判据：不一致对子比例 <10% → too_easy、>90% → too_hard、否则可扩批（`pilot_calibration.py`，含停止规则建议文案）
      （真实材料上的 pilot 数据跑批待 P1 启动后执行）
- [ ] 3.3 `docs/evals/metrics.md` §1 口径先行变更：登记因果下游指标口径、逃逸率口径、结论两句式、结论注册表；时间线加一行
      **阻塞**：该文件正被 parity delta 改动。
- [x] 3.4 P1 预登记文档起草（10 标的 pilot）：主指标 / MDE / 分型预算 / 终裁成本上限 / 停止规则 / rubric 版本锁定——`evals/ablation/preregister/2026-09-16-p1-injection-pilot.md`
- [ ] 3.5 人工验证报告落 `tests/validation/`：薄壳化通路验证 + 注入确定性复现抽查 + 阳性对照结果
      **部分完成**：框架部分验证报告已落 `tests/validation/2026-09-16-causal-ablation-framework-validation.md`；薄壳化通路验证（依赖 1.2）待解阻后补。
