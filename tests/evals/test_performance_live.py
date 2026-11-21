"""add-latency-cost-regression nightly @live：拉真实 Langfuse 跑性能度量。

无 LANGFUSE key 跳过（与 @live 惯例一致）；报告落 reports/。
"""

from pathlib import Path

import pytest
from evals.performance.measure import aggregate, compare_with_baseline, render_report

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_langfuse() -> bool:
    # @live 统一门禁（issue #605）：显式 opt-in（RUN_LIVE=1）优先，凭据在场不激活；
    # 纯 key 判据在全量套件中被 src.api import 时 load_dotenv 的 .env 键绕过
    from tests.evals._live_guard import require_live_opt_in

    return require_live_opt_in()


def test_perf_live_report(live_langfuse: bool):
    from evals.performance.run import fetch_perf_traces

    traces = fetch_perf_traces()
    assert traces, "Langfuse 应返回 traces（空即异常）"
    agg, samples = aggregate(traces)
    assert agg.total >= 1
    # 有 latency 的样本应统计到分位数
    assert any(s.latency_s is not None for s in samples)

    from evals.performance.measure import load_baseline

    baseline = load_baseline(Path("docs/evals/perf-baseline.json"))
    compares = compare_with_baseline(agg, baseline)
    report = render_report(agg, compares, False, baseline.get("as_of"))

    from datetime import datetime

    out = Path("reports") / f"perf-report-{datetime.now():%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(
        f"[E2E][PERF] 样本 {agg.total}（quick {agg.by_mode.get('quick', 0)}/deep {agg.by_mode.get('deep', 0)}）；报告: {out}"
    )
