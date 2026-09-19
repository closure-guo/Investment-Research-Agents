# evals/judges.py
"""LLM-as-Judge 评估器(spec Requirement「LLM-as-Judge 评估器与 rubric 标准」)。

- 裁判模型 deepseek/deepseek-chat,temperature=0(可复现)
- rubric 末尾强约束 JSON {score, reason} + 「不以篇幅长短论优劣」
- 解析失败重试一次,仍失败 score=None(计入失败率,不阻塞实验)
- judge generation 统一经 gateway 观测(purpose="judge"),environment
  标记(langfuse-llm-as-a-judge)经 trace.metadata 传递,成本 Dashboard
  独立核算口径保留
"""

from __future__ import annotations

import os
import re

from finance_agent.nodes._llm_utils import parse_json_response


def _judge_model() -> str:
    """调用时读环境（时序 bug 防护）：``python -m evals.run`` 的模块 import
    先于 main() 的 load_dotenv 执行，import 时固化常量会拿到空值 → 跑批
    judge 全败而「先 dotenv 后 import」的单测全通（baseline-v2 28 项全败根因）。
    """
    return os.getenv("JUDGE_MODEL", "openai/deepseek-v4-flash")


def _judge_base_url() -> str:
    """裁判端点：JUDGE_BASE_URL 优先，回退主 LLM 中转（LLM_BASE_URL）。"""
    return os.getenv("JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL", "") or ""


def _judge_api_key() -> str:
    """裁判凭据：JUDGE_API_KEY 优先，回退主 LLM（LLM_API_KEY）。"""
    return os.getenv("JUDGE_API_KEY") or os.getenv("LLM_API_KEY", "") or ""


JUDGE_ENV = "langfuse-llm-as-a-judge"

# 输出契约含 confidence（0-1）：round5 校准实证 judge 对残缺输入（图表路径+审批章）
# 仍「全面覆盖」打 5 分无任何不确定性信号（幻觉不可从分面识别）——置信度定义为
# 「对评分依据充分性的把握」，材料缺失/截断/不足时 MUST 降低，使幻觉可从低置信暴露。
_JSON_TAIL = (
    '只输出 JSON: {"score": <1-5>, "confidence": <0-1>, "reason": "<一句话理由>"}\n'
    "不以篇幅长短论优劣。\n"
    "置信度语义: confidence 是你对本次评分依据充分性的把握;输入材料缺失、截断或不足以"
    "支撑判断时 MUST 降低置信度(如 <0.5),依据完整充分才给高置信度。"
)

# debate_quality 专用输出尾（v6）：结构化枚举字段 + 程序封顶语义说明。
# round10 预登记 fallback：prompt 内强制枚举（v5）实测到顶——8 行离线重判准确率
# 2/8 → 6/8，但仍漏判宁德、招行回归（LLM 自我枚举随机）。v6 改为 judge 只负责
# 如实标注类型，封顶由 run_judge 按 points 执行（代码承担判据，不依赖 LLM 自律）。
_DEBATE_POINTS_TAIL = (
    '只输出 JSON: {"score": <1-5>, "confidence": <0-1>,'
    ' "points": [{"header": "<论点标头原文>", "type": "data" | "qualitative"}],'
    ' "reason": "<一句话理由>"}\n'
    "不以篇幅长短论优劣。\n"
    "points 语义（v6 必填）：逐条列出双方每一轮的「论点:」标头行原文与类型——\n"
    "  type=data：该论点有数据或事实出处；\n"
    "  type=qualitative：纯定性——无数据、无事实出处的断言（含「历史上……」类无样本\n"
    "  经验论断、「护城河」「周期位置」「率先受益」类表态）。\n"
    "封顶由程序按 points 执行：存在任一 qualitative 时分数会被压到 ≤4，你无须自行扣分；\n"
    "请如实标注类型，不得为控分误标，也不得漏列标头行。\n"
    "置信度语义: confidence 是你对本次评分依据充分性的把握;输入材料缺失、截断或不足以"
    "支撑判断时 MUST 降低置信度(如 <0.5),依据完整充分才给高置信度。"
)

# rubric 版本（变更递增，校准门禁按版本重校准；decision_grounding：
# v1 初版 → v2 evidence_refs 结构化核对 → v3 interpretation 语义核对 →
# v6 补风控指标+风险辩论进材料（round5 校准）→ v7 来源归属三层判法 +
# 组合 claim 归属规则 + 解读失当强制核对（round7 校准，2026-09-13 owner 终裁）；
# debate_quality：v1 初版 → v2 逐条回应+具体证据（round5）→ v3 5 分收紧
# （round7 校准：judge 恒 5 宽松偏置，个别论点纯定性应降 4）；
# consistency：v1 初版 → v2 approve 批准对象语义定义，消除「watch+approve=
# 冲突」误判——round5 实测 11 条中 7 条被误打 1-3 分；v3 无；
# v4（round9）材料加【Trader 方案】节 + 核对「Risk Judge 裁决相对 Trader 方案
# 是否有未说明的方向/参数推翻」；
# debate_quality v4（round9）：5 分档判例具体化——round8 代裁实测 judge 对
# 「论点标头」里的纯定性论点（「历史上……」类无样本经验论断）视而不见，
# 美的/宁德两行数据密集正文掩盖标头定性论点仍给满分；
# v5（round10）：加强制枚举动作——评分前 MUST 逐条列出论点标头并标注
# 「数据/事实」或「纯定性」，任一纯定性即封顶 4（round9 审计：v4 判例在
# 4 分档生效但 5 分档仍漏判 ≥3 行，照搬 dg v7 逐条强制核对的有效模式）；
# v6（round10 预登记 fallback）：v5 强制枚举离线重判 8 行（准确率 2/8 → 6/8）
# 仍有漏判（宁德）与回归（招行）——LLM 自我枚举随机不可依赖；输出契约加
# `points` 结构化枚举字段，封顶改由 run_judge 按枚举结果执行（代码承担判据）。
# decision_grounding v8（round9）：归属层判例——round8 代裁实测 judge 对
# 「同一评判在多来源出现」判定偏机械（比亚迪 ref7 归 debate_bear 被误扣，
# 该评判 bear R2 与 RM 结论均有原话）；
# 全维度最新一版 = 输出契约加 confidence（材料依据不充分 MUST 降低，
# 使残缺输入上的幻觉可从低置信暴露），评分档位语义未变）
RUBRIC_VERSIONS: dict[str, int] = {
    "report_relevance": 3,
    "debate_quality": 6,
    "decision_grounding": 8,
    "consistency": 4,
}

RUBRICS: dict[str, str] = {
    "report_relevance": """你是投资研究报告评审专家。
【用户查询】{{query}}
【分析报告】{{report}}
评估报告对查询的切题度:
口径必读:「切题」指回答了用户的问题——覆盖再多相关维度但回避用户所问的
直接决策问题(如问「能不能买」而不给出买/不买/观望的结论),SHALL ≤3;
因合规或数据约束无法直接回答时,如实说明约束并给出约束下的可行分析,视为已回答。
5 = 完全切题,紧扣查询意图展开
4 = 基本切题,少量无关内容
3 = 部分切题,有显著偏离或答非所需的段落
2 = 大部分答非所问,仅边缘相关
1 = 完全答非所问
"""
    + _JSON_TAIL,
    "debate_quality": """你是投资辩论质量评审专家。
【多空辩论记录】{{debate_history}}
评估辩论的实质交锋程度:
评分前强制枚举动作（v5，防 5 分档漏判——round9 审计：judge 在 4 分档能指认
纯定性论点，但 5 分档对论点标头的定性表述视而不见，宁德/美的/平安银行三行
标头含纯定性论点仍被给满分）：
- 先逐条列出双方每一轮的「论点:」标头行，对每条标注【数据/事实】或【纯定性】
  （纯定性 = 无数据、无事实出处的断言，含「历史上……」类无样本经验论断、
  「护城河」「周期位置」「率先受益」类表态）；
- 枚举结果存在任一【纯定性】→ 封顶 4 分，不得给 5；
- 全部论点均为【数据/事实】且逐条回应对方 → 方可进入 5 分档。
5 = 双方逐条回应对方论点且引用具体证据(数据/事实)，且所有论点均有数据或
  事实支撑——只要存在个别纯定性论点（如仅凭「护城河」「周期位置」表态而无
  数据/事实），降 4
判例(v4)：论点列表（【bull】/【bear】标头的「论点:」行）中任一条为纯定性
  表述——含「历史上……」类无样本、无出处的经验论断——即降 4，即使该回应
  正文数据密集（round8 代裁实测：美的/宁德两行正文掩盖标头定性论点被误给满分）。
4 = 有实质交锋,证据基本充分,个别论点空泛
3 = 有交锋但多为立场声明,证据引用不足
2 = 交锋形式化,双方自说自话
1 = 单方输出或内容空洞,无实质辩论
回应覆盖率（交锋覆盖 4/4）是形式指标：全回应但答非所问、避重就轻，不算实质交锋。
"""
    + _DEBATE_POINTS_TAIL,
    "decision_grounding": (
        """你是投资决策依据评审专家。
【分析师结论】{{analyst_reports}}
【多空辩论记录】{{debate_history}}
【Research Manager 结论】{{research_manager_decision}}
【风控指标】{{risk_metrics}}
【风险辩论记录】{{risk_debate_history}}
【交易决策】{{trade_decision}}
评估交易决策的论据是否有前文支撑:
若交易决策含 evidence_refs（结构化论据引用，每项含 claim 与 source），逐条核对。
核对口径（v7 三层判法，round7 校准 owner 终裁确定）：
- ①事实层：claim 的数值/事实对全材料为真；
- ②归属层：source 标签 = 主张内容的真实来源（数字真实存在于材料的其它来源
  不豁免归属错安）；
- ③指认层：claim 句内自称的归属（如「激进方紧止损（36.5-37.0）」）也要对。
评分：
- 三层全过：claim 的数值/事实能在对应 source（technical/macro/fundamental/
  sentiment/debate_bull/debate_bear/research_manager/risk_aggressive/
  risk_conservative/risk_neutral/risk_metrics）的结论中找到出处，
  且 reasoning 的主要论据都能在 evidence_refs 中找到对应项 → 4-5 分；
- 逐条强制核对动作（防解读失当漏检——round7 终裁：judge 曾只核数值在不在、
  漏检单向解读给 5 分）：对每条 evidence_ref，先复述所标 source 中的原文
  （数值/原话），再与 claim 比对——数值在但方向/语气被拔高（「托底」升格
  「支撑」）、只取有利证据忽略反向证据（单向解读）、术语张冠李戴（毛利率写成
  净利率、行业垫底表述为「行业领先」）、期次错位（年报值说成季度值）均属
  解读失当，不得仅因数值有出处判语义一致 → 降至 2-3 分；
- 归属层错安（claim 内容在所标 source 中不存在、而存在于其它来源；或解读为
  主张重心却未按解读来源标注）→ 扣 1 分，档位不高于 4；
- 判例(v8)：同一评判在多个来源（如 debate_bear 与 research_manager）均有原话
  时，引用任一真实来源即合法，不因未选「最早」或「主要」来源扣分；仅当 claim
  在所有被标来源中均无原话时才按归属错安处理（round8 代裁实测：比亚迪 ref7
  归 debate_bear 被误扣，该评判 bear R2 与 RM 结论均有原话）。
- 组合 claim（数字取自 A + 解读取自 B）：句内注明解读出处即属忠实拼装，按
  三层判法不扣，标签可取数字来源；解读为主张重心且实际出自他方时按归属层错安处理；
- source 与论据对不上、claim 数值在来源中不存在（无中生有）、或 evidence_refs 缺失
  reasoning 中大量论据的引用 → 1-2 分。
无 evidence_refs 时按以下原规则从自由文本推断（不因缺字段报错）:
5 = 决策的每条论据都能在分析师结论/辩论结论中找到出处
4 = 主要论据有出处,个别细节无明确支撑
3 = 部分论据有出处,存在未论证的跳跃
2 = 论据与前文关联薄弱,或与前文结论有张力未解释
1 = 决策与前文矛盾,或论据无中生有
"""
        + _JSON_TAIL
    ),
    "consistency": """你是投资报告一致性评审专家。
【分析师章节结论】{{analyst_reports}}
【Trader 方案】{{trader_plan}}
【Research Manager 结论】{{research_manager_decision}}
【Risk Judge 裁决】{{risk_judgment}}
【Fund Manager 最终决策】{{fund_manager_decision}}
【最终报告结论章节】{{report_conclusion}}
先明确决策语义(评分前必读):
- Fund Manager 的 approve/reject/return 针对的是 Risk Judge 裁决后的最终交易方案
  (即裁决 JSON 中的 action/position_size),不是对裁决本身的赞成/否决票;
- approve = 同意执行该方案:裁决为 watch(观望)而 FM approve,表示批准观望,方向一致,
  不是冲突;裁决为 buy/hold/sell 而 FM approve 同理;
- 真正的冲突是:FM 批准了与裁决方向相反的行动(如裁决 sell 而 FM 批准买入建仓)、
  FM 理由与裁决逻辑相悖、或报告结论章节与决策方向不一致。
评估各层结论的一致性:
5 = 各层结论完全一致,无静默推翻
4 = 基本一致,个别表述差异但不影响方向
3 = 存在不一致但已显式说明理由
2 = 存在未说明的结论冲突
1 = 明显自相矛盾(如 FM 批准与 Risk Judge 裁决方向相悖的行动)
特别关注:Risk Judge 裁决相对【Trader 方案】是否有未说明的方向/参数推翻(方向相反、
仓位/价位/触发条件被改写而无理由说明);Fund Manager 结论是否与 Risk Judge 裁决后的
方案方向一致;报告结论章节是否与分析师章节一致。
"""
    + _JSON_TAIL,
}


def _call_judge_llm(prompt: str) -> str:
    """裁判调用:统一经 gateway(purpose="judge")。

    llm_config 映射决策:resolver 的 judge 环境分支要求 JUDGE_* 三件套齐
    且不回退 LLM_*;为保留 judges.py 既有 JUDGE_*→LLM_* 回退语义(存量
    eval 配置只设 LLM_* 也能跑),这里始终走请求级分支——由
    _judge_model/_judge_base_url/_judge_api_key 调用时读环境拼出
    {model, baseUrl, apiKey}(apiKey 可省:keyless 端点或 resolver env 回退)。

    baseUrl 缺失的旧行为是 litellm 直连 provider 官方端点;迁移后仅
    deepseek/* 显式补官方端点(https://api.deepseek.com/v1),其余前缀
    由 resolver 请求分支显式报 IncompleteLLMConfigError——run_judge
    捕获后记 judge_parse_failed,不阻塞实验(显式失败好过静默打错网关)。

    Langfuse 环境审计:judge generation 改由 gateway 统一观测,
    environment 标记经 trace.metadata 保留独立核算口径。
    """
    from finance_agent.llm.gateway import complete_text

    model = _judge_model()
    base_url = _judge_base_url()
    if not base_url and model.startswith("deepseek/"):
        base_url = "https://api.deepseek.com/v1"
    llm_config = {
        "model": model,
        "baseUrl": base_url or "",
        "apiKey": _judge_api_key() or "",
    }
    text, _meta = complete_text(
        [{"role": "user", "content": prompt}],
        purpose="judge",
        temperature=0.0,
        llm_config=llm_config,
        trace={"name": "judge", "metadata": {"environment": JUDGE_ENV}},
    )
    return text


def _render(dimension: str, variables: dict[str, str]) -> str:
    """rubric 模板 {{var}} 单次替换;未提供的变量替换为空串(避免 judge 看到裸占位符)。

    单次扫描(re.sub 回调)而非逐键 str.replace 循环:后者会把变量值里
    字面 {{another_key}} 二次替换掉。回调内 variables.get(...) 只对原始模板
    的每个占位符求值一次,值中出现的 {{...}} 不会被重新扫描。
    """
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda m: str(variables.get(m.group(1), "")) or "",
        RUBRICS[dimension],
    )


# 各维度依赖的关键输入变量（评估链路输入合同，delta 3.4）：
# 缺失/为空时该维度记 input_missing 跳过，不得对空输入出具正常分数
# （r5 校准教训：空辩论静默打 1 分混入均值，「自信但失真」）。
_DIMENSION_REQUIRED_VARS: dict[str, tuple[str, ...]] = {
    "report_relevance": ("report",),
    "debate_quality": ("debate_history",),
    "decision_grounding": ("analyst_reports", "research_manager_decision"),
    "consistency": ("analyst_reports", "report_conclusion"),
}


def _input_missing(dimension: str, variables: dict[str, str]) -> str | None:
    """返回首个缺失的变量名；齐全返回 None。"""
    for var in _DIMENSION_REQUIRED_VARS.get(dimension, ()):
        if not str(variables.get(var, "")).strip():
            return var
    return None


_POINT_TYPES = ("data", "qualitative")


def _normalize_points(raw: object) -> list[dict] | None:
    """debate_quality 枚举字段归一化；缺失/非法返回 None（调用方标 enumeration_missing）。

    非法 = 非非空 list / 元素非 dict / `type` 不在 `{data, qualitative}` / `header` 为空。
    任何非法一律按「未提供枚举」处理：不猜、不部分采信（部分采信会让缺失样本混入封顶判定）。
    """
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        ptype = str(item.get("type", "")).strip().lower()
        header = str(item.get("header", "")).strip()
        if ptype not in _POINT_TYPES or not header:
            return None
        out.append({"header": header, "type": ptype})
    return out


def run_judge_mean(dimension: str, variables: dict[str, str], *, repeats: int = 3) -> dict:
    """K 次重复判分取均值（消融判分协议；round11 实测单次调用 5/4 双峰翻转 ~46%）。

    实测（宁德 04baff5c，同材料同 rubric，n=13 次调用）：4 分 7 次 / 5 分 6 次——
    调用级方差 σ≈0.5，与消融待测的层级增量（0.25–0.5 量级）同阶，单次调用不足以
    支撑层间比较。**取均值而非中位**：双峰分布的 p→0.5 时中位不降翻转概率（P(中位=5)
    仍 ≈0.5），均值才是无偏且方差除以 K 的估计量；`scores` / `score_spread` 一并
    返回使噪声保持可见（不静默平均掉）。

    口径：None（解析失败/输入缺失）不计入均值但计入 `judge_failures`；全部 None →
    score=None（沿实验既有失败率口径）；遥测字段（封顶/纯定性/枚举缺失）取**最低分
    那次**调用的枚举——最保守的一次观测（若任一次找到纯定性标头，封顶证据不丢）。
    """
    results = [run_judge(dimension, variables) for _ in range(max(1, int(repeats)))]
    scores = [r.get("score") for r in results]
    valid = [int(s) for s in scores if s is not None]
    if not valid:
        base = dict(results[0])
        base.update(
            {
                "score": None,
                "scores": scores,
                "score_spread": None,
                "judge_repeats": len(results),
                "judge_failures": len(results),
            }
        )
        return base
    mean = round(sum(valid) / len(valid), 3)
    base = min(
        (r for r in results if r.get("score") is not None),
        key=lambda r: (r.get("score"), r.get("qualitative_points") or 0),
    )
    out = dict(base)
    out.update(
        {
            "score": mean,
            "scores": scores,
            "score_spread": max(valid) - min(valid),
            "judge_repeats": len(results),
            "judge_failures": len(results) - len(valid),
        }
    )
    return out


def run_judge(dimension: str, variables: dict[str, str]) -> dict:
    """跑一个 Judge 维度;解析失败重试一次,仍失败 score=None。

    输入合同：维度关键变量缺失 → score=None + reason="input_missing"
    （不调 LLM、不评分），保证评估结果不被空输入污染。

    debate_quality v6：解析后按 `points` 枚举**由代码**执行封顶——存在任一
    `type="qualitative"` 时分数压至 `min(score, 4)`（round10 实测 LLM 自我枚举
    随机，判据不能挂在它的自觉上）。枚举缺失/非法时分数不变（fail-open）但标记
    `enumeration_missing`，供校准与代裁识别「机制未生效」的行。

    Returns: {"name", "score": int 1-5 | None, "reason": str,
              "confidence": float 0-1 | None}——confidence 缺失/非法时为 None
    （旧格式容错，不阻塞评分）；debate_quality 额外含 points / qualitative_points /
    cap_applied / enumeration_missing（其余维度结果形状不变）。
    """
    missing = _input_missing(dimension, variables)
    if missing is not None:
        return {
            "name": dimension,
            "score": None,
            "reason": f"input_missing:{missing}",
            "confidence": None,
        }
    prompt = _render(dimension, variables)
    for _attempt in range(2):
        try:
            data = parse_json_response(_call_judge_llm(prompt))
            score = int(data["score"])
            if not 1 <= score <= 5:
                raise ValueError(f"score 越界: {score}")
            confidence: float | None = None
            raw_conf = data.get("confidence")
            if isinstance(raw_conf, (int, float)) and 0 <= float(raw_conf) <= 1:
                confidence = float(raw_conf)
            extra: dict = {}
            if dimension == "debate_quality":
                points = _normalize_points(data.get("points"))
                qualitative = sum(1 for p in points if p["type"] == "qualitative") if points else 0
                cap_applied = bool(points) and qualitative > 0 and score > 4
                if cap_applied:
                    score = 4
                extra = {
                    "points": points or [],
                    "qualitative_points": qualitative,
                    "cap_applied": cap_applied,
                    "enumeration_missing": points is None,
                }
            return {
                "name": dimension,
                "score": score,
                "reason": str(data.get("reason", "")),
                "confidence": confidence,
                **extra,
            }
        except Exception:  # noqa: S112 -- 故意静默重试;解析失败已通过最终 judge_parse_failed 记录
            continue
    return {"name": dimension, "score": None, "reason": "judge_parse_failed", "confidence": None}
