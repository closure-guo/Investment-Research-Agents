"""因果主张登记表：无登记主张的对象不得进入消融矩阵（spec causal-ablation）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

VALID_METHODS = ("code", "nli", "judge")


@dataclass(frozen=True)
class CausalClaim:
    id: str
    family: str  # "A"（反幻觉机制）| "B"（编排层）
    target: str
    claim: str
    failure_mode: str
    primary_metric: str
    method: str  # code | nli | judge
    effect_expectation: str
    # 机制价值 = 暴露率 × 拦截率：拦截率由注入法测（primary_metric），暴露率由生产遥测测。
    # 缺暴露率栏会把「拦截率表」误读成「机制价值排行榜」——P1 round-3 实证：A5 拦截率面
    # 全 void 而暴露率 ≈0（决策层默认不产可执行价位），其期望价值趋零。
    exposure_metric: str = "待遥测基线（生产流量）"
    exposure_source: str = "生产遥测"
    suspended: str | None = None  # 悬置原因；None = 有对应实验


DEFAULT_REGISTRY: tuple[CausalClaim, ...] = (
    CausalClaim(
        id="A1",
        family="A",
        target="确定性指标注入",
        claim="没有代码算指标，LLM 会自算并算错",
        failure_mode="计算幻觉",
        primary_metric="计算型 claim 数值错误率",
        method="code",
        effect_expectation=(
            "实测（P1-A1 批，2026-09-17，10 标的 × 2 态，102 次调用）："
            "机制价值在**产出面**而非错误率——注入在场时 58.6%（355/606）的 claim 锚在代码算出的指标上，"
            "缺席时降到 2.3%（9/388）、claim 总量 -36%、路径不可解析率 0.2%→13.4%（数字失去可核对锚）。"
            "登记主张里的「LLM 自算并算错」**本批无样本**（可重算根 claim 319→4，0 FAIL）："
            "当前提示词下分析师不自算派生指标、而是回落原始报表路径——不是被证伪，是没有发生；"
            "要测该形态需另设「要求自行推导指标」的变体（待 owner 决策）"
        ),
        exposure_metric=(
            "计算型（派生指标）claim 占比实测 58.6%（355/606）；"
            "「未被重算覆盖比例」自然分布待遥测（重算注册表 10 根 vs compute 输出 14 键）"
        ),
        exposure_source="生产遥测：citation 重算注册表命中率 + claim 类型分布",
    ),
    CausalClaim(
        id="A2",
        family="A",
        target="语义头 + 单一词表",
        claim="没有语义头，序列方向/字段名会被误读",
        failure_mode="镜像叙事、路径幻觉",
        primary_metric="负索引/期次错位率",
        method="code",
        effect_expectation="中：incident 022 实证同族失败模式",
        exposure_metric="序列型/期次型 claim 占比（自然分布）",
        exposure_source="生产遥测：claim field_ref 根分布",
    ),
    CausalClaim(
        id="A3",
        family="A",
        target="claim 登记 + verify_citations",
        claim="没有校验器，编造数字无人拦截",
        failure_mode="无出处数字逃逸",
        primary_metric="注入污染逃逸率",
        method="code",
        effect_expectation="大：自然分布真幻觉≈0，需注入法测",
        exposure_metric=("自然分布真幻觉 ≈0（已登记）；无出处数字出现率待遥测"),
        exposure_source="生产遥测：citation 四桶 + UNVERIFIABLE 三分类计数",
    ),
    CausalClaim(
        id="A4",
        family="A",
        target="单点修复回路",
        claim="没有修复回路，残余 value_mismatch 滞留正文",
        failure_mode="错误数字进入报告",
        primary_metric="修复前后真 FAIL 率差",
        method="code",
        effect_expectation=(
            "中：实测修复回路把注入错误的改写值改对 20/20 且终稿留错 0/20，"
            "但流水线只记账 4/22——「同分析师另有其它 FAIL」时 all_passed=False，"
            "value_mismatch_repaired 遥测低估 16/20（incident 029）；"
            "自然分布的稀疏失败 2/2 因正文值型定位落空而无法参与（P1-A4 批，2026-09-17）"
        ),
        exposure_metric=(
            "稀疏残余 value_mismatch 发生率（<3 处场景）：20 标的离线复算命中 2/20（0.10）；"
            "可修复面上限 = 可产生 value_mismatch 的数值 claim 占比 765/1070（71.5%）"
            "× 正文可定位率 757/765（99.0%）"
        ),
        exposure_source="生产遥测：citation_fail_buckets.value_mismatch 分布",
    ),
    CausalClaim(
        id="A5",
        family="A",
        target="价位参考带 + 三价 sanity 校验",
        claim="没有 sanity 校验，Trader 可编造不可执行价位",
        failure_mode="价位幻觉",
        primary_metric="价位越带率 / 无出处价位率",
        method="code",
        effect_expectation=(
            "大：注入法实测拦截率 1.000（16/16，4 标的；2026-09-17），"
            "且为价位幻觉的唯一防线（关态即全放行）；027 的 state 键缺陷已修，本例即其修复后的首次实证"
        ),
        exposure_metric=("≈0：决策层默认不产可执行价位（3 标的 × 3 轮实测全 watch，P1 round-3）"),
        exposure_source="生产遥测 B4：决策动作分布（buy/sell/watch）+ 价位打回率",
    ),
    CausalClaim(
        id="A6",
        family="A",
        target="文本 claim 回声匹配",
        claim="没有回声匹配，编造事件/新闻无法被识别",
        failure_mode="事件幻觉",
        primary_metric="事件型 claim 不可回声率",
        method="code",
        effect_expectation="未知——当前最薄防线，实验价值最高",
        exposure_metric="无出处文本 claim 出现率（自然分布）",
        exposure_source="生产遥测：citation UNVERIFIABLE(text) 计数 / 文本 claim 占比",
    ),
    CausalClaim(
        id="A7",
        family="A",
        target="宏观时效标记",
        claim="没有时效标记，旧数据会被当现值引用",
        failure_mode="时效幻觉",
        primary_metric="stale 引用率",
        method="code",
        effect_expectation="小但确定",
        exposure_metric="宏观数据 stale 频率（自然分布）",
        exposure_source="生产遥测：macro_indicators.freshness 分布",
    ),
    CausalClaim(
        id="B1",
        family="B",
        target="辩论层",
        claim="没有辩论，单方分析师遗漏的风险点无人补",
        failure_mode="风险点遗漏",
        primary_metric="风险点增量率（新增且被决策吸收 / 只）",
        method="nli",
        effect_expectation="中：提示效应集中在大分歧标的",
    ),
    CausalClaim(
        id="B2",
        family="B",
        target="辩论层",
        claim="没有交锋，错误观点无人反驳",
        failure_mode="错误观点滞留",
        primary_metric="交锋修正率（rebuttal_to 锚定且被修正）",
        method="judge",
        effect_expectation="未知",
    ),
    CausalClaim(
        id="B3",
        family="B",
        target="决策+风控层",
        claim="没有该层，执行参数（VaR/止损/仓位）无前文出处",
        failure_mode="执行参数自构",
        primary_metric="风控数字出处率",
        method="code",
        effect_expectation="大：出处率 +10pp 量级",
    ),
    CausalClaim(
        id="B4",
        family="B",
        target="决策+风控层",
        claim="没有 sanity 校验，价位非法/不可执行",
        failure_mode="价位非法",
        primary_metric="sanity 打回率 / 修正触发率",
        method="code",
        effect_expectation="中",
    ),
    CausalClaim(
        id="B5",
        family="B",
        target="编排整体",
        claim="完整层应产出更有助于决策的报告",
        failure_mode="决策辅助价值不足",
        primary_metric="pairwise 盲评胜率",
        method="judge",
        effect_expectation="中：阈值须附成本换算依据",
    ),
    CausalClaim(
        id="B6",
        family="B",
        target="全层（事后）",
        claim="—（不作层间归因）",
        failure_mode="—",
        primary_metric="事后结算胜率（仅绝对质量线）",
        method="code",
        effect_expectation="不作裁剪裁决依据",
        suspended="track-record 历史 predictions 全为 full 变体产出，无法层间对比",
    ),
)


class UnregisteredTargetError(ValueError):
    """目标未登记因果主张，不得进入消融矩阵。"""


def load_registry(path: Path | None = None) -> tuple[CausalClaim, ...]:
    """默认表 + 可选 JSON 增量（同 id 覆盖默认行）。文件不存在则返回默认表。"""
    if path is None or not path.exists():
        return DEFAULT_REGISTRY
    raw = json.loads(path.read_text(encoding="utf-8"))
    extra = tuple(
        CausalClaim(
            id=row["id"],
            family=row["family"],
            target=row["target"],
            claim=row["claim"],
            failure_mode=row["failure_mode"],
            primary_metric=row["primary_metric"],
            method=row["method"],
            effect_expectation=row["effect_expectation"],
            suspended=row.get("suspended"),
        )
        for row in raw
    )
    by_id = {c.id: c for c in DEFAULT_REGISTRY}
    for c in extra:
        by_id[c.id] = c
    return tuple(by_id.values())


def validate_registry(registry: tuple[CausalClaim, ...]) -> list[str]:
    """返回问题列表（空 = 合法）。"""
    issues: list[str] = []
    seen: set[str] = set()
    for c in registry:
        if c.id in seen:
            issues.append(f"重复 id: {c.id}")
        seen.add(c.id)
        if c.method not in VALID_METHODS:
            issues.append(f"{c.id}: method 非法 {c.method!r}（须为 {VALID_METHODS}）")
        if not c.primary_metric:
            issues.append(f"{c.id}: 缺主指标")
        if not c.claim:
            issues.append(f"{c.id}: 缺因果主张")
    return issues


def assert_admissible(
    target_id: str, registry: tuple[CausalClaim, ...] | None = None
) -> CausalClaim:
    """准入校验：未登记即拒绝。"""
    reg = registry if registry is not None else DEFAULT_REGISTRY
    for c in reg:
        if c.id == target_id:
            return c
    raise UnregisteredTargetError(f"目标 {target_id} 未登记因果主张，不得进入消融矩阵")
