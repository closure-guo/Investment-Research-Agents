"""合成案例三连测运行器（2026-11-06 零买入第二轮 P0）。

纯离线：合成 fixture 直接进图（evals.ablation.build_variant_graph("full")），
不触网络、不派单、不写 sessions 库。变体注入复用 round-1 的 patch_prompts。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))  # evals/ 未安装进 venv（packages.find where=src）

from evals.ablation import build_variant_graph  # noqa: E402 - 需先补 sys.path 才能导入 evals 顶层包
from evals.synthetic_case import SYN_CODE, build_synthetic_state  # noqa: E402
from scripts.run_zero_buy_ablation import patch_prompts  # noqa: E402

from finance_agent.nodes.compute import compute_metrics  # noqa: E402

_ANALYSTS = ("fundamental_analyst", "technical_analyst", "macro_analyst", "sentiment_analyst")
_EXPECTED_PROMPTS = ("trader", "risk_judge", "fund_manager", *_ANALYSTS)
_OMIT = (
    "kline",
    "benchmark_kline",
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "quarterly_income",
    "peer_financials",
)


def _parse_mapping(paths: list[Path]) -> dict[str, str]:
    """文件名前缀 → prompt 名（含 4 分析师；与 round-1 的前缀规则正交）。"""
    mapping: dict[str, str] = {}
    for p in paths:
        stem = p.stem
        name: str | None = None
        for analyst in _ANALYSTS:
            if stem.startswith(analyst):
                name = analyst
                break
        if name is None:
            for fixed in ("trader", "risk_judge", "fund_manager"):
                if stem.startswith(fixed):
                    name = fixed
                    break
        if name is None:
            raise ValueError(f"无法从变体文件名推断 prompt 名: {p.name}")
        if name in mapping:
            raise ValueError(f"prompt {name} 重复指定: {p.name}")
        mapping[name] = p.name
    return mapping


def _json_safe(value: Any) -> Any:
    """递归序列化 state 值：决策层 dump 必须无损，禁止长度截断。

    顺序：pydantic → ``model_dump(mode="json")``；dict/list 递归；
    str/num/bool/None 原样；pandas 对象 → ``str`` repr（防御性，正常已由
    ``_OMIT`` 占位）；其余（如 SimpleNamespace）→ ``repr``。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (pd.DataFrame, pd.Series, pd.Index)):
        return str(value)
    return repr(value)


def _dump_layers(final: dict[str, Any]) -> dict[str, Any]:
    """决策相关 state 全量 dump（大表/K 线替换为占位串，其余递归无损序列化）。"""
    layers: dict[str, Any] = {}
    for key, value in final.items():
        if key in _OMIT:
            layers[key] = f"<{type(value).__name__} omitted>"
        else:
            layers[key] = _json_safe(value)
    return layers


def run_case(case: str, *, variants: list[Path] | None = None) -> dict[str, Any]:
    """构造合成态 → compute_metrics → 全图 → 决策 + 分层 dump。"""
    state = build_synthetic_state(case)
    state.update(compute_metrics(state))  # type: ignore[arg-type]
    mapping = _parse_mapping(variants or [])
    variant_dir = variants[0].parent if variants else None
    if variants:
        missing = [name for name in _EXPECTED_PROMPTS if name not in mapping]
        if missing:
            print(
                f"[synthetic] WARNING: partial variant set: missing {missing}",
                file=sys.stderr,
            )
        dirs = {v.parent.resolve() for v in variants}
        if len(dirs) > 1:
            raise ValueError(f"多个变体须同目录: {sorted(str(d) for d in dirs)}")
        with patch_prompts(variant_dir, mapping):  # type: ignore[arg-type]
            final = _invoke_with_retry(state)
    else:
        final = _invoke_with_retry(state)
    raw = final.get("final_trade_decision")
    if raw is not None and hasattr(raw, "model_dump"):
        decision: dict = raw.model_dump()
    else:
        decision = dict(raw or {})
    return {
        "case": case,
        "variants": mapping,
        "decision": decision,
        "layers": _dump_layers(final),
    }


def _invoke(state: dict[str, Any]) -> dict[str, Any]:
    graph = build_variant_graph("full")
    return graph.invoke({**state, "focus": f"{SYN_CODE} 综合评估投资价值"})


def _invoke_with_retry(
    state: dict[str, Any],
    *,
    attempts: int = 2,
    cooldown_s: float = 180.0,
) -> dict[str, Any]:
    """图无 checkpoint，限流只能整轮重跑；默认最多 2 次、间隔 180s。

    第一轮消融的教训（provider 过载窗口）：失败尝试仍会烧掉已完成节点的
    token，故不做无上限重试；2 次不成即落「基础设施未完成」结论，不再加码。
    """
    from finance_agent.llm.errors import RateLimitError

    for i in range(1, attempts + 1):
        try:
            return _invoke(state)
        except RateLimitError as exc:
            if i >= attempts:
                raise
            print(
                f"[synthetic] attempt {i}/{attempts} rate-limited: {exc}; "
                f"retry in {cooldown_s:.0f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(cooldown_s)
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--case", required=True, choices=("perfect", "perfect_cooled", "perfect_tight", "defect")
    )
    ap.add_argument("--variant", action="append", default=[], help="变体文件（可重复）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    variants = [Path(v) for v in args.variant]
    result = run_case(args.case, variants=variants or None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    action = result["decision"].get("action")
    print(f"[synthetic] case={args.case} variants={result['variants']} action={action} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
