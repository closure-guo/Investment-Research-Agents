## 1. 抽取契约

- [x] 1.1 `report_extraction.md` 增加 `sector_views` / `market_view` 字段与「定位不到置 null」规则
- [x] 1.2 同一 prompt 明确「正文讨论才入 stocks；披露表评级不得入 stocks」
- [x] 1.3 `_coerce_extraction` 收敛 `sector_views`（direction 白名单）与 `market_view`（可选字段），旧抽取缺失视为空
- [x] 1.4 `report_extractions` 增列 `sector_views_json` / `market_view_json`（幂等迁移）
- [x] 1.5 测试：策略报告空 stocks + 非空 sector_views；披露表条目不入 stocks；旧抽取兼容

## 2. 策略观点聚合

- [x] 2.1 实现 `get_strategy_digest()`：30 天窗口、按日期取前 3 篇、确定性聚合、无 LLM
- [x] 2.2 实现行业观点分组、方向分布、论据去重与 `disagreements`
- [x] 2.3 实现硬上限截断与陈旧语义（>30 天返回 None 并记 stale）
- [x] 2.4 实现 `format_strategy_digest()` 文本段（机构/日期/样本数/防锚定条款）
- [x] 2.5 测试：多篇聚合、分歧、陈旧、空态、上限截断

## 3. 上下文与引用

- [x] 3.1 `fetch` 节点新增 `strategy_report_digest`，失败降级 None 不阻断
- [x] 3.2 `_build_macro_context` 注入观点段；空/陈旧不注入
- [x] 3.3 确认 `_build_fundamental_context` 不读取该键
- [x] 3.4 `collect_text_sources` 纳入 digest 条目；测试回声命中与数值 claim 不放行
- [x] 3.5 `macro_analyst.md` 增加消费条款与防锚定声明

## 4. 发布与验收

- [x] 4.1 `uv run python scripts/deploy_prompts.py` 发布 `report_extraction` 与 `macro_analyst`，确认版本指纹
- [x] 4.2 `openspec validate add-macro-strategy-report-context --strict` 通过
- [x] 4.3 `uv run pytest` 定向测试全绿；`uv run ruff check` 通过
- [x] 4.4 端到端验证：投喂一份策略报告 → 抽取有 sector_views → 宏观上下文出现观点段 → 基本面上下文无该段
- [x] 4.5 人工抽查：观点段无编造数字，防锚定条款在场，报告未把策略观点写成个股结论
