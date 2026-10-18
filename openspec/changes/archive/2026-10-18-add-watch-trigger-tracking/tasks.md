# Tasks: add-watch-trigger-tracking

- [x] `TradeDecision` 新增 `trigger_high`/`trigger_low`（宽松清洗，不抛异常）+ 单测
- [x] 终稿完整性检查扩展：watch 终稿缺触发位打回一次、仍缺放行 + 如实标注 + 单测
- [x] `trader.md`/`risk_judge.md` prompt 加触发位申报纪律（方向语义 + 与 reeval_triggers 并存；合同测试 88+16 绿）
- [x] `scripts/deploy_prompts.py` Langfuse 发布——trader/risk_judge **v31**（预检拦截→指纹取证 production v30=pre-#262 仅陈旧→create_prompt 定向覆盖，指纹 EXACT PASS）
- [x] 价位门禁：结构化触发位方向校验（空洞 anomaly，共用一次打回预算，fail-open）+ 单测
- [x] K 线采集携带触发位 + 服务端 PNG 画「上破触发/下破触发」参考线 + 单测
- [x] 前端 K 线组件渲染触发位参考线（与入场/止损/目标可区分样式）
- [x] 报告「交易决策」节：触发位行（缺失如实「未申报」）+ 入池跟踪声明行 + 数据真空提示行（阈值配置化）+ 单测
- [x] `predictions` 表 ALTER 加 `session_id`/`trigger_high`/`trigger_low` 列 + ingest 落库 + 存量 NULL 不追溯 + 单测
- [x] E2E spec 覆盖 watch 报告触发位链路（`report-watch-trigger-lines.spec.ts`，2026-10-18 收口）：stub 决策产出 watch+触发位（181/178，过价位门禁方向校验）→ 报告「交易决策」节触发位行渲染申报值 + K 线卡 canvas 挂载；前注的「e2e 基建未建设」已由 Playwright 接管消解，本项以真实 E2E 补上而非替代品留存
- [x] 复跑 601066（会话 b434aedf-83c）：报告触发位行 23.57/22.63 = chart_data = predictions 行 p_2f2d5230155c（含 session_id），三处一致；LLM 首报即合规零打回
- [x] 人工验证报告落 `tests/validation/2026-10-08-add-watch-trigger-tracking-validation.md`（K线点线浏览器截图 `2026-10-08-watch-trigger-tracking-kline.png`；待 owner 签字）
