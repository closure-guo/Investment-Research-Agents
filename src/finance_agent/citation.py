"""确定性引用校验器 — 纯 Python 实现，不调 LLM，可进 CI。

参考 ADR-0011 和 FinGround (arXiv:2604.23588) 六类分类法。
复用 metrics/ 纯函数对 Agent 产出的 Claim 进行重算比对。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, TypeGuard

from pydantic import BaseModel

from finance_agent.metric_vocab import (
    canonical_metric,
    field_ref_metric_segments,
    field_ref_period_segment,
    normalize_period,
    period_matches,
    render_date,
)
from finance_agent.metrics.cashflow import calc_cashflow
from finance_agent.metrics.dupont import calc_dupont
from finance_agent.metrics.efficiency import calc_efficiency
from finance_agent.metrics.profitability import calc_profitability
from finance_agent.metrics.risk import calc_risk
from finance_agent.metrics.solvency import calc_solvency
from finance_agent.metrics.technical import calc_derived_series, calc_technical

if TYPE_CHECKING:
    import pandas as pd

# 容差常量（FinGround 标准）：数值/计算型裁决与 evals 离线复判（rejudge.py）
# 共用此唯一来源——改这里即改契约，benchmark 标签语义随 CI 门禁同步生效。
ABS_TOL = 0.01  # 绝对容差：|delta| < 0.01
REL_TOL = 0.005  # 相对容差：|delta| / |gt| < 0.5%


class Claim(BaseModel):
    """原子声明 — Agent 报告中的单个可验证数据点。"""

    claim_type: Literal[
        "numerical",
        "temporal",
        "entity",
        "comparative",
        "regulatory",
        "computational",
    ]
    source_type: Literal["data", "event", "llm_inference", "mixed"]
    field_ref: str  # state 字段路径，如 "solvency_metrics.资产负债率.2024"
    stated_value: float | str
    interpretation: str
    field_ref_b: str | None = None  # 比较型 claim 的第二个值路径
    # refine-citation-coverage-v3 D3：comparative 基期值双端申报。基期数值，
    # 与 field_ref_b 的真值按校验容差比对；缺申报（field_ref_b 设而
    # stated_value_b 缺）判 FAIL——比较基期不得裸奔。
    stated_value_b: float | str | None = None
    # harden-citation-semantic-coverage：术语/期次申报。None = 未申报（旧格式），
    # 校验器跳过对应检查并计覆盖缺口（显式降级，不静默 PASS）。
    metric_name: str | None = None  # 指标枚举（中文规范键或别名，见 metric_vocab）
    period: str | None = None  # 期次（2024 / 2025Q2 / 2026-08-28 / 2026-07）
    # ehr-style-claim-direction：数值型 claim 显式申报方向语义。negative =
    # 正文以正向数值表述负向事实（「下滑 10.05%」↔ gt=-10.05）；positive =
    # 正文直接写 signed 值（-10.05% 写 stated=-10.05）；flat = 存量水平类无数值
    # 方向语义（ROE/资产负债率等）。None = 旧格式未申报 → 校验器跳过方向检查并
    # 计覆盖缺口（与 metric_name/period 未申报的既有降级先例一致）。
    direction: Literal["positive", "negative", "flat"] | None = None
    # rework-citation-gate-attribution 阶段 4：由覆盖普查唯一匹配 state 条目自动合成的 claim
    auto: bool = False


class CitationResult(BaseModel):
    """单条 Claim 的校验结果。"""

    status: Literal["PASS", "FAIL", "UNVERIFIABLE"]
    claim: Claim
    ground_truth: float | str | None = None
    delta: float | None = None
    coverage_gap: bool = False  # 覆盖缺口（未注册根键 / 未申报术语期次）
    # 阶段 1 归一标记："亿"/"万" = interpretation 单位词缩放；"inferred" = 无单位词
    # 1e4/1e8 比值兜底；"echo" = 文本回声命中；"quarter"/"date" 由解析层隐式处理
    unit_normalized: str | None = None
    # FAIL 分桶（harden-citation-semantic-coverage）：value_mismatch=值级（gt 存在且
    # 超容差，定向重试）；path_unresolvable=路径/事件不可解析；semantic_*=术语/期次
    # 张冠李戴；internal_inconsistency=stated 与 interpretation 两张皮/方向矛盾；
    # direction_mismatch=已申报方向与真值符号冲突（ehr-style-claim-direction）。
    # comparative_delta_unregistered=比较型非三枚举差值申报的 UNVERIFIABLE 显式降级
    # （ground-comparative-delta-claims，非 FAIL 桶，独立计覆盖缺口）。
    bucket: (
        Literal[
            "value_mismatch",
            "path_unresolvable",
            "semantic_term_mismatch",
            "semantic_period_mismatch",
            "internal_inconsistency",
            "direction_mismatch",
            # 比较型差值申报（非三枚举 stated_value）：显式降级，独立桶计缺口
            "comparative_delta_unregistered",
            # 值槽类型错填（eval-driven-contract-fixes 任务 2）：变化量入水平值槽，
            # claim 契约病——不算分析师幻觉、不触发修复（600276 A4 自然腿终裁）
            "claim_contract_error",
        ]
        | None
    ) = None


class CitationReport(BaseModel):
    """批量校验汇总报告。"""

    results: list[CitationResult]
    total: int = 0
    passed: int = 0
    failed: int = 0
    unverifiable: int = 0
    coverage_gaps: int = 0
    all_passed: bool = False

    @classmethod
    def from_results(cls, results: list[CitationResult]) -> CitationReport:
        """从校验结果列表构建汇总报告。"""
        passed = sum(1 for r in results if r.status == "PASS")
        failed = sum(1 for r in results if r.status == "FAIL")
        unverifiable = sum(1 for r in results if r.status == "UNVERIFIABLE")
        return cls(
            results=results,
            total=len(results),
            passed=passed,
            failed=failed,
            unverifiable=unverifiable,
            coverage_gaps=sum(1 for r in results if r.coverage_gap),
            all_passed=failed == 0,
        )


def _expand_brackets(field_ref: str) -> list[str]:
    """展开 `[N]` 括号索引：`quarterly_trend.yoy[1]` → ["quarterly_trend", "yoy", "1"]。

    fix-citation-contract-diseases 修 B：LLM 按数组语义书写下标，resolver
    统一展开为路径段，负索引（-N）原样保留（list 负下标 = 倒数第 N 个）。
    """
    import re

    parts: list[str] = []
    pattern = re.compile(r"^(.*?)\[(-?\d+)\]$")
    for seg in field_ref.split("."):
        m = pattern.match(seg)
        if m:
            base, idx = m.group(1), m.group(2)
            if base:
                parts.append(base)
            parts.append(idx)
        else:
            parts.append(seg)
    return parts


def _is_dataframe(obj: object) -> TypeGuard[pd.DataFrame]:
    """鸭子判定 DataFrame（columns + iloc）；TypeGuard 供 mypy 收窄，运行时不导入 pandas。"""
    return hasattr(obj, "columns") and hasattr(obj, "iloc")


def _resolve_column_alias(col_name: str, columns: Any) -> str | None:
    """列名别名回退：词表 canonical 归一后匹配真实列（如「加权每股收益」→
    「加权每股收益(元)」）。分析师手写 field_ref 常省略单位后缀，路径解析
    与术语检查必须同一套归一口径。"""
    canonical = canonical_metric(col_name)
    if canonical is None:
        return None
    for col in columns:
        if canonical_metric(str(col)) == canonical:
            return str(col)
    return None


# 根键别名归一（前缀契约一致性）：analysts context 提示 LLM「field_ref 前缀 derived.」，
# 而 state 根键是 `derived_series`——图通道修复后该 context 节首次真正渲染，LLM 照提示
# 写 `derived.chg_5d` 时正确数值会被判 path_unresolvable 误 FAIL（2026-09-14 实测）。
# 归一后两种前缀都合法；别名只做根段替换，不改其余路径语义。
_ROOT_ALIASES: dict[str, str] = {"derived": "derived_series"}


def _apply_root_alias(parts: list[str]) -> list[str]:
    if parts and parts[0] in _ROOT_ALIASES:
        return [_ROOT_ALIASES[parts[0]], *parts[1:]]
    return parts


def _disambiguate_column(col_name: str, columns: Any) -> str | None:
    """同义列消歧（eval-driven-contract-fixes 任务 1）。

    canonical 等价的多列并存时取**最长列名**（官方科目全名，如「归属于母公司的净利润」
    对「归母净利润」）——杜绝短名列精确命中后静默取错值（601318 A4 自然腿终裁：两列
    数值 1347.78 亿 vs 235.23 亿，校验器取短名列产假 FAIL）。未注册 canonical 的列名
    维持精确匹配语义；无任何匹配返回 None（上层按不可验证进 blocked 桶，不错值）。"""
    cols = [str(c) for c in columns]
    if col_name in cols:
        canonical = canonical_metric(col_name)
        if canonical is None:
            return col_name
        matches = [c for c in cols if canonical_metric(c) == canonical]
        return max(matches, key=len) if matches else col_name
    return _resolve_column_alias(col_name, columns)


def _resolve_field_ref(
    field_ref: str, state: dict, claim_period: str | None = None
) -> object | None:
    """按 "." 分割路径（含 `[N]` 括号展开），逐层遍历 state dict / list / DataFrame。

    - 负索引（修 A）：list[-N] = 倒数第 N 个（-1 = 最新一期），与序列长度及
      context 裁剪窗口解耦；
    - DataFrame（修 B）：期望「行键.列名」两段——行键按任意列单元格值匹配
      （如 报告日 20251231），列名须为真实列，如
      income_statement.20251231.营业总收入；
    - fetch 守卫结构兼容：dict 形如 {"records": [...], "as_of_date", "freshness"}
      时，若当前 part 不是该 dict 的键，先自动下钻 records 再解析，保持
      field_ref 语义（macro_indicators.cpi.0.<列>）不变。
    """
    state = {**state, "_claim_period": claim_period} if claim_period else state
    parts = _normalize_quarter_segments(_apply_root_alias(_expand_brackets(field_ref)), state)
    current: object = state
    i = 0
    while i < len(parts):
        part = parts[i]
        if isinstance(current, dict) and "records" in current and part not in current:
            current = current["records"]
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif _is_dataframe(current):
            if i + 1 < len(parts):
                col_name = parts[i + 1]
                col = _disambiguate_column(col_name, current.columns)
                if col is None:
                    return None
                col_name = col
                mask = _dataframe_row_mask(current, part)
                if mask is None:
                    return None
                current = current[mask].iloc[0][col_name]
                i += 2
                continue
            # 路径止于列名（分析师省略行键）：取最新一行（与负索引「最新一期」
            # 约定一致），列名同样走消歧（同义列并存时官方全名优先）
            col = _disambiguate_column(part, current.columns)
            if col is None:
                return None
            value: object = current.iloc[-1][col]
            return value
        else:
            return None
        i += 1
    return current


_QUARTER_LABEL_RE = re.compile(r"^(\d{4})[Qq]([1-4])$")


def _normalize_quarter_segments(parts: list[str], state: dict) -> list[str]:
    """阶段 1（incident 026）：`quarterly_trend.yoy.2026Q2` 的季度标签 → quarters 位置索引。

    LLM 照抄 context 里的季度标签，校验器要位置索引——r2 该域 22/31 path_unresolvable。
    标签不在 quarters 列表中时原样保留（后续解析自然失败）。
    """
    if not parts or parts[0] != "quarterly_trend":
        return parts
    trend = state.get("quarterly_trend")
    quarters = trend.get("quarters") if isinstance(trend, dict) else None
    if not isinstance(quarters, list):
        return parts
    labels = [str(q).upper() for q in quarters]
    out: list[str] = []
    for seg in parts:
        m = _QUARTER_LABEL_RE.match(seg)
        if m:
            label = f"{m.group(1)}Q{m.group(2)}"
            if label in labels:
                out.append(str(labels.index(label)))
                continue
        out.append(seg)
    # r4 残余：路径止于序列名（如 quarterly_trend.yoy）而 claim.period 携带季度标签
    # → 追加位置段，等价于 claim 指向该季度的序列值
    if isinstance(trend, dict) and len(out) >= 2 and out[-1] not in labels and out[-1] in trend:
        period = str(state.get("_claim_period") or "").upper()
        if _QUARTER_LABEL_RE.match(period) and period in labels:
            out.append(str(labels.index(period)))
    return out


def _dataframe_row_mask(frame: pd.DataFrame, key: str) -> object | None:
    """行键按任意列单元格值匹配；阶段 1 归一：`2025-12-31` ↔ `20251231` 双向
    （context 经 render_date 显示带连字符，行键存无连字符——r2 financial_indicators 3/3 挂）。"""
    key_norm = key.replace("-", "")
    for col in frame.columns:
        ser = frame[col].astype(str)
        mask = ser == key
        if mask.any():
            return mask
        mask = ser.str.replace("-", "", regex=False) == key_norm
        if mask.any():
            return mask
    return None


# ── 计算型 claim 重算注册表 ──
# field_ref 根键 → 从 state 原始数据重算的函数。
# 覆盖 metrics/ 全部纯函数指标族；未注册根键 → UNVERIFIABLE + coverage_gap 计数。
_COMPUTATIONAL_RECALC: dict[str, Callable[[dict], object]] = {
    "dupont_tree": lambda s: calc_dupont(s["balance_sheet"], s["income_statement"]),
    "solvency_metrics": lambda s: calc_solvency(
        s["balance_sheet"], s["income_statement"], s.get("financial_indicators")
    ),
    "profitability_metrics": lambda s: calc_profitability(
        s["balance_sheet"], s["income_statement"], s.get("financial_indicators")
    ),
    "efficiency_metrics": lambda s: calc_efficiency(
        s["balance_sheet"], s["income_statement"], s.get("financial_indicators")
    ),
    "cashflow_metrics": lambda s: calc_cashflow(
        s["balance_sheet"], s["income_statement"], s["cash_flow_statement"]
    ),
    "technical_indicators": lambda s: calc_technical(s["kline"]),
    "risk_metrics": lambda s: calc_risk(s["kline"], s.get("benchmark_kline")),
    # 阶段 2（incident 026）：快照派生字段用同一份 compute 代码重算（r2 约 23 条恒 UNVERIFIABLE）
    "garp_result": lambda s: _recompute_snapshot(s, "garp_result"),
    "anomalies": lambda s: _recompute_snapshot(s, "anomalies"),
    # 派生键覆盖收口（close-citation-coverage-gaps）：compute_metrics 其余 8 个产出键
    # 此前未注册 → 按计算型引用即 UNVERIFIABLE（r9 未注册 ~2.9 条/轮 的主要来源）。
    # 统一走「同一份 compute 代码重算」，覆盖门禁见
    # tests/test_citation.py::TestComputationalRegistryCoverage::test_recompute_registry_covers_all_compute_outputs
    "derived_series": lambda s: calc_derived_series(s["kline"]),
    "growth_rates": lambda s: _recompute_snapshot(s, "growth_rates"),
    "health_score": lambda s: _recompute_snapshot(s, "health_score"),
    "price_levels": lambda s: _recompute_snapshot(s, "price_levels"),
    "relative_valuation": lambda s: _recompute_snapshot(s, "relative_valuation"),
    "traffic_lights": lambda s: _recompute_snapshot(s, "traffic_lights"),
    "peer_comparison": lambda s: _recompute_snapshot(s, "peer_comparison"),
    "quarterly_trend": lambda s: _recompute_snapshot(s, "quarterly_trend"),
}

# 派生键豁免表（键 → 理由）。空 = 当前无豁免；加入豁免必须写清为什么不能重算，
# 且仅用于「确定性不可重算」的键（如纯外部时序数据），不得用于「暂时懒得注册」。
_UNREGISTERED_EXEMPT: dict[str, str] = {}


def _recompute_snapshot(state: dict, key: str) -> object:
    from typing import Any, cast

    from finance_agent.nodes.compute import compute_metrics

    value = compute_metrics(cast(Any, state)).get(key)
    if value is None:
        raise KeyError(key)
    return value


def _recomputable_root(claim: Claim) -> bool:
    """claim 的 field_ref 根是否在重算注册表内（路由判据；两级判定与 `_verify_computational` 同源）。"""
    return claim.field_ref.split(".")[0] in _COMPUTATIONAL_RECALC


def _verify_computational(claim: Claim, state: dict) -> CitationResult:
    """计算型 claim：从原始数据重算指标，用相对容差 0.5% 比对。"""
    parts = _apply_root_alias(claim.field_ref.split("."))
    root = parts[0]
    sub_path = parts[1:]

    recalc_fn = _COMPUTATIONAL_RECALC.get(root)
    if recalc_fn is None:
        return CitationResult(status="UNVERIFIABLE", claim=claim, coverage_gap=True)

    try:
        recalculated = recalc_fn(state)
    except (KeyError, TypeError):
        # 重算输入不可得（state 缺原始报表等）→ 覆盖缺口（调用方据此决定是否降级直读）
        return CitationResult(status="UNVERIFIABLE", claim=claim, coverage_gap=True)

    # 从重算结果中按 sub_path 取值（dict 键 + list 序号，与 _resolve_field_ref 语义一致）
    current: object = recalculated
    for part in sub_path:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return CitationResult(status="UNVERIFIABLE", claim=claim)
        else:
            return CitationResult(status="UNVERIFIABLE", claim=claim)
        if current is None:
            return CitationResult(
                status="FAIL",
                claim=claim,
                ground_truth=None,
                delta=None,
                bucket="path_unresolvable",
            )

    # 阶段 2：字符串枚举（GARP failures）按归一后相等比对；列表（anomalies）按回声命中
    if isinstance(current, bool):
        current = float(current)
    if isinstance(current, str) and not _looks_numeric(current):
        same = _norm_text(str(claim.stated_value)) == _norm_text(current) or (
            _norm_text(current) and _norm_text(current) in _norm_text(claim.interpretation)
        )
        return CitationResult(
            status="PASS" if same else "FAIL",
            claim=claim,
            ground_truth=current,
            bucket=None if same else "value_mismatch",
            unit_normalized="echo" if same else None,
        )
    if isinstance(current, list | tuple):
        items = [str(x) for x in current]
        hay = _norm_text(claim.interpretation) + _norm_text(str(claim.stated_value))
        hit = next((it for it in items if _norm_text(it) and _norm_text(it) in hay), None)
        if hit is not None:
            return CitationResult(
                status="PASS", claim=claim, ground_truth=hit, unit_normalized="echo"
            )
        return CitationResult(status="UNVERIFIABLE", claim=claim)
    if not isinstance(current, int | float | str):
        return CitationResult(
            status="FAIL",
            claim=claim,
            ground_truth=None,
            delta=None,
            bucket="path_unresolvable",
        )
    try:
        ground_truth = float(current)
        stated = float(claim.stated_value)
    except (TypeError, ValueError):
        return CitationResult(
            status="FAIL",
            claim=claim,
            ground_truth=None,
            delta=None,
            bucket="path_unresolvable",
        )
    # 数值分支统一走共用比对（含方向对齐/候选归一/相对容差——口径不得复制）
    return _compare_numeric_claim(claim, stated, ground_truth)


_PUNCT_CHARS = (
    "，。；、：:;,()（）[]【】—_·.%％" + '"' + chr(0x2018) + chr(0x2019) + chr(0x201C) + chr(0x201D)
)
_TEXT_STRIP_RE = re.compile("[" + re.escape(_PUNCT_CHARS) + r"\s" + "]")


def _norm_text(text: object) -> str:
    """文本回声归一：去空白/标点/百分号，小写。"""
    return _TEXT_STRIP_RE.sub("", str(text or "")).lower()


def _looks_numeric(text: str) -> bool:
    try:
        float(str(text).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


_SIGNED_ROOTS = frozenset({"growth_rates", "quarterly_trend", "derived_series"})
_SIGNED_NAME_KEYWORDS = ("同比", "环比", "增速", "变动", "变化", "涨跌", "差额")


def _is_signed_claim(claim: Claim) -> bool:
    """阶段 1（incident 026）：direction 只对「有符号量」参与符号比对——
    增长率/同比环比/变动幅度为有符号量；PMI/比率/价格等恒正水平量的
    negative 申报是「低于阈值」的字面误读（r2 5/5 direction_mismatch 皆此因），
    记覆盖缺口提示而非 FAIL。"""
    root = claim.field_ref.split(".")[0]
    if root in _SIGNED_ROOTS:
        return True
    name = claim.metric_name or ""
    return any(k in name for k in _SIGNED_NAME_KEYWORDS)


def _align_signed_effective(
    claim: Claim, stated: float, ground_truth: float
) -> tuple[float, CitationResult | None, bool]:
    """direction 申报与真值符号的对齐（ehr-style-claim-direction 单一实现）。

    返回 (eff, direction_fail, direction_misapplied)：
    - eff：sign(stated)×direction 对齐后的有效值（未申报/flat/非有符号量 → 原值）
    - direction_fail：有符号量上符号冲突（eff 非零且与真值异号）→ 预构造
      direction_mismatch FAIL，调用方直接返回
    - direction_misapplied：非有符号量上申报 positive/negative 的字面误读 →
      覆盖缺口标记（调用方决定是否计入）

    数值/计算型两条校验路径共用，避免同一语义两份实现。
    """
    declared_sign: int | None = None
    if claim.direction == "negative":
        declared_sign = -1
    elif claim.direction == "positive":
        declared_sign = 1
    # 阶段 1：非有符号量上的 negative/positive 申报是「低于/高于阈值」的字面误读
    # ——不比符号，记覆盖缺口提示（歧义降级而非 FAIL）
    signed = _is_signed_claim(claim)
    direction_misapplied = declared_sign is not None and declared_sign != 0 and not signed
    # 阶段 1 r4 残余：stated 已带符号且与 direction 同向（-10.40 + negative）——
    # 数值本身即事实，不翻号（否则双重否定判 direction_mismatch 误报）
    double_signed = (
        declared_sign is not None
        and signed
        and ((declared_sign == -1 and stated < 0) or (declared_sign == 1 and stated > 0))
    )
    eff = (
        stated * declared_sign
        if (declared_sign is not None and signed and not double_signed)
        else stated
    )
    eff_sign = 1 if eff > 0 else (-1 if eff < 0 else 0)
    gt_sign = 1 if ground_truth > 0 else (-1 if ground_truth < 0 else 0)
    if (
        signed
        and declared_sign is not None
        and eff_sign != 0
        and gt_sign != 0
        and eff_sign != gt_sign
    ):
        return (
            eff,
            CitationResult(
                status="FAIL",
                claim=claim,
                ground_truth=ground_truth,
                delta=abs(ground_truth - eff),
                bucket="direction_mismatch",
            ),
            direction_misapplied,
        )
    return eff, None, direction_misapplied


_MAG_SCALES: tuple[tuple[str, float], ...] = (("万", 1e4), ("亿", 1e8))
_UNIT_TOKEN_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(亿|万)")


def _unit_from_interpretation(claim: Claim) -> str | None:
    """从 interpretation 中找与 stated_value 面值相同的 token 的紧邻单位词（亿/万）。"""
    try:
        face = abs(float(claim.stated_value))
    except (TypeError, ValueError):
        return None
    for m in _UNIT_TOKEN_RE.finditer(claim.interpretation or ""):
        token_val = abs(float(m.group(1).replace(",", "")))
        if abs(token_val - face) < 1e-9:
            return m.group(2)
    return None


# 值槽类型错填（eval-driven-contract-fixes 任务 2）：interpretation 声明数值为
# 环比/同比变化量（…个百分点）而 field_ref 真值为水平值 → claim 契约错，非分析师幻觉
_DELTA_SLOT_RE = re.compile(r"(环比|同比)[^。；]{0,16}个百分点")


def _is_delta_in_level_slot(interpretation: str | None, gt_float: float, stated: float) -> bool:
    """双信号判据：①变化量措辞（环比/同比…个百分点）②与真值量级差 >10 倍。

    单一信号不判——环比措辞配变化量字段（量级一致）是合法 claim；量级差但无
    变化量措辞走既有 percent/亿/万 归一候选。依据：600276 A4 自然腿终裁
    （PMI 环比 0.6 填进水平值槽 49.8，正文算术正确，修复回路定位落空是正确行为）。"""
    if not _DELTA_SLOT_RE.search(str(interpretation or "")):
        return False
    s, g = abs(float(stated)), abs(gt_float)
    return s > 0 and g > 0 and (g / s > 10 or s / g > 10)


def _verify_numerical(claim: Claim, state: dict) -> CitationResult:
    """数值型 claim：直接读 state 字段，绝对容差 0.01 比对。

    field_ref 解析结果非数值（dict/list 等，LLM 偶发指到容器节点）时按
    FAIL 处理而非抛 TypeError 炸管线（baseline-v2 r3 回归）。
    """
    ground_truth = _resolve_field_ref(claim.field_ref, state, claim.period)
    if not isinstance(ground_truth, int | float | str):
        return CitationResult(
            status="FAIL",
            claim=claim,
            ground_truth=None,
            delta=None,
            bucket="path_unresolvable",
        )
    try:
        gt_float = float(ground_truth)
        sv_float = float(claim.stated_value)
    except (TypeError, ValueError):
        return CitationResult(
            status="FAIL",
            claim=claim,
            ground_truth=None,
            delta=None,
            bucket="path_unresolvable",
        )
    if _is_delta_in_level_slot(claim.interpretation, gt_float, sv_float):
        return CitationResult(
            status="FAIL",
            claim=claim,
            ground_truth=gt_float,
            delta=None,
            bucket="claim_contract_error",
        )
    # ehr-style-claim-direction：已申报方向 → 先按 sign(stated)×direction 与
    # 真值符号对齐（「下滑 10.05%」↔ gt=-10.05 用 eff=-10.05 比对），符号冲突
    # 直接 FAIL + direction_mismatch 桶；符号一致后走既有容差（值级偏差仍归
    # value_mismatch）。flat/None 无数值方向语义 → 原值比对。
    return _compare_numeric_claim(claim, sv_float, gt_float)


def _compare_numeric_claim(claim: Claim, stated: float, gt_float: float) -> CitationResult:
    """数值型/计算型的**共用**比对：方向对齐 → 候选归一（percent/万/亿）→ 相对容差。

    `harden-recompute-routing`：计算型此前自带一套简化比对（只有 ×100 归一），
    与数值型不一致 → 同一 claim 换个标签就换一套容差语义。两条路径统一到此实现
    （口径不得复制），数值型与计算型只在**真值来源**上不同（直读 vs 重算）。
    """
    eff, direction_fail, direction_misapplied = _align_signed_effective(claim, stated, gt_float)
    if direction_fail is not None:
        return direction_fail
    # 候选统一按「各自参照系」判相对容差（阶段 1，incident 026）：
    #   raw      → 参照 gt
    #   percent  → 参照 gt 或 gt/100（各算各的；修注入演练暴露的缺陷——超大真值的
    #              gt 级容差曾放行 100 倍缩水值）
    #   亿/万/inferred → 参照 gt 或 gt/scale
    # 取相对误差最小者裁决；原值已是最佳时不打归一标记。
    candidates: list[tuple[str | None, float, float]] = [(None, abs(gt_float - eff), gt_float)]
    if gt_float != 0 and eff != 0:
        ratio = gt_float / eff
        if 50 < ratio < 200 or 0.005 < ratio < 0.02:
            candidates.append(("percent", abs(gt_float - eff / 100.0), gt_float))
            candidates.append(("percent", abs(gt_float / 100.0 - eff), abs(gt_float / 100.0)))
        unit = _unit_from_interpretation(claim)
        unit_cands = (
            [(unit, _UNIT_SCALE[unit])] if unit else [("inferred", sc) for _, sc in _MAG_SCALES]
        )
        for name, sc in unit_cands:
            candidates.append((name, abs(gt_float - eff * sc), gt_float))
            candidates.append((name, abs(gt_float / sc - eff), abs(gt_float / sc)))

    def _rel(entry: tuple[str | None, float, float]) -> float:
        _, d, ref = entry
        return d / abs(ref) if ref else d

    def _pass(d: float, ref: float) -> bool:
        return d < max(ABS_TOL, abs(ref) * REL_TOL)

    unit_note, delta, ref = min(candidates, key=_rel)
    if not _pass(delta, ref):
        unit_note, delta, ref = None, candidates[0][1], candidates[0][2]
    status: Literal["PASS", "FAIL"] = "PASS" if _pass(delta, ref) else "FAIL"
    return CitationResult(
        status=status,
        claim=claim,
        ground_truth=gt_float,
        delta=delta,
        bucket=None if status == "PASS" else "value_mismatch",
        coverage_gap=claim.direction is None or direction_misapplied,
        unit_normalized=unit_note,
    )


def _as_number(value: object) -> float | None:
    """数值化申报值（供差值型 comparative 判别；bool 不算数值）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _verify_comparative_difference(
    claim: Claim, a: float, b: float, stated_diff: float
) -> CitationResult:
    """差值型 comparative（close-citation-coverage-gaps ①）：

    申报对象是差值本身（「A 较 B 低约 X」）。双端真值重算差值 `a - b`，与申报值按
    容差比对（参考系取两操作数绝对值较大者）；`direction` 已申报则校验符号方向
    （negative = 正文以正向数值表述负向差值），未申报仅比量级并计覆盖缺口（显式
    降级，不静默 PASS）。基期值 `stated_value_b` 对差值型为可选——申报则按既有容差
    校验，缺省不判「裸奔」（申报对象是差值本身）。
    """
    # 回声短路（兼容既有形态）：LLM 偶发以「某一端的值」填 stated_value（如 49.8），
    # 不是差值申报——保持 UNVERIFIABLE（未知语义不武断判错，回归：
    # test_comparative_numeric_direction_field_not_false_fail）。仅当申报既不等于
    # a 也不等于 b 时按差值裁决。
    for operand in (a, b):
        if abs(abs(stated_diff) - abs(operand)) < max(ABS_TOL, abs(operand) * REL_TOL):
            return CitationResult(status="UNVERIFIABLE", claim=claim)

    diff = a - b
    tol = max(ABS_TOL, max(abs(a), abs(b)) * REL_TOL)
    magnitude_delta = abs(abs(stated_diff) - abs(diff))
    magnitude_ok = magnitude_delta < tol

    if claim.direction == "negative":
        direction_ok = diff < 0
    elif claim.direction == "positive":
        direction_ok = diff > 0
    elif claim.direction == "flat":
        direction_ok = abs(diff) < ABS_TOL
    else:
        direction_ok = True  # 未申报：跳过方向检查（coverage_gap 标记）

    if claim.field_ref_b is not None and claim.stated_value_b is not None:
        base_stated = _as_number(claim.stated_value_b)
        if base_stated is None or abs(b - base_stated) >= max(ABS_TOL, abs(b) * REL_TOL):
            return CitationResult(
                status="FAIL",
                claim=claim,
                ground_truth=b,
                delta=None if base_stated is None else abs(b - base_stated),
                bucket="value_mismatch",
            )

    passed = magnitude_ok and direction_ok
    return CitationResult(
        status="PASS" if passed else "FAIL",
        claim=claim,
        ground_truth=diff,
        delta=magnitude_delta,
        bucket=None if passed else "value_mismatch",
        coverage_gap=claim.direction is None,
    )


def _verify_comparative(claim: Claim, state: dict) -> CitationResult:
    """比较型 claim：验证两侧数值 + 比较方向/差值 + 基期值申报（v3 D3）。

    两类申报：
    - 方向型：stated_value 为 greater_than/less_than/equal_to → 校验比较方向；
      field_ref_b/stated_value_b 为基期双端（D3）：基期值按与当期相同容差语义
      比对 field_ref_b 真值；field_ref_b 设而 stated_value_b 缺 → FAIL（裸奔拦截）。
    - 差值型：stated_value 为数值 → 双端重算差值比对（见 _verify_comparative_difference）。
    """
    val_a = _resolve_field_ref(claim.field_ref, state)
    val_b = _resolve_field_ref(claim.field_ref_b, state) if claim.field_ref_b else None
    if not isinstance(val_a, int | float | str) or not isinstance(val_b, int | float | str):
        return CitationResult(
            status="FAIL", claim=claim, ground_truth=None, bucket="path_unresolvable"
        )

    try:
        a = float(val_a)
        b = float(val_b)
    except (TypeError, ValueError):
        return CitationResult(
            status="FAIL", claim=claim, ground_truth=None, bucket="path_unresolvable"
        )
    delta = abs(a - b)
    stated_diff = _as_number(claim.stated_value)
    if stated_diff is not None:
        return _verify_comparative_difference(claim, a, b, stated_diff)
    direction = str(claim.stated_value)

    if direction == "greater_than":
        passed = a > b
    elif direction == "less_than":
        passed = a < b
    elif direction == "equal_to":
        passed = delta < 0.01
    else:
        # 差值数字填入 stated_value（「MA5 较 MA20 低约 2.3%」的 2.3）：非三枚举
        # → 显式降级。不重算差值、不判 PASS/FAIL（ground-comparative-delta-claims
        # 明确推迟的「路线 2」）；独立桶 + 覆盖缺口使问题规模可见。
        return CitationResult(
            status="UNVERIFIABLE",
            claim=claim,
            bucket="comparative_delta_unregistered",
            coverage_gap=True,
        )

    # D3：基期值申报与校验（comparative 双端建档）。基期真值 b 取自 field_ref_b；
    # stated_value_b 缺失（裸奔）或与真值超容差 → FAIL。
    if claim.field_ref_b is not None:
        if claim.stated_value_b is None:
            return CitationResult(
                status="FAIL",
                claim=claim,
                ground_truth=b,
                delta=None,
                bucket="path_unresolvable",
            )
        try:
            base_stated = float(claim.stated_value_b)
        except (TypeError, ValueError):
            return CitationResult(
                status="FAIL",
                claim=claim,
                ground_truth=b,
                delta=None,
                bucket="path_unresolvable",
            )
        base_delta = abs(b - base_stated)
        if base_delta >= max(ABS_TOL, abs(b) * REL_TOL):
            return CitationResult(
                status="FAIL",
                claim=claim,
                ground_truth=b,
                delta=base_delta,
                bucket="value_mismatch",
            )

    status: Literal["PASS", "FAIL"] = "PASS" if passed else "FAIL"
    return CitationResult(
        status=status,
        claim=claim,
        ground_truth=a,
        delta=delta,
        bucket=None if passed else "value_mismatch",
    )


def collect_text_sources(state: dict) -> list[str]:
    """回声源集合（归一前原文）：news/key_events/公告/研报/解禁/大宗——文本 claim 与
    辩论 event 锚点共用同一集合（单一实现，勿复制）。"""
    sources: list[str] = []
    news = state.get("news_list") or []
    if isinstance(news, list):
        sources += [str(n.get("title") or "") for n in news if isinstance(n, dict)]
    events = state.get("key_events") or []
    if isinstance(events, list):
        for e in events:
            sources.append(str(e.get("title") or "") if isinstance(e, dict) else str(e))
    # add-analyst-data-coverage：公告/研报标题与解禁/大宗日期进回声源集合
    for key, fields in (
        ("announcements", ("title",)),
        ("research_reports", ("title",)),
        ("share_unlock", ("date",)),
        ("block_trades", ("date", "buyer", "seller")),
    ):
        items = state.get(key) or []
        if isinstance(items, list):
            sources += [
                str(i.get(f) or "")
                for i in items
                if isinstance(i, dict)
                for f in fields
                if i.get(f)
            ]
    return sources


def _verify_textual(claim: Claim, state: dict) -> CitationResult:
    """文本 claim 回声匹配：归一后子串命中 news_list / key_events / field_ref 解析文本。

    未命中判 UNVERIFIABLE(text)——文本 claim 的语义忠实性由 decision_grounding
    judge（rubric v6 逐条核对 claim 与 source）与人工盲标承担，确定性门禁不越权。
    """
    sources = collect_text_sources(state)
    resolved = _resolve_field_ref(claim.field_ref, state)
    if isinstance(resolved, str):
        sources.append(resolved)
    # 目标：stated_value / interpretation / field_ref（旧 event 契约用 field_ref 承载标题）
    targets = [
        t
        for t in (
            _norm_text(claim.stated_value),
            _norm_text(claim.interpretation),
            _norm_text(claim.field_ref),
        )
        if t
    ]
    if not targets:
        return CitationResult(status="UNVERIFIABLE", claim=claim)
    for src in sources:
        src_n = _norm_text(src)
        if src_n and any(src_n in t or t in src_n for t in targets):
            return CitationResult(
                status="PASS", claim=claim, ground_truth=src, unit_normalized="echo"
            )
    return CitationResult(status="UNVERIFIABLE", claim=claim)


def _verify_event(claim: Claim, state: dict) -> CitationResult:
    """事件型 claim：验证引用的事件存在于 key_events。"""
    key_events = state.get("key_events", [])
    if not isinstance(key_events, list):
        return CitationResult(
            status="FAIL", claim=claim, ground_truth=None, bucket="path_unresolvable"
        )

    event_title = claim.field_ref
    for event in key_events:
        if isinstance(event, dict) and event.get("title") == event_title:
            # 事件存在，可选校验日期
            if claim.stated_value and event.get("date"):
                if str(event["date"]) == str(claim.stated_value):
                    return CitationResult(status="PASS", claim=claim, ground_truth=event["date"])
                return CitationResult(
                    status="FAIL",
                    claim=claim,
                    ground_truth=event["date"],
                    bucket="value_mismatch",
                )
            return CitationResult(status="PASS", claim=claim)

    return CitationResult(status="FAIL", claim=claim, ground_truth=None, bucket="path_unresolvable")


# ── 语义层检查（harden-citation-semantic-coverage）──


def _check_metric_term(claim: Claim, state: dict) -> tuple[CitationResult | None, bool]:
    """术语一致性。返回 (FAIL 结果或 None, 是否覆盖缺口)。

    词表内规范键不一致 → FAIL（张冠李戴拦截面）；词表外（无规范键）→ 跳过
    检查计覆盖缺口（D5 扩展，2026-09-01 三标的冒烟实证：state 指标段空间
    开放——报表行名/dupont/health_score/garp，词表不可闭合，词表外 FAIL
    全为误报）。未申报（None）由外层缺口公式兜底，此处不重复计。

    阶段 1（incident 026）：报表域（DataFrame 根键）上，metric_name 与 field_ref
    段/真实列名一致（含词表别名归一后一致）即判术语一致——照抄真实列名
    （如「归属于母公司的净利润」）不得被词表判为张冠李戴。
    """
    name = (claim.metric_name or "").strip()
    if not name:
        return None, False
    root = claim.field_ref.split(".")[0]
    root_obj = state.get(root)
    if _is_dataframe(root_obj):
        cols = {str(c).strip() for c in root_obj.columns}
        name_variants = {name, canonical_metric(name) or ""}
        segs = {seg.strip() for seg in claim.field_ref.split(".")[1:]}
        if name_variants & cols or name_variants & segs:
            return None, False
    canonical = canonical_metric(name)
    if canonical is None:
        return None, True
    segments = field_ref_metric_segments(claim.field_ref)
    seg_keys = {(canonical_metric(s) or s) for s in segments}
    if canonical not in seg_keys:
        return (
            CitationResult(status="FAIL", claim=claim, bucket="semantic_term_mismatch"),
            False,
        )
    return None, False


def _resolve_index_period(field_ref: str, state: dict) -> str | None:
    """索引锚定引用（无显式期次段）从 state 解析实际期次标签；解析不出返回 None。

    technical_indicators.X.Y.<idx> → kline 日期列同索引（序列与 kline 等长、升序）；
    macro_indicators.<key>.<idx>.<列> → records[idx]["月份"]
        （4 段式索引在 parts[2]，亦接受键上括号 macro_indicators.<key>[<idx>].<列>；
        T3 修复：旧实现只看 parts[-1]，macro 期次恒解析不出、静默降级为缺口）；
    quarterly_trend.<key>[<idx>] → quarters[idx]。
    """
    import re as _re

    parts = field_ref.split(".")
    root = parts[0] if parts else ""
    idx: int | None = None
    try:
        if root == "macro_indicators":
            if len(parts) < 3:
                return None
            key = parts[1]
            bracket = _re.search(r"\[(-?\d+)\]$", key)
            if bracket:
                idx = int(bracket.group(1))
                key = key[: bracket.start()]
            elif _re.match(r"^-?\d+$", parts[2]):
                idx = int(parts[2])
            if idx is None:
                return None
            recs = state["macro_indicators"][key]
            if isinstance(recs, dict):
                recs = recs.get("records") or []
            return str(recs[idx].get("月份", "")) or None

        m = _re.match(r"^-?\d+$", parts[-1]) if parts else None
        bracket = _re.search(r"\[(-?\d+)\]$", parts[-1]) if parts else None
        if bracket:
            idx = int(bracket.group(1))
        elif m and root in {"technical_indicators", "quarterly_trend"}:
            idx = int(parts[-1])
        if idx is None:
            return None
        if root == "technical_indicators":
            dates = state["kline"]["日期"]
            return render_date(dates.iloc[idx])
        if root == "quarterly_trend":
            return str(state["quarterly_trend"]["quarters"][idx]) or None
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return None


def _check_period(claim: Claim, state: dict) -> tuple[CitationResult | None, bool]:
    """期次一致性。返回 (FAIL 结果或 None, 是否覆盖缺口)。"""
    declared = (claim.period or "").strip()
    if not declared:
        return None, False
    if normalize_period(declared) is None:
        return None, True  # 期次表述无法归一化 → 缺口，不误伤
    actual = field_ref_period_segment(claim.field_ref)
    if actual is None:
        actual = _resolve_index_period(claim.field_ref, state)
        if actual is None:
            return None, True  # 索引期次解析不出 → 缺口
    if period_matches(declared, actual):
        return None, False
    return (
        CitationResult(status="FAIL", claim=claim, bucket="semantic_period_mismatch"),
        False,
    )


# ── claim 内部一致性（harden-citation-semantic-coverage）──

_NUMBER_PATTERN = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|％|亿|万|元)?")
_UNIT_SCALE = {"亿": 1e8, "万": 1e4, "元": 1.0, "%": 1.0, "％": 1.0, "": 1.0}

_NEGATIVE_WORDS = ("负增长", "下降", "下滑", "下跌", "回落", "走低", "减少", "降低", "恶化", "走弱")
_POSITIVE_WORDS = ("增长", "上升", "上涨", "提升", "提高", "改善", "走高", "回升", "向好")
_NEGATION_PREFIXES = ("负", "未", "无", "不")


def _extract_numbers(text: str) -> list[float]:
    """从自由文本提取数值（去千分位，亿/万缩放为原始单位，% 取面值）。

    仅返回单位缩放后的值（"0.68亿" → 6.8e7）。回声匹配需要同时比对面值时
    用 `_extract_number_candidates`，本函数签名/口径被 TestExtractNumbers 钉死，
    不得改动。
    """
    out: list[float] = []
    for m in _NUMBER_PATTERN.finditer(text):
        token = m.group(0)
        unit = token[-1] if token and token[-1] in _UNIT_SCALE else ""
        digits = token[:-1] if unit else token
        try:
            out.append(float(digits.replace(",", "").strip()) * _UNIT_SCALE[unit])
        except ValueError:
            continue
    return out


def _extract_number_candidates(text: str) -> list[float]:
    """回声匹配候选数值：对每个单位后缀 token 同时产出面值与缩放值。

    `_extract_numbers` 只返回缩放值（"0.68亿" → 6.8e7），但 claim 的
    stated_value 常以亿元/万元面值申报（stated=0.68），缩放候选经
    {1,100,0.01,1e4,1e8} 缩放集永远无法还原面值 → 误报。本函数对单位后缀
    token 额外补一份面值（"0.68亿" → 0.68 与 6.8e7 并列），纯数字 token
    只产面值一份。% / 元 单位缩放为 1.0，面值与缩放值相等（重复无副作用）。
    """
    out: list[float] = []
    for m in _NUMBER_PATTERN.finditer(text):
        token = m.group(0)
        unit = token[-1] if token and token[-1] in _UNIT_SCALE else ""
        digits = token[:-1] if unit else token
        try:
            face = float(digits.replace(",", "").strip())
        except ValueError:
            continue
        out.append(face)
        if unit:
            out.append(face * _UNIT_SCALE[unit])
    return out


def value_close(a: float, b: float) -> bool:
    """容差比对（max(0.01, 0.5%)，与数值型校验同族，允许双向不对称）。"""
    return abs(a - b) < max(ABS_TOL, REL_TOL * max(abs(a), abs(b)))


def _check_internal_echo(claim: Claim) -> CitationResult | None:
    """数值回声：interpretation 含数值但无一与 stated_value 匹配 → FAIL。

    符号不敏感（abs 比对）：中文财务表述惯用「下降 5.2%」（幅度 + 方向词）
    而非「-5.2%」，符号一致性由方向词核对承担，回声只抓幅度两张皮。
    """
    try:
        stated = float(claim.stated_value)
    except (TypeError, ValueError):
        return None  # 非数值 stated（比较方向等）不适用回声检查
    candidates = _extract_number_candidates(claim.interpretation or "")
    if not candidates:
        return None  # 定性表述不强制回声（召回由正文覆盖率普查承担）
    for cand in candidates:
        for scale in (1.0, 100.0, 0.01, 1e4, 1e8):
            if value_close(abs(stated), abs(cand) * scale):
                return None
    return CitationResult(status="FAIL", claim=claim, bucket="internal_inconsistency")


def _direction_hits(text: str, words: tuple[str, ...]) -> list[int]:
    """方向词命中位置；排除紧邻否定前缀的「增长」类命中（负增长 ≠ 增长）。"""
    hits: list[int] = []
    for w in words:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            if w in _POSITIVE_WORDS and i > 0 and text[i - 1] in _NEGATION_PREFIXES:
                start = i + len(w)
                continue
            hits.append(i)
            start = i + len(w)
    return hits


def _is_growth_claim(claim: Claim) -> bool:
    """增长类 claim 判定（方向词核对适用面）。

    收敛口径（防误报）：root == "growth_rates"，或 root == "quarterly_trend"
    且系列段（parts[1]，去掉尾部 `[N]` 括号后）为 "yoy"/"qoq"。剔除原先
    「同比/环比/增速 in field_ref」子串判定——该子串误伤 macro 级 claim
    （macro_indicators.cpi.<idx>.全国-同比增长 引用的是 yoy RATE LEVEL，
    其 interpretation 对走势的评述（「回落」描述动能而非否定正值）不该判 FAIL）。
    """
    parts = claim.field_ref.split(".")
    root = parts[0] if parts else ""
    if root == "growth_rates":
        return True
    if root == "quarterly_trend" and len(parts) >= 2:
        series = re.sub(r"\[(-?\d+)\]$", "", parts[1])
        return series in ("yoy", "qoq")
    return False


def _check_direction_words(claim: Claim) -> CitationResult | None:
    """方向词核对（仅值级 PASS 时）：方向词与比较方向/增长符号矛盾 → FAIL。

    适用面收敛（v1 防误报）：comparative 全量；numerical/computational 仅
    growth_rates 根键或 quarterly_trend 的 yoy/qoq 系列增长类 claim。
    正负向词同时出现或均不出现 → 跳过（不赌复杂句语义）。
    """
    # ehr-style-claim-direction：已申报 direction 的 claim 不再走正文方向词核对
    # （双路径二义消除——申报方向是权威语义，正文词仅作未申报时的兜底）。
    if claim.direction is not None:
        return None
    text = claim.interpretation or ""
    pos = bool(_direction_hits(text, _POSITIVE_WORDS))
    neg = bool(_direction_hits(text, _NEGATIVE_WORDS))
    if pos == neg:
        return None
    expect_positive: bool | None = None
    if claim.claim_type == "comparative":
        if claim.stated_value == "greater_than":
            expect_positive = True
        elif claim.stated_value == "less_than":
            expect_positive = False
    else:
        if _is_growth_claim(claim):
            try:
                expect_positive = float(claim.stated_value) > 0
            except (TypeError, ValueError):
                return None
    if expect_positive is None:
        return None
    if expect_positive and neg:
        return CitationResult(status="FAIL", claim=claim, bucket="internal_inconsistency")
    if not expect_positive and pos:
        return CitationResult(status="FAIL", claim=claim, bucket="internal_inconsistency")
    return None


def verify_claims(claims: list[Claim], state: dict) -> list[CitationResult]:
    """校验所有 Claim，返回逐条结果。"""
    results: list[CitationResult] = []
    for claim in claims:
        if claim.claim_type in ("entity", "regulatory") or claim.source_type == "event":
            # 阶段 3（incident 026）：文本 claim 数值比对永远验不了（news_list 61/61、
            # key_events 14/14）——回声匹配（归一子串命中即 PASS(echo)），未命中
            # UNVERIFIABLE(text)，不进 FAIL 分母、不计覆盖缺口
            results.append(_verify_textual(claim, state))
        elif claim.source_type == "llm_inference":
            results.append(CitationResult(status="UNVERIFIABLE", claim=claim))
        elif claim.source_type == "event":
            results.append(_verify_event(claim, state))
        elif claim.claim_type == "numerical":
            # harden-recompute-routing：校验深度不得由被校验对象的自我声明决定——
            # field_ref 命中重算注册表根时**无视 claim_type** 一律走重算路径
            # （否则 LLM 把自算值标成 numerical 即可跳过重算，自算错误逃逸；
            #  P1 冻结批实证：5/5 载体 claim 标 numerical → 从被污染 state 取真值 → 恒 PASS）。
            if _recomputable_root(claim):
                verdict = _verify_data_claim(claim, state, _verify_computational)
                if verdict.status == "UNVERIFIABLE" and verdict.coverage_gap:
                    # 重算输入不可得 → 退回直读比对，但**保留覆盖缺口标记**（降级可见，
                    # 不静默；state 齐备时重算恒优先——生产 state 恒含原始报表）。
                    # #123 契约（tests/test_citation_semantic.py::test_term_match_passes）：
                    # 「重算不可得」本身就是覆盖缺口，即使 direction 已申报（2026-09-20
                    # 合并终裁——无条件 True，不采「尊重内层」融合语义）
                    verdict = _verify_data_claim(claim, state, _verify_numerical).model_copy(
                        update={"coverage_gap": True}
                    )
            else:
                verdict = _verify_data_claim(claim, state, _verify_numerical)
            results.append(verdict)
        elif claim.claim_type == "computational":
            results.append(_verify_data_claim(claim, state, _verify_computational))
        elif claim.claim_type == "comparative":
            results.append(_verify_data_claim(claim, state, _verify_comparative))
        else:
            results.append(CitationResult(status="UNVERIFIABLE", claim=claim))
    return results


def _verify_data_claim(
    claim: Claim,
    state: dict,
    value_fn: Callable[[Claim, dict], CitationResult],
) -> CitationResult:
    """data/mixed 数值族 claim 的完整校验链：术语 → 期次 → 内部回声 → 值级 → 方向词。

    首个 FAIL 短路；术语/期次缺省或不可解析计覆盖缺口（D5 显式降级）。
    方向词检查只在值级 PASS 上执行（值级 FAIL 已由重试反馈携带真值）。
    """
    term_fail, term_gap = _check_metric_term(claim, state)
    if term_fail is not None:
        return term_fail
    period_fail, period_gap = _check_period(claim, state)
    if period_fail is not None:
        return period_fail
    # 缺口口径（D5）：任一申报字段缺失即计缺口，回声/方向提前 FAIL 不得丢失缺口标记
    gap = (
        term_gap
        or period_gap
        or not (claim.metric_name or "").strip()
        or not (claim.period or "").strip()
        # ehr-style-claim-direction：数值/计算型 claim 未申报 direction 亦计缺口
        or (claim.claim_type in ("numerical", "computational") and claim.direction is None)
    )
    # fix(comparative-echo)：comparative claim 跳过单值回声检查——其 stated_value
    # 是基准/当前值，interpretation 常描述差值（「8月 49.8 较 7月 49.2 回升 0.6」），
    # 拿基准值匹配差值必然误判 internal_inconsistency（2026-09-08 trace 实测）。
    # 回声语义只适用于数值/计算型（值即事实本身）；comparative 交给值级校验。
    if claim.claim_type != "comparative":
        echo_fail = _check_internal_echo(claim)
        if echo_fail is not None:
            echo_fail.coverage_gap = gap
            return echo_fail
    result = value_fn(claim, state)
    if result.status == "PASS":
        direction_fail = _check_direction_words(claim)
        if direction_fail is not None:
            direction_fail.coverage_gap = gap
            return direction_fail
    if gap:
        result.coverage_gap = True
    return result
