# Tasks: add-analyst-revision-factors

- [ ] 派生视图纯函数：`derive_revision_matrices` 三因子（幂等可复现 / 基线缺失 NaN / 生效日 T+1 映射 / 广度下限外的 NaN 语义）——单测钉死 spec 四个场景
- [ ] 值因子候选形态接入 `admit_candidates`：values 候选跳过 DSL 求值、闸①事件人群口径、闸0 查重键 `revision:<feature_key>`（同键拒收 / retired 重提两态）
- [ ] `insufficient_event_history` 拒收留痕（记准入账本 gate=2）+ 7 交易日重放间隔跳过逻辑（幂等：同日重复执行零重复裁决）
- [ ] 衰减监控值因子分支：`source="research"` 走派生视图、派生输入不可得 fail loud
- [ ] 只读接口与账本 source 枚举扩容（`research`）；值因子「表达式」位展示派生定义标识
- [ ] 日批任务「研究面因子派生与准入」注册（统一日批通道：调度/运行历史/补跑/单飞锁；零 LLM 依赖；研报库不可达 fail loud / 三表空正常零候选）
- [ ] 前端因子库页 `research` 来源标签与派生定义展示（非交互类以外的小改动，跑既有 vitest）
- [ ] 与 `analyst-ex-post-verification` 读写边界回归测试（两线互不写对方表）
- [ ] 端到端验收：以 10-20 起的真实研报库跑一次日批（预期 `insufficient_event_history` 留痕），验证账本行与摘要 `{derived, submitted, admitted, rejected, insufficient_history}`
- [ ] `openspec validate add-analyst-revision-factors --strict` 通过；ruff / mypy / 全量相关 pytest 绿
