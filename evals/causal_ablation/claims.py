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
        effect_expectation="大：多位数心算错误率经验值 20%+",
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
    ),
    CausalClaim(
        id="A4",
        family="A",
        target="单点修复回路",
        claim="没有修复回路，残余 value_mismatch 滞留正文",
        failure_mode="错误数字进入报告",
        primary_metric="修复前后真 FAIL 率差",
        method="code",
        effect_expectation="中：只覆盖 <3 处的稀疏场景",
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
            "待 027-price-validate-state-keys-dropped 修复后的遥测实证"
            "（该回路曾因 state 键未声明从未生效）"
        ),
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
