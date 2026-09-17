"""P1 注入法反幻觉消融 pilot 跑批驱动（预登记 `evals/ablation/preregister/2026-09-16-p1-injection-pilot.md`）。

**薄壳**（同 `ablation_pilot.py` 的驱动约定）：实验逻辑全在库侧
`evals/causal_ablation/pilot_runner.py`——本脚本只保留四件事：
1. 预登记门禁（`assert_launch_allowed`，跑批第一动作，无预登记拒绝启动）；
2. 材料落盘/读取（`--materials-only` 一标的跑一趟 analysts 变体并存产物；
   跑批路径**不自动生成材料**——真跑型成本须单独申报）；
3. 断点续跑（每完成一个单元落 `resume.json`，重启跳过已完成 unit_id，不重烧 token）；
4. 成本记账（离线腿恒 0；真跑腿按 usage meter 差量归属，meter 缺失如实记 None）。

产物（全部落在 `--out-dir`）：
- `units.jsonl`：单元级判定记录（`units.write_units`，method=code）；
- `p1-injection-pilot-<ts>.json`：库侧 `pilot_report` 全量报告（分型逃逸率 / 校准判词 /
  阳性对照 / 预算分型 / 终裁工作清单 / 每单元两态载荷）；
- `resume.json`：续跑台账。

用法：
    # 1) 材料（真跑型成本的唯一入口，人工触发）
    uv run python tests/scripts/p1_injection_pilot.py --materials-only
    # 2) 离线腿（零 LLM）
    uv run python tests/scripts/p1_injection_pilot.py --legs offline
    # 3) 限量真跑腿
    uv run python tests/scripts/p1_injection_pilot.py --legs real --real-run-limit 6
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation.adjudication import adjudicated_case_ids  # noqa: E402
from evals.causal_ablation.injection import COST_CLASS, POLLUTION_TYPES  # noqa: E402
from evals.causal_ablation.preregister import MissingPreregistrationError  # noqa: E402
from evals.causal_ablation.units import write_units  # noqa: E402

from evals.causal_ablation import pilot_runner as pr  # noqa: E402

# 预登记口径：10 标的 pilot（强度校准）→ 20 标的正式批；单元数 = 标的数 × 8 类 × 4 实例
TICKERS: tuple[str, ...] = (
    "600519",
    "000001",
    "002415",
    "300750",
    "601318",
    "002594",
    "600036",
    "000858",
    "601899",
    "002304",
)
INSTANCES = 4
LEGS = "both"
REAL_RUN_LIMIT = 12  # 真跑型单元上限（每单元 = 2 次图运行：机制开/关同一污染输入）
MATERIALS_DIR = Path("reports/ablation/p1/materials")
# 终裁清单（回填「真逃逸」后 rate 出数；文件不存在或未回填 → rate 保持 None）
ADJUDICATION_CSV = Path("tests/validation/2026-09-16-p1-adjudication-cases.csv")
OUT_DIR = Path("reports/ablation/p1")
PREREG_DIR = Path("evals/ablation/preregister")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P1 注入法反幻觉消融 pilot 跑批（离线重放 + 限量真跑）"
    )
    parser.add_argument(
        "--tickers", nargs="+", default=list(TICKERS), help="标的列表（记入产物 config）"
    )
    parser.add_argument("--instances", type=int, default=INSTANCES, help="每类污染的实例数")
    parser.add_argument(
        "--legs",
        choices=("offline", "real", "frozen", "both"),
        default=LEGS,
        help="跑哪些腿（默认 both；frozen = 真跑型走冻结重放：1 趟真跑 + 2 态确定性重放）",
    )
    parser.add_argument(
        "--real-run-limit",
        type=int,
        default=REAL_RUN_LIMIT,
        help="真跑型单元上限（0 = 不限）；每单元 = 机制开/关两次图运行",
    )
    parser.add_argument("--materials-dir", type=Path, default=MATERIALS_DIR, help="产物读写目录")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="报告/单元/续跑台账目录")
    parser.add_argument("--prereg-dir", type=Path, default=PREREG_DIR, help="预登记目录（门禁）")
    parser.add_argument(
        "--resume", type=Path, default=None, help="续跑台账路径（默认 out-dir/resume.json）"
    )
    parser.add_argument(
        "--pollution-types",
        nargs="+",
        default=None,
        help="只跑指定污染型（缺省全部 8 类；用于真跑型分批申报成本）",
    )
    parser.add_argument(
        "--adjudication-csv",
        type=Path,
        default=ADJUDICATION_CSV,
        help="人工终裁清单（读回「真逃逸」的 case_id → escape_rate 出数；缺省按固定路径）",
    )
    parser.add_argument(
        "--materials-only",
        action="store_true",
        help="只跑材料生成（每标的 1 趟 analysts 变体）并落产物，不跑批",
    )
    return parser.parse_args(argv)


# ── 材料生成（真跑型成本的唯一入口） ──


def derive_base_values(state: dict) -> dict[str, float]:
    """从管线 state 派生注入基值（revenue/price/growth/entry）。

    V1 启发式，逐产物人工复核：kline 末行收盘 → price/entry；三大报表最新报告日行里
    名称含「营业总收入/营业收入」的列 → revenue；growth_rates 的任一数值叶子/营业收入
    同比 → growth。缺项显式报错（缺基值构造不出确定性污染 payload）。
    """
    values: dict[str, float] = {}
    kline = state.get("kline")
    if kline is not None and len(kline) and "收盘" in getattr(kline, "columns", []):
        close = float(kline["收盘"].iloc[-1])
        values["price"] = close
        values["entry"] = close
    income = state.get("income_statement")
    if income is not None and len(income):
        for column in income.columns:
            name = str(column)
            if "营业总收入" in name or "营业收入" in name:
                values["revenue"] = float(income[column].iloc[0])
                break
    growth_rates = state.get("growth_rates") or {}
    for leaf in _iter_numbers(growth_rates):
        values["growth"] = leaf
        break
    missing = [k for k in pr.REQUIRED_BASE_VALUES if k not in values]
    if missing:
        raise pr.InjectionError(f"材料生成无法派生基值 {missing}：须人工补 base_values 后再跑批")
    return values


def _iter_numbers(obj: Any) -> list[float]:
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _iter_numbers(v)]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [float(obj)]
    return []


def product_from_state(state: dict, *, digest: str) -> dict:
    """管线终态 → 产物（snapshot 子集 + 分析师 claims/markdown + citation 通道快照）。"""
    reports: dict[str, dict] = {}
    for agent, report in (state.get("analyst_reports") or {}).items():
        if hasattr(report, "model_dump"):
            report = report.model_dump()
        if not isinstance(report, dict):
            continue
        reports[str(agent)] = {
            "claims": [
                c.model_dump() if hasattr(c, "model_dump") else c
                for c in report.get("claims") or []
            ],
            "markdown": str(report.get("markdown") or ""),
        }
    snapshot_keys = _snapshot_keys(state)
    snapshot = {k: state[k] for k in snapshot_keys}
    from evals.ablation import citation_buckets_from_state

    base_values = derive_base_values(state)
    # 注入基值同时写入 snapshot（apply_injection 的 set 键须存在，且产物自带基值）
    snapshot.update(base_values)
    return {
        "ticker": str(state.get("stock_code") or ""),
        "snapshot_digest": digest,
        "snapshot": snapshot,
        "analyst_reports": reports,
        "base_values": base_values,
        "citation": {
            "citation_pass": bool(state.get("citation_pass")),
            "buckets": citation_buckets_from_state(state),
            "fail_buckets": dict(state.get("citation_fail_buckets") or {}),
        },
    }


# 快照 = 除「非数据键」外的全部 state（**白名单改为排除式**）：白名单会随代码漂移漏键，
# 已实测两次同类丢面——① price_levels（A5 sanity 消费面，§15）；② compute 输出 6 键
# （anomalies/garp_result/health_score/price_levels/risk_metrics/traffic_lights，A1 补测
# 建单元时才发现）。排除项只留「不可序列化 / 与测量无关」的键。
_SNAPSHOT_EXCLUDE: frozenset[str] = frozenset(
    {
        "llm_config",
        "api_key",
        "callbacks",
        "writer",
        "query",
        "focus",
        "enable_web_search",
        "analyst_reports",  # 单独以 claims + markdown 形态落盘
        "file_paths",
    }
)


def _snapshot_keys(state: dict) -> list[str]:
    """快照键：state 全部数据键 - 排除项，并**强制**包含 compute 的全部输出键。

    compute 输出缺一即报错（不静默丢面）：A1/A5 两个补测都因缺面判 void，
    根因都是「白名单手抄漂移」。缺面在这里就拦住。
    """
    from finance_agent.nodes.compute import compute_metrics

    keys = sorted(k for k in state if k not in _SNAPSHOT_EXCLUDE and not k.startswith("_"))
    try:
        compute_keys = set(compute_metrics(dict(state)).keys())
    except Exception as exc:  # noqa: BLE001 - 原始输入不全：不阻断材料生成，但如实交代
        print(
            f"[警告] compute_metrics 在本次 state 上跑不动（{type(exc).__name__}: {exc}）："
            "跳过 compute 输出键完整性校验",
            flush=True,
        )
        return keys
    missing = sorted(compute_keys - set(keys))
    if missing:
        raise pr.ProductError(
            f"快照缺 compute 输出键 {missing}：材料会丢测量面（A5/A1 两次踩过），补进快照再落盘"
        )
    return keys


def _default_snapshot_builder(ticker: str) -> dict:
    from evals.ablation import build_snapshot

    return build_snapshot(ticker)


def _default_digest(snapshot: dict) -> str:
    from evals.ablation import snapshot_digest

    return snapshot_digest(snapshot)


def _default_analysts_runner(*, variant: str, snapshot: dict, query: str) -> dict:
    from evals.ablation import Variant, build_variant_graph

    graph = build_variant_graph(cast(Variant, variant))
    return dict(graph.invoke({**snapshot, "focus": query}))


def run_materials(
    *,
    tickers: Sequence[str],
    materials_dir: Path = MATERIALS_DIR,
    query: str = pr.DEFAULT_QUERY,
    snapshot_builder: Callable[[str], dict] | None = None,
    graph_runner: Callable[..., dict] | None = None,
    digest: Callable[[dict], str] | None = None,
) -> list[str]:
    """每标的跑一趟 analysts 变体并存产物（真跑，消耗 LLM；人工触发）。

    `snapshot_builder` / `graph_runner` / `digest` 可注入（通路验证用 fake，零 LLM）。
    """
    builder = snapshot_builder or _default_snapshot_builder
    runner = graph_runner or _default_analysts_runner
    # 默认走真实摘要（曾用占位 "injected"：产物里的 snapshot_digest 不可核验——
    # 「三变体输入一致」的审计声明因此悬空；回填脚本已按新口径补真摘要）
    digest_fn = digest or _default_digest

    written: list[str] = []
    for ticker in tickers:
        snapshot = builder(ticker)
        state = dict(runner(variant="analysts", snapshot=snapshot, query=query))
        product = product_from_state({**snapshot, **state}, digest=digest_fn(snapshot))
        product["ticker"] = product["ticker"] or ticker
        if not product["ticker"]:
            raise pr.ProductError(f"{ticker}: 产物缺 ticker（state 未带 stock_code）")
        paths = pr.save_product(pr.product_path(Path(materials_dir), ticker), product)
        written.append(paths["json"])
        print(f"[材料] {ticker} → {paths['json']}（{paths['pickle']}）", flush=True)
    return written


# ── 跑批 ──


def _load_resume(path: Path) -> dict:
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def run_pilot(
    *,
    tickers: Sequence[str],
    instances: int = INSTANCES,
    legs: str = LEGS,
    real_run_limit: int = REAL_RUN_LIMIT,
    materials_dir: Path = MATERIALS_DIR,
    out_dir: Path = OUT_DIR,
    prereg_dir: Path = PREREG_DIR,
    resume_path: Path | None = None,
    graph_runner: Callable[..., dict] | None = None,
    llm_meter: Callable[[], int] | None = None,
    adjudication_csv: Path | None = None,
    pollution_types: Sequence[str] | None = None,
) -> Path:
    """P1 跑批主流程：门禁 → 读产物 → 构用例 → 跑腿 → 落单元/报告。"""
    # 1) 预登记门禁：缺预登记拒绝启动（spec「缺预登记拒绝跑批」）
    prereg = pr.assert_launch_allowed(prereg_dir)
    print(f"[门禁] 预登记 {prereg.path} 有效（主指标：{prereg.fields.get('主指标')}）", flush=True)
    # 2) 产物（缺产物显式报错，不自动生成材料）
    products = pr.load_products(Path(materials_dir), tickers)
    resume_path = Path(resume_path) if resume_path else Path(out_dir) / "resume.json"
    resume = _load_resume(resume_path)
    units: list[dict] = list(resume.get("units") or [])
    done = set(resume.get("done_unit_ids") or [])
    real_cases = int(resume.get("real_run_cases") or 0)
    # 决策复用缓存（同标的同类实例共用一趟全图；键 = variant|query|ticker）
    decision_cache: dict[str, dict] = {}
    selected = (
        {"offline": True, "real": False}
        if legs == "offline"
        else (
            {"offline": False, "real": True}
            if legs in ("real", "frozen")
            else {"offline": True, "real": True}
        )
    )
    planned = {"offline_replay": 0, "real_run": 0}
    print(
        f"[跑批] {list(tickers)} × {len(POLLUTION_TYPES)} 类 × {instances} 实例"
        f" | 腿={legs} | 真跑上限={real_run_limit or '不限'} | 续跑起点：已完成 {len(done)} 单元",
        flush=True,
    )

    def _persist() -> None:
        resume_path.parent.mkdir(parents=True, exist_ok=True)
        resume_path.write_text(
            json.dumps(
                {"units": units, "done_unit_ids": sorted(done), "real_run_cases": real_cases},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    for ticker in tickers:
        product = products[ticker]
        for case in pr.build_pilot_cases(
            product, instances=instances, pollution_types=pollution_types
        ):
            cost_class = COST_CLASS[case.pollution_type]
            planned[cost_class] += 1
            if not selected["offline" if cost_class == pr.LEG_OFFLINE else "real"]:
                continue
            if cost_class == pr.LEG_REAL and real_run_limit and real_cases >= real_run_limit:
                continue
            unit_id = f"{case.case_id}::{cost_class}"
            if unit_id in done:
                print(f"跳过已完成: {unit_id}", flush=True)
                continue
            try:
                if cost_class == pr.LEG_OFFLINE:
                    _, unit = pr.run_offline_case(product, case)
                elif legs == "frozen":
                    _, unit = pr.run_frozen_case(
                        product,
                        case,
                        graph_runner=graph_runner,
                        llm_meter=llm_meter,
                        frozen_dir=Path(out_dir) / "frozen",
                    )
                    real_cases += 1
                else:
                    _, unit = pr.run_real_case(
                        product,
                        case,
                        graph_runner=graph_runner,
                        llm_meter=llm_meter,
                        decision_cache=decision_cache,
                    )
                    real_cases += 1
            except Exception as exc:  # noqa: BLE001 - 单单元失败不拖垮整批，如实记 error 单元
                unit = pr.error_unit(case, ticker=ticker, reason=f"{type(exc).__name__}: {exc}")
                print(f"[错误] {unit_id} → {unit['status_reason']}", flush=True)
            units.append(unit)
            done.add(unit_id)
            _persist()
            print(
                f"[{datetime.now():%H:%M:%S}] {unit_id} 开态={unit['on_state']}"
                f" 关态={unit['off_state']} status={unit['status']}",
                flush=True,
            )

    # 3) 报告（阳性对照 = 本批 A3 型单元：已知劣化变体 = 关 verify_citations）
    positive_control = [
        u for u in units if u.get("mechanism_id") == "A3" and u.get("status") == pr.STATUS_OK
    ]
    adjudicated = adjudicated_case_ids(Path(adjudication_csv)) if adjudication_csv else set()
    report = pr.pilot_report(
        units,
        prereg_dir=prereg_dir,
        positive_control_units=positive_control,
        adjudicated=adjudicated,
        # meter 绝对读数：预算块的独立交叉核对（None = 未接线；不可用不得伪造成 0）
        llm_meter_total=None if llm_meter is None else int(llm_meter()),
    )
    report["config"] = {
        # 实际标的清单须记入产物（预登记口径是 10 标的，实际跑的可能是子集）
        "tickers": list(tickers),
        "instances": instances,
        "legs": legs,
        "real_run_limit": real_run_limit,
        "pollution_types": list(pollution_types) if pollution_types else list(POLLUTION_TYPES),
        "real_run_cases": real_cases,
        "materials_dir": Path(materials_dir).as_posix(),
        "adjudication_csv": (Path(adjudication_csv).as_posix() if adjudication_csv else None),
        "out_dir": Path(out_dir).as_posix(),
        "prereg_dir": Path(prereg_dir).as_posix(),
        "planned_units": planned,
        "pipeline_model": _env("LLM_MODEL"),
        "judge_model": _env("JUDGE_MODEL"),
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    units_path = out_dir / "units.jsonl"
    write_units(units_path, [pr.to_unit_judgment(u) for u in units])
    report_path = out_dir / f"p1-injection-pilot-{stamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _print_summary(report)
    print(f"单元记录 → {units_path}")
    print(f"报告 → {report_path}")
    print(f"续跑台账 → {resume_path}")
    return report_path


def _env(name: str) -> str | None:
    import os

    return os.environ.get(name)


def _summary_row(label: str, n: int, table: dict, verdict: str, pending: int | None) -> str:
    return (
        f"{label:<20}{n:>5}{int(table['b']):>6}{int(table['c']):>6}"
        f"{table['discordant_ratio']:>12.3f}   {verdict:<14}{str(pending):>6}"
    )


def _print_summary(report: dict) -> None:
    print("\n=== P1 注入 pilot 摘要 ===", flush=True)
    print(
        f"预登记: {report['preregistration']['path']}（valid={report['preregistration']['valid']}）"
    )
    print(f"单元: {report['counts']} | 缺型: {report['types_absent']}")
    header = f"{'污染型':<20}{'n':>5}{'b':>6}{'c':>6}{'不一致比':>12}   {'校准':<14}{'待终裁':>6}"
    print(header)
    print("-" * len(header))
    for pollution_type, block in report["by_type"].items():
        print(
            _summary_row(
                pollution_type,
                block["n_pairs"],
                block["mcnemar"],
                str(block["calibration"]["verdict"]),
                block["escape_rate"]["pending"],
            )
        )
    print(
        _summary_row(
            "合计",
            report["counts"]["ok"],
            report["discordant"]["overall"],
            str(report["calibration"]["overall"]["verdict"]),
            report["escape_rate"]["overall"]["pending"],
        )
    )
    print(
        f"\n阳性对照: 显著={report['positive_control']['sensitivity_confirmed']} "
        f"(b={report['positive_control']['b']} c={report['positive_control']['c']} "
        f"p={report['positive_control']['exact_p']:.4g}) → 阴性结论状态 "
        f"{report['negative_results_status']}"
    )
    print(f"预算: {json.dumps(report['budget'], ensure_ascii=False)}")
    print(f"停止规则: {json.dumps(report['stop_rules'], ensure_ascii=False)}")
    if report.get("input_side_evidence"):
        print(
            "输入侧证据（presence 检查，非拦截率）: "
            f"{json.dumps(report['input_side_evidence'], ensure_ascii=False)}"
        )
    if report["blind_spots"]:
        print(f"盲区（无任何已定义可测面，拦截率 0 不可读作机制无价值）: {report['blind_spots']}")
    if report.get("worklist_excluded"):
        print(f"清单排除（无拦截/逃逸语义，判读走输入侧证据）: {report['worklist_excluded']}")
    print(f"终裁工作清单: {len(report['adjudication_worklist'])} 条待人工终裁", flush=True)


def _install_cost_meter() -> Callable[[], int] | None:
    """接线 usage meter（真跑腿预算按 meter 差量归属；不可用则如实记 None，不伪造成 0）。

    与 `ablation_pilot.py` 同一套（`tests/scripts/backtest_pilot_2023`）：包装适配器层的
    raw_completion/raw_stream 计量 + 钉定管线模型（跑批与生产同模型，数字可迁移）。
    """
    scripts_dir = str(_ROOT / "tests" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import backtest_pilot_2023 as pilot_util

        pilot_util._pin_pipeline_model()
        pilot_util.install_usage_meter()
    except Exception as exc:  # noqa: BLE001 - 计量不可用不阻断跑批，预算记 unknown
        print(f"[警告] usage meter 未接线（{type(exc).__name__}: {exc}）：真跑预算记 unknown")
        return None

    return lambda: len(pilot_util._usage_ledger)


def main() -> None:
    from dotenv import load_dotenv

    args = parse_args()
    load_dotenv()
    try:
        if args.materials_only:
            pr.assert_launch_allowed(args.prereg_dir)  # 同一门禁：材料亦按预登记口径生成
            _install_cost_meter()  # 材料也要钉模型（产物须可复现）
            run_materials(
                tickers=args.tickers, materials_dir=args.materials_dir, query=pr.DEFAULT_QUERY
            )
            return
        run_pilot(
            tickers=args.tickers,
            instances=args.instances,
            legs=args.legs,
            real_run_limit=args.real_run_limit,
            materials_dir=args.materials_dir,
            out_dir=args.out_dir,
            prereg_dir=args.prereg_dir,
            resume_path=args.resume,
            adjudication_csv=args.adjudication_csv,
            pollution_types=args.pollution_types,
            llm_meter=_install_cost_meter() if args.legs != "offline" else None,
        )
    except (MissingPreregistrationError, pr.MissingProductError, pr.InjectionError) as exc:
        print(f"[拒绝启动] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
