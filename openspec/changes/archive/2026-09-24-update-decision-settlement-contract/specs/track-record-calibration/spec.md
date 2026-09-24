# Delta for track-record-calibration

## MODIFIED Requirements

### Requirement: 置信度校准分桶

系统 SHALL 将已结算观点按 confidence 分桶（[0.5,0.6)...[0.9,1.0]），每桶输出 {桶中值, 实际命中率, 样本数}，样本不足桶如实返回样本数。命中值映射 SHALL 按观点终态取：`resolved_win`→1.0、`resolved_loss`→0.0、`resolved_neutral`→中性概率参数 `neutral_prob`（默认 0.5，可配置剔除）；`status == "avoidance"` 的观点 SHALL 按 `avoidance_status` 映射命中值——`avoidance_win`→1.0、`avoidance_loss`→0.0、`avoidance_neutral`→中性概率参数 `neutral_prob`（默认 0.5）；`avoidance_status` 为空（未判定）SHALL 跳过（不参与校准）。
(Previously: neutral 观点按 0.5 命中处理。)

#### Scenario: 分桶输出

- **WHEN** 请求校准 API 且存在已结算观点
- **THEN** 返回各桶中值/实际命中率/样本数，neutral 观点按 `neutral_prob`（默认 0.5）命中处理（可配置剔除）

#### Scenario: 回避终态映射命中值

- **GIVEN** 存在 `status == "avoidance"` 的 neutral 观点（avoidance_win / avoidance_loss / avoidance_neutral）
- **WHEN** 请求校准 API
- **THEN** avoidance_win SHALL 按 1.0、avoidance_loss SHALL 按 0.0、avoidance_neutral SHALL 按 `neutral_prob`（默认 0.5）计入分桶与 Brier Score
- **AND** `avoidance_status` 为空的观点 SHALL 跳过（不参与校准）
