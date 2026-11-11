"""主档基线逐字段比对脚本（add-microcap-lane-analysis T9 Step 3）。

红线证据：主档三个日批入口的摘要 dict 在 feature HEAD 与基线 commit（缺省
``4b4abd60`` = 分支起点）对**同一份合成输入**逐字段一致：

- ``run_screening_daily()``——无参 ops 入口：三库走 env、面板由快照库重建；
  L3 triage 网关与事件装配按 ``tests/screening/test_pipeline.py`` 的 autouse
  stub 模式钉死零网络（``GatewayTriageClient`` 与 ``_assemble_events_by_symbol``
  模块属性替换——入口本身无注入参数，stub 方式与既有测试套件一致）。
- ``run_execution_daily(as_of=...)``——只传两侧签名**共有**的参数
  （db_path/execution_db_path/snapshot_db_path/regime_db_path）；``lane`` 不传
  （缺省 main）；regime 库缺失 → 摘要 regime 块带 ``no_regime_data`` 留痕。
- ``run_paper_settle_daily()``——无参调用（基线无 ``as_of``/账本参数）；快照与
  账本走 ``SNAPSHOT_DB_PATH``/``EXECUTION_DB_PATH`` env。

机制：

1. ``--dump <dir>``：在 ``<dir>/fixture`` 下确定性构建合成 fixture（80 交易日
   × 4 标的快照 + 交易日历 meta、1 个 active 因子 + 60 条 IC、前 2 交易日 L2 榜
   预置、predictions/execution 两库按各自 checkout 的 DDL 建表 + 1 条 main-lane
   已审批观点），依次跑三个入口，把摘要 dict 与取码探针落三个 JSON。探针
   （``finance_agent.__file__`` 等）只打印不比对——取码路径自证。
2. 主模式：HEAD 先 dump（``PYTHONPATH`` 前置本 worktree ``src``）；再从本
   worktree **内部** ``git worktree add --detach`` 检出基线 commit 到临时目录，
   以 ``PYTHONPATH`` 前置 ``<base>/src`` 跑同一 dumper（基线代码胜过已安装包）；
   逐 key 深比对三个摘要，PASS/FAIL 逐函数打印，任一 diff 退出非零；
   ``finally`` 清理临时 worktree 与临时目录。

用法::

    uv run python tests/scripts/compare_main_baseline.py [--base 4b4abd60] [--keep-temp]

幂等：每次运行全新临时目录、fixture 每次重建；重复运行结果一致。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = "4b4abd60"

# 合成 fixture 基准日（与 tests/screening/test_pipeline.py 同风格的工作日序列）
_AS_OF = "2026-10-13"
_CODES = ("600519", "000001", "300750")
_ST_CODE = "600999"  # 预置 ST 行：L1 必须剔除（非退化证据）
_CALENDAR: list[str] = []


def _business_days(end: str, count: int) -> list[str]:
    """``end`` 起向前 ``count`` 个工作日（升序）。"""
    day = date.fromisoformat(end)
    days: list[str] = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(days)


_CALENDAR.extend(_business_days(_AS_OF, 80))
_PREDICTION_ID = "p-main-1"

_DUMP_FILES = {
    "screening": "screening_summary.json",
    "execution": "execution_summary.json",
    "settle": "settle_summary.json",
}

# 规则运行时配置的 env 旋钮：比对期间必须清除，两侧都回退默认值
_RULES_ENV_KEYS = (
    "SCREENING_MIN_TRADING_DAYS",
    "SCREENING_MIN_AVG_AMOUNT_20D",
    "SCREENING_L2_TOP_N",
)


def _snapshot_rows() -> list[dict[str, Any]]:
    """逐日快照行：3 只正常标的 + 1 只 ST（金额 1 亿 > 5000 万门槛，仅 ST 被剔）。"""
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(_CALENDAR):
        for code_index, code in enumerate((*_CODES, _ST_CODE)):
            close = 10.0 + code_index * 5.0 + index * 0.1
            rows.append(
                {
                    "trade_date": day,
                    "code": code,
                    "name": f"合成{code}",
                    "open": close,
                    "high": close,
                    "low": close,
                    "close_raw": close,
                    "close_qfq": close,
                    "volume": 1_000_000.0,
                    "amount": 100_000_000.0 + code_index * 1_000_000.0,
                    "pct_chg": 0.0,
                    "prev_close": close,
                    "is_st": 1 if code == _ST_CODE else 0,
                    "is_suspended": 0,
                    "limit_up_sealed": 0,
                    "limit_down_sealed": 0,
                    "listed_days": len(_CALENDAR),
                    "list_date": _CALENDAR[0],
                }
            )
    return rows


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def build_fixture(fixture_root: Path) -> dict[str, Path]:
    """确定性构建合成 fixture；返回各库路径（供 env 与显式参数共用）。"""
    import pandas as pd

    from finance_agent.data.snapshot import set_meta_many, write_snapshot_rows
    from finance_agent.execution.store import EXECUTION_DDL
    from finance_agent.factors.library import add_factor, record_ic
    from finance_agent.outcome.track_record.model import PREDICTIONS_DDL
    from finance_agent.screening.ranker import Candidate
    from finance_agent.screening.store import record_l2_candidates

    fixture_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "snapshot": fixture_root / "market_snapshot.db",
        "library": fixture_root / "factor_library.db",
        "screening": fixture_root / "screening.db",
        "predictions": fixture_root / "predictions.db",
        "execution": fixture_root / "execution.db",
    }

    # 快照库：全部交易日 × 4 标的 + 交易日历 meta
    frame = pd.DataFrame(_snapshot_rows())
    for day in _CALENDAR:
        write_snapshot_rows(
            day, frame.loc[frame["trade_date"] == day].to_dict("records"), db_path=paths["snapshot"]
        )
    set_meta_many(
        {"last_success_date": _AS_OF, "trade_calendar": json.dumps(_CALENDAR)},
        db_path=paths["snapshot"],
    )

    # 因子库：1 个 active 因子 + 近 60 交易日 IC（样本达标，L2 正常打分）
    factor_id = add_factor(
        expression="close",
        hypothesis="主档基线比对的合成因子说明文本（长度达标的确定性假设）",
        category="builtin",
        status="active",
        db_path=paths["library"],
    )
    for index, day in enumerate(_CALENDAR[-60:]):
        record_ic(factor_id, day, 0.01 + 0.001 * index, db_path=paths["library"])

    # screening 库：预置前两交易日 L2 榜（600519/000001 两日在榜 → 当日 = 3 日达标）
    record_l2_candidates(
        _CALENDAR[-3],
        [
            Candidate(
                symbol="600519",
                trade_date=_CALENDAR[-3],
                composite_score=1.0,
                factor_contribs=(),
                rank=1,
            ),
            Candidate(
                symbol="000001",
                trade_date=_CALENDAR[-3],
                composite_score=0.9,
                factor_contribs=(),
                rank=2,
            ),
        ],
        db_path=paths["screening"],
    )
    record_l2_candidates(
        _CALENDAR[-2],
        [
            Candidate(
                symbol="600519",
                trade_date=_CALENDAR[-2],
                composite_score=1.0,
                factor_contribs=(),
                rank=1,
            ),
            Candidate(
                symbol="000001",
                trade_date=_CALENDAR[-2],
                composite_score=0.9,
                factor_contribs=(),
                rank=2,
            ),
            Candidate(
                symbol="300750",
                trade_date=_CALENDAR[-2],
                composite_score=0.8,
                factor_contribs=(),
                rank=3,
            ),
        ],
        db_path=paths["screening"],
    )

    # 观点库：按本 checkout 的 PREDICTIONS_DDL 建表；lane 列缺失（基线无归属列）
    # 时按 tests/execution/test_lane_execution_params.py 的既有模式 ALTER 补列。
    # fixture 只含 main-lane 观点（主档红线输入；基线无 lane 概念，microcap
    # 观点不在主档比对范围内）。
    conn_setup = _connect(paths["predictions"])
    conn_setup.executescript(PREDICTIONS_DDL)
    cols = {row[1] for row in conn_setup.execute("PRAGMA table_info(predictions)")}
    if "lane" not in cols:
        conn_setup.execute("ALTER TABLE predictions ADD COLUMN lane TEXT NOT NULL DEFAULT 'main'")
    conn_setup.commit()
    conn_setup.close()
    created_at = _CALENDAR[-2] + "T10:00:00"  # 决策日次一交易日口径：created_at[:10] < as_of
    conn = _connect(paths["predictions"])
    conn.execute(
        "INSERT INTO predictions (prediction_id, source_type, symbol, direction,"
        " confidence, rationale_snapshot, status, created_at, updated_at, lane)"
        " VALUES (?, 'live', '600519', 'long', 0.8, ?, 'open', ?, ?, 'main')",
        (
            _PREDICTION_ID,
            '{"fund_manager_decision":"approve","action":"buy"}',
            created_at,
            created_at,
        ),
    )
    conn.commit()
    conn.close()

    # 执行库：按本 checkout 的 EXECUTION_DDL 建表（broker 连接时 ensure_account）
    conn_exec = _connect(paths["execution"])
    conn_exec.executescript(EXECUTION_DDL)
    conn_exec.commit()
    conn_exec.close()
    return paths


class _ScriptedClient:
    """脚本化 triage stub（tests/screening/test_pipeline.py 同型）：全候选 keep。"""

    def __init__(self, payloads: dict[str, dict] | None = None, *, fail_all: bool = False) -> None:
        self._payloads = dict(payloads or {})
        self._fail_all = fail_all
        self.calls: list[str] = []

    def complete_json(self, prompt: str, *, purpose: Any, metadata: dict) -> dict:
        symbol = str(metadata.get("symbol"))
        self.calls.append(symbol)
        if self._fail_all:
            raise RuntimeError("LLM 网关不可用")
        return dict(self._payloads.get(symbol, {"verdict": "keep", "reason": "无风险事件"}))


def configure_env(fixture_root: Path) -> dict[str, str]:
    """比对运行的环境变量：全部库路径钉到 fixture，规则配置指向不存在文件。"""
    env = dict(os.environ)
    env.update(
        {
            "SNAPSHOT_DB_PATH": str(fixture_root / "market_snapshot.db"),
            "SCREENING_DB_PATH": str(fixture_root / "screening.db"),
            "FACTOR_LIBRARY_DB_PATH": str(fixture_root / "factor_library.db"),
            "SESSIONS_DB_PATH": str(fixture_root / "predictions.db"),
            "EXECUTION_DB_PATH": str(fixture_root / "execution.db"),
            "REGIME_DB_PATH": str(fixture_root / "regime.db"),  # 不存在 → no_regime_data
            "SCREENING_RULES_CONFIG_PATH": str(
                fixture_root / "absent_screening_rules.json"
            ),  # 不存在 → 各项回退默认值（两侧一致）
        }
    )
    return env


def apply_env(env: dict[str, str]) -> None:
    """把比对 env 施加到本进程（并清除规则配置旋钮，两侧同口径回退默认值）。"""
    for key, value in env.items():
        os.environ[key] = value
    for key in _RULES_ENV_KEYS:
        os.environ.pop(key, None)


def _patch_screening_stubs() -> None:
    """零网络纪律：默认 triage client 与默认事件装配钉死（同 test_pipeline autouse）。"""
    import finance_agent.screening.pipeline as pipeline

    pipeline.GatewayTriageClient = _ScriptedClient  # type: ignore[assignment]
    pipeline._assemble_events_by_symbol = lambda candidates, names_by_symbol=None: {}  # type: ignore[assignment,misc]


def _attempt(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """跑一个入口；错误文本本身就是比对证据（基线跑不动 ≠ 通过）。"""
    try:
        return {"summary": call()}
    except Exception as exc:  # noqa: BLE001 - 显式捕获任意入口异常，如实落 JSON
        return {"error": f"{type(exc).__name__}: {exc}"}


def _run_all() -> dict[str, dict[str, Any]]:
    """依次跑三个主档入口（env 已钉到 fixture；screening stub 已挂）。"""
    _patch_screening_stubs()
    from finance_agent.execution.brokers.paper import run_paper_settle_daily
    from finance_agent.execution.driver import run_execution_daily
    from finance_agent.screening.pipeline import run_screening_daily

    return {
        "screening": _attempt(run_screening_daily),
        "execution": _attempt(
            lambda: run_execution_daily(
                _AS_OF,
                db_path=os.environ["SESSIONS_DB_PATH"],
                execution_db_path=os.environ["EXECUTION_DB_PATH"],
                snapshot_db_path=os.environ["SNAPSHOT_DB_PATH"],
                regime_db_path=os.environ["REGIME_DB_PATH"],
            )
        ),
        "settle": _attempt(run_paper_settle_daily),
    }


def dump_mode(out_dir: Path) -> int:
    """``--dump``：构建 fixture → 跑三入口 → 摘要 + 探针落 JSON。返回 0。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = out_dir / "fixture"
    apply_env(configure_env(fixture_root))  # 就地生效（dump 模式可单独手动运行）
    build_fixture(fixture_root)

    import finance_agent
    import finance_agent.execution.brokers.paper as paper_mod
    import finance_agent.execution.driver as driver_mod
    import finance_agent.screening.pipeline as pipeline_mod

    results = _run_all()
    probe = {
        "finance_agent": str(finance_agent.__file__),
        "screening_pipeline": str(pipeline_mod.__file__),
        "execution_driver": str(driver_mod.__file__),
        "paper_broker": str(paper_mod.__file__),
    }
    for name, filename in _DUMP_FILES.items():
        payload = {
            "summary": results[name].get("summary"),
            "error": results[name].get("error"),
            "probe": probe,
        }
        (out_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )
    print("[dump] probe:", json.dumps(probe, ensure_ascii=False))
    for name in _DUMP_FILES:
        error = results[name].get("error")
        print(f"[dump] {name}: {'ERROR: ' + error if error else 'ok'}")
    return 0


def _deep_diff(expected: Any, actual: Any, path: str = "") -> list[str]:
    """递归逐 key 比对；返回 diff 行（``路径: HEAD值 != base值`` / 缺失侧标注）。"""
    diffs: list[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else str(key)
            if key not in expected:
                diffs.append(f"{child}: <missing-in-HEAD> != {actual[key]!r}")
            elif key not in actual:
                diffs.append(f"{child}: {expected[key]!r} != <missing-in-base>")
            else:
                diffs.extend(_deep_diff(expected[key], actual[key], child))
        return diffs
    if type(expected) is not type(actual) or expected != actual:
        diffs.append(f"{path}: {expected!r} != {actual!r}")
    return diffs


def _run_dump(checkout_root: Path, out_dir: Path, src_root: Path) -> None:
    """跑一次 dump 子进程（PYTHONPATH 前置目标 checkout 的 src，基线代码胜过已装包）。"""
    child_env = dict(os.environ)
    prepend = str(src_root / "src")
    child_env["PYTHONPATH"] = os.pathsep.join(
        [prepend] + ([child_env["PYTHONPATH"]] if child_env.get("PYTHONPATH") else [])
    )
    script = Path(__file__).resolve()
    proc = subprocess.run(  # noqa: S603 - 被测脚本是本文件自身，非外部输入
        [sys.executable, str(script), "--dump", str(out_dir)],
        cwd=str(checkout_root),
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    if proc.returncode != 0:
        raise RuntimeError(f"dump 子进程失败（{checkout_root}），exit={proc.returncode}")


def _git(*argv: str) -> str:
    proc = subprocess.run(  # noqa: S603 - 固定调 git，参数全部来自脚本自身
        ["git", *argv],  # noqa: S607 - git 为系统命令（同 check_secrets.py 既有模式）
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def compare_mode(base_ref: str, keep_temp: bool) -> int:
    """主模式：HEAD 与基线各 dump 一次，逐 key 比对三个摘要。"""
    tmp_root = Path(tempfile.mkdtemp(prefix="main-baseline-"))
    head_out = tmp_root / "head"
    base_out = tmp_root / "base"
    base_wt = tmp_root / f"base-{base_ref}"
    base_wt_added = False
    try:
        head_sha = _git("rev-parse", "HEAD")
        base_sha = _git("rev-parse", "--verify", f"{base_ref}^{{commit}}")
        print("=== 主档基线逐字段比对 ===")
        print(f"  HEAD : {head_sha}（{REPO_ROOT}）")
        print(f"  base : {base_sha}（{base_ref}）")

        _run_dump(REPO_ROOT, head_out, REPO_ROOT)

        subprocess.run(  # noqa: S603 - 固定调 git，commit sha 来自本仓 rev-parse
            ["git", "worktree", "add", "--detach", str(base_wt), base_sha],  # noqa: S607
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        base_wt_added = True
        _run_dump(base_wt, base_out, base_wt)

        print("--- 探针（取码路径自证，不参与比对）---")
        for label, out in (("HEAD", head_out), ("BASE", base_out)):
            probe = json.loads((out / _DUMP_FILES["screening"]).read_text(encoding="utf-8"))[
                "probe"
            ]
            print(f"  {label} finance_agent    = {probe['finance_agent']}")
            print(f"  {label} execution_driver = {probe['execution_driver']}")

        all_pass = True
        for name, filename in _DUMP_FILES.items():
            head_payload = json.loads((head_out / filename).read_text(encoding="utf-8"))
            base_payload = json.loads((base_out / filename).read_text(encoding="utf-8"))
            fatal = False
            for side, payload in (("HEAD", head_payload), ("BASE", base_payload)):
                if payload.get("error"):
                    fatal = True
                    print(f"[{name}] FAIL — {side} 入口抛错: {payload['error']}")
            if fatal:
                all_pass = False
                continue
            head_summary = head_payload["summary"]
            base_summary = base_payload["summary"]
            diffs = _deep_diff(head_summary, base_summary)
            if diffs:
                all_pass = False
                print(f"[{name}] FAIL — {len(diffs)} 处不一致:")
                for line in diffs:
                    print(f"    {line}")
            else:
                keys = len(head_summary) if isinstance(head_summary, dict) else 0
                print(f"[{name}] PASS — 摘要逐字段一致（keys={keys}）")
                print(f"    summary = {json.dumps(head_summary, ensure_ascii=False, default=str)}")
        print(f"RESULT: {'PASS' if all_pass else 'FAIL'} (3 项主档入口)")
        return 0 if all_pass else 1
    finally:
        if base_wt_added:
            subprocess.run(  # noqa: S603 - 固定调 git，路径为本脚本生成的临时目录
                ["git", "worktree", "remove", "--force", str(base_wt)],  # noqa: S607
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            subprocess.run(  # noqa: S603 - 固定调 git（清理兜底，无外部输入）
                ["git", "worktree", "prune"],  # noqa: S607
                cwd=str(REPO_ROOT),
                capture_output=True,
            )
        if not keep_temp:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            print(f"[keep-temp] 临时产物保留于 {tmp_root}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):  # 非 tty/重定向环境尽力而为
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description="主档基线逐字段比对（HEAD vs 基线 commit）")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"基线 commit（缺省 {DEFAULT_BASE}）")
    parser.add_argument("--keep-temp", action="store_true", help="保留临时目录（调试用）")
    parser.add_argument("--dump", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.dump is not None:
        return dump_mode(args.dump)
    return compare_mode(args.base, args.keep_temp)


if __name__ == "__main__":
    sys.exit(main())
