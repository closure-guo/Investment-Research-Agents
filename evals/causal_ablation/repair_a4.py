"""A4 单点修复回路补测：正文与 claim **一致**的稀疏数值错误 → 回路开/关两态（族 A 最后一格）。

**为什么不复用既有的 `unit_error` 注入器**：离线注入只改 claim、不改正文（`_inject_unit_error`），
而修复回路的第一步是 `citation_repair.locate_sentence`——按 `claim.stated_value` 在正文里找出错句。
注入后正文仍是原值 → 定位必然落空 → 回路连「尝试」都没有（本轮实测：`locate_sentence` 返回 None）。
那是注入形态造成的假阴性，不是机制读数。生产形态是**正文与 claim 一起写错**（分析师写下错数、
claim 申报同一个错数），本模块据此自建注入形态——不动 8 类污染矩阵、不改既有注入器。

**两态语义与登记主指标**：A4 的登记主指标是「修复前后真 FAIL 率差」。
- 关态（`a4_on=True` + `mechanism_toggle("A4", on=False)`）：回路被补丁置为 no-op → 错误数字滞留
  正文（**构造性确定**，不含 LLM 与随机性）；
- 开态（`on=True`，`repair_fn="module"`）：真调用单点修复 → 由 citation 节点同款**重校验仲裁**
  （全 PASS 才算修复成功）。

**不得读成「关态 = 生产反事实」**：生产在 A4 缺席时把稀疏 value_mismatch 交回**全量定向重试**
（`citation_node` 的 fallback 分支），本批不测该反事实——故关态读数只证「错误确实滞留过」，
不构成「没有 A4 就会交付错值」的证明。回路价值另有一层成本账（1 次轻量改写 vs 整份分析师重跑），
本批只记回路侧实测成本，不估算对侧。

**暴露率**：A4 的暴露 = 自然分布下的稀疏 value_mismatch（<3 处）。本模块从冻结产物离线复算
（零 LLM、确定性），并把自然命中的逐条案例导出为人工终裁表——**自然单元未必是真错误**
（可能是校验器误报；对误报做「成功修复」反而是伤害），故自然腿的结论以人工终裁为前提。
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from evals.causal_ablation import pilot_runner as pr

LEG_A4 = "offline_replay"
# 修复回路调用 LLM：单元成本按真跑型记账（不得记 0）
COST_CLASS_A4 = "real_run"

# 扰动倍数候选：取首个**真产生 FAIL** 者，判据复用校验器单一实现
# `pilot_runner._injection_effective`（口径不得复制）。不用 `_UNIT_ERROR_FACTORS`（量级换算）：
# A4 测的是修复回路而非单位误用，1e8 倍的数字写进正文既不自然，也会让「LLM 是否改对」
# 的判读掺入量级换算噪声。
DRIFT_FACTORS: tuple[float, ...] = (1.25, 0.8, 1.5, 2.0)

MODE_INJECTED = "injected"
MODE_NATURAL = "natural"


@dataclass(frozen=True)
class Target:
    """被修复对象：一个 claim 及其在该产物扁平 claims 序列里的位置。"""

    agent: str
    claim_index: int
    flat_index: int
    stated_value: float
    polluted_value: float | None  # 自然单元为 None（无注入，读数即该 claim 的 FAIL 下场）
    sentence: str = ""  # 出错句（人工终裁用；定位失败记空）


# ── 注入（正文与 claim 一致） ──


def value_drift(claim: dict, snapshot: dict) -> float | None:
    """首个**一致注入后落进 `value_mismatch` 桶**的扰动值；None = 该 claim 不可作 A4 目标。

    判据走校验器单一实现（口径不得复制），但比 `pilot_runner._injection_effective` 严两点：
    ① 只认 `value_mismatch` 桶——那是修复回路分流认的桶；`internal_inconsistency`
    （数值回声 / 宏观记录核对）等别的桶不进回路，混进来测的就不是 A4；② 按**一致注入**判定
    （claim 值与自述同步改写）——只改值会先撞回声检查，`_injection_effective` 会把那类 FAIL
    误当值级偏差（2026-09-17 试跑实证：002415 的回声 FAIL 在自述同步后消失）。
    """
    stated = claim.get("stated_value")
    if not pr._is_number(stated):
        return None
    original = float(cast(Any, stated))
    token = _token_of(str(claim.get("interpretation") or ""), original)
    if token is None:
        return None
    if not _original_passes(claim, snapshot):
        return None  # 本来就错的 claim 不进注入腿（那是自然腿的对象）
    for factor in DRIFT_FACTORS:
        candidate = original * factor
        if candidate == original:
            continue
        if _lands_in_value_mismatch(claim, snapshot, candidate, _fmt_like(token, candidate)):
            return candidate
    return None


def _original_passes(claim: dict, snapshot: dict) -> bool:
    """原版 claim 是否 PASS（注入腿的前提：FAIL 须由污染造成，不是本来就有的）。"""
    from finance_agent.citation import Claim, verify_claims

    try:
        verdict = verify_claims([Claim.model_validate(claim)], snapshot)[0]
    except Exception:  # noqa: BLE001 - 脏 claim 不作目标
        return False
    return verdict.status == "PASS"


def _lands_in_value_mismatch(claim: dict, snapshot: dict, polluted: float, new_token: str) -> bool:
    """一致注入（claim 值与 interpretation 同步改写）后是否 FAIL 且桶为 value_mismatch。"""
    from finance_agent.citation import Claim, verify_claims

    candidate = dict(claim)
    interpretation = rewrite_number(
        str(claim.get("interpretation") or ""), float(cast(Any, claim["stated_value"])), new_token
    )
    if interpretation is None:
        return False
    candidate["stated_value"] = polluted
    candidate["interpretation"] = interpretation
    try:
        verdict = verify_claims([Claim.model_validate(candidate)], snapshot)[0]
    except Exception:  # noqa: BLE001 - 脏 claim 不作目标
        return False
    return verdict.status == "FAIL" and verdict.bucket == "value_mismatch"


def _token_of(text: str, value: float) -> str | None:
    """正文里 ≈value 的数值 token 原文（保留千分位/小数位形态供改写时对齐）。"""
    from finance_agent.nodes.citation_repair import _NUM_RE, _close

    for match in _NUM_RE.finditer(text):
        try:
            parsed = float(match.group(0).replace(",", ""))
        except ValueError:  # pragma: no cover - 正则保证可解析
            continue
        if _close(parsed, value):
            return match.group(0)
    return None


def _fmt_like(template: str, value: float) -> str:
    """按原 token 的形态格式化新值（沿用千分位与小数位）。

    小数位不足时**逐位加精度**直到往返一致（`_close` 的 0.1% 容差）：修复回路第一步是按
    claim 申报值在正文里定位——写成「9.4」而申报「9.375」会让定位直接落空，回路连尝试
    都没有（2026-09-17 试跑：20 单元里 3 个因此没调到 LLM）。
    """
    from finance_agent.nodes.citation_repair import _close

    decimals = len(template.split(".", 1)[1]) if "." in template else 0
    thousands = "," in template
    token = template
    for extra in range(7):
        digits = decimals + extra
        token = f"{value:,.{digits}f}" if thousands else f"{value:.{digits}f}"
        try:
            if _close(float(token.replace(",", "")), value):
                return token
        except ValueError:  # pragma: no cover - 格式化保证可解析
            continue
    return token  # pragma: no cover - 7 位精度足够本批任何取值


def rewrite_number(markdown: str, old_value: float, new_token: str) -> str | None:
    """把正文中首个 ≈old_value 的数值 token 换成 new_token；无命中 → None（不盲写）。"""
    from finance_agent.nodes.citation_repair import _NUM_RE, _close

    for match in _NUM_RE.finditer(markdown):
        try:
            parsed = float(match.group(0).replace(",", ""))
        except ValueError:  # pragma: no cover - 正则保证可解析
            continue
        if _close(parsed, old_value):
            return markdown[: match.start()] + new_token + markdown[match.end() :]
    return None


def inject_text_consistent_error(product: dict, *, index: int = 0) -> dict:
    """构造 A4 单元：正文与 claim **一致**的数值扰动（生产形态）。

    目标选择确定性：按 agent 字典序 × claim 序扫描出可用目标（①一致扰动落进
    value_mismatch 桶且自述含值；②该值能在自己的正文句里定位），**从第 `index % n`
    个起依次尝试**，取第一个通过后置条件的——单个目标失败不等于该产物不可注入。
    全部失败才 void（把失败原因如实写下，不静默跳过）。
    """
    from finance_agent.citation import Claim
    from finance_agent.nodes.citation_repair import locate_sentence

    reports = product.get("analyst_reports") or {}
    snapshot = product.get("snapshot") or {}
    candidates: list[tuple[str, int, float, str, str]] = []
    for agent in sorted(reports):
        markdown = str(reports[agent].get("markdown") or "")
        if not markdown:
            continue
        for claim_index, claim in enumerate(reports[agent].get("claims") or []):
            if not isinstance(claim, dict):
                continue
            polluted = value_drift(claim, snapshot)
            if polluted is None:
                continue
            try:
                sentence = locate_sentence(markdown, Claim.model_validate(claim))
            except Exception:  # noqa: BLE001 - 脏 claim 不作目标
                sentence = None
            if not sentence:
                continue
            token = _token_of(sentence, float(claim["stated_value"]))
            if token is None:
                continue
            candidates.append((agent, claim_index, polluted, token, sentence))
    coverage = _coverage(product)
    if not candidates:
        return {
            "ok": False,
            "reason": "无可注入目标（正文含值的可扰动 claim）：该产物不含 A4 信息",
            "coverage": coverage,
        }
    start = index % len(candidates)
    for position in range(len(candidates)):
        agent, claim_index, polluted, token, sentence = candidates[
            (start + position) % len(candidates)
        ]
        attempt = _inject_candidate(
            product, agent, claim_index, polluted, token, sentence, coverage
        )
        if attempt["ok"]:
            return attempt
        reason = attempt["reason"]
    return {
        "ok": False,
        "reason": f"{len(candidates)} 个候选全部未通过后置条件（末次：{reason}）",
        "coverage": coverage,
    }


def _inject_candidate(
    product: dict,
    agent: str,
    claim_index: int,
    polluted: float,
    token: str,
    sentence: str,
    coverage: dict,
) -> dict:
    """对单个候选施加一致注入并校验后置条件（桶必须是 value_mismatch）。

    改写**只落在定位句内**（含该句在正文里的那一处）：按「全篇首个同值 token」改写会改到
    别的句子上去（2026-09-17 实测：600276 的 CPI 值 1.0 与「2026Q1」里的 1 同值，
    定位句与改写点分属两处），句子级读数随之失真。
    """
    out = copy.deepcopy(product)
    markdown = str(out["analyst_reports"][agent]["markdown"])
    original = float(out["analyst_reports"][agent]["claims"][claim_index]["stated_value"])
    new_token = _fmt_like(token, polluted)
    if sentence not in markdown:  # pragma: no cover - locate_sentence 的返回值必为子串
        return {"ok": False, "reason": "定位句不在正文中（切分口径不一致）"}
    new_sentence = rewrite_number(sentence, original, new_token)
    if new_sentence is None:  # pragma: no cover - 候选构造时已定位过同一 token
        return {"ok": False, "reason": f"句内数值改写失败（token={token!r}）"}
    at = markdown.find(sentence)
    claim = out["analyst_reports"][agent]["claims"][claim_index]
    claim["stated_value"] = polluted
    out["analyst_reports"][agent]["markdown"] = (
        markdown[:at] + new_sentence + markdown[at + len(sentence) :]
    )
    interpretation = rewrite_number(str(claim.get("interpretation") or ""), original, new_token)
    if interpretation is None:  # pragma: no cover - 候选构造时已检查过同一 token
        return {"ok": False, "reason": f"claim 自述改写失败（token={token!r}）"}
    claim["interpretation"] = interpretation

    if _token_of(new_sentence, polluted) is None:
        return {
            "ok": False,
            "reason": f"注入句内查不到污染值 {polluted!r}（token={new_token!r}）：读数会失真",
        }
    if _sentence_with(out, agent, claim_index) != new_sentence:
        return {"ok": False, "reason": "注入后按申报值定位不到注入句（回路无句可改）"}
    injection = {
        "mutated_agent": agent,
        "mutated_claim_index": claim_index,
        "mutated_flat_index": _flat_index(out["analyst_reports"], agent, claim_index),
        "mutated_fields": [
            f"{agent}.claims[{claim_index}].stated_value",
            f"{agent}.claims[{claim_index}].interpretation",
            f"{agent}.markdown",
        ],
        "injected_values": {"stated_value": polluted, "text_token": new_token},
        "polluted_values": [polluted],
        "original_value": original,
        # 句子级读数的锚：注入后目标句（含污染值）/ 原句（人工核对用）
        "injected_sentence": new_sentence,
        "original_sentence": sentence,
        "coverage": coverage,
        "notes": ["正文与 claim 一致改写（回路定位句的前提；claim-only 注入下 locate 必落空）"],
    }
    out["injection"] = injection
    landed = _verify(out)
    target = int(cast(Any, injection["mutated_flat_index"]))
    verdict = landed["verdicts"][target] if target < len(landed["verdicts"]) else None
    if verdict is None or verdict.status != "FAIL" or verdict.bucket != "value_mismatch":
        # 后置条件（不得静默）：单元必须落在修复回路认的 value_mismatch 桶，
        # 否则测的不是 A4（如落 internal_inconsistency 会走别的分流）
        return {
            "ok": False,
            "reason": (
                f"注入后目标判定 {getattr(verdict, 'status', None)}/{getattr(verdict, 'bucket', None)}"
                " 非 FAIL/value_mismatch：该目标不进修复回路分流"
            ),
            "coverage": coverage,
        }
    return {"ok": True, "product": out, "injection": injection, "coverage": coverage}


def _coverage(product: dict) -> dict:
    """目标面覆盖（读数范围交代）：数值 claim → 可产生 value_mismatch 的 → 正文可定位的。

    两道门都会缩样本：①只有落进 `value_mismatch` 桶的扰动才进修复回路分流
    （比率型 claim 因校验器绝对容差 0.01 吞掉值域而产不出该桶，见 value_drift 判据）；
    ②回路第一步是正文定位（按 stated_value 的 0.1% 容差找句），「正文以 亿元 表述、
    claim 申报 元」这类不同值型定位落空 → 不可修复。逐单元落盘，避免把子集成功率
    读成全域成功率。
    """
    from finance_agent.citation import Claim
    from finance_agent.nodes.citation_repair import locate_sentence

    snapshot = product.get("snapshot") or {}
    numeric = vm_capable = locatable = 0
    for agent in sorted(product.get("analyst_reports") or {}):
        markdown = str(product["analyst_reports"][agent].get("markdown") or "")
        for claim in product["analyst_reports"][agent].get("claims") or []:
            if not isinstance(claim, dict) or not pr._is_number(claim.get("stated_value")):
                continue
            numeric += 1
            if value_drift(claim, snapshot) is None:
                continue
            vm_capable += 1
            if not markdown:
                continue
            try:
                if locate_sentence(markdown, Claim.model_validate(claim)):
                    locatable += 1
            except Exception:  # noqa: BLE001, S112 - 脏 claim 不计入可定位（非静默跳过读数）
                continue
    return {
        "numeric_claims": numeric,
        "value_mismatch_claims": vm_capable,
        "locatable_claims": locatable,
    }


def _flat_index(reports: dict, agent: str, claim_index: int) -> int:
    flat = 0
    for name in sorted(reports):
        if name == agent:
            return flat + claim_index
        flat += len(reports[name].get("claims") or [])
    raise KeyError(f"{agent} 不在产物中")


def _target_from_index(product: dict, flat_index: int, *, polluted_value: float | None) -> Target:
    flat = 0
    for agent in sorted(product.get("analyst_reports") or {}):
        claims = product["analyst_reports"][agent].get("claims") or []
        if flat <= flat_index < flat + len(claims):
            claim = claims[flat_index - flat]
            stated = claim.get("stated_value")
            return Target(
                agent=agent,
                claim_index=flat_index - flat,
                flat_index=flat_index,
                stated_value=float(stated) if pr._is_number(stated) else float("nan"),
                polluted_value=polluted_value,
                sentence=_sentence_of(product, agent, flat_index - flat) or "",
            )
        flat += len(claims)
    raise IndexError(f"扁平下标越界：{flat_index}")


def _sentence_of(product: dict, agent: str, claim_index: int) -> str | None:
    """该 claim 对应的正文句（复用修复回路自己的定位实现；定位不到记 None）。"""
    from finance_agent.citation import Claim
    from finance_agent.nodes.citation_repair import locate_sentence

    report = (product.get("analyst_reports") or {}).get(agent) or {}
    markdown = str(report.get("markdown") or "")
    claims = report.get("claims") or []
    if not markdown or claim_index >= len(claims):
        return None
    try:
        return locate_sentence(markdown, Claim.model_validate(claims[claim_index]))
    except Exception:  # noqa: BLE001 - 脏 claim 不作定位
        return None


# ── 复算（零 LLM） ──


def _verify(product: dict) -> dict:
    """离线复算：全部 claims 的判定 + value_mismatch 分桶（口径 = 校验器单一实现）。"""
    from finance_agent import citation as citation_mod

    claims = [
        citation_mod.Claim.model_validate(c)
        for c in pr._flatten_claim_dicts(product.get("analyst_reports") or {})
    ]
    verdicts = list(citation_mod.verify_claims(claims, product.get("snapshot") or {}))
    per_agent: dict[str, int] = {}
    flat = 0
    for agent in sorted(product.get("analyst_reports") or {}):
        count = len(product["analyst_reports"][agent].get("claims") or [])
        per_agent[agent] = sum(
            1
            for v in verdicts[flat : flat + count]
            if v.status == "FAIL" and v.bucket == "value_mismatch"
        )
        flat += count
    return {
        "verdicts": verdicts,
        "vm_indices": [
            i for i, v in enumerate(verdicts) if v.status == "FAIL" and v.bucket == "value_mismatch"
        ],
        "vm_count": sum(per_agent.values()),
        "vm_by_agent": per_agent,
    }


def sparse_vm_units(product: dict) -> dict:
    """自然暴露复算（零 LLM）：该产物是否落在稀疏 value_mismatch（<3 处）区间。"""
    checked = _verify(product)
    return {
        "sparse_gate_fired": 0 < checked["vm_count"] < pr.SPARSE_REPAIR_LIMIT,
        "vm_count": checked["vm_count"],
        "vm_by_agent": checked["vm_by_agent"],
        "targets": [
            _target_from_index(product, i, polluted_value=None) for i in checked["vm_indices"]
        ],
    }


# ── 单元两态 ──


def run_repair_case(
    product: dict,
    *,
    mode: str = MODE_INJECTED,
    index: int = 0,
    usage_reader: Callable[[], Sequence[dict]] | None = None,
) -> dict:
    """A4 单元两态：关态（回路 no-op）vs 开态（真修复 + 重校验仲裁）。

    `usage_reader` 返回 usage 台账快照（如 `backtest_pilot_2023._usage_ledger` 的拷贝）；
    未接线时成本如实记 None（不得伪造成 0）。
    """
    snapshot = product.get("snapshot") or {}
    ticker = str(product.get("ticker") or "")
    unit: dict[str, Any] = {
        "unit_id": f"{ticker}::a4::{mode}:{index}",
        "case_id": f"{ticker}-a4-{mode}-{index}",
        "ticker": ticker,
        "leg": LEG_A4,
        "cost_class": COST_CLASS_A4,
        "mechanism_id": "A4",
        "mode": mode,
        "targets": [],
        "status": pr.STATUS_OK,
        "status_reason": "",
        "llm_calls": None,
        "usage": None,
    }
    if mode == MODE_INJECTED:
        injected = inject_text_consistent_error(product, index=index)
        if not injected["ok"]:
            unit.update(status=pr.STATUS_VOID, status_reason=str(injected["reason"]))
            return unit
        polluted_product = injected["product"]
        unit["injection"] = injected["injection"]
        unit["coverage"] = injected["coverage"]
        targets = [
            _target_from_index(
                polluted_product,
                int(injected["injection"]["mutated_flat_index"]),
                polluted_value=float(injected["injection"]["injected_values"]["stated_value"]),
            )
        ]
    elif mode == MODE_NATURAL:
        polluted_product = product
        natural = sparse_vm_units(product)
        targets = list(natural["targets"])
    else:
        raise ValueError(f"未知 mode {mode!r}（合法：{MODE_INJECTED} / {MODE_NATURAL}）")

    checked = _verify(polluted_product)
    unit["sparse_vm_count"] = checked["vm_count"]
    unit["sparse_vm_by_agent"] = checked["vm_by_agent"]
    unit["targets"] = [
        {
            "agent": t.agent,
            "claim_index": t.claim_index,
            "flat_index": t.flat_index,
            "original_value": injected.get("injection", {}).get("original_value")
            if mode == MODE_INJECTED
            else None,
            "stated_value": t.stated_value,
            "polluted_value": t.polluted_value,
            "sentence": t.sentence,
        }
        for t in targets
    ]
    if not targets:
        unit.update(status=pr.STATUS_VOID, status_reason="无 value_mismatch 目标（自然单元未命中）")
        return unit
    if checked["vm_count"] >= pr.SPARSE_REPAIR_LIMIT:
        unit.update(
            status=pr.STATUS_VOID,
            status_reason=(
                f"value_mismatch 密度 {checked['vm_count']} ≥ 门槛 {pr.SPARSE_REPAIR_LIMIT}："
                "不落在稀疏修复区间（同 citation_node 分流语义）"
            ),
        )
        return unit
    if checked["vm_count"] == 0:
        unit.update(status=pr.STATUS_VOID, status_reason="注入后无 value_mismatch FAIL：注入未生效")
        return unit

    claims = pr._flatten_claim_dicts(polluted_product["analyst_reports"])
    # 重校验仲裁按分析师范围（同 citation_node）：全库判会把别处的无关 FAIL 算到修复头上
    claim_agents = [
        agent
        for agent in sorted(polluted_product["analyst_reports"])
        for _ in polluted_product["analyst_reports"][agent].get("claims") or []
    ]
    markdown = "\n\n".join(
        str(polluted_product["analyst_reports"][a].get("markdown") or "")
        for a in sorted(polluted_product["analyst_reports"])
    )
    target_indices = [t.flat_index for t in targets]
    polluted_values = [t.polluted_value for t in targets]

    with pr.mechanism_toggle("A4", on=False) as off_toggle:
        off = pr.run_citation_chain(
            claims,
            snapshot,
            markdown=markdown,
            a4_on=True,
            repair_fn="module",
            claim_agents=claim_agents,
        )
    before_ledger = list(usage_reader()) if usage_reader else None
    with pr.mechanism_toggle("A4", on=True) as on_toggle:
        on = pr.run_citation_chain(
            claims,
            snapshot,
            markdown=markdown,
            a4_on=True,
            repair_fn="module",
            claim_agents=claim_agents,
        )
    after_ledger = list(usage_reader()) if usage_reader else None

    sentences = [
        (unit.get("injection") or {}).get("injected_sentence")
        if mode == MODE_INJECTED
        else t.sentence
        for t in targets
    ]
    unit["off"] = _readout(
        off,
        targets=target_indices,
        polluted_values=polluted_values,
        mechanism=off_toggle.payload(),
        sentences=sentences,
    )
    unit["on"] = _readout(
        on,
        targets=target_indices,
        polluted_values=polluted_values,
        mechanism=on_toggle.payload(),
        sentences=sentences,
    )
    unit["on"]["repaired_value_verdicts"] = _repaired_value_verdicts(on, snapshot)
    unit["on"]["target_value_correct"] = _target_value_correct(
        unit["on"]["repaired_value_verdicts"], targets
    )
    injected_sentence = (unit.get("injection") or {}).get("injected_sentence")
    agent_markdown = (
        _apply_rewrites(
            str(
                polluted_product["analyst_reports"][targets[0].agent].get("markdown")
                if targets
                else ""
            ),
            unit["on"]["repair_records"],
        )
        if targets
        else None
    )
    unit["on"]["target_sentence_cleared"] = sentence_cleared(
        unit["on"]["repair_records"],
        str(injected_sentence)
        if injected_sentence
        else (targets[0].sentence or None if targets else None),
        polluted_values[0] if polluted_values else None,
        agent_markdown=agent_markdown,
    )
    # 终态读数：注入腿 = 改写生效且改对（生产把改写回填正文后才重校验，仲裁失败也不回滚）；
    # 自然腿无污染值，此栏为 None（真伪待人工终裁）
    unit["error_left_in_report"] = (
        None
        if polluted_values[0] is None
        else not (bool(unit["on"]["rewrote_text"]) and bool(unit["on"]["target_value_correct"]))
    )
    unit["llm_calls"] = _ledger_calls(before_ledger, after_ledger)
    unit["usage"] = _ledger_usage(before_ledger, after_ledger)
    unit["true_fail_before"] = bool(unit["off"]["residual_fail"])
    unit["true_fail_after"] = bool(unit["on"]["residual_fail"])
    return unit


def _readout(
    chain: dict,
    *,
    targets: Sequence[int],
    polluted_values: Sequence[float | None],
    mechanism: dict,
    sentences: Sequence[str | None] = (),
) -> dict:
    """单态读数：目标 claim 的残留下场 + 正文是否还带污染值 / 真值 + 修复尝试落点。"""
    verdicts = list(chain["verdicts"])
    fail_indices = set(chain["fail_indices"])
    records = list(chain["repair_records"])
    residual = sorted(i for i in targets if i in fail_indices)

    def _state(index: int) -> dict:
        if index >= len(verdicts):
            return {"status": None, "bucket": None, "stated_value": None, "ground_truth": None}
        verdict = verdicts[index]
        return {
            "status": verdict["status"],
            "bucket": verdict.get("bucket"),
            "stated_value": (verdict.get("claim") or {}).get("stated_value"),
            "ground_truth": verdict.get("ground_truth"),
        }

    # `repair_participated` 在链里的语义是「调用过修复入口」——关态下入口被补丁换成 no-op，
    # 链照样记 True（调用发生了）。A4 的读数是「**真回路**是否参与」，故按开关记录扣掉补丁面。
    patched = bool(mechanism.get("patched"))
    markdown = str(chain.get("markdown") or "")
    carried = [
        index
        for index, value in zip(targets, polluted_values, strict=True)
        if value is not None and _text_carries(markdown, float(value))
    ]
    ground_truths: list[Any] = []
    for index in targets:
        truth = _state(index)["ground_truth"]
        if pr._is_number(truth) and _text_carries(markdown, float(truth)):
            ground_truths.append(truth)
    production_text = _apply_rewrites(markdown, records)
    return {
        "residual_fail": bool(residual),
        "residual_indices": residual,
        "target_states": {str(i): _state(i) for i in targets},
        "production_text_carries_polluted": [
            index
            for index, value in zip(targets, polluted_values, strict=True)
            if value is not None and _text_carries(production_text, float(value))
        ],
        "reverify_scope": chain.get("reverify_scope"),
        "reverify_global_all_pass": chain.get("reverify_global_all_pass"),
        "markdown_carries_polluted": carried,
        "markdown_has_ground_truth": ground_truths,
        "repair_participated": bool(chain["repair_participated"]) and not patched,
        "repair_entry_called": bool(chain["repair_participated"]),
        "mechanism_patched": patched,
        "repair_records": records,
        "rewrote_text": sum(1 for rec in records if rec.get("repaired")),
        "reverify_all_pass": bool(chain["repaired"]),
        "mechanism": mechanism,
    }


def _sentence_with(product: dict, agent: str, claim_index: int) -> str | None:
    """当前产物里该 claim 的正文句（按 claim 申报值定位）。"""
    from finance_agent.citation import Claim
    from finance_agent.nodes.citation_repair import locate_sentence

    report = (product.get("analyst_reports") or {}).get(agent) or {}
    claims = report.get("claims") or []
    markdown = str(report.get("markdown") or "")
    if not markdown or claim_index >= len(claims):
        return None
    try:
        return locate_sentence(markdown, Claim.model_validate(claims[claim_index]))
    except Exception:  # noqa: BLE001, S112 - 脏 claim 不定位（读数另行交代）
        return None


def final_target_sentence(records: Sequence[dict], sentence: str | None) -> str | None:
    """目标句在**生产口径**终态正文里的样子：命中该句的改写已回填则取改写后句。

    citation_node 先回填正文再重校验（重校验未过也不回滚），故终态正文 = 改写后的句子；
    链的保守口径（重校验未过即丢弃）另算一处，见 `markdown_carries_polluted`。
    """
    if not sentence:
        return None
    current = sentence
    for record in records:
        if str(record.get("before") or "") == current:
            current = str(record.get("after") or "") or current
    return current


def sentence_cleared(
    records: Sequence[dict],
    sentence: str | None,
    polluted: float | None,
    *,
    agent_markdown: str | None = None,
) -> bool | None:
    """污染值是否已从**目标句**里消失（None = 不可判：自然单元无污染值 / 无句子）。

    两步：①按污染 token 找到命中该句的改写记录，改写后的句子里若不再含污染值即算清除；
    ②没有命中记录时，退到**目标分析师正文**的范围判（全库扫描会把别处同值算进来——
    本批 601398/000651 的 CPI 句里合法出现同值，全库口径误报为「未清除」）。
    """
    if polluted is None or not sentence:
        return None
    token = _token_of(sentence, float(polluted))
    for record in records:
        before = str(record.get("before") or "")
        if not record.get("repaired") or (token is not None and token not in before):
            continue
        return not _text_carries(str(record.get("after") or ""), float(polluted))
    scope = agent_markdown if agent_markdown is not None else sentence
    return not _text_carries(scope, float(polluted))


def _repaired_value_verdicts(chain: dict, snapshot: dict) -> dict[str, str]:
    """逐条核对修复产出的 claim（LLM 改写的值对不对）——与被仲裁拒收与否无关。"""
    from finance_agent.citation import Claim, verify_claims

    out: dict[str, str] = {}
    for index, payload in (chain.get("repaired_claims") or {}).items():
        try:
            verdict = verify_claims([Claim.model_validate(payload)], snapshot)[0]
        except Exception:  # noqa: BLE001 - 脏产出如实记 ERROR，不得静默
            out[str(index)] = "ERROR"
            continue
        out[str(index)] = str(verdict.status)
    return out


def _target_value_correct(verdicts: dict[str, str], targets: Sequence[Target]) -> bool | None:
    """目标 claim 的改写值是否全部通过单条校验（None = 没有产出可判）。"""
    judged = [verdicts.get(str(t.flat_index)) for t in targets]
    if not any(status is not None for status in judged):
        return None
    return all(status == "PASS" for status in judged if status is not None)


def _apply_rewrites(markdown: str, records: Sequence[dict]) -> str:
    """按修复记录把改写施加到正文（生产口径：citation_node 先回填正文再重校验）。

    链在重校验未过时保守丢弃改写（见 run_citation_chain），生产不丢——两个口径都报，
    免得把「保守丢弃」读成「正文没被改过」。
    """
    from finance_agent.nodes.citation_repair import apply_repair

    current = markdown
    for record in records:
        rewritten = apply_repair(
            current, str(record.get("before") or ""), str(record.get("after") or "")
        )
        if rewritten is not None:
            current = rewritten
    return current


def _text_carries(markdown: str, value: float) -> bool:
    """正文里是否含 ≈value 的数值（复用修复回路的数值解析与容差，口径不得复制）。"""
    from finance_agent.nodes.citation_repair import _close, _numbers

    return any(_close(parsed, value) for parsed in _numbers(markdown))


def _ledger_calls(before: list[dict] | None, after: list[dict] | None) -> int | None:
    if before is None or after is None:
        return None
    return len(after) - len(before)


def _ledger_usage(before: list[dict] | None, after: list[dict] | None) -> dict | None:
    if before is None or after is None:
        return None
    fresh = after[len(before) :]
    return {
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in fresh),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in fresh),
        "entries": len(fresh),
    }


# ── 报告 ──


def a4_report(units: Sequence[dict], *, tickers: Sequence[str] = ()) -> dict:
    """A4 批报告：修复前后真 FAIL 率差（登记主指标）+ 正文清除读数 + 成本 + 自然暴露。"""
    ok = [u for u in units if u.get("status") == pr.STATUS_OK]
    void = [u for u in units if u.get("status") == pr.STATUS_VOID]
    errored = [u for u in units if u.get("status") == pr.STATUS_ERROR]
    on_readouts = [u.get("on") or {} for u in ok]
    natural = [u for u in ok if u.get("mode") == MODE_NATURAL]

    def _rate(count: int) -> float | None:
        return (count / len(ok)) if ok else None

    before = sum(1 for u in ok if u.get("true_fail_before"))
    after = sum(1 for u in ok if u.get("true_fail_after"))
    return {
        "mechanism_id": "A4",
        "primary_metric": "修复前后真 FAIL 率差",
        "units_total": len(units),
        "units_ok": len(ok),
        "units_void": len(void),
        "units_error": len(errored),
        "void_reasons": [u.get("status_reason") for u in void],
        "error_reasons": [
            {"unit_id": u.get("unit_id"), "reason": u.get("status_reason")} for u in errored
        ],
        "true_fail_before": before,
        "true_fail_after": after,
        "true_fail_rate_before": _rate(before),
        "true_fail_rate_after": _rate(after),
        "true_fail_rate_delta": _delta(_rate(before), _rate(after)),
        "repair_participated": sum(1 for r in on_readouts if r.get("repair_participated")),
        "rewrote_text": sum(1 for r in on_readouts if r.get("rewrote_text")),
        "reverify_all_pass": sum(1 for r in on_readouts if r.get("reverify_all_pass")),
        "text_cleared": sum(1 for r in on_readouts if not r.get("markdown_carries_polluted")),
        # 分解栏（登记主指标之外的诚实拆报）：
        # ①LLM 改写值是否修对（单条校验，与被仲裁拒收无关）；②错误数字是否留在终稿
        "target_value_correct": sum(
            1 for r in on_readouts if r.get("target_value_correct") is True
        ),
        "error_left_in_report": sum(1 for u in ok if u.get("error_left_in_report") is True),
        # 句子级（生产口径）：污染值是否已从**目标句**消失（None = 自然单元不可判）
        "target_sentence_cleared": sum(
            1 for r in on_readouts if r.get("target_sentence_cleared") is True
        ),
        "target_sentence_judged": sum(
            1 for r in on_readouts if r.get("target_sentence_cleared") is not None
        ),
        "production_text_cleared": sum(
            1 for r in on_readouts if not r.get("production_text_carries_polluted")
        ),
        "reverify_global_all_pass": sum(
            1 for r in on_readouts if r.get("reverify_global_all_pass")
        ),
        "ground_truth_in_text": sum(1 for r in on_readouts if r.get("markdown_has_ground_truth")),
        "fallback_to_full_retry": sum(
            1 for r in on_readouts if r.get("rewrote_text") and not r.get("reverify_all_pass")
        ),
        "natural_units": len(natural),
        "natural_targets": sum(len(u.get("targets") or []) for u in natural),
        "coverage": {
            "numeric_claims": _sum_optional(
                (u.get("coverage") or {}).get("numeric_claims") for u in units
            ),
            "value_mismatch_claims": _sum_optional(
                (u.get("coverage") or {}).get("value_mismatch_claims") for u in units
            ),
            "locatable_claims": _sum_optional(
                (u.get("coverage") or {}).get("locatable_claims") for u in units
            ),
            "note": "数值 claim → 可产生 value_mismatch 的 → 正文可定位的：后两者之比 = "
            "修复回路的有效覆盖面（比率型容差吞值、不同值型定位落空 → 该处不可修复，回落全量重试）",
        },
        "cost": {
            "llm_calls": _sum_optional(u.get("llm_calls") for u in units),
            "prompt_tokens": _sum_optional(
                (u.get("usage") or {}).get("prompt_tokens") for u in units
            ),
            "completion_tokens": _sum_optional(
                (u.get("usage") or {}).get("completion_tokens") for u in units
            ),
            "note": "修复回路真跑型成本（每单元 1 次轻量改写调用）；全量重试侧反事实未测",
        },
        "tickers": list(tickers),
        "reading_notes": [
            "登记主指标 = true_fail_rate_delta（仲裁口径：只有被修复分析师的 claims 全 PASS 才记账）——"
            "该口径把「数值改对但同分析师另有别的 FAIL」也算作未修复，读结论须并列 target_value_correct"
            "（LLM 改写值是否修对）与 error_left_in_report（错误数字是否留在终稿）两栏",
            "句子级（target_sentence_cleared）与全库扫描（production_text_cleared）都受「同值合法复现」"
            "干扰（本批 CPI 句里 1.0% 合法出现），仅作交叉核对，不作主读数",
            "关态（回路 no-op）读数只证「错误数字滞留正文」，不等于生产反事实"
            "（生产缺席 A4 时回退全量定向重试，该路径本批未测）",
            "自然单元可能命中校验器误报——对误报做「成功修复」是伤害，"
            "结论以人工终裁为前提（导出表见 natural_cases_rows）",
            "分流门槛 = 全局 value_mismatch < 3（复现 citation_node 的分流语义；"
            "本批为单点注入，逐人与全局同值）",
        ],
    }


def _delta(before: float | None, after: float | None) -> float | None:
    """率差：任一端 None（无可用单元）→ None（「没判定过」不得记成 0）。"""
    if before is None or after is None:
        return None
    return before - after


def _sum_optional(values: Iterable[Any]) -> int | None:
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None


def natural_cases_rows(units: Sequence[dict]) -> list[dict]:
    """自然腿导出（人工终裁表）：逐条给出标的 / claim 申报值 / 真值 / 出错句 / 回路动作。"""
    rows: list[dict] = []
    for unit in units:
        if unit.get("mode") != MODE_NATURAL or unit.get("status") != pr.STATUS_OK:
            continue
        on = unit.get("on") or {}
        for target in unit.get("targets") or []:
            state = (on.get("target_states") or {}).get(str(target["flat_index"])) or {}
            rows.append(
                {
                    "unit_id": unit.get("unit_id"),
                    "ticker": unit.get("ticker"),
                    "agent": target.get("agent"),
                    "claim_index": target.get("claim_index"),
                    "stated_value": target.get("stated_value"),
                    "ground_truth": state.get("ground_truth"),
                    "on_status": state.get("status"),
                    "on_bucket": state.get("bucket"),
                    "rewrote_text": bool(on.get("rewrote_text")),
                    "reverify_all_pass": bool(on.get("reverify_all_pass")),
                    "sentence": target.get("sentence"),
                }
            )
    return rows


def natural_cases_csv(rows: Sequence[dict]) -> str:
    """自然腿人工终裁 CSV（真错误?/误报?/待查 由人填，机器不代答）。"""
    from evals.causal_ablation.adjudication import _cell

    columns = [
        "unit_id",
        "ticker",
        "agent",
        "claim_index",
        "stated_value",
        "ground_truth",
        "on_status",
        "on_bucket",
        "rewrote_text",
        "reverify_all_pass",
        "sentence",
        "human_verdict(真错误?/误报?/待查)",
    ]
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(_cell(row.get(c)) for c in columns[:-1]) + ",")
    return "\n".join(lines) + "\n"
