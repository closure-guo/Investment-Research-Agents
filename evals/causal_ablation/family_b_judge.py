"""族 B 判定腿（nli/judge）：B1 吸收、B2 修正、B5 pairwise 盲评 + 校准材料导出。

**门控纪律**（spec「校准门控全覆盖」）：本模块产出的判定**不得直接进结论**——
每批须抽 ≥20% 交人工标注，一致率 ≥0.80 才允许进消融结论；未过门控的读数一律标注为 provisional。
本模块负责把「判定 + 校准抽样表」一起做出来，人工标注列留空（机器不代答）。

**成本控制（写进预登记 §6）**：每标的判定行数设上限（确定性取前 N 行），避免行数随辩论
轮次膨胀——B1 ≤4 行/标的、B2 ≤4 行/标的、B5 = 1 对 × K=3 票/标的。

**B5 盲评协议**：同一对（analysts 报告 vs full 报告）并排，A/B 位置用 `pairwise_assign`
按 pair_key 确定性随机（同 key 可复现），K=3 次取多数决（`majority_verdict`，平票记 tie），
并记录位置分布供位置偏倚检验（预登记停止规则③）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from evals.causal_ablation.family_b import majority_verdict, pairwise_assign
from finance_agent.nodes._llm_utils import parse_json_response

# 每标的判定行数上限（成本闸门；确定性取前 N 行）
B1_PER_TICKER = 4
B2_PER_TICKER = 4
B5_VOTES = 3

B5C_RUBRIC = "b5c-v1"

# rubric 版本（校准门控绑定）。默认 = v1（2026-09-18 过门：B1 吸收 0.875 / B2 0.906）。
# v2（通道分账+双问+顶撞判例）2026-09-18 校准样本验证**未过门**：B1 吸收 0.825 / B1 新增 0.750 /
# B2 0.690（顶撞判例判松 + 通道规则判严 + 双变量同动无法归因）——模板归档为 _*_TEMPLATE_V2，
# 只作单变量迭代基线，不得进结论。
B1_RUBRIC = "b1-v1"
B2_RUBRIC = "b2-v1"

JudgeFn = Callable[[str], str]


def default_judge_fn() -> JudgeFn:
    """默认裁判通道：复用 `evals.judges._call_judge_llm`（gateway purpose=judge）。"""
    from evals.judges import _call_judge_llm

    return _call_judge_llm


def _ask_json(prompt: str, llm_fn: JudgeFn) -> dict | None:
    """单次判定调用：解析失败重试一次，仍失败返回 None（计入失败率，不静默丢弃）。"""
    for attempt in range(2):
        try:
            raw = llm_fn(prompt)
            parsed = parse_json_response(raw) if isinstance(raw, str) else None
        except Exception:  # noqa: BLE001 - 调用/解析异常同处理：重试一次后记 None
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        if attempt == 1:
            return None
    return None


# ── B1：新增风险点是否被决策吸收（nli）── v1 默认（过门 0.875）──

_B1_TEMPLATE = """你是判定员。判断「决策是否吸收了下面这条风险点」。

判定口径（宁严勿宽）：
- 吸收 = 决策的理由/依据里**明确回应**了该风险点（采纳、量化、或作为不行动的理由）；
- 只是同一主题被提到但未回应风险本身 → 未吸收；
- 决策方向与该风险相反但给出理由 → 算吸收（理由即回应）。

风险点：{risk_point}

决策动作：{action}
决策理由：{reasoning}
决策依据引用：{evidence_refs}
基金经理裁决：{fm_decision}；理由：{fm_reasoning}

只输出 JSON: {{"absorbed": true|false, "reason": "<一句话依据>"}}"""


def b1_prompt(row: dict) -> str:
    refs = "；".join(
        f"{r.get('claim')}（来源 {r.get('source')}）" for r in row.get("evidence_refs") or []
    )
    return _B1_TEMPLATE.format(
        risk_point=row.get("risk_point") or "",
        action=row.get("decision_action") or "",
        reasoning=row.get("decision_reasoning") or "",
        evidence_refs=refs or "（无）",
        fm_decision=row.get("fm_decision") or "（无）",
        fm_reasoning=row.get("fm_reasoning") or "（无）",
    )


# ── B1 v2（归档：2026-09-18 未过门 0.825/0.750，单变量迭代基线）──

_B1_TEMPLATE_V2 = """你是判定员。对下面这条「辩论空方风险点」回答两个问题。

Q1「是否新增」（new_risk_point）：风险点里有没有分析师发现**没有提供过**的判断或事实？
判法（两问口诀）：把风险点里分析师已说过的事实全部删掉，看剩下什么——
- 剩下的是关于这家公司的、可对错的主张（因果归属 / 预测情景 / 跨源合成 / 新事实断言）→ true
- 只剩下风险化措辞、权重口径主张、或对对方观点的评价（该侧重哪个数 / 该怎么说 / 对方观点不行）→ false
判例（人工终裁确认）：
- true：「负增长是需求端被动收缩而非主动改革控货」（因果归属）；「收购整合后商誉减值压力将累积」（预测）；
  「技术强势与资金面背离」（跨源合成，两条发现里谁都没说过）；「机构资金持续撤离」（新事实断言——上游无此数据则无源）
- false：「利润改善缺乏营收端支撑」（发现里已把营收零增长与利润微增并置，只是换成风险口吻）；
  「边际动能才是定价核心」（权重主张）；「低胜率的左侧赌博」（评价对方观点）；「PMI 跌破荣枯线压制需求」（宏观分析师原话）

Q2「是否吸收」（absorbed）：决策是否接住了这条风险点。**通道分账**——决策依据引用每条带来源标签：
fundamental/technical/macro/sentiment = 分析师通道；debate_*/risk_*/research_manager = 辩论下游通道（其内容只能起源于辩论层）。
- Q1=true：只认**新增那部分**被接住——它出现在决策理由正文、或出现在辩论下游通道的引用条目里 → true；
  分析师通道的引用再相似也不算（决策本来就会引用分析师发现，分不清是不是辩论的功劳）；
  若发现新增部分出现在分析师通道条目里，说明 Q1 判错了，改 Q1=false；
  新增部分在正文与引用中零踪影 → false
- Q1=false：决策理由 / 依据引用 / 触发条件任一处实质回应风险点 → true

风险点：{risk_point}

分析师发现（判 Q1 的参照池，共 {n_references} 条）：{reference_points}
决策动作：{action}
决策理由：{reasoning}
决策依据引用（含来源标签）：{evidence_refs}
基金经理裁决：{fm_decision}；理由：{fm_reasoning}

只输出 JSON: {{"new_risk_point": true|false, "absorbed": true|false, "reason": "<一句话依据>"}}"""


def b1_prompt_v2(row: dict) -> str:
    refs = "；".join(
        f"{r.get('claim')}（来源 {r.get('source')}）" for r in row.get("evidence_refs") or []
    )
    points = [str(p) for p in row.get("reference_points") or []]
    return _B1_TEMPLATE_V2.format(
        risk_point=row.get("risk_point") or "",
        n_references=len(points),
        reference_points=" ｜ ".join(points) or "（无）",
        action=row.get("decision_action") or "",
        reasoning=row.get("decision_reasoning") or "",
        evidence_refs=refs or "（无）",
        fm_decision=row.get("fm_decision") or "（无）",
        fm_reasoning=row.get("fm_reasoning") or "（无）",
    )


# ── B2：被反驳的论点是否被修正（judge）── v1 默认（过门 0.906）──

_B2_TEMPLATE = """你是判定员。判断「后续轮次的反驳是否**修正**了被反驳的论点」。

判定口径（宁严勿宽）：
- 修正 = 反驳给出了与原论点冲突的证据/推理，且原论点被放弃或收窄（改口、让步、降级措辞）；
- 只是重申立场、换个说法重复、或新增无关论点 → 未修正；
- 双方各说各话（原论点未被回应）→ 未修正。

被反驳的论点（前一轮）：{rebutted_point}

反驳行文（后一轮）：{rebuttal_text}

只输出 JSON: {{"corrected": true|false, "reason": "<一句话依据>"}}"""


def b2_prompt(row: dict) -> str:
    return _B2_TEMPLATE.format(
        rebutted_point=row.get("rebutted_point") or "",
        rebuttal_text=row.get("rebuttal_text") or "",
    )


# ── B2 v2（归档：2026-09-18 未过门 0.690——顶撞判例判松，单变量迭代基线）──

_B2_TEMPLATE_V2 = """你是判定员。判断「后续轮次的反驳是否**修正**了被反驳的论点」。

判定口径（宁严勿宽）：
- 修正 = 反驳给出了与原论点冲突的证据/推理，**直接顶撞原论点本身**（否定其前提、归因或结论，逼其收窄或放弃）；
- 只是重申立场、换个说法重复、或新增无关论点（各说各话）→ 未修正；
- 只表达怀疑 / 提出另一种可能而无冲突实证 → 未修正。
判例（人工终裁确认）：
- 修正：「毛利率 0.81% 为实际发生值，价格战延续将致亏损扩大」直接否定「价格战出清接近尾声」（实证顶撞前提）；
  「单季修复不足以推翻净利连续下滑趋势」直接否认「拐点已现/最坏已过」；
  「主动控量挺价，负增长是主动阵痛而非需求坍塌」直接顶撞「改革红利已透支」的归因
- 未修正：「单季转正可能来自拨备调节」对「ROE 降幅收窄已企稳」只提出另一种可能、无冲突实证（终裁站未修正）；
  「负债率高」对「回购分红提升回报」属另起话题（各说各话）

被反驳的论点（前一轮）：{rebutted_point}

反驳行文（后一轮）：{rebuttal_text}

只输出 JSON: {{"corrected": true|false, "reason": "<一句话依据>"}}"""


def b2_prompt_v2(row: dict) -> str:
    return _B2_TEMPLATE_V2.format(
        rebutted_point=row.get("rebutted_point") or "",
        rebuttal_text=row.get("rebuttal_text") or "",
    )


# ── B5：pairwise 盲评（决策辅助价值） ──

_B5_TEMPLATE = """你是投资报告评审。两份报告基于**同一份数据快照**生成，请判断哪一份**更有助于做决策**。

评判口径（只看决策辅助价值，不看篇幅与文采）：
- 依据是否可核对（数字/事实有出处、与数据一致）；
- 结论是否明确可执行（动作、价位/仓位、风险边界）；
- 是否交代了反方证据与不确定性。

报告 A：
{report_a}

报告 B：
{report_b}

只输出 JSON: {{"winner": "A"|"B"|"tie", "reason": "<一句话依据>"}}"""


def b5_prompt(report_a: str, report_b: str) -> str:
    return _B5_TEMPLATE.format(report_a=report_a, report_b=report_b)


# ── B5c：结论级盲评（预登记 §10，b5c-v1）——只比最终交易指令的内生质量 ──

_B5C_TEMPLATE = """你是交易指令质量评审。同一分析系统对同一股票（{ticker}）产出两份最终交易指令
（A/B 展示顺序随机）。一份经额外复核环节修改，一份为原始草案——你不需要知道哪份是哪份。

【逐条检查六准则】
1 方向自洽：动作（买入/卖出/持有/观望）与自述多空证据权重一致（理由明说均衡却单边或高置信 = 不自洽）
2 置信度校准：置信度数字与理由的确定性语言匹配（"证据均衡"配 70% = 校准差）
3 可执行性：具体参数（价位/仓位/时限）或明确的再评估触发条件（"观望等待好转" = 差）
4 风险边界：止损/退出/仓位上限是否交代，且与自述风险一致（自述回撤大却无退出 = 差）
5 可证伪性：写明什么情况说明这份指令错了
6 内部无矛盾：方向/置信度/触发条件/理由之间互不打架

【硬性排除——违反即无效评分】
- 谨慎不加分：更保守的方向/更低的置信度本身不是优点，除非其理由证明了保守的必要性；激进方案论证自洽同样胜
- 决断不加分：买卖不优于观望；观望论证充分同样胜
- 篇幅、文风、流畅度不计分
- 对流程环节的提及（辩论/审批/复核字样）不是质量证据，忽略之

【裁决】六准则实质等价 → 判 tie（不得凭文风微差强分高下）；否则选整体质量更高的一份。

指令 A：
{instruction_a}

指令 B：
{instruction_b}

只输出 JSON: {{"winner": "A"|"B"|"tie", "decisive_criterion": "<起决定作用的准则号+短名，如'2 置信度校准'；tie 为 null>", "reason": "<必须引用两份指令的具体字段值作证据，≤120字>"}}"""


def b5_conclusion_prompt(ticker: str, instruction_a: str, instruction_b: str) -> str:
    return _B5C_TEMPLATE.format(
        ticker=ticker, instruction_a=instruction_a, instruction_b=instruction_b
    )


def run_b5_conclusion(
    pairs: Sequence[dict], *, llm_fn: JudgeFn | None = None, votes: int = B5_VOTES
) -> list[dict]:
    """结论级盲评：`pair` = {unit_id, pair_key, ticker, instruction_a, instruction_b}。

    位置随机化（pairwise_assign 按 pair_key 确定性）后展示，票面 A/B 映射回逻辑臂 a/b。"""
    fn = llm_fn or default_judge_fn()
    out: list[dict] = []
    for pair in pairs:
        first, second = pairwise_assign("a", "b", pair_key=str(pair.get("pair_key") or ""))
        display = {
            "a": str(pair.get("instruction_a") or ""),
            "b": str(pair.get("instruction_b") or ""),
        }
        raw_votes: list[str] = []
        vote_reasons: list[str | None] = []
        decisive: list[str | None] = []
        for _ in range(votes):
            parsed = _ask_json(
                b5_conclusion_prompt(
                    str(pair.get("ticker") or ""), display[first], display[second]
                ),
                fn,
            )
            if parsed is None:
                continue
            winner = str(parsed.get("winner") or "").strip().lower()
            if winner in ("a", "b"):
                picked = first if winner == "a" else second
            elif "tie" in winner:
                picked = "tie"
            else:
                continue
            raw_votes.append(picked)
            vote_reasons.append(parsed.get("reason"))
            decisive.append(parsed.get("decisive_criterion"))
        verdict = majority_verdict(raw_votes)
        out.append(
            {
                "unit_id": pair.get("unit_id"),
                "pair_key": pair.get("pair_key"),
                "ticker": pair.get("ticker"),
                "position_first": first,
                "votes": raw_votes,
                "vote_reasons": vote_reasons,
                "decisive_criteria": decisive,
                "judge_reason": next(
                    (r for v, r in zip(raw_votes, vote_reasons, strict=False) if v == verdict), None
                ),
                "verdict": verdict,
                "votes_requested": votes,
                "judge_parse_failed": len(raw_votes) < votes,
                "rubric": B5C_RUBRIC,
            }
        )
    return out


# ── 跑判定 ──


def run_b1_judgments(
    rows: Sequence[dict],
    *,
    llm_fn: JudgeFn | None = None,
    per_ticker: int = B1_PER_TICKER,
    prompt_fn: Callable[[dict], str] | None = None,
    rubric: str | None = None,
) -> list[dict]:
    """B1 吸收判定（每标的取前 N 行；行数上限是成本闸门，写进预登记 §6）。

    prompt_fn/rubric 覆盖仅用于**单变量迭代实验**（如 b1_prompt_v2 + "b1-v2"）——
    非 v1 rubric 的判定未过校准门控，不得进结论。"""
    return _run_rows(
        rows,
        prompt_fn=prompt_fn or b1_prompt,
        key="absorbed",
        llm_fn=llm_fn,
        per_ticker=per_ticker,
        extra_keys=("new_risk_point",),
        rubric=rubric or B1_RUBRIC,
    )


def run_b2_judgments(
    rows: Sequence[dict],
    *,
    llm_fn: JudgeFn | None = None,
    per_ticker: int = B2_PER_TICKER,
    prompt_fn: Callable[[dict], str] | None = None,
    rubric: str | None = None,
) -> list[dict]:
    """B2 修正判定（每标的取前 N 行；覆盖参数语义同 run_b1_judgments）。"""
    return _run_rows(
        rows,
        prompt_fn=prompt_fn or b2_prompt,
        key="corrected",
        llm_fn=llm_fn,
        per_ticker=per_ticker,
        rubric=rubric or B2_RUBRIC,
    )


def _run_rows(
    rows: Sequence[dict],
    *,
    prompt_fn: Callable[[dict], str],
    key: str,
    llm_fn: JudgeFn | None,
    per_ticker: int,
    extra_keys: tuple[str, ...] = (),
    rubric: str | None = None,
) -> list[dict]:
    fn = llm_fn or default_judge_fn()
    group: dict[str, list[dict]] = {}
    for row in rows:
        group.setdefault(str(row.get("ticker") or ""), []).append(row)
    out: list[dict] = []
    for ticker_rows in group.values():
        for row in _spread(ticker_rows, per_ticker):
            parsed = _ask_json(prompt_fn(row), fn)
            label = parsed.get(key) if isinstance(parsed, dict) else None
            extra = {
                f"judge_{k}": (parsed.get(k) if isinstance(parsed, dict) else None)
                for k in extra_keys
            }
            out.append(
                {
                    **row,
                    "judge_label": label,
                    "judge_reason": (parsed or {}).get("reason"),
                    "judge_parse_failed": parsed is None,
                    **({"rubric": rubric} if rubric else {}),
                    **extra,
                }
            )
    return out


def _spread(rows: Sequence[dict], limit: int) -> list[dict]:
    """每标的取 `limit` 行：带相似度时按相似度**等距抽样**（覆盖两端的极端样本），
    否则取前 N 行。确定性（同输入同结果），成本闸门不改变可复现性。"""
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    if all(isinstance(r.get("max_similarity"), (int, float)) for r in rows):
        ordered = sorted(rows, key=lambda r: float(r["max_similarity"]))
        step = (len(ordered) - 1) / (limit - 1) if limit > 1 else 0
        picked = {int(round(i * step)) for i in range(limit)} if limit > 1 else {0}
        return [ordered[i] for i in sorted(picked)]
    return list(rows[:limit])


def run_b5_pairwise(
    pairs: Sequence[dict],
    *,
    llm_fn: JudgeFn | None = None,
    votes: int = B5_VOTES,
) -> list[dict]:
    """B5 盲评：每对 K 票多数决。`pair` = {pair_key, ticker, report_a, report_b}（a/b 已随机化）。"""
    fn = llm_fn or default_judge_fn()
    out: list[dict] = []
    for pair in pairs:
        first, second = pairwise_assign(
            str(pair.get("report_a_id") or "A"),
            str(pair.get("report_b_id") or "B"),
            pair_key=str(pair.get("pair_key") or pair.get("ticker") or ""),
        )
        reports = {
            str(pair.get("report_a_id") or "A"): str(pair.get("report_a") or ""),
            str(pair.get("report_b_id") or "B"): str(pair.get("report_b") or ""),
        }
        raw_votes: list[str] = []
        vote_reasons: list[str | None] = []  # 与 raw_votes 对齐（校准材料自证可读）
        for _ in range(votes):
            parsed = _ask_json(b5_prompt(reports[first], reports[second]), fn)
            if parsed is None:
                continue
            winner = str(parsed.get("winner") or "").strip().upper()[:1]
            picked: str | None = None
            if winner in ("A", "B"):
                # 位置映射回真实臂：A=先展示的那份
                picked = first if winner == "A" else second
            elif "TIE" in str(parsed.get("winner") or "").upper():
                picked = "tie"
            if picked is not None:
                raw_votes.append(picked)
                vote_reasons.append(parsed.get("reason"))
        verdict = majority_verdict(raw_votes)
        out.append(
            {
                # 保留调用方给的 unit_id（判定缓存按它回读；此前丢掉导致结果匹配不上）
                "unit_id": pair.get("unit_id"),
                "pair_key": pair.get("pair_key"),
                "ticker": pair.get("ticker"),
                "position_first": first,
                "position_second": second,
                "votes": raw_votes,
                "vote_reasons": vote_reasons,
                # 多数派第一票的理由（多数决的依据须可追溯）
                "judge_reason": next(
                    (r for v, r in zip(raw_votes, vote_reasons, strict=False) if v == verdict), None
                ),
                "verdict": verdict,
                "votes_requested": votes,
                "judge_parse_failed": len(raw_votes) < votes,
            }
        )
    return out


# ── 校准材料与门控判词 ──


CALIBRATION_COLUMNS: tuple[str, ...] = (
    "unit_id",
    "ticker",
    "material",
    "judge_label",
    "judge_reason",
    "human_label(与判定一致?是/否)",
)


def calibration_rows(judged: Sequence[dict], *, material_key: str) -> list[dict]:
    """校准抽样行：判定 + 材料摘要 + 留空的人工标注列（20% 抽样由调用方执行）。"""
    rows: list[dict] = []
    for row in judged:
        rows.append(
            {
                "unit_id": row.get("unit_id"),
                "ticker": row.get("ticker"),
                "material": str(row.get(material_key) or "")[:500],
                "judge_label": row.get("judge_label"),
                "judge_reason": row.get("judge_reason"),
                "human_label(与判定一致?是/否)": "",
            }
        )
    return rows


def gate_verdict(judged: Sequence[dict], *, method: str) -> dict:
    """校准门控前的判词：只报**材料事实**（判定数/解析失败数/yes 率），不下结论。

    一致率须待人工标注回填后由 `calibration_gate.gate_dimension` 裁决——
    本函数不接触人工标签，避免机器代答。
    """
    labeled = [r for r in judged if r.get("judge_label") is not None]
    positives = sum(1 for r in labeled if r.get("judge_label") is True)
    return {
        "method": method,
        "rows": len(judged),
        "labeled": len(labeled),
        "parse_failed": sum(1 for r in judged if r.get("judge_parse_failed")),
        "positive_rate": (positives / len(labeled)) if labeled else None,
        "status": "provisional（未过校准门控：须人工标注 ≥20%，一致率 ≥0.80 才可进结论）",
    }


def summarize(judged: Sequence[dict], *, key: str) -> dict[str, Any]:
    """逐标的判定汇总（给报告用；rate=None 表示该标的无可判行，不得记 0）。"""
    per_ticker: dict[str, dict[str, Any]] = {}
    for row in judged:
        ticker = str(row.get("ticker") or "")
        bucket = per_ticker.setdefault(ticker, {"rows": 0, "labeled": 0, "positive": 0})
        bucket["rows"] += 1
        if row.get("judge_label") is not None:
            bucket["labeled"] += 1
            if row.get("judge_label") is True:
                bucket["positive"] += 1
    for bucket in per_ticker.values():
        bucket["rate"] = (bucket["positive"] / bucket["labeled"]) if bucket["labeled"] else None
    return {"key": key, "per_ticker": per_ticker}
