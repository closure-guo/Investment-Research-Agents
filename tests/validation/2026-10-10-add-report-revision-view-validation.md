# 人工验证报告：add-report-revision-view（报告修订视图）

- 日期：2026-10-10
- 验证人：agent（owner 授权代签，会话授权「都做完，不用等我确认」）
- 对应 PR：#285（初版实现，235874e1）+ 修复 PR（本地实跑暴露两缺陷）
- 方式：本地 worktree 后端（127.0.0.1:8010）+ 生产 sessions.db 副本（`SESSIONS_DB_PATH` 覆盖，不触碰运行中的 docker 栈），真实 LLM 管线连跑两份 601066 深度分析

## 环境与数据准备

- 副本 DB：`data/sessions.db`（复制自生产，只读基准；跑前 seed 了什么见下）
- run-1（会话 `290505da`，2026-10-09T23:47，初版代码）：真实管线产出，决策 watch 0.55（触发位 22.4/24.8）
- run-1 落库后手工把其 `final_trade_decision` 列从「repr 串」修为等值 JSON（其真实决策值逐字提取），充当 run-2 的回溯上一份——用于全维度 diff 验证
- run-2（会话 `cef0cfb2`，2026-10-10T09:04，修复后代码）：真实管线产出

## 实跑发现的两处缺陷（run-1 抓到，均已修复并补回归测试）

1. **AnalysisState 未声明 `previous_report_snapshot`**：TypedDict 未声明键在图入口被静默丢弃，「距上次报告」节恒不渲染。修复：state.py 显式声明（附 schema 契约测试）。
2. **TradeDecision pydantic 对象三处口径**：
   - 持久化侧 `json.dumps(default=str)` 落 repr 串 → 回溯侧 `json.loads` 失败恒「未申报」。修复：落库前 `model_dump()`；
   - 渲染侧对 state 中的终稿直接 `.get()` → AttributeError。修复：`_decision_as_dict` 归一；
   - 均以真实形态补 TDD 回归（`TestRealPipelineShapes` + pydantic 落库测试）。

## run-2 实跑证据（全链路真实 LLM，无 stub）

### 「距上次报告」节（报告头「研究聚焦」之后）

```markdown
## 距上次报告（2026-10-09）

- 决策方向: watch → watch（无变化）
- 置信度: 0.55 → 0.60
- 触发位/价位: 上破 24.80 / 下破 22.40 → 上破 24.60 / 下破 22.60
- 现价: 23.21 → 23.21（无变化）
- PE: 未申报
```

核对（重读用户视角）：

- 方向/置信度/触发位：旧值与 run-1 真实决策逐字一致（0.55、22.4/24.8）；新值与 run-2 终稿一致（0.60、22.6/24.6）——diff 数字全部可溯源到两份终稿，无编造
- 现价「无变化」：两跑同日，行情快照同值——如实渲染正是设计意图
- PE「未申报」：副本库上一行 kpi.pe 确实为 NULL（腾讯主源 PE 缺失，#276 已挂账）——缺失如实标注，未回填
- 「多空辩论结论」节头部分歧卡：`评级: 看空 · 置信度 0.60 · 分歧焦点: 多空双方交锋聚焦三点：①技术面主导权之争——…`，置于结论文本之前，首句截断在句界

### 终稿决策持久化

run-2 会话行 `final_trade_decision` 为可解析 JSON（`action=watch, confidence=0.6, trigger_low=22.6, trigger_high=24.6`）——下一份报告即可全维度 diff，验证自愈闭环。

## 未申报形态的独立验证

`TestRevisionEndToEnd.test_full_chain_unreported_when_legacy_row`（DB 落 NULL → 回溯 → 渲染）：决策三维「未申报」、kpi 维度正常对比。历史行（本 feature 前落库的存量会话）首跑即为此形态，随新报告自愈。

## 结论

- 增量摘要五维度 diff 数字全部可溯源、缺失维度如实「未申报」、首份报告不渲染（单测覆盖）、分歧卡退化纯文本（单测覆盖）：**通过**
- 两处真实管线缺陷已修并有回归测试钉住：**通过**
- 生产部署：后端代码走主检出挂载，PR 合并推进主检出后 restart 生效（需按红线先查 `/api/sessions` 无 running 会话）；存量行无 `final_trade_decision`，首批增量摘要决策维度按「未申报」渲染属预期
