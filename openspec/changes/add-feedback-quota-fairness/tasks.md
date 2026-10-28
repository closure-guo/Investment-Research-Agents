# Tasks: add-feedback-quota-fairness

- [x] 前置：10-31 契约回喂判效读数归档（untranslatable 趋势 + 入库数），确认本 delta 实施排期不与 #482 归因窗口重叠（排期已确认：实施 10-28 完成，**部署门至 10-31 判效归档后**——不部署即不重叠）
- [x] rejected per-gate 保底分配器（floor + 占比余量 + 空位归还）TDD：先红（闸④ 0 出场的复现测试，用真实比例合成数据）后绿
- [x] untranslatable stage 轮转代表性 + 既有按日轮转回归测试
- [x] `quota_allocation` 读数字段 + 读数与清单一致性测试
- [x] 既有语义零回退回归（admitted 全量/去重/limit/contract 块/gate_rejections）
- [x] 真实 `data/factor_research/harvest_state.json` 全量回放：闸④ 出场 9 ≥5、闸② 25/48 不独占、五闸全可见（0:3 1:6 2:25 4:9 5:5）、双 stage 各 10
- [x] **独立审查修复轮**（两 subagent 交叉审查）：C1 渲染层可见性（闸间轮转输出 + 渲染层回归测试；真实数据端到端渲染块含 gate4/gate5/REDUNDANT_WITH_POOL）、I1 稀缺预算轮转公平、M1 floor 负数钳制、M2 读数 unknown 桶全量对账、M3 文件头口径同步；500 轮不变量模糊测试 + 端到端复验全过
- [ ] 部署（`--build backend`，避开日批窗口）+ 次日 15:55 后核对生产载荷 `quota_allocation`
- [ ] 人工验证报告落 `tests/validation/` → sync + archive
