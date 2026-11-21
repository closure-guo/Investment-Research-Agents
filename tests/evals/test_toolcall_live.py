"""add-toolcall-evaluation nightly @live：拉真实 Langfuse 跑工具调用评估。

无 LANGFUSE key 跳过；报告落 reports/；金标样本门禁由离线 fixtures 承载，
本用例为生产流量监控（分布 + 违例清单，不因方差硬失败）。
"""

from pathlib import Path

import pytest
from evals.toolcall.measure import evaluate, render_report

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_langfuse() -> bool:
    # @live 统一门禁（issue #605）：显式 opt-in（RUN_LIVE=1）优先，凭据在场不激活；
    # 纯 key 判据在全量套件中被 src.api import 时 load_dotenv 的 .env 键绕过
    from tests.evals._live_guard import require_live_opt_in

    return require_live_opt_in()


def test_toolcall_live_report(live_langfuse: bool):
    from evals.toolcall.run import fetch_toolcall_traces

    traces = fetch_toolcall_traces(limit_pages=2)
    assert traces, "Langfuse 应返回 react_loop traces"

    report, sequences = evaluate(traces)
    # 埋点上线后应有工具调用被观测到（近期 E2E stub trace 可能无调用——不硬断言，
    # 只保证无异常且报告可生成）
    from datetime import datetime

    out = Path("reports") / f"toolcall-report-{datetime.now():%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(report), encoding="utf-8")
    print(f"[E2E][TOOLCALL] traces {len(traces)}，调用 {report.total_calls}；报告: {out}")
