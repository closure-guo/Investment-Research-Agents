"""Shared test fixtures: synthetic financial data mimicking AKShare DataFrames."""

import os

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _isolate_reports_dir(request, tmp_path):
    """为 live E2E 测试隔离 REPORTS_DIR，避免污染 reports/ 目录。

    generate_file 节点（5 层管线终点）会向 REPORTS_DIR 写 .docx/.pptx。
    E2E 测试（test_5layer_pipeline.py / Playwright pipeline 场景）反复跑同一
    只股票会在 reports/ 堆积大量垃圾文件（见 docs/incidents 约定）。

    对带 `live` mark 的测试注入临时目录 tmp_path/e2e-reports，跑完自动清理；
    非 live 测试（单元/集成）保持原样，不改变其 REPORTS_DIR 行为。
    """
    marker = request.node.get_closest_marker("live")
    if marker is None:
        yield
        return
    old = os.environ.get("REPORTS_DIR")
    os.environ["REPORTS_DIR"] = str(tmp_path / "e2e-reports")
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("REPORTS_DIR", None)
        else:
            os.environ["REPORTS_DIR"] = old


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """probe 缓存逐测试重置（#77 测试卫生）：单例跨用例残留会让 resolver 用例 order-dependent。

    resolver 解析会合并 probe 缓存事实（capability 以 probe 为准、未命中标
    probe_required），一旦某用例写入缓存，后续用例的解析结果就随执行顺序漂移。
    此前只在个别文件手工 reset，新增用例漏 reset 即引入顺序依赖。
    """
    from finance_agent.llm.probe_cache import _reset_probe_cache_for_tests

    _reset_probe_cache_for_tests()
    try:
        yield
    finally:
        _reset_probe_cache_for_tests()


@pytest.fixture(autouse=True)
def _isolate_runtime_dbs(request, tmp_path):
    """运行时数据库隔离（#133 / incident 031）：非 live 测试一律不得写开发库。

    背景：非 live 测试也会走真实落库挂点——tests/test_deep_trace_root.py 调
    真实 api._run_graph_streaming（仅图被 patch），未隔离 DB 时每次跑套件都往
    开发库 data/sessions.db 写一行 600519 buy/0.7 假决策（9/7–9/22 累计 66 行）。

    三处路径都要改：
    ① 环境变量 SESSIONS_DB_PATH——predictions 侧 model._default_db_path() 调用时读取；
    ② session_store._DB_PATH——模块导入期冻结的常量，只能直接改模块属性；
    ③ 环境变量 SCREENING_DB_PATH——screening/store._default_db_path() 调用期读取
    （change-9 评审：未来用例省略 db_path 时同样不得写开发库 data/screening.db）。

    **live 测试豁免**：live 是手动发起的真实测量（如 hallucination live 从开发库
    取样历史报告），按设计读开发库；其写入即真实运行产物。个别非 live 测试自行
    monkeypatch 具体路径时，其 patch 覆盖本 fixture 值并在测试后恢复，不受影响。
    REPORTS_DIR 的 live 隔离（_isolate_reports_dir）是同类先例。
    """
    if request.node.get_closest_marker("live") is not None:
        yield None
        return

    tmp_db = tmp_path / "runtime-sessions.db"
    old_env = os.environ.get("SESSIONS_DB_PATH")
    os.environ["SESSIONS_DB_PATH"] = str(tmp_db)

    from finance_agent import session_store

    old_path = session_store._DB_PATH
    session_store._DB_PATH = tmp_db
    # 隔离库必须自带 schema：非 live 测试会经真实挂点写两张表
    # （session_store → sessions；ingest → predictions），空文件会报 no such table
    session_store.init_db()
    from finance_agent.outcome.track_record.model import init_predictions

    init_predictions(tmp_db)
    # ③ SCREENING_DB_PATH：env 调用期读取，无需预建表（store 各入口自身幂等 init）
    old_screening_env = os.environ.get("SCREENING_DB_PATH")
    os.environ["SCREENING_DB_PATH"] = str(tmp_path / "runtime-screening.db")
    try:
        yield tmp_db
    finally:
        session_store._DB_PATH = old_path
        if old_env is None:
            os.environ.pop("SESSIONS_DB_PATH", None)
        else:
            os.environ["SESSIONS_DB_PATH"] = old_env
        if old_screening_env is None:
            os.environ.pop("SCREENING_DB_PATH", None)
        else:
            os.environ["SCREENING_DB_PATH"] = old_screening_env


@pytest.fixture
def balance_sheet():
    """资产负债表 fixture — 3 年数据，圆整数字便于手算验证。"""
    return pd.DataFrame(
        {
            "报告日": ["20241231", "20231231", "20221231"],
            "货币资金": [200.0, 180.0, 150.0],
            "存货": [100.0, 90.0, 80.0],
            "流动资产合计": [500.0, 450.0, 400.0],
            "固定资产净值": [300.0, 280.0, 260.0],
            "累计折旧": [120.0, 100.0, 80.0],
            "非流动资产合计": [500.0, 450.0, 400.0],
            "资产总计": [1000.0, 900.0, 800.0],
            "短期借款": [80.0, 70.0, 60.0],
            "应付账款": [60.0, 50.0, 45.0],
            "应收账款": [40.0, 35.0, 30.0],
            "一年内到期的非流动负债": [20.0, 15.0, 10.0],
            "流动负债合计": [300.0, 280.0, 260.0],
            "长期借款": [50.0, 40.0, 30.0],
            "应付债券": [30.0, 20.0, 20.0],
            "非流动负债合计": [100.0, 70.0, 60.0],
            "负债合计": [400.0, 350.0, 320.0],
            "所有者权益(或股东权益)合计": [600.0, 550.0, 480.0],
            "实收资本(或股本)": [125.0, 125.0, 125.0],
            "未分配利润": [200.0, 170.0, 140.0],
        }
    )


@pytest.fixture
def income_statement():
    """利润表 fixture — 3 年数据。"""
    return pd.DataFrame(
        {
            "报告日": ["20241231", "20231231", "20221231"],
            "营业收入": [1000.0, 900.0, 800.0],
            "营业成本": [600.0, 550.0, 500.0],
            "销售费用": [50.0, 45.0, 40.0],
            "管理费用": [60.0, 55.0, 50.0],
            "研发费用": [30.0, 25.0, 20.0],
            "财务费用": [22.0, 20.0, 18.0],
            "利息费用": [20.0, 18.0, 16.0],
            "营业利润": [200.0, 180.0, 160.0],
            "利润总额": [200.0, 180.0, 160.0],
            "所得税费用": [30.0, 27.0, 24.0],
            "净利润": [170.0, 153.0, 136.0],
            "归属于母公司所有者的净利润": [168.0, 151.0, 134.0],
        }
    )


@pytest.fixture
def cash_flow():
    """现金流量表 fixture — 3 年数据。"""
    return pd.DataFrame(
        {
            "报告日": ["20241231", "20231231", "20221231"],
            "经营活动产生的现金流量净额": [250.0, 220.0, 200.0],
            "购建固定资产、无形资产和其他长期资产所支付的现金": [
                80.0,
                70.0,
                60.0,
            ],
            "投资活动产生的现金流量净额": [-100.0, -90.0, -80.0],
            "分配股利、利润或偿付利息所支付的现金": [50.0, 45.0, 40.0],
            "筹资活动产生的现金流量净额": [-30.0, -20.0, -10.0],
        }
    )


@pytest.fixture
def indicators():
    """AKShare 预计算财务指标 fixture — 3 年数据。"""
    return pd.DataFrame(
        {
            "日期": ["2024-12-31", "2023-12-31", "2022-12-31"],
            "销售毛利率(%)": [40.0, 38.89, 37.5],
            "销售净利率(%)": [17.0, 17.0, 17.0],
            "净资产收益率(%)": [28.33, 27.82, 28.33],
            "加权净资产收益率(%)": [28.33, 27.82, 28.33],
            "总资产净利润率(%)": [17.0, 17.0, 17.0],
            "存货周转率(次)": [6.32, 6.47, 6.58],
            "应收账款周转率(次)": [None, None, None],
            "总资产周转率(次)": [1.05, 1.06, 1.05],
            "流动比率": [1.67, 1.61, 1.54],
            "速动比率": [1.33, 1.29, 1.23],
            "资产负债率(%)": [40.0, 38.89, 40.0],
            "利息支付倍数": [11.0, 11.0, 11.0],
        }
    )
