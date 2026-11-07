"""零买入消融：隔离库 + 变体提示词跑单次分析，落 final_trade_decision。

⚠️ 关键顺序：必须先设 SESSIONS_DB_PATH，再 import 任何 finance_agent 模块——
session_store 在 import 期就把路径固化到模块常量（session_store._DB_PATH，
tests/conftest.py 的 DB 隔离 fixture 同款处理）。本文件因此把业务 import
全部放进函数内部，main() 在 import 前落 env。

用法（变体可重复传，B 臂须同时传 trader + risk_judge 两个变体）：
  uv run python scripts/run_zero_buy_ablation.py \
    --variant evals/variants/zero_buy/trader_no_catalyst.md \
    --symbols 002916,300033 --db tmp/ablation.db --out reports/ablation/zero-buy-A.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(
        0, str(_ROOT)
    )  # evals/ 未安装进 venv（packages.find where=src），ops_api 依赖 evals 包，需显式加根

_NODE_MODULES = (
    "finance_agent.nodes.analysts",
    "finance_agent.nodes.trader",
    "finance_agent.nodes.risk",
    "finance_agent.nodes.fund_manager",
    "finance_agent.nodes.debate",
    "finance_agent.nodes.research_manager",
)
_PROD_DB = Path("data/sessions.db")


def assert_isolated_db(path: Path) -> Path:
    """硬校验：消融实验禁止写生产库 data/sessions.db。返回解析后的绝对路径。"""
    resolved = Path(path).expanduser().resolve()
    if resolved == _PROD_DB.resolve():
        raise RuntimeError("消融实验禁止写生产库 data/sessions.db")
    return resolved


def parse_variant_mapping(variant_paths: list[Path]) -> dict[str, str]:
    """变体文件名 → prompt 名映射：前缀 trader*→trader、risk_judge*→risk_judge。"""
    mapping: dict[str, str] = {}
    for p in variant_paths:
        stem = p.stem
        if stem.startswith("risk_judge"):
            name = "risk_judge"
        elif stem.startswith("trader"):
            name = "trader"
        else:
            raise ValueError(
                f"无法从变体文件名推断 prompt 名: {p.name}（文件名须以 trader/risk_judge 开头）"
            )
        if name in mapping:
            raise ValueError(f"prompt {name} 重复指定: {p.name}")
        mapping[name] = p.name
    return mapping


def _reader(variant_dir: Path, mapping: dict[str, str]):
    from finance_agent.prompts.loader import PromptInfo
    from finance_agent.prompts.loader import load_prompt_with_meta as _real

    def _load(name: str) -> PromptInfo:
        if name in mapping:
            text = (variant_dir / mapping[name]).read_text(encoding="utf-8")
            return PromptInfo(
                template=text, prompt_name=f"variant:{name}", prompt_version="variant"
            )
        return _real(name)

    return _load


@contextlib.contextmanager
def _swap(obj, attr: str, value):
    original = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, original)


@contextlib.contextmanager
def patch_prompts(variant_dir: Path, mapping: dict[str, str]):
    """把 mapping 指定的 prompt 换成变体文件，覆盖六个节点模块（含分析师）的模块级引用。

    节点在函数调用期才调 load_prompt_with_meta，故 patch 模块属性即可生效；
    未命中 mapping 的名字走真实 loader（Langfuse production → 本地兜底）。
    """
    import importlib

    stack = contextlib.ExitStack()
    reader = _reader(Path(variant_dir), mapping)
    for mod_name in _NODE_MODULES:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "load_prompt_with_meta"):
            stack.enter_context(_swap(mod, "load_prompt_with_meta", reader))
    try:
        yield
    finally:
        stack.close()


def _read_session(session_id: str) -> dict | None:
    import sqlite3

    conn = sqlite3.connect(os.environ["SESSIONS_DB_PATH"])
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT session_id, stock_code, status, final_trade_decision "
        "FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


async def run_symbol(symbol: str, *, timeout_s: int = 2400) -> dict:
    from finance_agent.api import start_analysis_session_for_operator

    receipt = await start_analysis_session_for_operator(symbol)
    session_id = receipt.get("session_id") or receipt.get("sessionId")
    if not session_id:
        raise RuntimeError(f"{symbol} 派单未返回 session_id: {receipt!r}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        row = _read_session(session_id)
        if row and row["status"] in ("completed", "failed", "interrupted"):
            result = {
                "symbol": symbol,
                "session_id": session_id,
                "status": row["status"],
                "final_trade_decision": row["final_trade_decision"],
            }
            if row["status"] == "failed":
                # 失败含可重试形态（provider 过载）→ 抛错交符号级重试；决策类失败
                # （如价位校验打回）不重试——那是变体的真实输出，重跑会掩盖
                reason = str(_read_failure_reason(session_id) or "")
                if "RateLimitError" in reason or "限流" in reason:
                    raise RuntimeError(f"provider_overloaded: {reason[:200]}")
            return result
        await asyncio.sleep(15)
    raise TimeoutError(f"{symbol} 分析超时（{timeout_s}s）")


def _read_failure_reason(session_id: str) -> str | None:
    import sqlite3

    conn = sqlite3.connect(os.environ["SESSIONS_DB_PATH"])
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT failure_reason FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return row["failure_reason"] if row else None


def _action(result: dict) -> str | None:
    raw = result.get("final_trade_decision")
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    action = payload.get("action")
    return str(action) if action else None


async def _run_symbol_with_retry(
    symbol: str,
    *,
    timeout_s: int,
    attempts: int = 6,
    cooldown_s: float = 180.0,
) -> dict:
    """符号级重试：调度失败（如 Windows 上间歇性 SQLite 文件锁窗口）冷却后重派。

    失败派单只留 failed 会话行、无 prediction，重派不产生重复数据；
    TimeoutError（已跑满 40 分钟）不重试——重跑代价过高，直接上抛。
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await run_symbol(symbol, timeout_s=timeout_s)
        except TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - 瞬态窗口类型不可枚举，冷却重试兜底
            last = exc
            print(
                f"[ablation] {symbol} 第 {attempt}/{attempts} 次失败: "
                f"{type(exc).__name__}: {exc}；{cooldown_s}s 后重试",
                flush=True,
            )
            if attempt < attempts:
                await asyncio.sleep(cooldown_s)
    raise last  # type: ignore[misc]


async def _main(args: argparse.Namespace) -> int:
    variant_paths = [Path(v) for v in args.variant]
    dirs = {p.parent.resolve() for p in variant_paths}
    if len(dirs) > 1:
        raise RuntimeError(f"多个变体须同目录: {sorted(str(d) for d in dirs)}")
    mapping = parse_variant_mapping(variant_paths)
    results = []
    with patch_prompts(next(iter(dirs)), mapping):
        for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            print(
                f"[ablation] symbol={symbol} variants={mapping} db={os.environ['SESSIONS_DB_PATH']}",
                flush=True,
            )
            results.append(await _run_symbol_with_retry(symbol, timeout_s=args.timeout))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    buys = sum(1 for r in results if _action(r) == "buy")
    actions = [f"{r['symbol']}={_action(r) or 'none'}({r['status']})" for r in results]
    print(f"[ablation] done: {len(results)} runs, buy={buys} -> {out}", flush=True)
    print("[ablation] actions: " + "; ".join(actions), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--variant",
        action="append",
        required=True,
        help="变体提示词文件（可重复；同目录；文件名以 trader/risk_judge 开头）",
    )
    ap.add_argument("--symbols", required=True, help="逗号分隔股票代码")
    ap.add_argument("--db", default="tmp/ablation.db", help="隔离 sessions 库路径（禁生产库）")
    ap.add_argument("--out", required=True, help="结果 JSON 输出路径")
    ap.add_argument("--timeout", type=int, default=2400, help="单只票超时秒数")
    args = ap.parse_args(argv)
    # 必须在 import finance_agent 之前落 env（session_store 导入期固化路径）
    db = assert_isolated_db(Path(args.db))
    db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["SESSIONS_DB_PATH"] = str(db)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
