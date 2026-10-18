# Design: add-strategy-family-scorecards

## 族注册表 v1（代码内注册，不建新表）

| family_id | 名称 | 数据源 | 收益口径 | 费后处理 |
|---|---|---|---|---|
| decision | 观点线 | `decision_log` 已结算行（`status != 'open'`） | 虚拟结算（`decision_return`） | 净 = 毛 − 往返成本估算率（见下） |
| execution | 执行线 | `orders` 已成交流水 + `paper_equity` 净值序列 | 仿真成交（已含费） | 已含费，无二次扣减 |

## 口径细则（先登记 `docs/evals/metrics.md` §1，与本节一致）

- **成本估算率（decision 族）**：复用 `config/matching_engine.json` 参数 + 名义仓位常量 `SCORECARD_NOTIONAL=100_000`：
  - 佣金 `max(commission_rate × 名义仓位, min_commission)` × 2（双边）；
  - 印花税 `stamp_tax_rate`（卖出单边）；
  - 滑点取中档 `slippage_rate`（0.001）× 2；
  - 合计 ≈ 0.30%（0.0005 + 0.0005 + 0.002），参数同源撮合配置，禁止复制。
- **最大回撤（族级）**：按结算日聚合当日样本等权收益 → 日度收益序列 → 复利累计净值 → `MDD = max(1 − nav/峰值)`；结算日样本 < 20 → null + `insufficient_history`。
- **容量提示**：最近 20 个样本标的，各取近 20 交易日日均成交额（快照 `amount` 均值；缺失时回填表回退），取最小值 × 参与率上限 1% → 建议资金上限；缺数据标的排除并在槽位留痕，全部缺失 → null + `no_liquidity_data`。
- **极端样本**：`decision_return ≤ EXTREME_LOSS_THRESHOLD`（默认 −0.15，可配置）；清单含 ticker / 结算日 / 收益 / 口径标注。
- **胜率定位**：输出保留 `win_rate` 等字段但置于四槽之后；静态核查无自动处置/门禁/告警引用。

## 端点载荷（GET /api/scorecards，只读）

```json
{
  "families": [
    {
      "family_id": "decision",
      "name": "观点线",
      "as_of": "2026-10-18",
      "expectancy": {"gross": 0.012, "net": 0.009, "n": 34, "insufficient": false},
      "max_drawdown": {"value": -0.18, "n_days": 41},
      "capacity": {"suggested_capital": 1200000, "min_adv20": 120000000, "excluded": 2},
      "extreme_samples": [{"ticker": "600519", "settled_at": "2026-09-30", "return": -0.22}],
      "win_rate": 0.56,
      "caliber_note": "……口径注释随行……"
    }
  ]
}
```

缺数据槽位以 `null` + 原因码呈现（`insufficient_sample` / `insufficient_history` / `no_liquidity_data`），MUST NOT 折算 0。

## 备选与取舍

- **备选（弃）**：直接扩展 `track-record` 既有统计。理由：既有统计是观点线专属且被页面/API/评估多处消费，叠加四槽破坏既有契约；成绩单作为独立只读层，注册表后续可容纳事件族（变更 3）与选股族。
- **执行线进场样本量天然小**（paper 台账近期才接链）：`insufficient_sample` 会频繁出现——如实呈现，不是缺陷。
- 容量为参与率近似，仅量级提示；口径注释必须随行展示。

## 已知边界

- 不引入新表、不做写入路径、不含自动处置；纯读层。
- 阈值（门槛 10 / 极端 −15% / 参与率 1% / 名义仓位 10 万）均为具名常量，集中定义，便于后续注册表化。
