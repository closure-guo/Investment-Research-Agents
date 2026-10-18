# Design: add-strategy-family-scorecards

## 族注册表 v1（代码内注册，不建新表）

| family_id | 名称 | 数据源 | 收益口径 | 费后处理 |
|---|---|---|---|---|
| predictions | 观点线 | `predictions` 已判定行（`status ∈ {resolved_win, resolved_loss, resolved_neutral}`，与既有 `prediction_stats` 分母一致；`avoidance`/`unresolvable`/`duplicate_of_day` 排除） | 虚拟结算（`raw_return`） | 净 = 毛 − 往返成本估算率（见下） |
| execution | 执行线 | `paper_equity` 净值序列 + `positions` 当前持仓 | 组合级日收益（净值曲线派生） | 已含费（撮合费用模型），无二次扣减 |

> 注：`decision_log`（`outcome/store.py`）为存量/测试链——生产写入已迁移至 `predictions`（`api.py::_persist_decision_log` 委托 `persist_prediction_from_accumulated`），故 v1 不纳入 `decision_log`，避免口径混淆。

## 口径细则（先登记 `docs/evals/metrics.md` §1，与本节一致）

- **成本估算率（predictions 族）**：复用 `config/matching_engine.json` 参数 + 名义仓位常量 `SCORECARD_NOTIONAL=100_000`：
  - 佣金 `max(commission_rate × 名义仓位, min_commission)` × 2（双边）；
  - 印花税 `stamp_tax_rate`（卖出单边）；
  - 滑点取中档 `slippage_rate`（0.001）× 2；
  - 合计 ≈ 0.30%（0.0005 + 0.0005 + 0.002），参数同源撮合配置，禁止复制。
- **最大回撤（族级）**：按日期（`resolved_at` 前 10 字符）聚合当日样本等权收益 → 日度收益序列 → 复利累计净值 → `MDD = max(1 − nav/峰值)`；结算日样本 < 20 → null + `insufficient_history`。执行族回撤直接用 `paper_equity` 净值序列（同一 MDD 函数）。
- **容量提示**：最近 20 个样本标的（predictions：最近判定样本的 symbol；execution：当前持仓 symbol），各取近 20 交易日日均成交额（快照 `amount` 均值，经 `read_bars`），取最小值 × 参与率上限 1% → 建议资金上限；缺数据标的排除并在槽位留痕，全部缺失 → null + `no_liquidity_data`。
- **极端样本**：按族阈值（predictions 默认 −0.15、execution 组合级默认 −0.05，具名常量可配置）；清单含 ticker / 日期 / 收益 / 口径标注。
- **胜率定位**：输出保留 `win_rate`（predictions：`resolved_win` 占比）但置于四槽之后；静态核查无自动处置/门禁/告警引用。

## 端点载荷（GET /api/scorecards，只读）

```json
{
  "families": [
    {
      "family_id": "predictions",
      "name": "观点线",
      "as_of": "2026-10-18",
      "expectancy": {"gross": 0.012, "net": 0.009, "n": 34, "insufficient": false},
      "max_drawdown": {"value": -0.18, "n_days": 41},
      "capacity": {"suggested_capital": 1200000, "min_adv20": 120000000, "excluded": 2},
      "extreme_samples": [{"ticker": "600519", "date": "2026-09-30", "ret": -0.22}],
      "win_rate": 0.56,
      "caliber_note": "……口径注释随行……"
    }
  ]
}
```

执行线族：`win_rate` 为 `null`（不适用——执行线无胜率口径，毛=净）。

缺数据槽位以 `null` + 原因码呈现（`insufficient_sample` / `insufficient_history` / `no_liquidity_data`；族声明数据源未接线装载器时四槽原因码为 `no_loader`），MUST NOT 折算 0。

## 备选与取舍

- **备选（弃）**：直接扩展 `track-record` 既有统计。理由：既有统计是观点线专属且被页面/API/评估多处消费，叠加四槽破坏既有契约；成绩单作为独立只读层，注册表后续可容纳事件族（变更 3）与选股族。
- **执行线进场样本量天然小**（paper 台账近期才接链）：`insufficient_sample` 会频繁出现——如实呈现，不是缺陷。
- 容量为参与率近似，仅量级提示；口径注释必须随行展示。

## 已知边界

- 不引入新表、不做写入路径、不含自动处置；纯读层。
- 阈值（门槛 10 / 极端 −15% / 参与率 1% / 名义仓位 10 万）均为具名常量；族级门槛 / 极端阈值 / 费后口径 / 数据源随注册表逐族声明（spec「策略族注册与口径声明」），参与率与名义仓位集中定义。
