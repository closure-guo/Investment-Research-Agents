"""Δ4 Task 5 离线通路验证驱动：零网络 / 零 LLM 跑通「探针 → 批次准入 → 报告 → 台账」。

验证对象（delta add-backtest-leakage-controls）：
1. 泄漏探针三层题全流程（假 LLM + 构造行情/新闻）：命中率、未知占比、超阈降级、
   真值不可得三态；命中率真值由本脚本**独立实现**（同口径阈值）后再喂给假 LLM 作答，
   故命中率=1.0 是「探针判分正确」的实证，不是自证。
2. 干净窗口判定（交易日口径）：过 / 不过两态。
3. `--batch-kind pathway` 通路批：结论恒「通路验证定位」、五键齐、md 落盘且 status 头可解析。
4. `--batch-kind formal` 无预登记 → 拒绝；补临时预登记 → 通过并输出超阈降级句式；
   探针不可测 / 干净窗口未过 → 回退通路验证。
5. 既有 `evals/ablation/preregister/` 的 outcome 预登记在门禁字段下有效（真实门禁读数）。
6. pilot md 生命周期 status 头 + `evals/backtest/results` 索引扫描。

真值口径与生产一致：K 线走 `SETTLEMENT_ADJUST`（后复权 hfq，Δ4 Task 1 统一）；
假 client 的 `fetch_kline` 带 `adjust` 形参，签名与 AKShareClient 对齐。

用法：
    uv run python tests/scripts/d4_task5_pathway_offline.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402
from evals.backtest.leakage_probe import (  # noqa: E402
    DIRECTION_PROMPT,
    EVENT_PROMPT,
    MAGNITUDE_PROMPT,
    run_leakage_probe,
)
from evals.backtest.report import (  # noqa: E402
    POSITIONING_PATHWAY,
    POSITIONING_SKILL,
    PROBE_DOWNGRADED,
    PROBE_UNMEASURABLE,
    assert_clean_window,
    render_backtest_report_md,
)
from evals.backtest.run_backtest import run_backtest  # noqa: E402
from evals.causal_ablation.preregister import (  # noqa: E402
    OUTCOME_REQUIRED_FIELDS,
    MissingPreregistrationError,
    assert_preregistered,
)
from evals.causal_ablation.report_status import (  # noqa: E402
    assert_status_valid,
    parse_status,
)
from evals.causal_ablation.status_index import collect_status_index  # noqa: E402
from evals.outcome.caliber import NEUTRAL_BAND  # noqa: E402
from evals.outcome.conclusion import assert_outcome_sentence_legal  # noqa: E402

CODES = ("600519", "000858", "300308", "002412")
PROBE_CODES = CODES[:3]  # 探针抽样池（每 regime 一只，见 SAMPLE）
DECISION_DATES = ("2024-06-03", "2024-07-15")  # 两个 distinct 决策日（逐窗口探针）
WINDOW_DAYS = 20
REPEATS = 3
LARGE_MOVE = 0.05  # 与 leakage_probe.LARGE_MOVE 同口径（本脚本独立实现，故本地定义）
EVENT_MOVE_PROXY = 0.15
FIVE_KEYS = ("batch_kind", "preregister", "clean_window", "leakage_probe", "regime_coverage")

# 逐标的日涨幅相位（涨/跌交替 → 有限 Sharpe；20 日净漂移各异 → 幅度桶不同）
DRIFT: dict[str, tuple[float, float]] = {
    "600519": (1.008, 0.998),
    "000858": (1.005, 0.999),
    "300308": (1.010, 0.995),
    "002412": (1.004, 0.9995),
}
BENCH_DRIFT = (1.003, 0.9995)
BENCH_START = 3000.0
STOCK_START = 10.0
GRID_N = 200

# 两个 regime 各一只，第二 regime 复用标的（决策日不同 → 独立窗口）
SAMPLE: list[dict[str, str]] = [
    {"code": "600519", "regime": "sideways", "decision_date": DECISION_DATES[0]},
    {"code": "000858", "regime": "sideways", "decision_date": DECISION_DATES[0]},
    {"code": "300308", "regime": "bull", "decision_date": DECISION_DATES[1]},
    {"code": "002412", "regime": "bull", "decision_date": DECISION_DATES[1]},
]
# 非可执行决策（hold → neutral 回避语义）整条排除（§1.8②）：002412 记 hold
ACTIONS = {"600519": "buy", "000858": "buy", "300308": "sell", "002412": "hold"}

_FAILURES: list[str] = []
_CHECKS = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if condition:
        print(f"  [ok] {label}{f' — {detail}' if detail else ''}", flush=True)
    else:
        _FAILURES.append(f"{label}{f' — {detail}' if detail else ''}")
        print(f"  [FAIL] {label}{f' — {detail}' if detail else ''}", flush=True)


# ---------------------------------------------------------------- 构造行情


def _grid(n: int = GRID_N) -> list[str]:
    return pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d").tolist()


def _frame(dates: list[str], up: float, down: float, start: float) -> pd.DataFrame:
    closes = [start]
    for i in range(1, len(dates)):
        closes.append(closes[-1] * (up if i % 2 else down))
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘": closes,
            "收盘": closes,
            "最高": [c * 1.01 for c in closes],
            "最低": [c * 0.99 for c in closes],
        }
    )


DATES = _grid()
BENCHMARK = _frame(DATES, *BENCH_DRIFT, BENCH_START)
FRAMES: dict[str, pd.DataFrame] = {code: _frame(DATES, *DRIFT[code], STOCK_START) for code in CODES}
AS_OF = DATES[-1]  # 距两个决策日均 ≫ 20 交易日
AS_OF_TOO_SOON = DATES[DATES.index(DECISION_DATES[1]) + 4]  # 仅 4 个交易日


# ---------------------------------------------------------------- 真值（独立实现）


def _window_rows(frame: pd.DataFrame, decision_date: str) -> pd.DataFrame:
    dates = frame["日期"].astype(str).str[:10]
    after = frame[dates > decision_date].reset_index(drop=True)
    return after.iloc[:WINDOW_DAYS]


def _rel_return(frame: pd.DataFrame, decision_date: str) -> tuple[str, str, float]:
    """(start, end, 相对基准区间收益)。口径：标的后复权区间收益 − 基准区间收益。"""
    win = _window_rows(frame, decision_date)
    start, end = str(win["日期"].iloc[0])[:10], str(win["日期"].iloc[-1])[:10]
    ticker_ret = float(win["收盘"].iloc[-1]) / float(win["收盘"].iloc[0]) - 1.0
    bdates = BENCHMARK["日期"].astype(str).str[:10]
    seg = BENCHMARK[(bdates >= start) & (bdates <= end)]
    bench_ret = float(seg["收盘"].iloc[-1]) / float(seg["收盘"].iloc[0]) - 1.0
    return start, end, ticker_ret - bench_ret


def _direction(rel: float) -> str:
    return "up" if rel > 0 else "down"


def _bucket(rel: float) -> str:
    if rel > LARGE_MOVE:
        return "大涨"
    if rel > NEUTRAL_BAND:
        return "小涨"
    if rel >= -NEUTRAL_BAND:
        return "持平"
    if rel >= -LARGE_MOVE:
        return "小跌"
    return "大跌"


def build_prompt_map() -> dict[str, str]:
    """探针三层题的 prompt → 正确答案（脚本侧独立算真值）。事件题真值恒「无」：
    构造新闻标题不含关键词，且 |rel| < 15% 代理阈值。"""
    mapping: dict[str, str] = {}
    for code in PROBE_CODES:
        for decision_date in DECISION_DATES:
            start, end, rel = _rel_return(FRAMES[code], decision_date)
            if abs(rel) >= EVENT_MOVE_PROXY:
                raise AssertionError("构造数据不该触发事件价格代理（脚本前提失效）")
            mapping[DIRECTION_PROMPT.format(ticker=code, start=start, end=end)] = json.dumps(
                {"direction": _direction(rel)}, ensure_ascii=False
            )
            mapping[MAGNITUDE_PROMPT.format(ticker=code, start=start, end=end)] = json.dumps(
                {"bucket": _bucket(rel)}, ensure_ascii=False
            )
            mapping[EVENT_PROMPT.format(ticker=code, start=start, end=end)] = json.dumps(
                {"event": "无"}, ensure_ascii=False
            )
    return mapping


PROMPT_MAP = build_prompt_map()


def answer_correctly(prompt: str) -> str:
    return PROMPT_MAP.get(prompt, "我不确定")


def answer_refused(prompt: str) -> str:  # noqa: ARG001 — 签名契约
    return "这个问题我无法回答，请提供资料。"


# ---------------------------------------------------------------- 假 client


class _FakeClient:
    """AKShareClient 最小替身：签名对齐（fetch_kline 带 adjust 形参）。"""

    def __init__(self, *, empty_kline: bool = False, empty_news: bool = False) -> None:
        self.empty_kline = empty_kline
        self.empty_news = empty_news

    def fetch_kline(
        self, stock_code: str, days: int = 250, *, adjust: str = "qfq", **_kw: Any
    ) -> pd.DataFrame:
        if self.empty_kline:
            return pd.DataFrame()
        return FRAMES[str(stock_code)].tail(days).reset_index(drop=True)

    def fetch_index_kline(self, index_code: str, days: int = 250) -> pd.DataFrame:  # noqa: ARG002
        return BENCHMARK.tail(days).reset_index(drop=True)

    def fetch_news(self, stock_code: str, limit: int = 20) -> list[dict]:  # noqa: ARG002
        if self.empty_news:
            return []
        return [{"title": "公司发布日常经营公告", "date": DECISION_DATES[0]}]


# ---------------------------------------------------------------- 假 replay


def make_replay():
    """(code, decision_date) → 与 replay_with_consistency 同形态的结果 dict。"""

    def replay(
        code: str,
        decision_date: str,
        *,
        n: int = REPEATS,
        full_kline: pd.DataFrame | None = None,
        full_benchmark: pd.DataFrame | None = None,
    ) -> dict:
        frame = FRAMES[code]
        dates = frame["日期"].astype(str).str[:10]
        entry = float(frame[dates <= decision_date].iloc[-1]["收盘"])
        after = frame[dates > decision_date].reset_index(drop=True)
        settle_row = after.iloc[WINDOW_DAYS - 1]
        action = ACTIONS[code]
        return {
            "code": code,
            "decision_date": decision_date,
            "actions": [action] * n,
            "agreement": 1.0,
            "settlement": {
                "status": "expired",
                "settle_date": str(settle_row["日期"])[:10],
                "settle_price": float(settle_row["收盘"]),
                "hold_days": WINDOW_DAYS,
                "decision_return": None,
                "benchmark_return": None,
                "decision_excess": None,
                "decision_hit": None,
            },
            "entry_price": entry,
            "action": action,
            "snapshot_metadata": {},
        }

    return replay


def batch_probe(llm) -> dict[str, Any] | None:
    """批级探针：逐 distinct 决策日各探一次 → 最差态汇总。

    与 `run_backtest.run_batch_probe` 同构（后者无 `llm` 注入口，离线不可驱动），
    故此处显式调用 `run_leakage_probe` 后复用 `aggregate_probes`。
    """
    from evals.backtest.report import aggregate_probes

    probes = []
    for window in sorted({item["decision_date"] for item in SAMPLE}):
        result = run_leakage_probe(
            list(PROBE_CODES),
            window,
            window_days=WINDOW_DAYS,
            client=_FakeClient(),
            llm=llm,
        )
        probes.append({**result, "probe_window": [window]})
    return aggregate_probes(probes, windows=sorted({i["decision_date"] for i in SAMPLE}))


PREREG_BODY = """# 临时预登记（Δ4 Task 5 离线验证）

- 主指标：T+20 相对沪深300超额收益
- MDE：0.2802/√n（均值，σ=10pp）
- 决策阈值：CI 下界 > 0 依据 MDE 换算式反算样本量
- 样本量依据：MDE 0.2802/√n 换算
- 停止规则：样本量达标即停
- 成本分型：cohort 记账真值
- 泄漏控制：探针阈值 0.60，超阈降级上界证据
"""


def write_temp_preregister(root: Path) -> Path:
    path = root / "2024-01-01-temp-outcome-prereg.md"
    path.write_text(PREREG_BODY, encoding="utf-8")
    return path


# ---------------------------------------------------------------- 主流程


def main() -> int:
    print("=" * 72, flush=True)
    print("[0] 构造数据：假 client / 假 LLM / 假 replay（零网络零 LLM）", flush=True)
    rels = {
        (code, d): round(_rel_return(FRAMES[code], d)[2], 4)
        for code in PROBE_CODES
        for d in DECISION_DATES
    }
    print(f"  相对基准区间收益 rel：{rels}", flush=True)
    check(len(PROMPT_MAP) == len(PROBE_CODES) * len(DECISION_DATES) * 3, "三层题 prompt 真值表齐备")
    check(
        all(r > 0 for r in rels.values()),
        "构造数据方向均为 up（可预期）",
        str(sorted(set(rels.values()))),
    )

    print("\n[1] 探针全流程：答对（假 LLM 依独立真值作答）", flush=True)
    probe_ok = batch_probe(answer_correctly)
    assert probe_ok is not None
    print(
        f"  state={probe_ok['state']} direction={probe_ok['direction_hit_rate']} "
        f"unknown={probe_ok['unknown_ratio']} probe_n={probe_ok['probe_n']} "
        f"windows={probe_ok['probe_window']}",
        flush=True,
    )
    check(probe_ok["state"] == PROBE_DOWNGRADED, "全答对 → 超阈降级态", probe_ok["state"])
    check(probe_ok["direction_hit_rate"] == 1.0, "方向命中率 1.0（判分正确）")
    check(probe_ok["unknown_ratio"] == 0.0, "未知占比 0.0")
    check(probe_ok["probe_n"] == len(PROBE_CODES) * len(DECISION_DATES), "probe_n = 标的×窗口")
    check(
        len(probe_ok["per_window"] or []) == len(DECISION_DATES), "逐窗口读数 2 条（各带自身窗口）"
    )
    check(
        all(w and len(w) == 1 for w in (item["probe_window"] for item in probe_ok["per_window"])),
        "per_window 每项带它自己的窗口（不冒充全窗口）",
    )

    print("\n[2] 探针全流程：拒答（不可解析 → 未知，不折算答错）", flush=True)
    probe_refused = batch_probe(answer_refused)
    assert probe_refused is not None
    print(
        f"  state={probe_refused['state']} direction={probe_refused['direction_hit_rate']} "
        f"unknown={probe_refused['unknown_ratio']}",
        flush=True,
    )
    check(probe_refused["state"] == PROBE_UNMEASURABLE, "全拒答 → 不可测态")
    check(probe_refused["direction_hit_rate"] is None, "命中率 None（不报 0）")
    check(probe_refused["unknown_ratio"] == 1.0, "未知占比 1.0")
    check(probe_refused["downgraded"] is False, "不可测不得冒充降级")

    print("\n[3] 探针全流程：真值不可得（空 K 线 → truth_unavailable）", flush=True)
    single = run_leakage_probe(
        list(PROBE_CODES),
        DECISION_DATES[0],
        window_days=WINDOW_DAYS,
        client=_FakeClient(empty_kline=True),
        llm=answer_correctly,
    )
    print(
        f"  direction={single['direction_hit_rate']} unknown={single['unknown_ratio']} "
        f"reasons={sorted({d['reason'] for d in single['details']})}",
        flush=True,
    )
    check(single["direction_hit_rate"] is None, "真值不可得 → 命中率 None")
    check(
        {d["reason"] for d in single["details"]} == {"truth_unavailable"},
        "原因标记 truth_unavailable（真值缺失，非答错）",
    )
    check(single["unknown_ratio"] == 1.0, "未知占比 1.0")

    print("\n[4] 探针全流程：事件题真值不可得（空新闻源）", flush=True)
    no_news = run_leakage_probe(
        list(PROBE_CODES),
        DECISION_DATES[0],
        window_days=WINDOW_DAYS,
        client=_FakeClient(empty_news=True),
        llm=answer_correctly,
    )
    event_details = [d for d in no_news["details"] if d["kind"] == "event"]
    check(no_news["event_hit_rate"] is None, "事件题命中率 None")
    check(
        all(d["reason"] == "truth_unavailable" for d in event_details),
        "事件题原因 truth_unavailable",
    )
    check(no_news["direction_hit_rate"] == 1.0, "方向题不受事件真值缺失影响")

    print("\n[5] 干净窗口判定（交易日口径）", flush=True)
    ok_window = assert_clean_window(
        [i["decision_date"] for i in SAMPLE],
        as_of=AS_OF,
        window_days=WINDOW_DAYS,
        benchmark=BENCHMARK,
    )
    bad_window = assert_clean_window(
        [i["decision_date"] for i in SAMPLE],
        as_of=AS_OF_TOO_SOON,
        window_days=WINDOW_DAYS,
        benchmark=BENCHMARK,
    )
    print(
        f"  as_of={AS_OF} → {ok_window['passed']}；as_of={AS_OF_TOO_SOON} → {bad_window['passed']}",
        flush=True,
    )
    check(ok_window["passed"] is True, "远端 as_of → 干净窗口通过", ok_window["reason"][:60])
    check(bad_window["passed"] is False, "近端 as_of → 干净窗口不通过")
    check("交易日" in bad_window["reason"], "不通过理由含交易日实测距离")

    print("\n[6] pathway 通路批（默认身份）：结论/五键/md/status 头", flush=True)
    report = run_backtest(
        SAMPLE,
        {c: FRAMES[c] for c in CODES},
        benchmark_kline=BENCHMARK,
        repeats=REPEATS,
        sanity_note="Δ4 Task 5 离线通路验证：构造样本，不具统计意义",
        replay_fn=make_replay(),
        batch_kind="pathway",
        as_of=AS_OF,
        probe=probe_ok,
    )
    print(f"  conclusion={report['conclusion']}", flush=True)
    print(
        f"  positioning={report['positioning']} n_sample={report['n_sample']} "
        f"excluded_non_executable={report['methodology']['excluded_non_executable']}",
        flush=True,
    )
    check(report["conclusion"].startswith("通路验证定位"), "通路批结论为通路验证句")
    check("不产出 skill" in report["conclusion"], "通路批结论显式声明不产出 skill 结论句")
    guard_rejected = False
    try:
        assert_outcome_sentence_legal(report["conclusion"])
    except ValueError:
        guard_rejected = True
    check(guard_rejected, "通路批结论不构成合法 outcome 结论句（Δ1 句式守卫拒绝）")
    check(report["positioning"] == POSITIONING_PATHWAY, "positioning=pathway")
    check(
        all(k in report for k in FIVE_KEYS),
        "报告五键齐备",
        str([k for k in FIVE_KEYS if k not in report]),
    )
    check(report["clean_window"]["passed"] is True, "通路批亦登记干净窗口判定结果")
    check(
        report["leakage_probe"]["state"] == PROBE_DOWNGRADED
        and report["leakage_probe"]["per_window"] is not None,
        "探针段带三态与逐窗口读数",
    )
    check(report["methodology"]["excluded_non_executable"] == 1, "hold 决策整条排除并计数")
    check("hfq" in report["methodology"]["adjust"], "复权口径披露为 hfq")

    with tempfile.TemporaryDirectory() as tmp:
        md_dir = Path(tmp)
        md = render_backtest_report_md(report, name="pathway-offline")
        md_path = md_dir / "pathway-offline.md"
        md_path.write_text(md, encoding="utf-8")
        on_disk = md_path.read_text(encoding="utf-8")
        print(f"  md 落盘：{md_path}（{len(on_disk)} 字符）", flush=True)
        status, target = parse_status(on_disk)
        check(
            status == "active" and target is None,
            "落盘 md 的 status 头可解析",
            f"{status}/{target}",
        )
        assert_status_valid(on_disk)
        check("## 2. 泄漏探针" in on_disk, "md 含探针固定段")
        check("逐窗口读数" in on_disk and "| 窗口 | 状态 |" in on_disk, "md 含逐窗口读数表")
        check("**批次类型**: pathway" in on_disk, "md 批次类型与报告一致")
        check("通路验证定位" in on_disk, "md 结论段逐字一致")

    print("\n[7] formal 批无预登记 → 拒绝", flush=True)
    with tempfile.TemporaryDirectory() as empty_dir:
        raised = False
        try:
            assert_preregistered(
                Path(empty_dir),
                name_contains="outcome",
                required_fields=OUTCOME_REQUIRED_FIELDS,
            )
        except MissingPreregistrationError as exc:
            raised = True
            print(f"  拒绝理由：{exc}", flush=True)
        check(raised, "空预登记目录 → MissingPreregistrationError")
        raised = False
        try:
            run_backtest(
                SAMPLE,
                {c: FRAMES[c] for c in CODES},
                benchmark_kline=BENCHMARK,
                replay_fn=make_replay(),
                batch_kind="formal",
                as_of=AS_OF,
                probe=probe_ok,
                preregister_dir=Path(empty_dir),
            )
        except MissingPreregistrationError as exc:
            raised = True
            print(f"  run_backtest 拒绝理由：{exc}", flush=True)
        check(raised, "formal 批经 run_backtest 亦被拒绝（不降级不静默）")

    print("\n[8] formal 批 + 临时预登记 + 超阈探针 → 上界证据句式", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        prereg_dir = Path(tmp)
        prereg_path = write_temp_preregister(prereg_dir)
        formal = run_backtest(
            SAMPLE,
            {c: FRAMES[c] for c in CODES},
            benchmark_kline=BENCHMARK,
            repeats=REPEATS,
            sanity_note="Δ4 Task 5 离线通路验证：构造样本，不具统计意义",
            replay_fn=make_replay(),
            batch_kind="formal",
            as_of=AS_OF,
            probe=probe_ok,
            preregister_dir=prereg_dir,
        )
        print(f"  conclusion={formal['conclusion']}", flush=True)
        check(
            formal["preregister"] == {"path": str(prereg_path), "valid": True}, "预登记指针 + valid"
        )
        check(
            formal["positioning"] == POSITIONING_SKILL, "formal + 干净窗口 + 可测探针 → skill 定位"
        )
        check("泄漏污染下的上界证据" in formal["conclusion"], "超阈 → 上界证据句式")
        check("真实 skill ≤ 读数" in formal["conclusion"], "含「真实 skill ≤ 读数」限定")
        check("不得单独作为赚钱能力主张" in formal["conclusion"], "含禁止单独主张的限定语")

        print("\n[9] formal 批 + 预登记 + 探针不可测 → 回退通路验证", flush=True)
        formal_unmeas = run_backtest(
            SAMPLE,
            {c: FRAMES[c] for c in CODES},
            benchmark_kline=BENCHMARK,
            replay_fn=make_replay(),
            batch_kind="formal",
            as_of=AS_OF,
            probe=probe_refused,
            preregister_dir=prereg_dir,
        )
        print(f"  conclusion={formal_unmeas['conclusion']}", flush=True)
        check(formal_unmeas["positioning"] == POSITIONING_PATHWAY, "不可测 → pathway 定位")
        check("探针不可测" in formal_unmeas["conclusion"], "结论写明「探针不可测」")

        print("\n[10] formal 批 + 干净窗口未过 → 回退通路验证", flush=True)
        formal_dirty = run_backtest(
            SAMPLE,
            {c: FRAMES[c] for c in CODES},
            benchmark_kline=BENCHMARK,
            replay_fn=make_replay(),
            batch_kind="formal",
            as_of=AS_OF_TOO_SOON,
            probe=probe_ok,
            preregister_dir=prereg_dir,
        )
        print(f"  conclusion={formal_dirty['conclusion']}", flush=True)
        check(formal_dirty["positioning"] == POSITIONING_PATHWAY, "窗口未过 → pathway 定位")
        check("干净窗口未过" in formal_dirty["conclusion"], "结论写明「干净窗口未过」")
        check(formal_dirty["clean_window"]["passed"] is False, "披露段如实记不通过")

    print("\n[11] 真实门禁读数：仓库 outcome 预登记在 outcome 字段下有效", flush=True)
    real = assert_preregistered(
        Path("evals/ablation/preregister"),
        name_contains="outcome",
        required_fields=OUTCOME_REQUIRED_FIELDS,
    )
    print(f"  path={real.path} valid={real.valid}", flush=True)
    check(real.valid is True, "既有 outcome 预登记通过 OUTCOME_REQUIRED_FIELDS 门禁")
    check(
        all(real.fields.get(f, "").strip() for f in OUTCOME_REQUIRED_FIELDS),
        "7 个门禁字段均有取值",
    )

    print("\n[12] 台账索引：pilot status 头 + evals/backtest/results 扫描", flush=True)
    pilot_path = Path("evals/backtest/results/pilot-2023-shock.md")
    pilot_text = pilot_path.read_text(encoding="utf-8")
    p_status, p_target = parse_status(pilot_text)
    print(f"  pilot status={p_status}/{p_target}", flush=True)
    check(p_status == "active", "pilot 就地补的 status 头可解析")
    check("通路验证" in pilot_text and "泄漏风险" in pilot_text, "pilot 已标注通路验证 + 泄漏风险")
    entries, unstamped = collect_status_index(Path("evals/backtest/results"))
    indexed = [e.path for e in entries]
    print(f"  索引命中：{indexed}；未标注：{unstamped}", flush=True)
    check(
        any(p.endswith("pilot-2023-shock.md") for p in indexed),
        "evals/backtest/results 纳入结论注册表索引",
    )

    print("\n" + "=" * 72, flush=True)
    if _FAILURES:
        print(f"结论: FAIL（{len(_FAILURES)}/{_CHECKS} 项不通过）", flush=True)
        for item in _FAILURES:
            print(f"  - {item}", flush=True)
        return 1
    print(f"结论: PASS（全部 {_CHECKS} 项核对通过）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
