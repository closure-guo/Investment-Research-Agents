# 选股空因子库降级通道（add-screening-empty-library-fallback）

## Why

因子库当前为空（内置 5 因子与 RD-Agent 外部批全部倒在真实闸口读数；解锁真因子供给是独立工作线），
而 L2 打分器对「无有效 IC」按契约 fail loud（`ValueError: 无有效 IC 信息，拒绝打分`）——按现行
「选股日批编排」契约，L2 失败即批次 failed、零池。后果：选股页长期挂 failed（10-15 与 10-16 两批
实测），L1（确定性过滤，10-16 实过 3990 只）与 L3（LLM 粗筛）以及下游的池→批量分析→决策→执行
整段链路因 L2 单点而空转。

治理边界（本 delta 明确不做）：不动五道闸、不降阈值、不伪造因子权重——那会把噪声包装成信号，
正是系统相对外部软择优的核心价值所在（spike 已实证）。本 delta 只补一条**显式披露的过渡通道**：
库内无可打分因子时，L2 显式跳过 + 确定性流动性排序，页面与接口三处披露「非因子信号」；
真因子入库后自动回到 L2 正常路径（降级为死分支）。

## What Changes

- **新 Requirement「无可打分因子降级通道」（stock-screening ADDED）**：触发条件 = L2 打分器拒绝
  打分的条件（无 active 因子，或全部 active 因子无有效 IC 记录）；L2 显式跳过（MUST NOT 静默等权/
  伪造权重）；以近 20 日日均成交额降序（同额按代码升序）产出 Top-N 并照常落 L2 名单（在榜稳定性
  门槛照常累计）、照常执行 L3 与精选池；批次标注 `selection_mode`（`factor` / `fallback`）；
  其余任一步骤失败仍 fail-closed 记 failed（降级不吞异常）。
- **MODIFIED「选股日批编排」（stock-screening）**：L2 一步注明按降级通道执行（库空时显式跳过并
  披露）；「任一步骤失败 → failed」语义保持。
- **MODIFIED「批次历史查询」（stock-screening）**：批次字段新增「选择模式（`factor` / `fallback`）」。
- **MODIFIED「选股页」（frontend）**：`fallback` 批次展示降级披露横幅（明示「非因子信号、按近 20 日
  日均成交额排序」）；综合分列切换为「近 20 日日均成交额」语义呈现。

## Impact

- 影响面：`screening/{ranker,pipeline,store}.py`、`api.py`（池载荷 + test seed 造数）、
  `frontend`（ScreeningPage + types）、E2E `screening-page.spec.ts`；screening 库新增
  `screening_batches.selection_mode` 列（幂等迁移，旧行默认 `factor`）。
- 不做：闸门/阈值/因子库内容零改动；L2 打分器本体零改动（降级判定在其上游做前置检查）。
- 恢复语义：库内出现可打分因子后同批日自动走 `factor` 路径，无需配置或手工切换。
