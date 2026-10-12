"""Agent 间通信的结构化输出模型。

参考 ADR-0011 和 TradingAgents (arXiv:2412.20138)。
结构化输出解决"电话效应"：agent 间传递 Pydantic 对象而非自由 Markdown。
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from finance_agent.citation import Claim


class AnalystReport(BaseModel):
    """Layer I 分析师 Agent 的结构化输出。

    每个 analyst agent（宏观/基本面/技术面/舆情）输出此对象，
    供下游辩论 agent 和引用校验器使用。
    """

    agent_name: str  # "macro" | "fundamental" | "technical" | "sentiment"
    summary: str
    plain_conclusion: str = Field(..., min_length=1)
    key_findings: list[str]
    claims: list[Claim]  # 用于确定性引用校验
    markdown: str  # 完整章节 Markdown，用于最终报告渲染
    # 解析降级标记：LLM 输出解析失败时为 True，使下游能区分
    # 「解析失败导致的零 claim」与「LLM 正常输出的零 claim」
    # （零 claim 会使引用校验 all_passed=True，见 citation.py 的 failed == 0）
    parse_degraded: bool = False

    @field_validator("plain_conclusion")
    @classmethod
    def _plain_conclusion_not_blank(cls, v: str) -> str:
        """add-agent-readable-conclusion：普通人可读结论必填非空（纯空白等同缺失）。"""
        if not v.strip():
            raise ValueError("plain_conclusion 不得为空或纯空白")
        return v


def _normalize_anchors(value: object) -> list[str]:
    """LLM 形态噪声归一，不抛解析异常（显式降级口径）。

    - `str` → 单元素列表（P1 pilot 真跑：LLM 把 anchors 返回为字符串，
      旧实现直接抛 ValidationError 致整个 full-graph 运行失败）
    - `list` / `tuple` → 保留 str 条目、丢弃其余（不字符串化 dict/int/None，
      以维持 `list[str]` 契约；丢弃即「该项未申报」，比伪造锚点更诚实）
    - 其余（`None` / dict / int…）→ `[]`：无法解释为锚点列表即视为未申报
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


class DebateArgument(BaseModel):
    """辩论论点（add-debate-argument-anchors）。

    kind 对 LLM 只暴露 data/event/inference；unspecified 是旧格式或非法值的
    显式降级（不猜 kind，与 direction=None 计覆盖缺口的先例一致）。
    anchors：data 型为 state 英文键路径（与分析师 claim 同一词表）；event 型为
    来源事件标题要点；inference 型可为空。
    """

    text: str = Field(min_length=1)
    kind: Literal["data", "event", "inference", "unspecified"] = "unspecified"
    anchors: list[str] = Field(default_factory=list)


class DebateMessage(BaseModel):
    """Layer II/IV 辩论消息。

    Bull/Bear 辩论（Layer II）和 Risk Management 辩论（Layer IV）的消息单元。
    每轮辩论中各角色用 Send 并行产出，收集到 debate_history。
    """

    role: Literal[
        "bull",
        "bear",
        "aggressive",
        "conservative",
        "neutral",
        "research_manager",
        "risk_judge",
    ]
    round: int = Field(ge=1)
    content: str
    key_arguments: list[DebateArgument]
    # 交锋结构化引用（harden-decision-report-semantics 1.11）：本轮回应的对方
    # 上一轮论点编号（1-based，对应对方 key_arguments 序号）。首轮开场为空。
    # 使「对方论点被回应的比例」可由代码直接计算（零 token 确定性指标）。
    rebuttal_to: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_arguments(cls, data: object) -> object:
        """旧格式兼容：裸字符串列表 → unspecified（历史会话/夹具/TESTING stub）。

        已构造的 DebateArgument 实例原样保留（调用方直接手搓类型化论点时
        不得被 else 分支字符串化——那会静默丢失 text/kind/anchors）。
        anchors 形态噪声（字符串/None/dict/混合类型项）一并归一，见
        `_normalize_anchors`——SHALL NOT 因形态差异抛解析异常使整图失败；
        text 缺失/为空仍照常抛验证异常（形态噪声 ≠ 内容缺失）。
        """
        if not isinstance(data, dict):
            return data
        args = data.get("key_arguments")
        if not isinstance(args, list):
            return data
        coerced: list[dict] = []
        for item in args:
            if isinstance(item, str):
                coerced.append({"text": item, "kind": "unspecified", "anchors": []})
            elif isinstance(item, DebateArgument):
                coerced.append(item.model_dump())
            elif isinstance(item, dict):
                d = dict(item)
                if d.get("kind") not in ("data", "event", "inference", "unspecified"):
                    d["kind"] = "unspecified"
                d["anchors"] = _normalize_anchors(d.get("anchors"))
                coerced.append(d)
            else:
                coerced.append({"text": str(item), "kind": "unspecified", "anchors": []})
        return {**data, "key_arguments": coerced}


# TradeDecision.evidence_refs 的 source 规范枚举（improve-decision-grounding）
TRADE_EVIDENCE_SOURCES = frozenset(
    {
        "technical",
        "macro",
        "fundamental",
        "sentiment",
        "debate_bull",
        "debate_bear",
        "research_manager",
    }
)

# LLM 输出常见别名 → 规范 source（归一不拒绝，见 TradeEvidenceRef validator）
_SOURCE_ALIASES = {
    "technical_analyst": "technical",
    "macro_analyst": "macro",
    "fundamental_analyst": "fundamental",
    "sentiment_analyst": "sentiment",
    "bull": "debate_bull",
    "bear": "debate_bear",
    "research_manager_conclusion": "research_manager",
    "aggressive": "risk_aggressive",
    "conservative": "risk_conservative",
    "neutral": "risk_neutral",
    "aggressive_debater": "risk_aggressive",
    "conservative_debater": "risk_conservative",
    "neutral_debater": "risk_neutral",
    # r2 实证：Risk Judge 把多空辩论误挂风险层前缀
    "risk_bull": "debate_bull",
    "risk_bear": "debate_bear",
}

# Risk Judge 的论据来源：Trader 来源 + 三方风险辩论 + 风控指标（Risk Judge 在风险
# 辩论之后裁决，其理由建立在这两样上；只给 Trader 那套来源会使「中性方/beta」
# 类论据无法列入 evidence_refs，judge 必然判「关键论据未引用」）
RISK_EVIDENCE_SOURCES = TRADE_EVIDENCE_SOURCES | frozenset(
    {"risk_aggressive", "risk_conservative", "risk_neutral", "risk_metrics"}
)


class TradeEvidenceRef(BaseModel):
    """交易决策的论据引用 — 决策论据到来源的可核对映射。

    source 宽松接收：仅做大小写/别名归一，未知值原样保留（LLM 抖动
    不炸管线——judge 自会因无法核对而判低分；与 action/confidence 的
    硬校验相反，此处是「降级不中断」路线）。
    """

    claim: str
    source: str

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        norm = value.strip().lower()
        return _SOURCE_ALIASES.get(norm, norm)


class TradeDecision(BaseModel):
    """Layer III (Trader) / Layer IV (Risk Judge) 的交易决策。

    action 受 Literal 约束，仅允许 buy/sell/hold/watch。
    confidence 为 0-1 的置信度。
    """

    action: Literal["buy", "sell", "hold", "watch"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    position_size: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    # sell 分型（update-sell-action-typing，#188）：exit=持有者减仓/退出敞口（申报
    # exit_schedule 减仓节奏，无新建仓参数，重新介入条件走 reeval_triggers）；
    # short=做空建仓（沿用 entry/stop/target 必填模板）。None=未申报/历史形态，
    # 校验层打回申报，仍缺默认按 exit 处理（add-holdings-context 可执行默认：原为
    # short，与生产分型语义相反且 A 股散户普遍不可执行）
    sell_type: Literal["exit", "short"] | None = None
    exit_schedule: str | None = None

    # add-watch-trigger-tracking：watch 双向触发位（上破/下破门槛）。宽松清洗同
    # _scrub_evidence_refs 哲学——噪声归 None 不炸管线；必填约束由 risk_judge
    # 终稿完整性检查承担（同 inaction_reason 先例），schema 保持宽松。
    trigger_high: float | None = None
    trigger_low: float | None = None

    @staticmethod
    def _coerce_trigger_level(value: object) -> float | None:
        """可解析数值字符串→float；None/bool/负值/0/非有限值/不可解析→None（未申报）。"""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                return None
        if not isinstance(value, (int, float)):
            return None
        coerced = float(value)
        if not math.isfinite(coerced) or coerced <= 0:
            return None
        return coerced

    @field_validator("trigger_high", mode="before")
    @classmethod
    def _normalize_trigger_high(cls, value: object) -> object:
        return cls._coerce_trigger_level(value)

    @field_validator("trigger_low", mode="before")
    @classmethod
    def _normalize_trigger_low(cls, value: object) -> object:
        return cls._coerce_trigger_level(value)

    @field_validator("sell_type", mode="before")
    @classmethod
    def _normalize_sell_type(cls, value: object) -> object:
        """sell_type 归一（update-sell-action-typing）：大小写/同义词容错——
        减仓/退出族→exit、做空族→short；未识别杂值归 None（不抛异常炸管线——
        申报约束由 risk_judge 校验层承担，打回后仍缺默认 exit）。
        非 sell 动作上的取值由 model_validator 归 None（防 LLM 在 buy 上塞值）。
        """
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if normalized == "exit":
            return "exit"
        if normalized == "short":
            return "short"
        # 中文同义词族（前缀匹配：「减仓」「退出敞口」「做空建仓」等）
        if value.startswith(("减仓", "退出")):
            return "exit"
        if value.startswith("做空"):
            return "short"
        return None

    # toolize-price-levels：sanity 校验二次未通过时由工具参考带修正（可观测标注）
    price_level_corrected: bool = False
    price_level_correction_reason: str | None = None
    evidence_refs: list[TradeEvidenceRef] = Field(default_factory=list)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _scrub_evidence_refs(cls, value: object) -> object:
        """宽松清洗证据引用：None/非列表 → []；丢弃结构非法条目（LLM 抖动不炸管线）。"""
        if value is None or not isinstance(value, list):
            return []
        cleaned: list[object] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            claim, source = item.get("claim"), item.get("source")
            if not isinstance(claim, str) or not isinstance(source, str):
                continue
            cleaned.append(item)
        return cleaned

    # require-watch-hold-rationale：非执行动作（watch/hold）结构化理由与再评估触发条件。
    # 必填约束由规则节点承担（同价位必填化先例），schema 保持宽松、清洗不抛异常。
    inaction_reason: str | None = None
    reeval_triggers: list[str] = Field(default_factory=list)

    @field_validator("inaction_reason", mode="before")
    @classmethod
    def _blank_inaction_reason_to_none(cls, value: object) -> object:
        """非字符串 / 纯空白一律归一为 None（未申报）——形态噪声不炸管线（同 anchors 先例）。

        非 str 形态（int/dict/list：LLM 会把理由包成 `{"reason": "..."}`）若原样交给
        pydantic 会抛 ValidationError 中断整条管线；trader.py / risk.py 的 LLM JSON
        入口无 schema 强制，故此处按「未申报」降级（必填约束由规则节点承担）。
        """
        if not isinstance(value, str):
            return None
        return None if not value.strip() else value

    @field_validator("reeval_triggers", mode="before")
    @classmethod
    def _normalize_reeval_triggers(cls, value: object) -> object:
        """LLM 形态噪声归一（同 anchors 先例）：str→单元素列表；
        None/非列表→[]；非 str 条目与纯空白条目丢弃（不字符串化，不抛异常）。

        与 `_normalize_anchors` 的差异：本条把纯空白条目录视为未申报丢弃。
        """
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, (list, tuple)):
            return [v for v in value if isinstance(v, str) and v.strip()]
        return []

    @model_validator(mode="after")
    def _scrub_sell_type_off_sell(self) -> TradeDecision:
        """非 sell 动作上的 sell_type/exit_schedule 一律归 None（#188：防 LLM 在
        buy/watch 上塞值污染下游分模板渲染与校验豁免）。"""
        if self.action != "sell":
            self.sell_type = None
            self.exit_schedule = None
        return self


class HoldingsContext(BaseModel):
    """受众持仓上下文（add-holdings-context 预留框架）。

    三态：True=已申报持有、False=已申报未持有、None=未申报。未申报 MUST NOT 被
    等同于「未持有」——二者对方向可执行性的含义不同（未持有者看空只能观望；
    未申报者按可执行默认 exit 表达并要求说明未持有情形处置）。

    当前唯一来源是分析请求的显式声明；未来自动选股/交易接入真实账户持仓时，
    在本模型上扩展来源与持仓明细字段（数量/成本随消费方一并增加，避免死字段）。
    """

    holds: bool | None = None


class FundManagerDecision(BaseModel):
    """Layer V Fund Manager 的审批决策。

    decision 受 Literal 约束，仅允许 approve/reject/return（ADR-0011 Layer V）。
    校验前做归一化（去首尾空白 + 转小写），容许 LLM 输出的大小写抖动；
    归一化后仍非法则抛 ValidationError 中断管线，不降级为任何默认决策
    （harden-llm-output-validation 决策 1、2）。
    """

    decision: Literal["approve", "reject", "return"]
    reasoning: str = Field(..., min_length=1)
    # 操作性结论（harden-decision-report-semantics D1）：FM 对最终方案的操作定性
    # 与把握度——approve 时必填（缺失中断管线），reject/return 时可缺
    # （reject 是终止无操作可定，return 方案将重做定性无意义）。
    action: str | None = None
    confidence: float | None = None

    @field_validator("reasoning")
    @classmethod
    def _reasoning_not_blank(cls, v: str) -> str:
        """spec「否决理由完整」：决策必须带理由（审批/退回/否决依据），纯空白等同缺失。"""
        if not v.strip():
            raise ValueError("reasoning 不得为空或纯空白")
        return v

    @model_validator(mode="after")
    def _approve_requires_action_and_confidence(self) -> FundManagerDecision:
        """approve 必须给出操作定性（action）与置信度——消除「approve 同意了什么」
        需通读理由推断的歧义（round5 校准实证 consistency rubric v1 因此误判）。"""
        if self.decision == "approve" and (self.action is None or self.confidence is None):
            raise ValueError("approve 决策必须包含 action 与 confidence（操作性结论）")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence 越界: {self.confidence}（须 0-1）")
        return self

    @field_validator("decision", mode="before")
    @classmethod
    def _normalize_decision(cls, value: object) -> object:
        """归一化决策值：仅处理大小写与首尾空白，不做同义词映射。"""
        if isinstance(value, str):
            return value.strip().lower()
        return value
