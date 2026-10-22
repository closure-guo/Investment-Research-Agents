# 修复验证：研报深度层抽取单次失败即停摆

**日期**: 2026-10-22
**类型**: bug 修复（意图不变，spec 已有「投喂文档 → 结构化抽取」行为）
**路径**: `src/finance_agent/data/research_reports.py::extract_document`

## 现象与归因

`research_report_inbox_daily`（2026-10-21 16:59）唯一一份投喂 PDF 抽取失败：

```
UBS-China Equity Strategy：Top Q&As...pdf 抽取: No JSON object found: line 1 column 1 (char 0)
```

`report_documents=1`、`report_extractions=0`——文档已入库但停在「未抽取」，日批 `degraded`。

容器内复现同一 doc（12 页、58k 字符，截断 30k）：同一 prompt + `max_tokens=6000` 返回
2529 字符合法 JSON，`finish_reason=stop`。→ **非确定性空正文/纯散文响应**（与 incident 017
同族的「思考后即止」），不是解析器或 prompt 结构问题。

原有实现：`extract_json(raw_text)` 一次失败即抛，调用方记 degraded 后不再重试；而抽取是
投喂链唯一的 LLM 步骤，一次抖动就让整份研报永远停在未抽取。

## 修复

`extract_json` 抛 `JSONDecodeError` 时追加一次强化重试（prompt 尾部追加「只输出一个 JSON
对象本身，不要任何解释、前后缀或 Markdown 围栏」，trace name=`report_extraction_retry`）；
仍失败才上抛，不无限重试。

## 验证

| 项 | 命令 | 结果 |
|---|---|---|
| 先红 | `uv run pytest tests/data/test_research_reports_collectors.py -k "retries_once or non_json_twice"` | 1 failed（无重试）/ 1 passed |
| 后绿 | 同文件全量 | 26 passed |
| Lint | `uv run ruff check`（改动两文件） | All checks passed |
| 真实链路 | 容器内 `extract_all(force=False)` | `{'extracted': 1, 'skipped': 0, 'errors': []}`；`report_extractions` 0 → 1 |

## 边界

- 只加一次重试：连续两次非 JSON 仍如实失败并进 `errors`（有测试钉死），不掩盖模型侧故障。
- 未改 prompt 主体与 `EXTRACTION_MAX_CHARS`；30k 截断对长报告仍可能丢尾部（另项观察）。
- 事件 watchlist 的 430 条 pending 与本次无关：spec 只要求「规则化标注 + 只读清单 + 手动分析
  入口」，消费是用户动作，不是链路故障。
