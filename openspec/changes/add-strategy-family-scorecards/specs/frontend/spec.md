# Delta: frontend（add-strategy-family-scorecards）

## ADDED Requirements

### Requirement: 战绩页策略族成绩单区块

战绩页 SHALL 新增策略族成绩单区块：族切换控件、四槽卡片（期望值毛/净与样本量、最大回撤、容量提示、极端样本数）、极端样本明细表（标的 / 结算日 / 收益）、口径注释随行展示；样本不足（`insufficient_sample` 标注）SHALL 展示专用文案，MUST NOT 显示 0 或空白冒充；族无数据 SHALL 展示空态文案（与样本不足文案区分）；区块主位 SHALL 为四槽，胜率类字段 MUST NOT 占据区块主指标位置；数据缺失或请求失败 SHALL 如实展示对应空态/错误态，MUST NOT 显示骨架屏冒充。

#### Scenario: 四槽渲染

- **GIVEN** 端点返回观点线四槽完整读数
- **WHEN** 打开战绩页
- **THEN** 四槽卡片分别展示期望值（毛/净 + 样本量）、最大回撤、容量提示与极端样本数
- **AND** 极端样本明细表渲染对应条目

#### Scenario: 样本不足文案

- **GIVEN** 端点返回 `insufficient_sample` 标注
- **WHEN** 打开战绩页
- **THEN** 展示「样本不足」文案
- **AND** MUST NOT 显示 0 值或空白冒充读数

#### Scenario: 空数据族空态

- **GIVEN** 端点返回某族 null 读数
- **WHEN** 切换至该族
- **THEN** 展示空态文案，MUST NOT 显示错误提示冒充
