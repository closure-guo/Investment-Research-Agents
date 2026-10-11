"""双重阈值红黄绿灯矩阵 + 四维度健康度评分。

评判规则（ADR-0003）：
- 绝对值水平：各指标硬编码阈值
- 同比变化率：<20%🟢 / 20-50%🟡 / >50%🔴
- 最终灯色 = max(绝对值灯, 变化率灯)

评分：四维度各 25 分，🟢=满分 🟡=半分 🔴=零分
85-100=healthy | 60-84=caution | <60=warning
"""

from __future__ import annotations

# ── 绝对值阈值表 ──
# 格式: (green_threshold, yellow_threshold, higher_is_better)
# higher_is_better=True: 值>=green → 🟢, 值<yellow → 🔴
# higher_is_better=False: 值<=green → 🟢, 值>yellow → 🔴

# 格式: (green_threshold, yellow_threshold, higher_is_better)
# higher_is_better=True: 值>=green → 🟢, 值<yellow → 🔴
# higher_is_better=False: 值<=green → 🟢, 值>yellow → 🔴

ABSOLUTE_THRESHOLDS: dict[str, tuple] = {
    # 偿债
    "资产负债率": (40, 65, False),
    "流动比率": (2.0, 1.0, True),
    "速动比率": (1.5, 0.8, True),
    "利息覆盖倍数": (6, 2, True),
    "净债务/EBITDA": (0, 2, False),
    # 盈利
    "毛利率": (30, 15, True),
    "净利率": (15, 5, True),
    "ROE": (15, 8, True),
    "ROA": (10, 3, True),
    "ROIC": (12, 6, True),
    # 运营（通用阈值，行业覆盖见下方）
    "存货周转率": (5, 2, True),
    "应收账款周转率": (8, 3, True),
    "总资产周转率": (0.8, 0.3, True),
    "应付账款周转率": (6, 3, True),
    # 现金流
    "经营现金流/净利润": (1.0, 0.5, True),
    "FCF": (0, None, True),
    "资本支出/折旧": (3, 1, True),
    "现金流覆盖比率": (1.0, 0.5, True),
    "FCF收益率": (0.05, 0.02, True),
    "留存现金流比率": (0.5, 0.2, True),
}

# ── 行业阈值覆盖 ──
# key 为行业名称子串（模糊匹配），值为 {指标名: 阈值三元组}
INDUSTRY_OVERRIDES: dict[str, dict[str, tuple | None]] = {
    "白酒": {
        "存货周转率": (0.5, 0.2, True),  # 基酒需 3-5 年陈酿，周转率天然低
    },
    "酿酒": {
        "存货周转率": (0.5, 0.2, True),
    },
    # 半导体设备：验收确认收入+合同负债预收模式，通用制造业阈值系统性误判红灯
    # 校准：北方华创/中微/拓荆/芯源微/华海清科 FY2025 分布（delta design.md）：
    # 存货周转 0.56-1.06 / 速动 0.74-1.90 / 应付周转 2.08-4.26
    "半导体设备": {
        "存货周转率": (1.2, 0.5, True),
        "速动比率": (1.5, 0.6, True),
        "应付账款周转率": (4.5, 1.5, True),
    },
}

# ── 银行业口径覆盖（add-banking-industry-calibration，issue #241 一期）──
# 校准锚点与分布论证见 delta design.md D4（光大 FY2025 实算 + 大行/股份行公开分布）。
# None = 行业不适用排除：不参与评灯与评分（D1——银行风险约束是资本充足率，
# 存款是经营原料而非杠杆风险；编造 93/96 类阈值才是拍脑袋）。
_BANKING_OVERRIDES: dict[str, tuple | None] = {
    # 偿债维度全部不适用（专项四指标二期接入）
    "资产负债率": None,
    "流动比率": None,
    "速动比率": None,
    "利息覆盖倍数": None,
    "净债务/EBITDA": None,
    # 盈利：ROE/ROA 换银行业阈值；毛利率数据天然缺失（无营业成本）；ROIC 不适用
    # （银行投入资本即计息负债，倍数无判别意义）
    "ROE": (13, 6, True),
    "ROA": (0.9, 0.5, True),
    "ROIC": None,
    # 效率维度全部不适用（资产=贷款与投资，无经营循环）
    "总资产周转率": None,
    "存货周转率": None,
    "应收账款周转率": None,
    "应付账款周转率": None,
    # OCF 衍生信号全部不适用（银行 OCF 含存贷款净进出）；保留 资本支出/折旧
    "经营现金流/净利润": None,
    "FCF": None,
    "现金流覆盖比率": None,
    "FCF收益率": None,
    "留存现金流比率": None,
}
# 「银行」子串覆盖东财系全部银行行业名（国有大型银行/股份制银行/农商行…）；
# 「货币金融服务」= cninfo 降级源形态——两键共享同一覆盖表对象
INDUSTRY_OVERRIDES["银行"] = _BANKING_OVERRIDES
INDUSTRY_OVERRIDES["货币金融服务"] = _BANKING_OVERRIDES

BANKING_INDUSTRY_KEYS = ("银行", "货币金融服务")


def is_banking_industry(industry: str | None) -> bool:
    """银行业判定（GARP/图表/健康度共享，单一实现勿另写子串表）。"""
    if not industry:
        return False
    return any(key in industry for key in BANKING_INDUSTRY_KEYS)


def industry_excluded_metrics(industry: str | None) -> frozenset[str]:
    """行业覆盖中值为 None（不适用）的指标集合。"""
    if not industry:
        return frozenset()
    for key, overrides in INDUSTRY_OVERRIDES.items():
        if key in industry:
            return frozenset(m for m, t in overrides.items() if t is None)
    return frozenset()


LIGHT_ORDER = {"green": 0, "yellow": 1, "red": 2}

SAFETY_FLOOR_MULTIPLIER = 10


def assess_change_rate(change_rate: float) -> str:
    """评判同比变化率灯色。取绝对值：<20%🟢 / 20-50%🟡 / >50%🔴。"""
    abs_rate = abs(change_rate)
    if abs_rate < 0.20:
        return "green"
    elif abs_rate <= 0.50:
        return "yellow"
    else:
        return "red"


def _get_thresholds(metric_name: str, industry: str | None) -> tuple | None:
    """获取指标阈值，优先使用行业覆盖。"""
    if not industry:
        return ABSOLUTE_THRESHOLDS.get(metric_name)
    for key, overrides in INDUSTRY_OVERRIDES.items():
        if key in industry and metric_name in overrides:
            return overrides[metric_name]
    return ABSOLUTE_THRESHOLDS.get(metric_name)


def matched_industry_overrides(industry: str | None) -> dict[str, tuple | None]:
    """返回该行业命中的阈值覆盖指标集合（用于健康度口径披露）。"""
    if not industry:
        return {}
    for key, overrides in INDUSTRY_OVERRIDES.items():
        if key in industry:
            return {m: t for m, t in overrides.items() if m in ABSOLUTE_THRESHOLDS}
    return {}


def _assess_absolute(metric_name: str, value: float, industry: str | None = None) -> str | None:
    """评判绝对值灯色。"""
    thresholds = _get_thresholds(metric_name, industry)
    if thresholds is None:
        return None

    green_thresh, yellow_thresh, higher_is_better = thresholds

    if metric_name == "FCF":
        return "green" if value > 0 else ("yellow" if value == 0 else "red")

    if higher_is_better:
        if value >= green_thresh:
            return "green"
        elif value >= yellow_thresh:
            return "yellow"
        else:
            return "red"
    else:
        if value <= green_thresh:
            return "green"
        elif value <= yellow_thresh:
            return "yellow"
        else:
            return "red"


def _compute_change_rate(current: float, previous: float) -> float | None:
    """计算同比变化率。"""
    if previous == 0:
        return None
    return (current - previous) / abs(previous)


def _max_light(a: str | None, b: str | None) -> str | None:
    """取两个灯色中更差的那个。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if LIGHT_ORDER[a] >= LIGHT_ORDER[b] else b


def _apply_safety_floor(
    metric_name: str,
    value: float,
    abs_light: str | None,
    change_light: str | None,
    industry: str | None = None,
) -> str | None:
    """绝对值远超优良阈值时，将变化率灯色上限降为绿色。"""
    if abs_light != "green" or change_light == "green":
        return change_light

    thresholds = _get_thresholds(metric_name, industry)
    if thresholds is None:
        return change_light

    green_thresh, _, higher_is_better = thresholds

    if green_thresh == 0:
        return change_light

    if higher_is_better:
        if value >= green_thresh * SAFETY_FLOOR_MULTIPLIER:
            return "green"
    else:
        if value <= green_thresh / SAFETY_FLOOR_MULTIPLIER:
            return "green"

    return change_light


def assess_traffic_lights(
    metrics: dict[str, dict[str, dict[str, float | None]]],
    industry: str | None = None,
) -> dict[str, dict[str, dict[str, dict]]]:
    """对全部指标做双重阈值评判。

    Parameters
    ----------
    metrics : dict
        {dimension: {metric_name: {year: value}}}
        dimension: solvency, profitability, efficiency, cashflow
    industry : str | None
        行业名称，用于加载行业特定阈值覆盖。

    Returns
    -------
    dict
        {dimension: {metric_name: {year: {absolute, change, final}}}}
    """
    result: dict[str, dict[str, dict[str, dict]]] = {}
    all_years: set[str] = set()

    # 收集所有年份
    for dim_metrics in metrics.values():
        for metric_values in dim_metrics.values():
            all_years.update(metric_values.keys())
    sorted_years = sorted(all_years, reverse=True)

    # 行业不适用排除（add-banking-industry-calibration D1）：override=None 的指标
    # 对该行业无判别意义——绝对灯/变化率灯/final 全 None，MUST NOT 触发通用阈值
    excluded = industry_excluded_metrics(industry)

    for dim_name, dim_metrics in metrics.items():
        result[dim_name] = {}
        for metric_name, year_values in dim_metrics.items():
            if metric_name.endswith("_source"):
                continue
            if metric_name in excluded:
                result[dim_name][metric_name] = {
                    year: {"absolute": None, "change": None, "final": None} for year in sorted_years
                }
                continue
            result[dim_name][metric_name] = {}

            for idx, year in enumerate(sorted_years):
                val = year_values.get(year)
                if val is None:
                    result[dim_name][metric_name][year] = {
                        "absolute": None,
                        "change": None,
                        "final": None,
                    }
                    continue

                abs_light = _assess_absolute(metric_name, val, industry)

                # 变化率：与上一年比较
                prev_year_idx = idx + 1  # sorted_years 是最新在前
                if prev_year_idx < len(sorted_years):
                    prev_year = sorted_years[prev_year_idx]
                    prev_val = year_values.get(prev_year)
                    if prev_val is not None and prev_val != 0:
                        change_rate = _compute_change_rate(val, prev_val)
                        change_light = (
                            assess_change_rate(change_rate) if change_rate is not None else None
                        )
                    else:
                        change_light = None
                else:
                    change_light = None

                change_light = _apply_safety_floor(
                    metric_name, val, abs_light, change_light, industry
                )

                final_light = _max_light(abs_light, change_light)

                result[dim_name][metric_name][year] = {
                    "absolute": abs_light,
                    "change": change_light,
                    "final": final_light,
                }

    return result


def compute_health_score(
    traffic_lights: dict[str, dict[str, dict[str, dict]]],
    year: str,
    industry: str | None = None,
) -> dict:
    """计算四维度健康度评分。

    四维度各 25 分，🟢=满分 🟡=半分 🔴=零分。
    None 不计入。
    行业阈值覆盖命中时结果携带 industry_override 口径标注（industry-threshold-coverage）。
    """
    dimension_weight = 25
    dimension_scores = {}
    red_metrics = []

    # 行业不适用排除集：维度内全部指标被排除 → 维度剔除（满分缩放，
    # add-banking-industry-calibration D2）；数据缺失维度不剔除（0 分是诚实信号）
    excluded = industry_excluded_metrics(industry)

    applicable_dims = 0
    for dim_name, dim_metrics in traffic_lights.items():
        if dim_metrics and all(name in excluded for name in dim_metrics):
            continue
        applicable_dims += 1
        points = 0.0
        count = 0
        for metric_name, year_data in dim_metrics.items():
            entry = year_data.get(year)
            if entry is None or entry.get("final") is None:
                continue
            count += 1
            light = entry["final"]
            if light == "green":
                points += 1.0
            elif light == "yellow":
                points += 0.5
            else:
                red_metrics.append(f"{dim_name}.{metric_name}")

        if count > 0:
            dimension_scores[dim_name] = points / count * dimension_weight
        else:
            dimension_scores[dim_name] = 0.0

    total = sum(dimension_scores.values())
    # 满分缩放：通用路径适用维度恒 4 → 100 分、阈值 85/60 零回归；
    # 行业剔除维度后 rating 阈值按满分等比缩放（healthy ≥ 85%、caution ≥ 60%）
    score_cap = dimension_weight * applicable_dims
    healthy_line = 85 * score_cap / 100
    caution_line = 60 * score_cap / 100

    if total >= healthy_line:
        rating = "healthy"
    elif total >= caution_line:
        rating = "caution"
    else:
        rating = "warning"

    matched = matched_industry_overrides(industry)

    return {
        "total": round(total, 1),
        "rating": rating,
        "score_cap": score_cap,
        "dimensions": {k: round(v, 1) for k, v in dimension_scores.items()},
        "red_metrics": red_metrics,
        "industry_override": {
            "industry": industry if matched else None,
            "metrics": sorted(matched),
        },
    }
