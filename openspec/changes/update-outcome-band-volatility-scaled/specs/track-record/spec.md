# Delta for track-record

> 提案阶段 delta。owner 拍板进入实施时改写为对「判定规则（Outcome Resolution）」
> 的 MODIFIED 合并形态。

## ADDED Requirements

### Requirement: 波动率自适应中性带与连续超额统计（提案）

系统 SHALL 为观点判定提供波动率自适应中性带：辅助分类带 SHALL 取
`max(1.5%, 0.5 × 决策归属日起 20 个交易日历史年化波动率折算的窗口波动)`，
替代全局常量 ±2%；既有 resolved_win/loss/neutral 状态机 SHALL 保持产出（兼容），
但从主读数降为分层切片。统计层 SHALL 新增连续超额读数族：平均超额、超额胜率、
超额 IR（超额均值 / 超额标准差），作为主指标展示；对外接口 SHALL 在 2 周评估期
内双轨输出旧带与新口径两套读数。存量 resolved 分类 SHALL NOT 回填或重判。

#### Scenario: 高波动标的带宽放宽

- **GIVEN** 某标的 20 日窗口波动折算区间波动为 8%
- **WHEN** 判定该标的观点
- **THEN** 中性带 SHALL 取 max(1.5%, 4%) = 4%
- **AND** resolved_neutral 带宽按 4% 计算

#### Scenario: 低波动标的保底带宽

- **GIVEN** 某标的 20 日窗口波动折算区间波动为 1%
- **WHEN** 判定该标的观点
- **THEN** 中性带 SHALL 取保底 1.5%

#### Scenario: 连续超额双轨展示

- **GIVEN** 评估期内某标的观点完成判定
- **WHEN** 读取 track-record API
- **THEN** 响应 SHALL 同时含旧带（±2% 或自适应带）与新口径（连续超额族）读数
- **AND** 存量 resolved 分类 SHALL NOT 被回填或重判
