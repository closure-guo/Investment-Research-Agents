"""外部研发供给导入（unify-quant-research-architecture P0-3）。

外部研究员（RD-Agent / Qlib 生态等）产出的候选因子 → 与本地提案**完全相同的
五道闸**（spec factor-proposal「外部研发供给接入」）：

- **输入**：候选序列，每条 ``expression``（DSL 字符串）/ ``hypothesis`` 必填、
  ``category`` 可选（缺省 ``"外部"``）；可携带任意外部元数据字段（外部 IC/收益
  等）——**准入判定只取闸口读数**，外部结论字段 MUST NOT 影响裁决（仅随输入留档，
  不入库）；
- **来源强制**：``source`` 恒为 ``"external"``（通道改写——文件自称
  proposal/builtin 一律覆盖，MUST NOT 借通道伪造来源）；
- **预检**：DSL 解析/复杂度失败 → ``errors`` 桶（gate=None）留痕，单条失败不阻断
  整批（与月批 ``_preflight`` 同一套 ``parse`` 单一来源）；
- **面板**：缺省经 ``monthly._panel_from_snapshot``（准入面板深度下限 250 交易日，
  浅面板 fail loud 共用路径）；
- **真源唯一**：未过闸者不入库（``admit_candidates`` 语义），本因子库是唯一生产
  因子真源。

摘要 ``{admitted, rejected, errors, rejections}``（与提案月批同一口径；``rejections``
逐条 ``{expression, gate, reason}``，预检失败 ``gate=None``）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.monthly import (  # noqa: PLC2701 - 预检/面板口径单一来源（同先例）
    _panel_from_snapshot,
    _preflight,
    _rejected_gate_detail,
    _rejection_reason,
)
from finance_agent.factors.validate import admit_candidates

# 通道来源标注（逐字；库内 factors.source 列值——与 proposal/builtin 并列）
EXTERNAL_SOURCE = "external"

# 导入批次闸③置换检验的固定种子（与月批/注册同款 42；同面板同候选逐位可复现）
DEFAULT_IMPORT_SEED = 42

# 缺省 category（候选未申报分桶时；库层 category 为自由文本列）
DEFAULT_CATEGORY = "外部"


def run_factor_external_import(
    candidates: Sequence[Mapping[str, Any]],
    *,
    panel: MarketPanel | None = None,
    seed: int = DEFAULT_IMPORT_SEED,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """外部候选导入入口：DSL 表达式候选 → 预检 → 同一五道闸 → 摘要。

    空输入 → 零摘要（无可导入候选，非错误）。预检失败与闸口拒收均计入
    ``rejections``；面板不可得/深度不足 → ``ValueError`` 上抛（fail loud）。
    """
    if not candidates:
        return {"admitted": 0, "rejected": 0, "errors": 0, "rejections": []}

    normalized: list[dict[str, Any]] = [
        {
            "expression": str(raw.get("expression") or "").strip(),
            "hypothesis": str(raw.get("hypothesis") or "").strip(),
            "category": str(raw.get("category") or DEFAULT_CATEGORY),
            "source": EXTERNAL_SOURCE,  # 通道强制标注（不采信输入自称）
        }
        for raw in candidates
    ]
    positions, admissible, failures = _preflight(normalized)
    rejections: list[dict[str, Any]] = [
        {
            "expression": normalized[position]["expression"],
            "gate": None,
            "reason": reason,
        }
        for position, reason in sorted(failures.items())
    ]

    admitted = 0
    if admissible:
        resolved_panel = panel if panel is not None else _panel_from_snapshot(snapshot_db_path)
        results = admit_candidates(admissible, resolved_panel, seed=seed, db_path=db_path)
        for position, result in zip(positions, results, strict=True):
            if result.factor_id is not None:
                admitted += 1
                continue
            rejections.append(
                {
                    "expression": normalized[position]["expression"],
                    "gate": result.rejected_gate,
                    "reason": _rejection_reason(result),
                    # 判定读数（阈值/统计量等）：拒收可判读，不再只给原因码
                    "detail": _rejected_gate_detail(result),
                }
            )

    return {
        "admitted": admitted,
        "rejected": len(admissible) - admitted,
        "errors": len(failures),
        "rejections": rejections,
    }
