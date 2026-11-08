# Delta for track-record

## MODIFIED Requirements

### Requirement: 观点结算与判定窗口

系统 SHALL 按统一规则判定观点：**默认判定窗口 T+20 交易日**（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`；观点自带 `horizon_days` 则以自带为准，上限 1 年）；**判定入场基准 SHALL 为 `settle_entry_price`（判定时从行情派生，非参考价）**；对同一标的发出**方向相反**的新观点时，旧的 long/short 观点 SHALL 立即以当前价结算（superseded，入场基准同为派生 `settle_entry_price`）；**同方向的新观点（含仅调整目标价）SHALL NOT 触发旧观点结算**——目标价调整是同一方向观点的跟踪更新，不是方向改变，旧观点按其自身入场基准与窗口继续判定；**neutral 观点 SHALL NOT 走 superseded 提前结算**（回避判定以完整 horizon 窗口为准）。

(Previously: `should_supersede` 在「同标的方向相反**或目标价不同**」时结算旧观点。叠加「同标的几乎每交易日重新分析」，一条连续方向性观点被切成 1~2 天碎片：生产实测方向性观点中位存活 2 天、活过 20 天者 0%，T+20 主指标永远为空。)

#### Scenario: 同方向调目标价不结算旧观点

- **GIVEN** 某标的 09-01 有一条 long 观点（target 120），09-03 又产出 long 观点（target 121）
- **WHEN** 日批判定运行
- **THEN** 09-01 观点 SHALL 保持 open，继续按自身窗口判定
- **AND** SHALL NOT 记为 superseded

#### Scenario: 方向翻转仍结算

- **GIVEN** 某标的旧观点 long、新观点 short
- **WHEN** 日批判定运行
- **THEN** 旧观点 SHALL 按当前价结算为 superseded
- **AND** 状态按 ±2% 中性带三分

#### Scenario: neutral 不走 superseded

- **GIVEN** 某标的旧观点为 neutral、新观点为 long
- **WHEN** 日批判定运行
- **THEN** neutral 观点 SHALL NOT 被 superseded
- **AND** 其回避判定 SHALL 以完整 horizon 窗口为准
