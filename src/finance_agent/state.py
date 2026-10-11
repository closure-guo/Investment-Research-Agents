from operator import add
from typing import Annotated, Literal, TypedDict

import pandas as pd

from finance_agent.llm.config import LLMConfig


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Send 并行 agent 的 dict 合并 reducer。"""
    result = dict(left or {})
    result.update(right or {})
    return result


class AnalysisState(TypedDict, total=False):
    # ── 输入 ──
    query: str
    stock_code: str
    stock_name: str
    analysis_type: Literal["financial", "investment", "comprehensive"]
    peer_codes: list[str] | None
    enable_web_search: bool  # 是否启用实时事件搜索
    api_key: str | None  # 用户自带的 DeepSeek API Key（HF Spaces 用）
    focus: str  # 深度研究意图澄清环节用户填写的关注点（Kimi 风格反问回答）
    # 请求级 LLM 配置（incident 035：曾未声明 → LangGraph 静默过滤输入键，
    # 全部管线节点回退 env 模型，请求级配置整条哑火——027 同族）
    llm_config: LLMConfig | None

    # ── Cache ──
    cache_result: str  # HIT | MISS

    # ── Validation ──
    validation_result: str  # PASS | FAIL
    validation_warnings: list[str]

    # ── Layer 1: 基础公共数据 ──
    balance_sheet: pd.DataFrame
    income_statement: pd.DataFrame
    cash_flow_statement: pd.DataFrame
    stock_quote: dict
    industry_info: dict
    # 最新报告期快照（update-financial-freshness-and-valuation Task 4）：fetch 产出、
    # cache HIT 随附；未声明会被图合并静默丢弃（incident 027 同型补声明）
    latest_period_snapshot: dict | None

    # ── Layer 2: 分析导向 (MVP: 仅预计算指标) ──
    financial_indicators: pd.DataFrame | None
    industry_pe: dict | None

    # ── Layer 3: 衍生计算 ──
    solvency_metrics: dict
    profitability_metrics: dict
    efficiency_metrics: dict
    cashflow_metrics: dict
    dupont_tree: dict
    growth_rates: dict
    anomalies: list
    traffic_lights: dict
    health_score: dict | None

    # ── Layer 3 扩展: 同业+估值 ──
    peer_financials: pd.DataFrame | None
    peer_comparison: (
        str | None
    )  # compute 写 markdown 字符串；analysts 注入保留 dict 兼容（遗留标志位形态）
    relative_valuation: dict | None
    garp_result: dict | None
    # 估值快照（update-financial-freshness-and-valuation Task 5）：compute_metrics 产出，
    # {market_cap, PE, PE_ttm, PE_caliber, PB, missing_reasons}；GARP/相对估值/上下文注入消费
    valuation_snapshot: dict | None

    # ── Layer 3 扩展: 季度趋势 ──
    quarterly_income: pd.DataFrame | None
    quarterly_trend: dict | None

    # ── Layer 2.5: 关键非财务事件（舆情分析师输入之一）──
    key_events: list[dict] | None

    # ── Agent 输出 ──
    final_report: str | None
    file_path: str | None
    file_paths: dict | None

    # ── 5 层架构（ADR-0011）──

    # PREP 扩展
    kline: pd.DataFrame  # 日 K 线（OHLCV）
    benchmark_kline: pd.DataFrame  # 沪深 300 K 线
    technical_indicators: dict  # calc_technical() 输出
    risk_metrics: dict  # calc_risk() 输出
    derived_series: dict  # calc_derived_series() 输出（toolize-price-levels；此前未声明被图丢弃）
    # price_levels 同族同病：compute 写入但从未声明 → 图合并静默丢弃，validate 恒走
    # 「price_levels 不可用，跳过校验」分支（参考带 band 校验与二次失败修正从未生效）
    price_levels: dict  # calc_price_levels() 输出
    macro_indicators: dict  # CPI/PMI/M2/LPR
    news_list: list[dict]  # 新闻列表
    announcements: list[dict]  # 公司公告列表（add-analyst-data-coverage）
    research_reports: list[dict]  # 券商研报列表（含评级/目标价）
    share_unlock: list[dict]  # 限售解禁排队
    block_trades: list[dict]  # 大宗交易明细（近 30 天）

    # Layer I: Analyst Team（4 个并行分析师）
    analyst_reports: Annotated[dict[str, dict], merge_dicts]

    # Layer II: Researcher Team（Bull/Bear 辩论）
    debate_history: Annotated[list[dict], add]
    # 论点锚点校验记录（add-debate-argument-anchors）：辩手/风控两层辩论按论点追加，
    # 由 debate_anchors.check_argument_anchors 产出（fail-open，不参与路由）
    debate_anchor_checks: Annotated[list[dict], add]
    focus_summary: str  # 研究聚焦摘要（report 节点无条件生成，judge report_conclusion 直取源）
    research_manager_conclusion: str  # 评级前置拼装（RM 结构化输出的人读渲染）
    research_manager_rating: str | None  # 看多/看空/中性（战绩结算与 judge 变量直取）
    research_manager_confidence: float | None
    # RM 结构化输出解析失败降级标记（research_manager 写入；此前未声明被图合并
    # 静默丢弃，incident 027 同族补声明）
    parse_degraded: bool

    # Layer III: Trader
    trader_plan: dict  # TradeDecision 序列化

    # 价位校验回路（toolize-price-levels；incident 027：以下键曾未声明，被图合并
    # 静默丢弃——fail 打回 trader 与参考带价位修正在真实图中从未生效，路由恒读空）
    price_check: dict  # {result: pass|fail|corrected, reason?, note?}
    price_check_feedback: str  # fail 时打回 trader 的重出反馈
    price_check_attempts: int  # 已校验次数（<1 fail 打回；>=1 二次失败走参考带修正）
    # 非执行动作理由回路（require-watch-hold-rationale）：watch/hold 结构化理由缺失
    # 同款一次打回（独立计数，不与价位回路互相消耗）
    inaction_rationale_check: dict  # {result: pass|fail, reason?, note?}
    inaction_rationale_feedback: str  # fail 时打回 trader 的重出反馈
    inaction_rationale_attempts: int  # 理由检查已打回次数（<1 fail 打回；>=1 放行+标注）
    price_level_corrected: bool  # 价位已按工具参考带修正（可观测）
    price_level_correction_reason: str  # 修正原因（报告「价位修正」行）
    # 派生风险指标（deterministic-derived-metrics）：validate 代码计算，辩论/裁决引用
    derived_metrics: dict  # {stop_distance_pct, risk_reward_ratio, missing_reason}

    # 工具预算的价位参考与派生值（toolize-price-levels；incident 027 同型补漏：
    # 二者 compute_metrics 一直产出但未声明 → 被图静默丢弃，三处消费方恒读 None：
    # ① validate_trade_prices 的参考带/价格关系/偏离校验从未生效（恒走「不可用跳过」）
    # ② Trader context 的「价位参考」节 ③ 分析师 context 的「常用派生值」表。
    # 门禁：tests/test_graph_5layer.py::TestNodeOutputChannels（节点产出键 ⊆ 声明）
    price_levels: (
        dict  # calc_price_levels 产出：{available, entry_ref, *_band_*, full_band, reason?}
    )
    derived_series: (
        dict  # calc_derived_series 产出：区间涨跌幅/距高低点回撤反弹（field_ref 前缀 derived.）
    )

    # Layer IV: Risk Management（3 辩论者 + Risk Judge）
    risk_debate_history: Annotated[list[dict], add]
    final_trade_decision: dict  # TradeDecision 序列化

    # add-report-revision-view：同标的上一份 completed 报告的回溯快照
    # （session_store.get_previous_completed_session 产物，组合根注入；
    # 未声明的键会被图入口静默丢弃——本地实跑实证），None = 首份报告
    previous_report_snapshot: dict | None

    # Layer V: Fund Manager
    fund_manager_decision: Literal["approve", "reject", "return"]
    fund_manager_decision_reasoning: str  # FM 退回/批准理由（回路契约：未声明则被图合并丢弃）
    fund_manager_action: str | None  # FM 操作定性（approve 必有；reject/return 为 None）
    fund_manager_confidence: float | None  # FM 对操作定性的把握（approve 必有）
    return_count: int  # 退回次数（上限 1）
    langfuse_trace_id: str  # fund_manager approve 时捕获,decision_log 反向上报用

    # 引用校验（ADR-0010 Step 3）
    citation_report: dict  # CitationReport 序列化
    citation_pass: bool
    iteration_count: int  # 重试次数（上限 3）
    # 各轮校验失败率历史（citation-retry-policy delta）：失败率停滞时
    # after_citation 提前放行渲染，不再全量重跑分析师
    citation_fail_rates: list[float]
    citation_minor_fail: bool  # 轻微失败降级放行（skip-citation-retry-on-minor-failures）
    # harden-citation-semantic-coverage：FAIL 分桶与定向重试
    citation_retry_targets: list[str]  # 值级 FAIL 分析师（Send 定向重跑）
    # 阶段 0 停滞保护（incident 026）：重试目标输出哈希与「重写无进展」标记
    citation_retry_prev_hash: dict[str, str]
    citation_retry_no_progress: bool
    # 阶段 5 门禁三层分置 + 指标拆报（incident 026）
    citation_blocked: bool  # 阻断层：归一后残余 FAIL > 0
    citation_analyst_true_fail: int  # 分析师真错数 = 残余 FAIL + 单点修复回填数
    citation_coverage_warn: bool  # 警告线：coverage < 0.90
    citation_unverifiable_text: int  # 跟踪：文本 claim UNVERIFIABLE（分型排除，不进阻断分母）
    citation_unverifiable_unregistered: int  # 跟踪：未注册/空值 UNVERIFIABLE
    citation_unverifiable_comparative_delta: int  # 跟踪：比较型差值申报（非三枚举 stated_value）
    citation_verifier_normalized: int  # 归一后由 FAIL 转 PASS 的计数（unit/percent/echo）
    auto_claims: int  # 阶段 4 自动合成 claim 数
    citation_retry_feedback: dict[str, list[dict]]  # 每分析师失败明细（重试上下文注入）
    citation_fail_buckets: dict[str, int]  # 桶计数（value_mismatch/path_unresolvable/...）
    citation_coverage_gap: bool  # 覆盖率缺口（重试准入路由读取；incident 027 补声明）
    value_mismatch_repaired: (
        int  # 数值失配修复数（deprecated all_passed 口径，保留一轮跨口径对照；incident 029）
    )
    value_mismatch_repaired_claims: (
        int  # 按 claim 修复记账：重校验后目标 claim PASS 即计（incident 029 处置）
    )
    payout_ratio_corrected: bool  # 赔率自检：reasoning 自报赔率与代码计算冲突已原位修正
    payout_ratio_conflict_skipped: (
        int  # 赔率自检转述护栏：跳过替换的冲突计数（批评语境，防反转语义）
    )
    final_price_check: (
        dict  # 终稿价位完整性（601888 实证）：{result, note}——打回后申报/仍缺如实标注
    )
    final_inaction_check: (
        dict  # 终稿非执行动作理由完整性（require-watch-hold-rationale）：{result, note}
    )
    # 终稿执行动作再评估触发条件必填化（update-decision-integrity-gates Task 3）：
    # {result, note}——打回后申报/「已打回仍未申报」如实标注
    final_reeval_check: dict
    # watch 终稿双向触发位申报完整性（add-watch-trigger-tracking）：{result, note}——
    # 打回后申报/「已打回仍未申报触发位」如实标注（未声明会被图合并静默丢弃）
    final_trigger_check: dict
    # 决策文本价位交叉校验残留 anomaly（update-decision-integrity-gates Task 1 →
    # update-decision-price-gate 收窄）：保存 risk_judge **最终终稿**的残留 anomaly
    # （打回修正后为空列表），仅经 state/Langfuse trace 可观测，MUST NOT 渲染进报告
    decision_price_anomalies: list[dict]
    # 决策滞回（add-decision-hysteresis）：{balanced_zone, prior_direction,
    # prior_date, flip, incremental_claimed, needs_reaffirm, reaffirmed, applied}——
    # 均衡带/翻转判定与处置标注（「维持前判（无证据增量）」/「证据均衡无增量，
    # 默认观望」），trace 可观测，报告不渲染
    decision_hysteresis: dict
    # 价位交叉校验门禁（update-decision-price-gate）：{result: pass|fail, note}——
    # anomaly 打回重试后修正放行（note「打回后已修正」）；仍异常 fail，
    # after_risk_judge 据此阻断（不进 FM 审批、不产出报告）
    decision_price_gate: dict
    citation_coverage: float  # 正文数字普查覆盖率（0-1，监控不进路由）

    # ── URL 信源溯源（Kimi 风格引用）──
    web_sources: list[dict]  # [{"query","title","url","content"}]

    # ── 图表数据（用于前端 ECharts + docx/pptx PNG）──
    chart_data: dict  # 结构化财务/股价序列，JSON-serializable
