# Tasks: revamp-ablation-v2-causal-claims

> 顺序约束：1.x 依赖 `update-ablation-driver-parity-and-report-status` 落库（见执行计划 GATED 前置检查）。
> 本 delta 不含 P1/P2 正式跑批——跑批按预登记另行启动，本清单止于框架收口与通路验证。
> 进度记录（2026-09-16）见 `tests/validation/2026-09-16-causal-ablation-framework-validation.md`。

## 1. P0 工程收口（零 LLM 成本）

- [x] 1.1（2026-09-16 解阻）parity delta 已提交（`b24341c`）并归档；本 delta rebase 至其合并结果（21 提交零冲突），因果套件自检 147 passed；同名 Requirement 按「保留双方实质」并入 parity 三段 + 两条 scenario（`881a1ea`）
- [x] 1.2（G1，`94ee58e`）judge 判分/维度过滤/材料落盘移入库侧 `score_run`；驱动仅续跑/记账/coverage；红 14 → 绿 56 passed；审查 Spec ✅/Approved
- [x] 1.3（G2，`83f161e`）四桶层增量条目（配对口径同 judge 维度）+ `citation_pass` 退出层增量结论（`not_for_conclusions`，变体级仍产出）；红 22 → 绿 70 passed；审查 Spec ✅/Approved
- [x] 1.4（G3，`a869996`）索引落 `docs/evals/README.md`（与 render 逐字节一致）；pilot/n10 核对通过；未标注 12 份如实列出（未伪填）；两处解析器小瑕疵待 owner 决策
- [x] 1.5（G5，2026-09-16）`TESTING=1` stub（judge 进程内确定性注入）3 标的 × 3 变体 = 9 runs 跑通；续跑零新增；篡改 digest → RuntimeError；脚本入 `tests/scripts/ablation_pathway_verify.py`。**副产**：发现并修复 parity digest 核验高危缺陷（`1c33a53`，对象列 tobytes 为指针致核验恒失败）

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
- [x] 3.3（G4，`a869996`）metrics §1.7 四类口径 + 「消融 v2 口径切点」时间线段；§1.5/§1.6 与 adba 段落保留
- [x] 3.4 P1 预登记文档起草（10 标的 pilot）：主指标 / MDE / 分型预算 / 终裁成本上限 / 停止规则 / rubric 版本锁定——`evals/ablation/preregister/2026-09-16-p1-injection-pilot.md`
- [x] 3.5（G6，`881a1ea`）报告补录：GATED 执行记录（G1–G5 + 高危 digest 缺陷 + spec 合并）+ 待 owner 复核项更新（4+4 项）；阳性对照/注入确定性由 Task 11/12 既有用例覆盖

## 4. G7 收尾修复（2026-09-16，审查跟进项，非 owner 决策项）

- [x] 4.1 ⑤a `report_status.py` 状态解析行界——跨行捕获曾致 malformed `superseded-by` 通过校验（红→绿，malformed 现抛错）
- [x] 4.2 ⑤b `status_index.py` 徽章携真实目标 + 索引 README 重渲染（marker 区与现场重跑逐字节一致）
- [x] 4.3 ⑥ 四桶层条目 `lower_is_better` 元数据（`blocked`/`analyst_true_fail`=True；`surgical_repaired`/`verifier_normalized`=None）——结论措辞未动
- [x] 4.4 ⑦ `ablation_aggregate_90.py` 四桶渲染 + 标量行标注「不入层增量结论」（路径/阈值/参数化未动，另属登记遗留）
- [x] 4.5 ⑧ 驱动 `main()` 透传 `--judge-repeats`（CLI 契约修复；端到端断言 kwargs + 产物 config + 库侧判分 K）
- 提交 `96a1faf`（11 文件）；scoped 113 passed + `tests/evals -m "not live"` 696 passed；审查 Spec ✅ / Approved

## 5. P1 启动（2026-09-16，owner 指令"启动P1"）

- [x] 5.1 P1 runner 建成（`5f1e4f2`，含盲区/接线缺口披露）+ 注入载荷接线到真实 state 结构（`21b2700`）；两提交审查 Spec ✅/Approved
- [x] 5.2 基准材料生成：10 标的 × analysts 变体真跑 → `reports/ablation/p1/materials/`（002304 遇数据源降级走回退）
- [x] 5.3 跑批执行：离线腿 200 单元（**0 LLM 已核验**）+ 限量真跑腿 13 单元（204 调用）；阳性对照显著（p=4.09e-16）；总体不一致比 0.485 落健康区间
- [x] 5.4 校准报告 + 终裁清单：`tests/validation/2026-09-16-p1-injection-pilot-validation.md` + `2026-09-16-p1-adjudication-worklist.csv`（201 条待 owner 终裁）
- [x] 5.5 P1 命中缺陷修复：anchors 形态崩溃（`09a5029`）+ 预算漏计/void 先跑后废（`fdfeb9f`）
- [x] 5.6（待 owner）终裁 201 条 → 回填 escape_rate；随后按校准结论重校注入强度（period_shift 降强度；context 级真跑强化靶点）
  （完成核验 2026-09-19：①终裁由 P1 正式批覆盖——278/278 真逃逸终裁、pending=0（metrics §1.7）；②period_shift 强度重校完成——pilot 1.000 too_hard → 正式批 0.500 ok（双形态实例，validation §L77）；③context 级靶点强化演化为 claim/价位级注入——A4 正文一致注入 + A5 illegal_price（族 A 补测批））
