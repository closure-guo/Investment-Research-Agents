# 验证报告：calibrate-decision-confidence（单变量 prompt 校准，round15）

**日期**: 2026-10-22（round15 运行 09:36–11:23，107 分钟）
**变更**: 三决策节点（trader / risk_judge / fund_manager）confidence 段改为「三档判据 + 示例 0.85/0.55/0.30 + 证据引用义务」
**产物**: `reports/evals/round15-confidence-calibration-20261022-112331.json`
**判分**: judge K=3，9 条 deep，`judge_failures=0`，四维无缺失

## 1. 假设与判据

- 假设：confidence 扎堆 0.50–0.60 是**示例锚定**造成（旧模板只给区间、示例全在中段）→ 改为三档示例后，高/低置信尾部应出现。
- 成功判据（跑批前登记于 `openspec/changes/calibrate-decision-confidence/proposal.md`）：`confidence_ge_070_rate > 0` 或 `confidence_lt_040_rate > 0`，且 judge 四维不作成败判据（只作健康检查）。

## 2. 读数

| 指标 | 基线（近 14 天生产预测 84 条） | round15（9 条 deep） | 判据 |
|---|---|---|---|
| `confidence_n` | 84 | 9 | — |
| 中位数 | 0.55 | **0.55** | — |
| 0.50–0.60 占比 | 75% | **100%**（9/9 落在 0.50–0.58） | — |
| `≥0.70` 占比 | 0% | **0%** | 未达成 |
| `<0.40` 占比 | ~1% | **0%** | 未达成 |
| action 分布 | watch/neutral 为主 | **9/9 watch** | — |

judge 健康检查（仅参照）：relevance 4.778 / debate 4.185 / dg 4.296 / consistency 4.741 —— 与 r14（4.556/4.111/4.370/4.815）同量级，无回归。

citation 侧同轮读数：blocked 9/9、真错 17.1/条、归一 23.3/条、coverage 0.901（与 r14 的 12.0/22.2/0.911 同向，继续待分桶归因）。

## 3. 结论（假设被证伪）

- **prompt 三档校准没有改变分布形状**：9 条 deep 全部落在 0.50–0.58，且 9/9 动作为 watch。
- 结合 decision-hysteresis 规格（证据均衡带内默认观望）与 dataset 构成（9 只大盘蓝筹、两融/行业分歧常态），更可能的机制是：**决策策略本身在该样本上处于均衡带 → 输出 watch → confidence 随「中等证据」落在中段**。示例锚定不是主因。
- 因此本轮为**阴性结果**：改法正确执行、可观测已建立，但目标指标未移动。按评估纪律不再叠加 prompt 补丁（避免对同一指标反复试错）。

## 4. 处置

- **保留**三档判据与示例：契约更明确、judge/分布无回归，且它是后续任何 confidence 机制的前置（避免回退成区间描述）。
- 登记下一轮候选（择一，需 owner 决策）：
  1. **数据集分层**：加入强信号/强反向样本（涨停/业绩暴雷/强赎），验证均衡带外的尾部是否出现；
  2. **代码侧 confidence 先验**：由确定性证据计数（锚点条数、矛盾数、数据缺失率）映射档位，LLM 只在档内微调；
  3. 接受现状：在「观望为主」的产品定位下，confidence 中段是正确语义，仅停止把它当分辨力指标。

## 5. 关联

- `openspec/changes/calibrate-decision-confidence/`（本轮 sync + archive）
- [metrics.md §1.20 confidence 分布口径](../docs/evals/metrics.md)
- [round14 收口报告](../docs/evals/2026-10-22-round14-收口报告.md)
