"""add-factor-dsl Task 4：Alpha101 公开公式的 DSL 函数式转写 round-trip 核对。

对 10 条公开公式（Kakushadze, *101 Formulaic Alphas*, 2015）逐条：

1. 给出**原式**与**转写映射**（仅用 v1 的 15 算子 + 6 字段；DSL 没有的算子按
   注释声明的结构性替换改写——本核对验证的是 DSL 的**结构契约**（可解析 /
   round-trip / 复杂度上限），**不验证数值等价**）；
2. ``parse(转写, enforce_complexity=False)`` → ``serialize`` → 再 ``parse`` →
   **AST 相等**断言（D7 旁路：公开公式普遍超出上限，旁路仅供验证）；
3. 追加报告同一转写在**默认 ``enforce_complexity=True``** 下的结果：通过或被
   拒收的完整原因（复杂度上限严格性的证据）。深度/自由参数/字段种类为按
   D3/D4 口径**手算后内嵌**的期望值（不从被测函数推导），运行后逐一断言。
   注：D4 把**所有** Number 节点计入自由参数，因此一元负的 ``sub(0,·)`` 改写
   会额外消耗 1 个自由参数（0 也是 Number 节点）——本核对的口径注记。

运行：``uv run python tests/validation/2026-10-13-add-factor-dsl-alpha101.py``
（10 条 round-trip 全对、手算度量全符 → exit 0；任一不符打印明细 → exit 1）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from finance_agent.factors.dsl import (
    FactorComplexityError,
    ast_depth,
    count_fields,
    count_free_params,
    parse,
    serialize,
)


@dataclass(frozen=True)
class _Case:
    """单条公开公式：原式 + 转写映射 + DSL 转写 + 手算期望（D3/D4 + 上限判定）。"""

    alpha: str
    original: str
    mapping: str
    dsl: str
    depth: int
    params: int
    fields: int
    expected_cap: str  # "通过" | "拒收"


# 手算依据（D3 叶子=1、Call=1+max(子)；D4 Number 总数 / 去重字段名数；上限 4/3/3）
_CASES: tuple[_Case, ...] = (
    _Case(
        alpha="Alpha#2",
        original="(-1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6))",
        mapping="correlation→ts_corr；rank→rank；delta→delta；log→log；"
        "(close-open)/open→div(sub(close,open),open)；一元负→sub(0,·)",
        dsl="sub(0, ts_corr(rank(delta(log(volume), 2)), rank(div(sub(close, open), open)), 6))",
        depth=6,
        params=3,
        fields=3,
        expected_cap="拒收",
    ),
    _Case(
        alpha="Alpha#4",
        original="(-1 * Ts_Rank(rank(low), 9))",
        mapping="Ts_Rank→ts_rank；rank→rank；一元负→sub(0,·)",
        dsl="sub(0, ts_rank(rank(low), 9))",
        depth=4,
        params=2,
        fields=1,
        expected_cap="通过",
    ),
    _Case(
        alpha="Alpha#6",
        original="(-1 * correlation(open, volume, 10))",
        mapping="correlation→ts_corr；一元负→sub(0,·)",
        dsl="sub(0, ts_corr(open, volume, 10))",
        depth=3,
        params=2,
        fields=2,
        expected_cap="通过",
    ),
    _Case(
        alpha="Alpha#12",
        original="(sign(delta(volume, 1)) * (-1 * delta(close, 1)))",
        mapping="sign(x)→div(x, abs(x))（x≠0 的结构性等价，DSL 无 sign）；"
        "delta→delta；一元负→sub(0,·)",
        dsl="mul(div(delta(volume, 1), abs(delta(volume, 1))), sub(0, delta(close, 1)))",
        depth=5,
        params=4,
        fields=2,
        expected_cap="拒收",
    ),
    _Case(
        alpha="Alpha#13",
        original="(-1 * rank(sum(delta(close, 1), 5)))",
        mapping="sum(x,n)→ts_mean(x,n)（DSL 无 sum；均值同构替换，量纲差常数 n）；一元负→sub(0,·)",
        dsl="sub(0, rank(ts_mean(delta(close, 1), 5)))",
        depth=5,
        params=3,
        fields=1,
        expected_cap="拒收",
    ),
    _Case(
        alpha="Alpha#14",
        original="((-1 * rank(delta(returns, 3))) * correlation(open, volume, 10))",
        mapping="returns→delta(close,1)（收盘差分作收益代理）；delta(returns,3)→"
        "delta(delta(close,1),3)；correlation→ts_corr；一元负→sub(0,·)",
        dsl="mul(sub(0, rank(delta(delta(close, 1), 3))), ts_corr(open, volume, 10))",
        depth=6,
        params=4,
        fields=3,
        expected_cap="拒收",
    ),
    _Case(
        alpha="Alpha#15",
        original="(-1 * sum(rank(correlation(rank(high), rank(volume), 3)), 3))",
        mapping="correlation→ts_corr；rank→rank；sum(x,n)→ts_mean(x,n)；一元负→sub(0,·)",
        dsl="sub(0, ts_mean(rank(ts_corr(rank(high), rank(volume), 3)), 3))",
        depth=6,
        params=3,
        fields=2,
        expected_cap="拒收",
    ),
    _Case(
        alpha="Alpha#16",
        original="(-1 * rank(covariance(rank(high), rank(volume), 5)))",
        mapping="covariance(x,y,n)→ts_corr(x,y,n)（DSL 无协方差；相关系数为其标准化"
        "同族，结构性替换）；一元负→sub(0,·)",
        dsl="sub(0, rank(ts_corr(rank(high), rank(volume), 5)))",
        depth=5,
        params=2,
        fields=2,
        expected_cap="拒收",
    ),
    _Case(
        alpha="Alpha#53",
        original="(-1 * delta((((close - low) - (high - close)) / (close - low)), 9))",
        mapping="delta→delta；四则运算→sub/div；一元负→sub(0,·)",
        dsl="sub(0, delta(div(sub(sub(close, low), sub(high, close)), sub(close, low)), 9))",
        depth=6,
        params=2,
        fields=3,
        expected_cap="拒收",
    ),
    _Case(
        alpha="Alpha#101",
        original="((close - open) / ((high - low) + .001))",
        mapping="四则运算→sub/div/add；字面量 .001→0.001（DSL 数字语法要求整数部分）",
        dsl="div(sub(close, open), add(sub(high, low), 0.001))",
        depth=4,
        params=1,
        fields=4,
        expected_cap="拒收",
    ),
)


def main() -> int:
    """逐条核对；全对 exit 0，任一不符打印明细并 exit 1。"""
    failures: list[str] = []
    table: list[tuple[str, int, int, int, str, str]] = []
    print(f"Alpha101 round-trip 核对（{len(_CASES)} 条；D7 旁路 enforce_complexity=False）")
    print("=" * 108)
    for case in _CASES:
        print(f"\n## {case.alpha}")
        print(f"   原式     : {case.original}")
        print(f"   映射     : {case.mapping}")
        print(f"   转写     : {case.dsl}")
        # ① round-trip：parse(enforce_complexity=False) → serialize → 再 parse → AST 相等
        try:
            first = parse(case.dsl, enforce_complexity=False)
        except Exception as exc:  # noqa: BLE001 - 验证脚本：任何解析失败都要落表
            failures.append(f"{case.alpha}: 旁路解析失败：{exc!r}")
            table.append((case.alpha, -1, -1, -1, "解析失败", f"解析失败：{exc}"))
            print(f"   round-trip: 解析失败 —— {exc!r}")
            continue
        text = serialize(first)
        second = parse(text, enforce_complexity=False)
        round_trip_ok = second == first
        if not round_trip_ok:
            failures.append(f"{case.alpha}: round-trip AST 不相等（serialize={text}）")
        # 手算期望（D3/D4）逐一断言
        depth, params, fields = ast_depth(first), count_free_params(first), count_fields(first)
        measured_ok = (depth, params, fields) == (case.depth, case.params, case.fields)
        if not measured_ok:
            failures.append(
                f"{case.alpha}: 度量不符（期望 深度 {case.depth}/参数 {case.params}/字段 {case.fields}，"
                f"实际 深度 {depth}/参数 {params}/字段 {fields}）"
            )
        # ② 默认上限下的判定
        try:
            parse(case.dsl)
            verdict = "通过（未超限）"
        except FactorComplexityError as exc:
            verdict = f"拒收 —— {exc}"
        got_cap = "通过" if verdict.startswith("通过") else "拒收"
        if got_cap != case.expected_cap:
            failures.append(
                f"{case.alpha}: 默认上限判定不符（期望 {case.expected_cap}，实际 {got_cap}）"
            )
        print(f"   serialize: {text}")
        print(
            f"   度量     : 深度 {depth} / 自由参数 {params} / 字段种类 {fields}"
            f"（手算期望 {case.depth}/{case.params}/{case.fields}）"
        )
        print(f"   round-trip: {'OK（AST 相等）' if round_trip_ok else 'FAIL'}")
        print(f"   默认上限 : {verdict}")
        table.append(
            (case.alpha, depth, params, fields, "OK" if round_trip_ok else "FAIL", verdict)
        )

    rejected = [row for row in table if row[5].startswith("拒收")]
    passed = [row[0] for row in table if row[5].startswith("通过")]
    print("\n" + "=" * 108)
    print("汇总表")
    print(f"{'alpha':<10}{'深度':<6}{'参数':<6}{'字段':<6}{'round-trip':<12}默认上限判定")
    for alpha, depth, params, fields, rt, verdict in table:
        print(f"{alpha:<10}{depth:<6}{params:<6}{fields:<6}{rt:<12}{verdict}")
    print(
        f"\nround-trip 一致 {sum(1 for row in table if row[4] == 'OK')}/{len(table)}；"
        f"默认上限拒收 {len(rejected)}/{len(table)}（通过：{'、'.join(passed) if passed else '无'}）"
    )
    if failures:
        print("\n不一致明细：")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("全部核对一致（exit 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
