"""#57 子项1:import 期建表失败降级——单一存储初始化失败不得炸死 API 启动。

init_db / init_decision_log / init_predictions / init_track_record_tables /
init_cohort_runs 任一抛错仅记 ERROR 并继续;DB 不可写场景下后续写入本就有
per-call 兜底,进程活着才能服务健康检查与只读路由。
"""

import logging

import finance_agent.api as api

_API_LOGGER = "finance_agent.api"


class TestInitStoresDegradation:
    def test_failing_init_logs_error_and_continues_remaining(self, monkeypatch, caplog):
        calls: list[str] = []

        def _boom():
            calls.append("decision_log")
            raise OSError("disk read-only")

        def _ok(label):
            return lambda: calls.append(label)

        monkeypatch.setattr(api, "init_db", _ok("sessions"))
        monkeypatch.setattr(api, "init_decision_log", _boom)
        monkeypatch.setattr(api, "init_predictions", _ok("predictions"))
        monkeypatch.setattr(api, "init_track_record_tables", _ok("track_record"))
        monkeypatch.setattr(api, "init_cohort_runs", _ok("cohort_runs"))

        with caplog.at_level(logging.ERROR, logger=_API_LOGGER):
            api._init_stores()  # 不抛

        assert calls == ["sessions", "decision_log", "predictions", "track_record", "cohort_runs"]
        assert "decision_log" in caplog.text
        assert "disk read-only" in caplog.text

    def test_all_healthy_no_error(self, monkeypatch, caplog):
        calls: list[str] = []
        for name in (
            "init_db",
            "init_decision_log",
            "init_predictions",
            "init_track_record_tables",
            "init_cohort_runs",
        ):
            monkeypatch.setattr(api, name, lambda n=name: calls.append(n))
        with caplog.at_level(logging.ERROR, logger=_API_LOGGER):
            api._init_stores()
        assert len(calls) == 5
        assert caplog.text == ""
