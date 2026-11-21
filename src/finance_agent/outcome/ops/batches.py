"""回测批 / 泄漏探针 / 健康检查的进程内封装(delta add-eval-ops-console Task 4)。

供 ``/api/v1/ops`` 的后台线程调用(端点只做参数校验、门禁拒绝与 202 派发)。
**薄编排、零复制**:全部计算与判定 import 既有 ``evals.*`` 实现——抽样
(``sampling.stratified_sample``)、干净窗口(``report.assert_clean_window``)、批级探针
(``run_backtest.run_batch_probe``)、回放一致性(``replay.replay_with_consistency``)、
绩效与四段披露(``run_backtest.run_backtest``)、报告渲染(``report.render_backtest_report_md``)、
健康读数(``outcome.health.collect_outcome_health``);目录与门禁常量亦取自既有 CLI 模块,
不另定义副本(防口径漂移,见 ``tests/outcome/test_ops_batches.py::TestConsistencyGuards``)。

**formal 批门禁顺序(硬约束,任何回放之前)**:
``assert_preregistered``(缺/无效 → ``MissingPreregistrationError``)
→ 指数取数 + ``stratified_sample``
→ ``assert_clean_window``(不过 → ``CleanWindowError``)
→ 批级探针 → ``run_backtest``。
门禁不过一律**抛错**(端点翻 409),绝不降级为 pathway 静默跑批、绝不先跑回放。
:func:`prepare_backtest` 即「同步前置门禁 + 取数」,供端点在任何回放之前调用;
其返回值经 ``prepared=`` 透传回本模块(不重复抽样/不重复取指数)。

**落盘**:md 落 ``evals/backtest/results``(入库,报告注册表扫描面)、JSON 全量落
``reports/backtest``(gitignored);同名(同秒二次发起)自动加 ``-2``/``-3`` 后缀,
不覆盖上一份。返回的报告 dict 供端点裁剪成 ``job_runs.summary``(完整报告在 md/json)。

**离线可驱动**:``client`` / ``replay_fn`` / ``llm`` 均为注入缝(默认真实实现),
测试零网络零 LLM。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.backtest.leakage_probe import run_leakage_probe
from evals.backtest.replay import replay_with_consistency
from evals.backtest.report import assert_clean_window, probe_state, render_backtest_report_md
from evals.backtest.run_backtest import (
    BATCH_KINDS,
    JSON_REPORT_DIR,
    MD_REPORT_DIR,
    PREREGISTER_DIR,
    PREREGISTER_NAME_CONTAINS,
    run_backtest,
    run_batch_probe,
)
from evals.backtest.sampling import stratified_sample
from evals.causal_ablation.preregister import (
    OUTCOME_REQUIRED_FIELDS,
    assert_preregistered,
)
from evals.outcome.caliber import PRIMARY_WINDOW_DAYS
from evals.outcome.health import (
    MAX_UNRESOLVABLE_RATE,
    MIN_SETTLEMENT_SUCCESS_RATE,
    collect_outcome_health,
)

from finance_agent.data.market_caliber import settlement_series
from finance_agent.outcome.track_record.job import BENCHMARK_CODE

logger = logging.getLogger(__name__)

# 取数天数:与 ``run_backtest`` CLI main() 的 1500 日一致(前视截断需足够历史,
# 250 日默认值会让历史决策日截断后 K 线为空——试跑缺陷 #104 的口径)。
KLINE_DAYS = 1500

# 健康检查门禁:前三项为阻断门禁(判据复用 collect_outcome_health 的 checks,不另算),
# 第四项记账完整率为披露项(既有健康检查不以其阻断,故 passed=None)。
_BLOCKING_GATES = (
    (
        "settlement_success",
        "结算成功率",
        "settlement_success_rate",
        f">= {MIN_SETTLEMENT_SUCCESS_RATE}",
        "settlement_success",
    ),
    (
        "unresolvable",
        "不可判定率",
        "unresolvable_rate",
        f"<= {MAX_UNRESOLVABLE_RATE}",
        "unresolvable",
    ),
    ("integrity", "快照完整性", "integrity_mismatches", "== 0", "integrity"),
)
_BOOKKEEPING_GATE = ("bookkeeping", "记账完整率", "bookkeeping_completeness", None, None)


class CleanWindowError(RuntimeError):  # noqa: N818 —— 名称是实施计划约定的跨任务接口
    """正式批干净窗口判定不通过(门禁拒绝;端点翻 409,不得降级静默跑批)。"""


# ── 客户端注入缝 ──


def _default_client() -> Any:
    """默认取数客户端(延迟构造:测试注入 ``client`` 或 patch 本函数即零网络)。"""
    from finance_agent.data.akshare_client import AKShareClient

    return AKShareClient()


def _client(client: Any | None) -> Any:
    return client if client is not None else _default_client()


# ── 前置门禁 + 取数(formal 批在任何回放之前)──


def prepare_backtest(
    *,
    batch_kind: str,
    codes: list[str],
    per_regime: int = 10,
    as_of: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """formal 批同步前置门禁 + 取数;**只读**(不落报告、不跑回放)。

    顺序(硬约束):``assert_preregistered`` → 指数 K 线 + ``stratified_sample``
    → ``assert_clean_window``。任一不过 → 抛 ``MissingPreregistrationError`` /
    ``CleanWindowError``(端点翻 409),此时**没有任何回放、也没有逐标的取数**。

    Returns:
        ``{"batch_kind","as_of","codes","sample","index_kline","preregistration",
        "clean_window"}``,供 :func:`run_backtest_task` 经 ``prepared=`` 透传。
    """
    if batch_kind not in BATCH_KINDS:
        raise ValueError(f"未知批次类型 {batch_kind!r}（可选 {BATCH_KINDS}）")
    pool = [str(code).strip() for code in codes if str(code).strip()]
    if not pool:
        raise ValueError("codes 不得为空")
    day = as_of or datetime.now().strftime("%Y-%m-%d")
    preregistration = None
    if batch_kind == "formal":
        # 门禁①:有效预登记(缺文档/字段不齐 → 端点 409,不降级 pathway)
        preregistration = assert_preregistered(
            PREREGISTER_DIR,
            name_contains=PREREGISTER_NAME_CONTAINS,
            required_fields=OUTCOME_REQUIRED_FIELDS,
        )
    index_kline = _client(client).fetch_index_kline(BENCHMARK_CODE, days=KLINE_DAYS)
    # 标的池不足 per_regime / 指数历史未覆盖三 regime → ValueError(端点翻 422)
    sample = stratified_sample(index_kline, pool, per_regime=per_regime)
    clean_window = None
    if batch_kind == "formal":
        # 门禁②:干净窗口(交易日口径;基准不可得/空样本 → 保守判不通过)
        clean_window = assert_clean_window(
            [str(item.get("decision_date", "")) for item in sample],
            as_of=day,
            window_days=PRIMARY_WINDOW_DAYS,
            benchmark=index_kline,
        )
        if not clean_window.get("passed", False):
            raise CleanWindowError(str(clean_window.get("reason") or "干净窗口判定不通过"))
    return {
        "batch_kind": batch_kind,
        "as_of": day,
        "codes": pool,
        "sample": sample,
        "index_kline": index_kline,
        "preregistration": preregistration,
        "clean_window": clean_window,
    }


# ── 回测批 ──


def _unique_stem(directory: Path, stem: str) -> str:
    """同名报告消歧:md 已存在则追加 ``-2``/``-3``…(同秒二次发起不覆盖上一份)。"""
    candidate, n = stem, 2
    while (directory / f"{candidate}.md").exists():
        candidate = f"{stem}-{n}"
        n += 1
    return candidate


def _write_reports(report: dict[str, Any], *, batch_kind: str) -> dict[str, str]:
    """落盘:md(入库,注册表扫描面)+ JSON 全量(gitignored);返回仓库相对路径。"""
    md_dir = Path(MD_REPORT_DIR)
    md_dir.mkdir(parents=True, exist_ok=True)
    stem = _unique_stem(md_dir, f"{batch_kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    md_path = md_dir / f"{stem}.md"
    # 渲染内含 assert_outcome_report 自校(缺 status 头即抛,不落半成品报告)
    md_path.write_text(render_backtest_report_md(report, name=stem), encoding="utf-8")
    json_dir = Path(JSON_REPORT_DIR)
    json_dir.mkdir(parents=True, exist_ok=True)
    json_path = json_dir / f"{stem}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("回测报告已落盘:%s(md) / %s(json)", md_path, json_path)
    return {"md": md_path.as_posix(), "json": json_path.as_posix()}


def run_backtest_task(
    *,
    batch_kind: str,
    codes: list[str],
    per_regime: int = 10,
    repeats: int = 3,
    as_of: str | None = None,
    client: Any = None,
    replay_fn: Callable[..., dict] | None = None,
    prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """跑一批回测并落 md/json,返回报告 dict(``run_backtest`` 原样 + ``report_paths``)。

    ``prepared`` 为端点在**任何回放之前**已同步过的前置门禁结果;缺省时本函数自己调
    :func:`prepare_backtest`(直接调用方同样受「预登记 → 干净窗口」门禁保护)。
    ``replay_fn`` 缺省 ``replay_with_consistency``。
    """
    if batch_kind not in BATCH_KINDS:
        raise ValueError(f"未知批次类型 {batch_kind!r}（可选 {BATCH_KINDS}）")
    plan = (
        prepared
        if prepared is not None
        else prepare_backtest(
            batch_kind=batch_kind, codes=codes, per_regime=per_regime, as_of=as_of, client=client
        )
    )
    data_client = _client(client)
    sample = list(plan["sample"])
    if not sample:
        raise ValueError("抽样结果为空:无样本可回放(不得产出空批报告)")
    # 逐标的 K 线:结算/基线同源后复权(hfq),与 CLI 一致
    klines = {
        str(code): settlement_series(data_client, str(code), days=KLINE_DAYS)
        for code in plan["codes"]
    }
    probe = None
    if batch_kind == "formal":
        # 批内 distinct 决策日各探一次 → 最差态汇总(既有实现;formal 缺读数会被 run_backtest 拒)
        probe = run_batch_probe(
            list(plan["codes"]),
            [str(item.get("decision_date", "")) for item in sample],
            window_days=PRIMARY_WINDOW_DAYS,
            client=data_client,
        )
    report = run_backtest(
        sample,
        klines,
        benchmark_kline=plan["index_kline"],
        repeats=repeats,
        batch_kind=batch_kind,
        as_of=plan["as_of"],
        window_days=PRIMARY_WINDOW_DAYS,
        probe=probe,
        preregistration=plan["preregistration"],
        clean_window=plan["clean_window"],
        replay_fn=replay_fn or replay_with_consistency,
    )
    # report_paths 在落盘后追加:JSON 产物是纯报告(不含自指路径),summary 侧据它指路完整报告
    report["report_paths"] = _write_reports(report, batch_kind=batch_kind)
    return report


# ── 泄漏探针单跑 ──


def run_probe_task(
    *,
    codes: list[str],
    decision_date: str,
    window_days: int = PRIMARY_WINDOW_DAYS,
    n_tickers: int = 10,
    seed: int = 42,
    client: Any = None,
    llm: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """候选窗口的探针单跑:既有 ``run_leakage_probe`` 读数 + 三态 ``state`` 标注。

    ``state`` 复用 ``evals.backtest.report.probe_state`` 的取值域
    (measurable / downgraded / unmeasurable):不可测态 ``direction_hit_rate`` 为
    ``None``(「不可测」),与全答错的 ``0.0`` 严格可分——界面据此渲染三态。

    单跑只有一个窗口,故按请求入参合成 ``probe_window=[decision_date]`` 与一条
    ``per_window``(形状对齐 ``report.aggregate_probes`` 的逐窗口条目:每项带**它自己**
    的窗口 + 读数),让运行历史与批报告的窗口披露字段同形;``window_days`` 随条目披露。
    """
    pool = [str(code).strip() for code in codes if str(code).strip()]
    if not pool:
        raise ValueError("codes 不得为空")
    reading = run_leakage_probe(
        pool,
        decision_date,
        window_days=window_days,
        n_tickers=n_tickers,
        seed=seed,
        client=_client(client),
        llm=llm,
    )
    state = probe_state(reading)
    window = [decision_date]
    per_window = [
        {
            "probe_window": list(window),
            "window_days": window_days,
            "state": state,
            "probe_n": reading.get("probe_n"),
            "direction_hit_rate": reading.get("direction_hit_rate"),
            "magnitude_hit_rate": reading.get("magnitude_hit_rate"),
            "event_hit_rate": reading.get("event_hit_rate"),
            "unknown_ratio": reading.get("unknown_ratio"),
            "downgraded": bool(reading.get("downgraded")),
        }
    ]
    return {
        **reading,
        "state": state,
        "probe_window": list(window),
        "per_window": per_window,
    }


# ── 健康检查 ──


def _gate_row(
    *,
    gate_id: str,
    label: str,
    value: Any,
    threshold: str | None,
    passed: bool | None,
) -> dict[str, Any]:
    """门禁行:读数缺失 → None(「无读数」,不折算 0/100%);passed=None 表示披露项。"""
    if value is None:
        reason = "无读数（样本不足或读数缺失）"
    elif passed is None:
        reason = "披露项（非阻断门禁）"
    else:
        reason = f"实测 {value}；阈值 {threshold}" + ("" if passed else " → 不通过")
    return {
        "id": gate_id,
        "label": label,
        "value": value,
        "threshold": threshold,
        "passed": passed,
        "reason": reason,
    }


def _no_readings_payload(path: Path, *, error: str) -> dict[str, Any]:
    """「无读数」载荷(DB 缺失 / 缺 predictions 表共用同一形状):门禁全 None,不折算 0/100%。"""
    specs = (*_BLOCKING_GATES, _BOOKKEEPING_GATE)
    return {
        "db": str(path),
        "available": False,
        "error": error,
        "gates": [
            _gate_row(gate_id=gate_id, label=label, value=None, threshold=threshold, passed=None)
            for gate_id, label, _key, threshold, _check in specs
        ],
        "passed": None,
        "readings": None,
    }


def run_health_task(*, db_path: str | Path | None = None) -> dict[str, Any]:
    """outcome 收口健康读数 + 门禁表格(JSON 可序列化,供 ``job_runs.summary``)。

    门禁四项:结算成功率 / 不可判定率 / 快照完整性(阻断门禁,判据复用
    ``collect_outcome_health`` 的 ``checks``)+ 记账完整率(披露项)。读数缺失
    (无已结算样本等)→ ``value=None`` + 原因「无读数」,不冒充 0/100%。
    DB 不存在、或 DB 存在但缺 ``predictions`` 表(空库/半初始化库)→ 同一「无读数」
    形状(``available=False`` + ``error`` 说明原因),端点不 500。
    """
    path = Path(db_path) if db_path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))
    specs = (*_BLOCKING_GATES, _BOOKKEEPING_GATE)
    if not path.exists():
        return _no_readings_payload(path, error=f"DB 不存在:{path}(无读数,不得折算 0/100%)")
    try:
        readings = collect_outcome_health(path)
    except sqlite3.OperationalError as exc:
        # 只有「缺表」是本层可如实降级的情形(空库/半初始化库);其余 OperationalError
        # (锁超时/磁盘故障)是环境故障,原样上抛交端点落 failed,不冒充「无读数」。
        if "no such table" not in str(exc).lower():
            raise
        logger.warning("健康检查读数不可得(缺表): %s: %s", path, exc)
        return _no_readings_payload(path, error=f"DB 缺表({exc}):无读数,不得折算 0/100%")
    checks = readings.get("checks") or {}
    gates = [
        _gate_row(
            gate_id=gate_id,
            label=label,
            value=readings.get(key),
            threshold=threshold,
            passed=bool(checks.get(check)) if check else None,
        )
        for gate_id, label, key, threshold, check in specs
    ]
    return {
        "db": str(path),
        "available": True,
        "error": None,
        "gates": gates,
        "passed": bool(readings.get("passed")),
        "readings": readings,
    }
