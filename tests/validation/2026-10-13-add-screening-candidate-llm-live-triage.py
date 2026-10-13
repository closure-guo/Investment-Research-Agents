"""add-screening-candidate-llm Task 3 live 抽检：25 只合成候选（5 风险 + 20 中性）真跑 L3。

用法::

    uv run python tests/validation/2026-10-13-add-screening-candidate-llm-live-triage.py
    uv run python tests/validation/2026-10-13-add-screening-candidate-llm-live-triage.py --out <路径.json>

- 读取宿主仓库根 ``.env``（``find_dotenv(usecwd=False)``，与 ``api.py``/既有 live 脚本
  同一来源；worktree 场景指向主仓库 .env）；
- 经**生产路径** ``GatewayTriageClient``（真实 gateway、purpose 钉死 quick）真跑
  ``run_triage``——零 mock、零 stub；落库到临时 screening 库并读回核对行数；
- 25 只合成候选：5 只注入风险事件（立案 ×2 / 大额减持 ×2 / 业绩预亏 ×1）、
  20 只注入中性事件（定期报告/分红/股东大会等）；每只带综合分、因子贡献与排名；
- 统计 keep/drop 分布、reason 长度（全部 ≤50）、5 只风险候选的 drop 率；
  **抽 20 条理由逐条记录**（全部 5 风险 + 前 15 中性，固定抽样非随机）供人工终审，
  每条附 verdict/reason/reason 长度/事件标题/原始 prompt 摘要；
- 凭据/网络失败（全部降级 keep 且 warnings 非空）→ 如实记 ``status="blocked"``
  （退出码 2），**不伪造**任何分布数字；
- 打印不含任何密钥：只报 key 存在性、endpoint host、模型名与结果统计。

**诚实注记**：2026-09 真实候选不可得（东财行情域 IP 封禁持续生效，
incident 033 / issue #325）→ 以合成候选 + 注入事件替代。本次真跑验证的是
「输出合同与 LLM 行为（reason/verdict 是否可用）」，**不构成真实市场有效性的证据**。
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
try:
    # 与 api.py 的无参 load_dotenv() 同源：向上查找到仓库根 .env（worktree 场景为宿主仓库）
    ENV_FILE = find_dotenv(usecwd=False)
except AssertionError:  # 无文件帧（交互/EOF 执行）时退化按脚本路径向上查找
    ENV_FILE = ""
if not ENV_FILE:
    for _parent in Path(__file__).resolve().parents:
        if (_parent / ".env").exists():
            ENV_FILE = str(_parent / ".env")
            break
load_dotenv(ENV_FILE or None)

sys.path.insert(0, str(WORKTREE_ROOT / "src"))

from finance_agent.llm.resolver import resolve_profile  # noqa: E402
from finance_agent.screening.candidate_llm import (  # noqa: E402
    MAX_REASON_CHARS,
    GatewayTriageClient,
    build_triage_prompt,
    run_triage,
)
from finance_agent.screening.ranker import Candidate, FactorContribution  # noqa: E402
from finance_agent.screening.store import list_triage_decisions  # noqa: E402

OUT_JSON = Path(__file__).resolve().with_name("2026-10-13-add-screening-candidate-llm-live.json")

TRADE_DATE = "2026-10-13"
MAX_WORKERS = 8
SAMPLE_FLOOR = 20  # 抽检理由条数下限（5 风险 + 15 中性，固定抽样）

# 25 只合成候选：(symbol, 组别, 事件标题, 事件类型, 事件日期)
# 排列对齐 spec 场景「事件雷剔除」：rank 5 的候选（600519）注入「收到证监会立案告知书」，
# 其余风险候选分散在中段 rank（7/9/13/18），排除「高排名才被 drop」的混淆。
_CANDIDATES: tuple[tuple[str, str, str, str, str], ...] = (
    ("600036", "neutral", "发布 2026 年半年度报告", "定期报告", "2026-08-28"),
    ("601398", "neutral", "股东大会决议公告", "公司治理", "2026-09-15"),
    ("600030", "neutral", "获得政府补助的公告", "政府补助", "2026-09-22"),
    ("000001", "neutral", "关于回购公司股份的进展公告", "股份回购", "2026-09-25"),
    ("600519", "risky", "收到中国证监会立案告知书", "立案调查", "2026-09-28"),
    ("600887", "neutral", "新产品上市发布会纪要", "经营动态", "2026-09-29"),
    ("000858", "risky", "控股股东因涉嫌信息披露违法违规被证监会立案", "立案调查", "2026-09-30"),
    ("601166", "neutral", "董事会换届选举完成", "公司治理", "2026-09-30"),
    ("300750", "risky", "控股股东拟减持不超过 3% 公司股份", "大额减持", "2026-10-08"),
    ("000333", "neutral", "2026 年中期分红派息实施公告", "分红派息", "2026-10-08"),
    ("600276", "neutral", "获得药品注册证书的公告", "经营动态", "2026-10-08"),
    ("002415", "neutral", "签署日常经营重大合同", "重大合同", "2026-10-09"),
    ("002594", "risky", "董事及高级管理人员集中减持计划公告", "大额减持", "2026-10-09"),
    ("600309", "neutral", "关于全资子公司完成工商变更登记的公告", "子公司事项", "2026-10-09"),
    ("601012", "neutral", "员工持股计划实施进展", "员工持股", "2026-10-09"),
    ("000651", "neutral", "2026 年第三季度经营情况简报", "经营动态", "2026-10-10"),
    ("600900", "neutral", "关于参与设立产业基金的公告", "对外投资", "2026-10-10"),
    ("601318", "risky", "2026 年三季报业绩预亏公告", "业绩预亏", "2026-10-10"),
    ("601888", "neutral", "接待投资者调研活动记录", "投资者关系", "2026-10-10"),
    ("002475", "neutral", "关于变更公司注册资本并修订公司章程的公告", "公司治理", "2026-10-12"),
    ("600585", "neutral", "2026 年 9 月生产经营数据公告", "经营动态", "2026-10-12"),
    ("000725", "neutral", "关于部分募集资金专户销户的公告", "募集资金", "2026-10-12"),
    ("601899", "neutral", "关于全资子公司通过高新技术企业重新认定的公告", "资质认定", "2026-10-12"),
    ("600104", "neutral", "2026 年 9 月产销快报", "经营动态", "2026-10-12"),
    ("601288", "neutral", "关于召开 2026 年第三季度业绩说明会的公告", "投资者关系", "2026-10-12"),
)

_RISKY_SYMBOLS = [row[0] for row in _CANDIDATES if row[1] == "risky"]
# spec 场景「事件雷剔除」的 GIVEN：综合分排名第 5，事件含「收到证监会立案告知书」
_SPEC_SCENARIO_SYMBOL = "600519"


def _out_path() -> Path:
    """输出路径：默认既有 artifact，`--out <路径>` 可另存（不覆盖既有结果）。"""
    if "--out" in sys.argv:
        index = sys.argv.index("--out")
        if index + 1 < len(sys.argv):
            return Path(sys.argv[index + 1])
    return OUT_JSON


def _build_candidates() -> list[Candidate]:
    """25 只合成候选：综合分递减（3.0 → 0.3）、rank 1..25、两只因子贡献。"""
    candidates: list[Candidate] = []
    for index, (symbol, _group, _title, _type, _date) in enumerate(_CANDIDATES):
        score = round(3.0 - 0.1 * index, 4)
        candidates.append(
            Candidate(
                symbol=symbol,
                trade_date=TRADE_DATE,
                composite_score=score,
                factor_contribs=(
                    FactorContribution(
                        factor_id=1,
                        zscore=0.8 - 0.05 * index,
                        weight=0.6,
                        contribution=0.6 * (0.8 - 0.05 * index),
                    ),
                    FactorContribution(
                        factor_id=2,
                        zscore=0.3 - 0.02 * index,
                        weight=0.4,
                        contribution=0.4 * (0.3 - 0.02 * index),
                    ),
                ),
                rank=index + 1,
            )
        )
    return candidates


def _events_by_symbol() -> dict[str, list[dict]]:
    return {
        symbol: [{"title": title, "type": event_type, "date": event_date}]
        for symbol, _group, title, event_type, event_date in _CANDIDATES
    }


def _event_title(symbol: str) -> str:
    for row in _CANDIDATES:
        if row[0] == symbol:
            return row[2]
    return ""


def _group_of(symbol: str) -> str:
    for row in _CANDIDATES:
        if row[0] == symbol:
            return row[1]
    return "unknown"


def main() -> int:
    out_path = _out_path()
    profile = resolve_profile(purpose="quick")
    host = urlparse(profile.base_url).netloc if profile.base_url else None
    candidates = _build_candidates()
    events_by_symbol = _events_by_symbol()
    print(f"[env] dotenv: {ENV_FILE or '(none)'}")
    print(
        f"[profile] {profile.name} | model={profile.model} | host={host} | key_present={bool(profile.api_key)}"
    )
    print(f"[run] {len(candidates)} candidates x 1 quick call (max_workers={MAX_WORKERS}) ...")

    started = datetime.now()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "screening-live.db")
        result = run_triage(
            candidates,
            events_by_symbol=events_by_symbol,
            client=GatewayTriageClient(),
            trade_date=TRADE_DATE,
            max_workers=MAX_WORKERS,
            db_path=db_path,
        )
        db_rows = list_triage_decisions(trade_date=TRADE_DATE, db_path=db_path)
    elapsed = round((datetime.now() - started).total_seconds(), 1)

    decisions = list(result.decisions)
    keep = [d for d in decisions if d.verdict == "keep"]
    drop = [d for d in decisions if d.verdict == "drop"]
    degraded = [d for d in decisions if d.degraded]
    lengths = [len(d.reason) for d in decisions]
    risky = [d for d in decisions if d.symbol in _RISKY_SYMBOLS]
    risky_drops = [d for d in risky if d.verdict == "drop"]

    # 全部降级且 warnings 非空 → 凭据/网络失败，如实记 blocked（不伪造分布结论）
    blocked = len(degraded) == len(decisions) and bool(result.warnings)

    # 抽检：全部 5 风险 + 前 15 中性（固定抽样，非随机），共 20 条
    risky_samples = [d for d in decisions if d.symbol in _RISKY_SYMBOLS]
    neutral_samples = [d for d in decisions if d.symbol not in _RISKY_SYMBOLS][
        : max(0, SAMPLE_FLOOR - len(risky_samples))
    ]
    samples = []
    for decision in [*risky_samples, *neutral_samples]:
        prompt = build_triage_prompt(
            next(c for c in candidates if c.symbol == decision.symbol),
            events_by_symbol[decision.symbol],
        )
        samples.append(
            {
                "symbol": decision.symbol,
                "group": _group_of(decision.symbol),
                "event": _event_title(decision.symbol),
                "verdict": decision.verdict,
                "reason": decision.reason,
                "reason_len": len(decision.reason),
                "degraded": decision.degraded,
                "prompt_excerpt": prompt[:400],
            }
        )

    # spec 场景「事件雷剔除」直接证据：rank 5 立案候选 → drop 且 reason 提及立案
    scenario = next(d for d in decisions if d.symbol == _SPEC_SCENARIO_SYMBOL)
    spec_scenario = {
        "symbol": scenario.symbol,
        "rank": next(c.rank for c in candidates if c.symbol == scenario.symbol),
        "event": _event_title(scenario.symbol),
        "verdict": scenario.verdict,
        "reason": scenario.reason,
        "reason_mentions_lizhan": "立案" in scenario.reason,
    }

    payload = {
        "change": "add-screening-candidate-llm",
        "task": "T3 live 抽检（合成候选 + 注入事件）",
        "run_at": started.isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed,
        "status": "blocked" if blocked else "ok",
        "blocked_reason": (
            "全部候选降级 keep（凭据/网络失败，warnings 见下）" if blocked else None
        ),
        "profile": {
            "name": profile.name,
            "model": profile.model,
            "base_url_host": host,
            "api_key_present": bool(profile.api_key),
        },
        "candidate_count": len(candidates),
        "risky_count": len(_RISKY_SYMBOLS),
        "neutral_count": len(candidates) - len(_RISKY_SYMBOLS),
        "distribution": {"keep": len(keep), "drop": len(drop), "degraded": len(degraded)},
        "reason_lengths": {
            "max": max(lengths) if lengths else None,
            "limit": MAX_REASON_CHARS,
            "all_le_limit": all(length <= MAX_REASON_CHARS for length in lengths),
            "values": lengths,
        },
        "risky_drop_rate": (len(risky_drops) / len(risky)) if risky else None,
        "risky_verdicts": {d.symbol: d.verdict for d in risky},
        "neutral_keep_rate": (
            sum(1 for d in decisions if d.symbol not in _RISKY_SYMBOLS and d.verdict == "keep")
            / max(1, len(candidates) - len(_RISKY_SYMBOLS))
        ),
        "spec_scenario_rank5_lizhan": spec_scenario,
        "warnings": [dict(warning) for warning in result.warnings],
        "trace_metadata": result.trace_metadata,
        "db_readback_rows": len(db_rows),
        "samples": samples,
        "honesty_note": (
            "2026-09 真实候选不可得（东财行情域 IP 封禁，incident 033 / issue #325）→ "
            "合成候选 + 注入事件替代；本真跑验证输出合同与 LLM 行为，不构成真实市场有效性证据。"
        ),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[status] {payload['status']} (elapsed {elapsed}s)")
    print(f"[distribution] {payload['distribution']}")
    print(
        f"[reason] max_len={payload['reason_lengths']['max']} all_le_50={payload['reason_lengths']['all_le_limit']}"
    )
    print(f"[risky drop rate] {payload['risky_drop_rate']} {payload['risky_verdicts']}")
    print(f"[spec 场景 rank5 立案] {spec_scenario}")
    print(
        f"[db readback] {len(db_rows)} rows == candidates {len(candidates)}: {len(db_rows) == len(candidates)}"
    )
    if result.warnings:
        print(f"[warnings] {len(result.warnings)}: {result.warnings[:3]}")
    print(f"[artifact] {out_path}")
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
