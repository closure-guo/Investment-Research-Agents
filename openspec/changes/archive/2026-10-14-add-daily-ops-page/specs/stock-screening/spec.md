# stock-screening delta: add-daily-ops-page

## ADDED Requirements

### Requirement: 批次历史查询

系统 SHALL 提供选股批次历史只读接口：按交易日期倒序返回近 N 批次，字段含 日期、状态（ok / failed）、各层计数（`l1_passed` / `l2_top` / `l3_keep`）、精选池数量（`pool_size`）、错误信息与起止时间；库为空时 SHALL 返回空列表而非错误；接口 SHALL NOT 暴露写操作。

#### Scenario: 历史字段齐备

- **GIVEN** 库中含 1 条成功批次与 1 条失败批次
- **WHEN** 请求批次历史
- **THEN** 返回两条（日期倒序），成功批次含各层计数与池数、失败批次含错误信息

#### Scenario: 空库不报错

- **GIVEN** 尚无任何批次
- **WHEN** 请求批次历史
- **THEN** 返回 200 与空列表
