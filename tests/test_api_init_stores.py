"""#57 子项1:import 期建表失败降级——单一存储初始化失败不得炸死 API 启动。

init_db / init_decision_log / init_predictions / init_track_record_tables /
init_cohort_runs / init_events_db / init_pool_analysis_runs 任一抛错仅记 ERROR
并继续;DB 不可写场景下后续写入本就有 per-call 兜底,进程活着才能服务健康检查
与只读路由。
"""

import logging

import finance_agent.api as api

_API_LOGGER = "finance_agent.api"

# api._init_stores 注册的全部 (函数名, 存储标签)（顺序与注册元组一致;
# add-microcap-lane-analysis 评审 Finding 1:pool 迁移须在启动链发生,
# 否则旧库首个 16:22 作业前任何 ``AND pool='main'`` 裸 SQL 都是 OperationalError）
_INIT_STORES = (
    ("init_db", "sessions"),
    ("init_decision_log", "decision_log"),
    ("init_predictions", "predictions"),
    ("init_track_record_tables", "track_record"),
    ("init_cohort_runs", "cohort_runs"),
    ("init_events_db", "events"),
    ("init_pool_analysis_runs", "pool_analysis_runs"),
)

_EXPECTED_LABELS = [label for _, label in _INIT_STORES]


class TestInitStoresDegradation:
    def test_failing_init_logs_error_and_continues_remaining(self, monkeypatch, caplog):
        calls: list[str] = []

        def _boom():
            calls.append("decision_log")
            raise OSError("disk read-only")

        def _ok(label):
            return lambda: calls.append(label)

        for fn_name, label in _INIT_STORES:
            monkeypatch.setattr(api, fn_name, _ok(label))
        monkeypatch.setattr(api, "init_decision_log", _boom)

        with caplog.at_level(logging.ERROR, logger=_API_LOGGER):
            api._init_stores()  # 不抛

        assert calls == _EXPECTED_LABELS
        assert "decision_log" in caplog.text
        assert "disk read-only" in caplog.text

    def test_all_healthy_no_error(self, monkeypatch, caplog):
        calls: list[str] = []
        for fn_name, label in _INIT_STORES:
            monkeypatch.setattr(api, fn_name, lambda lab=label: calls.append(lab))
        with caplog.at_level(logging.ERROR, logger=_API_LOGGER):
            api._init_stores()
        assert calls == _EXPECTED_LABELS
        assert caplog.text == ""
