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


# ── B1：新增风险点是否被决策吸收（nli） ──

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


# ── B2：被反驳的论点是否被修正（judge） ──

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


# ── 跑判定 ──


def run_b1_judgments(
    rows: Sequence[dict], *, llm_fn: JudgeFn | None = None, per_ticker: int = B1_PER_TICKER
) -> list[dict]:
    """B1 吸收判定（每标的取前 N 行；行数上限是成本闸门，写进预登记 §6）。"""
    return _run_rows(
        rows, prompt_fn=b1_prompt, key="absorbed", llm_fn=llm_fn, per_ticker=per_ticker
    )


def run_b2_judgments(
    rows: Sequence[dict], *, llm_fn: JudgeFn | None = None, per_ticker: int = B2_PER_TICKER
) -> list[dict]:
    """B2 修正判定（每标的取前 N 行）。"""
    return _run_rows(
        rows, prompt_fn=b2_prompt, key="corrected", llm_fn=llm_fn, per_ticker=per_ticker
    )


def _run_rows(
    rows: Sequence[dict],
    *,
    prompt_fn: Callable[[dict], str],
    key: str,
    llm_fn: JudgeFn | None,
    per_ticker: int,
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
            out.append(
                {
                    **row,
                    "judge_label": label,
                    "judge_reason": (parsed or {}).get("reason"),
                    "judge_parse_failed": parsed is None,
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
        for _ in range(votes):
            parsed = _ask_json(b5_prompt(reports[first], reports[second]), fn)
            if parsed is None:
                continue
            winner = str(parsed.get("winner") or "").strip().upper()[:1]
            if winner in ("A", "B"):
                # 位置映射回真实臂：A=先展示的那份
                raw_votes.append(first if winner == "A" else second)
            elif "TIE" in str(parsed.get("winner") or "").upper():
                raw_votes.append("tie")
        out.append(
            {
                # 保留调用方给的 unit_id（判定缓存按它回读；此前丢掉导致结果匹配不上）
                "unit_id": pair.get("unit_id"),
                "pair_key": pair.get("pair_key"),
                "ticker": pair.get("ticker"),
                "position_first": first,
                "position_second": second,
                "votes": raw_votes,
                "verdict": majority_verdict(raw_votes),
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
