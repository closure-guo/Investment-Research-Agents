"""会话级 LLM usage 记账（composer-stat-pills）。

deep 分析有两条执行路径——ReAct 的 ``agent_factory._run_graph`` 与
``api._run_graph_streaming``（`/api/analyze` 快路径、池分析经 PipelineRunner）——
都必须把 provider 返回的真实 usage 累加落库到 ``sessions`` 表。集中在本模块是
为了让「无调用/无 usage 不写库、不造数」这条规则只有一个实现（两条路径各写一份
会在「calls == 0 跳过」这类边界上漂移）。
"""

from __future__ import annotations

import logging
from typing import Any

from finance_agent import session_store
from finance_agent.nodes._llm_utils import UsageAccumulator

logger = logging.getLogger("finance_agent.usage_accounting")


def persist_session_usage(session_id: str | None, acc: Any) -> None:
    """把一次分析的 usage 真值累加进会话；``calls == 0`` 或形态不符时静默跳过。

    旁路铁律：落库失败只记 WARNING，不阻断管线收尾。``TESTING=1`` 的 stub 不产生
    usage（``calls == 0``），记账保持 0——MUST NOT 以估算填充。
    """
    if not session_id or not isinstance(acc, UsageAccumulator) or acc.calls == 0:
        return
    try:
        session_store.update_session_usage(
            session_id,
            prompt=acc.prompt_tokens,
            completion=acc.completion_tokens,
            total=acc.total_tokens,
            context_peak=acc.max_prompt_tokens,
        )
    except Exception:  # noqa: BLE001 -- 旁路铁律：记账失败不得影响报告产出
        logger.warning("会话用量落库失败(不阻断): session=%s", session_id, exc_info=True)
