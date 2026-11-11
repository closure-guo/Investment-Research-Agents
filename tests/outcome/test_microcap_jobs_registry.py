"""三个新 job 的注册表接线（add-microcap-lane-analysis T6）。"""

from finance_agent.outcome.ops.jobs import JOB_FUNCS, JOB_IDS, JOB_LABELS, SCHEDULES


def test_job_ids_registered():
    for job in ("paper_execute_microcap", "paper_settle_microcap", "auto_pool_analysis_microcap"):
        assert job in JOB_IDS
        assert job in JOB_LABELS
        assert job in JOB_FUNCS
        assert job in SCHEDULES


def test_schedule_times():
    # 16:12 → hour=16, minute=12（brief 原文误写 hour==12，与本 brief 的 schedule dict
    # 及「16:12 在 16:10 与 16:15 之间」的时序锚点矛盾，按意图修正为 hour/minute 双钉）
    assert SCHEDULES["paper_execute_microcap"]["hour"] == 16
    assert SCHEDULES["paper_execute_microcap"]["minute"] == 12
    assert SCHEDULES["paper_settle_microcap"]["hour"] == 16
    assert SCHEDULES["paper_settle_microcap"]["minute"] == 24
    assert SCHEDULES["auto_pool_analysis_microcap"]["hour"] == 16
    assert SCHEDULES["auto_pool_analysis_microcap"]["minute"] == 47


def test_job_ids_order_ascending():
    """日批链顺序断言：16:12 在 16:10 与 16:15 之间，16:24 在 16:22 与 16:45 之间。"""

    def pos(job):
        return JOB_IDS.index(job)

    assert pos("paper_execute_daily") < pos("paper_execute_microcap") < pos("earnings_triage_daily")
    assert (
        pos("auto_pool_analysis_daily")
        < pos("paper_settle_microcap")
        < pos("microcap_screening_daily")
    )
    assert pos("microcap_screening_daily") < pos("auto_pool_analysis_microcap")
