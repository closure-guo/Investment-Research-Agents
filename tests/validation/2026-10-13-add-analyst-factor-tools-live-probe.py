"""add-analyst-factor-tools 最小 live 探针：技术分析师带因子库工具（真实 LLM，1 次调用）。

背景（如实记录）：ablation 配对烟测（1 对样本 = 2 次完整分析师层分析）在数据准备完成、
若干分析师调用成功后，被供应商限流中断（RateLimitError，见验证报告 §烟测）。本探针以
最小成本（单次 LLM 调用）验证本 change 的新链路在真实 LLM 下端到端可用：

1. 工具路径用 env 注入的合成 fixture（快照库面板 + active 因子 + IC 记录）；
2. 分析上下文为真实数据抓取（fetch_data + compute_metrics）；
3. 调生产入口 `technical_analyst`（带工具）→ 检查因子库段确实含因子取值/z-score/IC；
4. 对产出的 claims 跑 citation 校验，观察因子库数值是否产生不可验证 claim（citation 腿）。

用法（凭据经 env 显式传入，不读宿主 .env）：
    LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=openai/glm-4.6 \\
        uv run python tests/validation/2026-10-13-add-analyst-factor-tools-live-probe.py
无 LLM_API_KEY → 打印 [blocked] 并落 artifact（blocked=true），退出码 0。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

ARTIFACT = ROOT / "tests" / "validation" / "2026-10-13-add-analyst-factor-tools-live-probe.json"
_TICKER = "600519"
_PEERS = ("000858", "601318")
_EXPRESSION = "delta(close,20)"
_CLOSES = {
    _TICKER: [1500.0 + 3.0 * i for i in range(30)],
    "000858": [200.0 + 1.0 * i for i in range(30)],
    "601318": [50.0 + 0.2 * i for i in range(30)],
}


def _seed_fixture_dbs(tmp: Path) -> dict[str, str]:
    """合成面板（30 个工作日）+ active 动量因子 + 3 条 IC → env 注入工具路径。"""
    import pandas as pd

    from finance_agent.data.snapshot import write_snapshot_rows
    from finance_agent.factors.dsl import parse, serialize
    from finance_agent.factors.library import add_factor, record_ic

    snapshot_db = tmp / "market_snapshot.db"
    library_db = tmp / "factor_library.db"
    dates = [day.strftime("%Y-%m-%d") for day in pd.bdate_range(end="2026-10-13", periods=30)]
    for index, day in enumerate(dates):
        rows = []
        for code, closes in _CLOSES.items():
            close = closes[index]
            rows.append(
                {
                    "code": code,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close_raw": close,
                    "close_qfq": close,
                    "volume": 1000.0,
                    "amount": 1000.0 * close,
                }
            )
        write_snapshot_rows(day, rows, db_path=snapshot_db)
    factor_id = add_factor(
        expression=_EXPRESSION,
        hypothesis="探针用假设文本：20 日动量与未来收益相关。",
        category="动量",
        status="active",
        ast_json=serialize(parse(_EXPRESSION)),
        db_path=library_db,
    )
    for day, value in zip(dates[-3:], (0.09, 0.12, 0.15), strict=True):
        record_ic(factor_id, day, value, db_path=library_db)
    os.environ["SNAPSHOT_DB_PATH"] = str(snapshot_db)
    os.environ["FACTOR_LIBRARY_DB_PATH"] = str(library_db)
    return {"snapshot_db": str(snapshot_db), "library_db": str(library_db), "as_of": dates[-1]}


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # 本仓库根 .env（缺失则 no-op；宿主 .env 不读取）
    artifact: dict = {"probe": "analyst-factor-tool-live", "ticker": _TICKER}
    if not os.environ.get("LLM_API_KEY"):
        artifact["blocked"] = True
        artifact["reason"] = "LLM_API_KEY 未设置（凭据不满足，未发任何 LLM 调用）"
        # blocked 落独立兄弟文件：绝不覆盖已产出的 live 证据 artifact
        blocked_path = ARTIFACT.with_name(f"{ARTIFACT.stem}-blocked{ARTIFACT.suffix}")
        blocked_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[blocked] LLM_API_KEY 未设置：live 探针未执行（{blocked_path.name} 落盘）")
        return 0

    from finance_agent.citation import verify_claims
    from finance_agent.nodes import analysts
    from finance_agent.nodes.compute import compute_metrics
    from finance_agent.nodes.fetch import fetch_data

    with tempfile.TemporaryDirectory() as tmp_dir:
        fixture = _seed_fixture_dbs(Path(tmp_dir))
        artifact["fixture"] = fixture

        base = {"stock_code": _TICKER, "enable_web_search": False}
        state: dict = {**base, **fetch_data(base)}
        state.update(compute_metrics(state))

        block = analysts._factor_library_block(state)
        artifact["factor_block"] = block
        artifact["factor_block_has_value"] = bool(block and '"value": ' in block)
        artifact["factor_block_has_zscore"] = bool(block and '"zscore": ' in block)
        artifact["factor_block_has_ic"] = bool(block and '"ic_mean_60d"' in block)

        report = analysts.technical_analyst(state)["analyst_reports"]["technical"]
        artifact["report"] = {
            "summary": report.summary,
            "key_findings": list(report.key_findings),
            "claims": len(report.claims),
            "parse_degraded": bool(getattr(report, "parse_degraded", False)),
            "markdown_head": (report.markdown or "")[:400],
        }
        results = verify_claims(list(report.claims), state)
        buckets: dict[str, int] = {}
        for result in results:
            buckets[result.status] = buckets.get(result.status, 0) + 1
        factor_claims = [
            {
                "field_ref": getattr(claim, "field_ref", None),
                "claim_type": getattr(claim, "claim_type", None),
                "statement": (getattr(claim, "statement", "") or "")[:120],
            }
            for claim in report.claims
            if "factor" in (getattr(claim, "field_ref", "") or "")
            or "因子" in (getattr(claim, "statement", "") or "")
            or "动量" in (getattr(claim, "statement", "") or "")
        ]
        artifact["citation"] = {
            "buckets": buckets,
            "blocked": any(result.status == "FAIL" for result in results),
            "factor_related_claims": factor_claims,
            # 逐条 claim 状态（归因用：单条 FAIL 是否与因子库段相关须逐条看，不看聚合桶）
            "claims_detail": [
                {
                    "status": result.status,
                    "bucket": result.bucket,  # FAIL 分桶（归因：值级/路径/语义/方向）
                    "coverage_gap": bool(result.coverage_gap),
                    "ground_truth": str(result.ground_truth)[:80],
                    "field_ref": getattr(claim, "field_ref", None),
                    "claim_type": getattr(claim, "claim_type", None),
                    "statement": (getattr(claim, "statement", "") or "")[:120],
                }
                for claim, result in zip(report.claims, results, strict=True)
            ],
        }
        verdict = (
            "ok"
            if report.summary
            and not getattr(report, "parse_degraded", False)
            and buckets.get("FAIL", 0) == 0
            else "concern"
        )
        artifact["verdict"] = verdict

    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[verdict] {artifact['verdict']}")
    print(
        f"因子库段含 value/zscore/IC: {artifact['factor_block_has_value']}/"
        f"{artifact['factor_block_has_zscore']}/{artifact['factor_block_has_ic']}"
    )
    print(
        f"claims={artifact['report']['claims']} citation_buckets={artifact['citation']['buckets']}"
    )
    print(f"因子相关 claims: {len(artifact['citation']['factor_related_claims'])}")
    print(f"artifact: {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
