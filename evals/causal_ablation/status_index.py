"""结论注册表索引：把 docs/evals 报告的 status 头渲染成可查询索引（spec causal-ablation）。

只读扫描 + 纯渲染，不改动任何报告文件——报告状态由撰写者维护，本模块只负责让它可见。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evals.causal_ablation.report_status import parse_status, status_badge


@dataclass(frozen=True)
class ReportEntry:
    path: str
    status: str
    target: str | None


def collect_status_index(
    dir_path: Path, *, recursive: bool = False
) -> tuple[list[ReportEntry], list[str]]:
    """返回 (entries, unstamped)。

    entries：带合法 status 头的报告，按路径排序。
    unstamped：缺 status 头的报告（存量文档未回填时可见，不被静默忽略）。
    非法 status（未知取值 / superseded-by 缺目标）直接抛错——那是有缺陷的报告，不静默跳过。
    """
    if not dir_path.exists():
        return [], []
    pattern = "**/*.md" if recursive else "*.md"
    entries: list[ReportEntry] = []
    unstamped: list[str] = []
    for path in sorted(dir_path.glob(pattern)):
        text = path.read_text(encoding="utf-8")
        if "**status**" not in text:
            unstamped.append(path.as_posix())
            continue
        status, target = parse_status(text)
        if status == "superseded-by" and not target:
            raise ValueError(f"{path.as_posix()}: superseded-by 缺目标路径")
        entries.append(ReportEntry(path=path.as_posix(), status=status, target=target))
    return entries, unstamped


def render_status_index(entries: list[ReportEntry], unstamped: list[str]) -> str:
    """渲染索引（markdown）：有效结论在前，未标注生命周期的存量文档单列。"""
    lines = ["# 评估结论注册表", ""]
    if entries:
        lines.append("| 报告 | 状态 | 取代者 |")
        lines.append("|---|---|---|")
        for e in sorted(entries, key=lambda x: x.path):
            # 徽章带上目标（G7/⑤b）：否则 superseded 行恒显 `<缺目标>`，取代者只藏于末列
            lines.append(f"| {e.path} | {status_badge(e.status, e.target)} | {e.target or '—'} |")
    else:
        lines.append("（暂无带 status 头的报告）")
    if unstamped:
        lines += ["", f"## 未标注生命周期（{len(unstamped)} 份）", ""]
        lines += [f"- {p}" for p in sorted(unstamped)]
    return "\n".join(lines) + "\n"
