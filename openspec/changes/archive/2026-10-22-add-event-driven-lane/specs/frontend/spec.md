# Delta: frontend（add-event-driven-lane）

## ADDED Requirements

### Requirement: 选股页事件观察区块

选股页 SHALL 新增事件观察区块：清单列表（来源标识、标的与名称、来源相关要素[业绩预告：报告期/预告类型/净利润变动幅度区间；条款事件：现价/转股溢价率/强赎倒计时]、分诊或标注结论三态[`outperform` / `in_line` / `risk`，按语义色区分]、理由与证据引用、公告日期、清单状态[是否已发起深度分析及排队/运行状态]）；条目 SHALL 提供「发起深度分析」入口（入既有队列，成功后展示排队状态）。空清单 SHALL 展示空态文案；请求失败 SHALL 如实展示错误态，MUST NOT 显示骨架屏或伪数据冒充。结论 SHALL 附口径注释（研究标记，非投资建议）。

#### Scenario: 清单渲染

- **GIVEN** 端点返回 3 条条目（结论分别为 outperform / in_line / risk 之外的至少两态）
- **WHEN** 打开选股页
- **THEN** 列表渲染各条目全要素，结论按三态语义色区分
- **AND** 附口径注释文案

#### Scenario: 发起深度分析

- **GIVEN** 一条未发起分析的条目
- **WHEN** 点击发起深度分析
- **THEN** 条目状态变为排队/运行中并展示关联信息
- **AND** 重复点击 MUST NOT 产生重复队列条目

#### Scenario: 空态

- **GIVEN** 清单为空
- **WHEN** 打开选股页
- **THEN** 展示空态文案，MUST NOT 显示骨架屏冒充
