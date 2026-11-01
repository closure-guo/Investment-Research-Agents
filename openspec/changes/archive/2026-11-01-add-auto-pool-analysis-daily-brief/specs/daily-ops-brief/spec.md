# Delta for daily-ops-brief（add-auto-pool-analysis-daily-brief）

## ADDED Requirements

### Requirement: 每日 LLM 运营汇报生成

系统 SHALL 在工作日 20:30（Asia/Shanghai）自动生成一份当日运营汇报，无需人工触发。

- 汇报输入 SHALL 仅来自系统当日真实数据：选股批次与精选池、自动分析会话状态与结论、观点
  （created/resolved）、执行订单与持仓、因子 IC、当日失败/跳过的日批任务。
- 汇报 SHALL 经 `gateway.complete_text(..., purpose="daily_brief")` 调用 LLM；默认模型为
  opencode `openai/deepseek-v4.1-flash`（经 `DAILY_BRIEF_*` 逐字段覆盖，未设置时回退 `LLM_*`）。
- prompt MUST 明确禁止编造：只能引用输入事实；缺失数据必须写「无数据」，MUST NOT 用推测补齐。
- 输出 SHALL 为中文 Markdown，至少覆盖：今日结论 / 选股与精选池 / 分析与决策 / 交易与持仓 /
  风险与异常 / 明日关注。
- 汇报 SHALL 以 `brief_date` 幂等写入 `daily_briefs` 表（同一日期重复生成覆盖旧行），并写
  `reports/daily-brief-YYYY-MM-DD.md`（顶层 `reports/`，可被既有文件接口发现）。
- LLM 调用失败时，系统 SHALL 写入一份标注「LLM 生成失败」的确定性降级摘要（事实照录），
  将行状态置为 `failed` 并保留错误信息，同时让日批任务失败（重试 3 次）；MUST NOT 以空报告
  或成功状态掩盖失败。
- 同一日期手动补跑 SHALL 覆盖旧行与旧文件，不产生重复行。

#### Scenario: 正常生成并落库落盘

- **GIVEN** 当日有选股批次、分析观点与执行记录
- **WHEN** `daily_brief_daily` 触发
- **THEN** 调用 `purpose="daily_brief"` 生成 Markdown
- **AND** `daily_briefs` 写入一行 `status=ok`，含模型标识与 prompt 版本
- **AND** `reports/daily-brief-YYYY-MM-DD.md` 存在且内容与行一致

#### Scenario: 空活动日仍生成诚实汇报

- **GIVEN** 当日选股失败、无分析、无订单
- **WHEN** 汇报任务触发
- **THEN** 汇报明确写出「选股失败/无分析/0 笔订单」及原因
- **AND** MUST NOT 编造收益、持仓或标的

#### Scenario: LLM 失败降级

- **GIVEN** LLM 网关返回 typed error
- **WHEN** 汇报任务触发
- **THEN** 写入确定性降级摘要，`status=failed` 且 `error` 非空
- **AND** 日批任务以 failed 收尾（触发既有重试），MUST NOT 标记成功

#### Scenario: 同日补跑幂等

- **GIVEN** 当日已有一份汇报行
- **WHEN** 手动补跑
- **THEN** `daily_briefs` 仍只有一行，内容与文件被覆盖为最新一次结果

### Requirement: 每日汇报读取接口

系统 SHALL 提供只读接口读取每日汇报：

- `GET /api/v1/ops/daily-brief?date=YYYY-MM-DD`：返回指定日期汇报；
- `GET /api/v1/ops/daily-brief/latest`：返回最近一份汇报；
- 返回体 SHALL 含 `brief_date/status/markdown/model/prompt_version/error/created_at`；
- 指定日期无汇报时 SHALL 返回 404 + 稳定错误码 `daily_brief_not_found`，MUST NOT 返回空文档
  冒充已生成。

#### Scenario: 读取最新汇报

- **GIVEN** 库中存在 2026-10-20 与 2026-10-21 两份汇报
- **WHEN** 请求 `/api/v1/ops/daily-brief/latest`
- **THEN** 返回 2026-10-21 的完整 Markdown 与状态

#### Scenario: 无汇报显式 404

- **GIVEN** 指定日期没有汇报行
- **WHEN** 请求该日期
- **THEN** 返回 404 且 `error=daily_brief_not_found`
