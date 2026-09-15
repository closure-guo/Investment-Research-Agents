# Tasks: revamp-ablation-v2-causal-claims

> 顺序约束：1.1 完成前不得动 2.x；3.x 跑批类任务前 1.x/2.x 须全勾。
> 本 delta 不含 P1/P2 正式跑批——跑批按预登记另行启动，本清单止于通路验证与框架收口。

## 1. P0 工程收口（零 LLM 成本）

- [ ] 1.1 确认 `update-ablation-driver-parity-and-report-status` 已收口合并，本 delta rebase 到其合并结果（project-workflow §6 同 Requirement 冲突处理）
- [ ] 1.2 F0 后半：`tests/scripts/ablation_pilot.py` 的 judge 判分与 `judge_vars` 落盘逻辑移入库侧 `evals/ablation.py`，驱动只保留续跑/记账/coverage 包装；驱动侧不再存在判分/过滤/落盘重复实现（先写失败测试：驱动薄壳化后现有跑批行为不变）
- [ ] 1.3 F3：消融聚合的 citation 腿接入四桶拆报口径（blocked / analyst_true_fail / surgical_repaired / verifier_normalized），`citation_pass` 标量退出层增量比较路径（带回归测试：旧标量字段仍产出但不进层增量结论）
- [ ] 1.4 F8：结论注册表——evals 报告头部 `status: active | superseded-by` 字段约定 + docs/evals 索引状态渲染；存量报告（pilot.md / n10 报告）作为首批注册对象核对一遍
- [ ] 1.5 薄壳化后 3 标的通路验证（复用 resume.json 断点续跑设施，TESTING=1 stub 模式跑通全链路，digest 核验生效）

## 2. 因果主张登记与指标管线（框架代码）

- [ ] 2.1 因果主张登记表落库 `evals/ablation/claims_registry`（A1–A7 / B1–B6 数据结构 + 无主张对象进矩阵时的拒绝校验 + 单元测试）
- [ ] 2.2 预登记机制：`evals/ablation/preregister/` 文档 schema（主指标/MDE/决策阈值含换算依据/样本量依据/停止规则/rubric 版本）+ 驱动侧「无有效预登记拒绝跑批」门禁（单元测试覆盖拒绝路径）
- [ ] 2.3 单元级判定落库：`unit_id, ticker, run, variant, unit_type, judgment, method(code|nli|judge), confidence` 记录结构与写入（单元测试）
- [ ] 2.4 聚类 bootstrap 聚合：以标的为重采样簇的 bootstrap CI + 设计效应（ICC 折算有效 n）披露，复用 `evals/stats.py` 底座（单元测试：簇结构正确、设计效应计算正确）
- [ ] 2.5 注入矩阵基础设施：8 类污染的确定性 fixture 构造器 + 机制开关点注册（配置项/函数级）+ 离线重放型/真跑型分型标记（单元测试：每类污染可确定性复现、开关遥测可验证）
- [ ] 2.6 逃逸率统计与四桶误报分离的聚合逻辑（单元测试：误报不进逃逸率分母）
- [ ] 2.7 族 B 指标管线：B1 差集提取 + NLI 吸收判定接口、B3 风控数字出处代码化检查、B5 pairwise 盲评协议（位置随机化 + K=3）——接口与确定性部分先行，LLM 判定挂校准门控占位（单元测试：随机化分布、K 次多数决、B6 不进层间归因的输出结构约束）
- [ ] 2.8 LLM 判定校准门控：凡 method=nli|judge 的维度挂 `evals/judge_calibration/` 一致率 ≥80% 门控（单元测试：未达标阻断结论）

## 3. 验证与文档

- [ ] 3.1 阳性对照验证：关闭 verify_citations 的已知劣化变体在注入污染下测出显著差异（管线灵敏度证实；离线重放型即可）
- [ ] 3.2 注入强度 pilot 校准：小规模（2–3 标的 × 8 类污染 × 1 实例）验证污染不是全拦/全穿两极，McNemar 不一致对子比例 ≥10%；不达标则按停止规则收窄并记录
- [ ] 3.3 `docs/evals/metrics.md` §1 口径先行变更：登记因果下游指标口径、逃逸率口径、结论两句式、结论注册表；时间线加一行
- [ ] 3.4 P1 预登记文档起草（10 标的 pilot）：主指标 / MDE / 分型预算 / 终裁成本上限 / 停止规则 / rubric 版本锁定
- [ ] 3.5 人工验证报告落 `tests/validation/`：薄壳化通路验证 + 注入确定性复现抽查 + 阳性对照结果
