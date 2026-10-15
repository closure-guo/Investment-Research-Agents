"""语义记忆月批提炼（delta add-monthly-batch-scheduling）：ops 通道入口。

``run_memory_extract_monthly`` 是「记忆提炼」月批任务（每月 1 日 09:00
Asia/Shanghai）的编排入口，走统一日批通道（``ops.jobs.run_job``：调度 / 运行历史 /
手动补跑 / 单飞锁）：

1. **取源**：``settled_records``（predictions 已结算终态；只读 fail-open）按
   ``as_of`` 与近 ``window_days`` 天窗口过滤——默认 90 天，避免全量重跑；
2. **提炼**：``extract_candidate_clauses``（LLM 优先 / 规则兜底，模块既有契约；
   ``client`` 缺省 → ``_default_client`` 网关薄适配，端点失败自动降级规则式）；
3. **落库**：``store_candidate_clauses`` 落 **pending 候选态**——注入契约不变
   （未确认不注入，经 ``/memory`` 人工确认后方可进 prompt）。

摘要（运行历史）``{"window_days", "settled_count", "candidates", "path"}``：
``candidates`` = 本次**新入库**候选数（同窗口重提炼经 store 侧去重为 0，不造假）；
``path`` = 实际生效的提炼路径（``"llm"`` = LLM 产出 / ``"rule"`` = 规则式兜底）；
无可用已结算决策 → ``settled_count=0`` + ``candidates=0`` 的成功空摘要。

与 ``semantic.run_monthly_extraction`` 的分工：后者是全量历史、返回 clause_id 列表的
既有窄入口（TESTING 造数 / 验证脚本消费，语义不变）；本模块是窗口 + 摘要的 ops 入口。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent.llm.gateway import complete_text
from finance_agent.llm.types import Purpose
from finance_agent.memory.episodic import settled_records
from finance_agent.memory.semantic import extract_candidate_clauses, store_candidate_clauses

logger = logging.getLogger(__name__)

# 提炼窗口默认天数（design 决策：近窗口提炼，避免全量重跑）
DEFAULT_WINDOW_DAYS = 90

# 单样本组不入候选（与 ``extract_candidate_clauses`` 默认一致）
_DEFAULT_MIN_SAMPLES = 2

# 默认网关适配器的档位：quick（低延迟低成本；REQUIRED_CAPS 无硬要求），
# 同 ``screening.candidate_llm`` 的成本纪律先例
_PURPOSE_TIER: Purpose = "quick"
# trace 用途标签（Langfuse span 名 / 元数据；非 Purpose 字面量）
PURPOSE_TAG = "memory_extraction"


class GatewayExtractionClient:
    """缺省 ``client``：gateway ``complete_text`` 薄适配（LLM 优先路径的唯一生产入口）。

    - 档位钉死 ``quick`` 并经 trace 记用途标签（调用契约与 candidate_llm 同款）；
    - 调用失败由 ``semantic._llm_extract`` 捕获并降级规则式提炼——月批不因端点
      故障中断（旁路铁律）；失败事实体现在摘要 ``path="rule"``。
    """

    def __init__(
        self, *, llm_config: dict[str, Any] | None = None, max_tokens: int | None = None
    ) -> None:
        self._llm_config = llm_config
        self._max_tokens = max_tokens

    def complete_text(self, prompt: str) -> str:
        text, _meta = complete_text(
            [{"role": "user", "content": prompt}],
            purpose=_PURPOSE_TIER,
            max_tokens=self._max_tokens,
            llm_config=self._llm_config,
            trace={"name": PURPOSE_TAG, "metadata": {"purpose_tag": PURPOSE_TAG}},
        )
        return text


def _default_client() -> Any:
    """缺省提炼客户端（独立函数 = 测试可 monkeypatch 的单一 seam，零网络）。"""
    return GatewayExtractionClient()


def _settled_in_window(
    *, as_of: date, window_days: int, db_path: str | Path | None
) -> list[dict[str, Any]]:
    """近 ``window_days`` 天窗口内的已结算记忆条目（``as_of`` 严格早于；含窗口左端点）。"""
    cutoff = (as_of - timedelta(days=window_days)).isoformat()
    return [
        entry
        for entry in settled_records(as_of=as_of.isoformat(), db_path=db_path)
        if str(entry.get("date") or "") >= cutoff
    ]


def _extraction_path(clauses: list[dict[str, Any]]) -> str:
    """实际生效的提炼路径：LLM 产出带 ``evidence.source="llm"``；否则规则式兜底。"""
    evidence = (clauses[0].get("evidence") or {}) if clauses else {}
    return "llm" if str(evidence.get("source") or "") == "llm" else "rule"


def run_memory_extract_monthly(
    *,
    db_path: str | Path | None = None,
    client: Any = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    as_of: str | None = None,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """月批入口（job 零参数调用）：近窗口已结算决策 → 提炼 → 候选态；返回运行摘要。

    ``as_of`` 缺省 = 今日（严格早于该日的结算记录才入窗口；同日自污染不进提炼）。
    ``client`` 缺省 = ``_default_client()``（LLM 优先）；注入 stub 走零网络路径。
    """
    if int(window_days) < 1:
        raise ValueError(f"window_days 必须是正整数: {window_days!r}")
    as_of_date = date.fromisoformat(str(as_of)[:10]) if as_of else date.today()
    settled = _settled_in_window(as_of=as_of_date, window_days=int(window_days), db_path=db_path)
    clauses = extract_candidate_clauses(
        settled,
        client=client if client is not None else _default_client(),
        min_samples=min_samples,
    )
    new_ids = store_candidate_clauses(
        [str(clause["text"]) for clause in clauses], db_path=db_path, source="monthly_batch"
    )
    summary = {
        "window_days": int(window_days),
        "settled_count": len(settled),
        "candidates": len(new_ids),
        "path": _extraction_path(clauses),
    }
    logger.info("记忆提炼月批完成（候选落 pending，未确认不注入）: %s", summary)
    return summary
