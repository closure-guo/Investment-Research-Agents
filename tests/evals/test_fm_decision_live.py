"""calibrate-fm-approval nightly @live：拉取真实 Langfuse 跑 FM 决策度量。

与 @live E2E 同纪律：需要 LANGFUSE_PUBLIC_KEY/SECRET，未配置时跳过；
nightly（pytest -m live）跑真实数据，防 stub 漂移。报告落 reports/。
"""

from pathlib import Path

import pytest
from evals.fm_decision.measure import render_report, run_offline

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_langfuse() -> bool:
    # @live 统一门禁（issue #605）：显式 opt-in（RUN_LIVE=1）优先，凭据在场不激活；
    # 原「load .env 后查 key」判据在全量套件中被 src.api import 时 load_dotenv 绕过
    from tests.evals._live_guard import require_live_opt_in

    return require_live_opt_in()


def test_fm_decision_live_report(live_langfuse: bool):
    from evals.fm_decision.run import fetch_fund_manager_traces

    traces = fetch_fund_manager_traces()
    assert traces, "Langfuse 应返回 fund_manager trace（空即异常）"

    result = run_offline(traces)
    agg, reason = result["aggregate"], result["reason_complete"]

    # 分布抽样参考（不设占比下限，取证为 approve ~53%/return ~30%/reject ~17%）
    total = agg["total"]
    assert total >= 10, "FM 决策样本应足够支撑分布统计"
    counts = agg["counts"]
    assert set(counts) & {"approve", "return", "reject"}, "分布应覆盖三档决策"

    # 理由完整门禁：live 数据缺失理由即失败（防无理由拒绝退化）
    assert reason["missing_count"] == 0, f"存在缺失理由的决策: {reason['missing'][:5]}"

    # 产物落盘
    from datetime import datetime

    out = Path("reports") / f"fm-decision-report-{datetime.now():%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_report(agg, result["veto_recall"], reason, result["parse_fail"]),
        encoding="utf-8",
    )
    print(f"[E2E][FM] 分布: {counts} 总 {total}；报告: {out}")
