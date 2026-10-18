"""评估结论注册表索引刷新（docs/evals/README.md，运维脚本）。

背景：索引由 ``evals/causal_ablation/status_index.py`` 纯函数生成（收集 + 渲染），
但历史上只有一次性调用（无 CLI），导致新报告入库后索引反复陈旧（2026-10-17 盘点：
缺 1 条 active 报告行 + 7 份未标注文档）。本脚本把同一生成方式固化为可复跑入口。

行为：
- 扫描范围与 README「索引维护」节约定一致：``docs/evals/*.md``（非递归）+
  ``evals/ablation/results/*.md`` + ``evals/backtest/results/*.md``；索引自身与
  ``metrics.md`` 等非报告文档经 status 头判定自然落入「未标注生命周期」段，不静默忽略；
- 保留既有 README 的手工「## 索引维护」尾节（生成器不产该节，不得在刷新时丢失）；
- 幂等：内容不变时零写入；``--check`` 只校验不写入（退出码 1 = 需要刷新）。

用法::

    uv run python scripts/refresh_eval_index.py            # 刷新（有变化才写）
    uv run python scripts/refresh_eval_index.py --check    # 校验索引是否新鲜
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 直接以脚本方式运行时 sys.path[0] 为 scripts/，仓库根不在路径上——显式补入，
# 保证 `evals` 顶层包（非安装包）可导入。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.causal_ablation.status_index import (  # noqa: E402 - 需先补 sys.path 才能导入未安装的 evals 顶层包
    ReportEntry,
    collect_status_index,
    render_status_index,
)

INDEX_PATH = REPO_ROOT / "docs" / "evals" / "README.md"
SCAN_DIRS = (
    REPO_ROOT / "docs" / "evals",
    REPO_ROOT / "evals" / "ablation" / "results",
    REPO_ROOT / "evals" / "backtest" / "results",
)
_FOOTER_HEADING = "## 索引维护"


def _existing_footer(text: str) -> str:
    """提取既有 README 的手工尾节（``## 索引维护`` 起）；无则返回空串。"""
    pos = text.find(_FOOTER_HEADING)
    return text[pos:].rstrip() + "\n" if pos >= 0 else ""


def _repo_relative(path_text: str) -> str:
    """绝对路径 → 仓库相对 posix 路径（索引既有口径）；不在仓库内则原样返回。"""
    try:
        return Path(path_text).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path_text).as_posix()


def render_index() -> str:
    """收集三目录 → 渲染索引正文 + 保留手工尾节。"""
    entries: list[ReportEntry] = []
    unstamped: list[str] = []
    for directory in SCAN_DIRS:
        found, uns = collect_status_index(directory)
        entries.extend(
            ReportEntry(path=_repo_relative(e.path), status=e.status, target=e.target)
            for e in found
        )
        unstamped.extend(_repo_relative(p) for p in uns)
    # 索引自身不参与扫描（避免索引自登）
    unstamped = [p for p in unstamped if Path(p).name != "README.md"]
    body = render_status_index(entries, unstamped)
    footer = _existing_footer(INDEX_PATH.read_text(encoding="utf-8")) if INDEX_PATH.exists() else ""
    return body + ("\n" + footer if footer else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="只校验（不写入）；索引陈旧 → 退出码 1"
    )
    args = parser.parse_args(argv)

    wanted = render_index()
    current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else None
    if current == wanted:
        print("索引已是最新（零写入）")
        return 0
    if args.check:
        print("索引陈旧：运行 `uv run python scripts/refresh_eval_index.py` 刷新", file=sys.stderr)
        return 1
    INDEX_PATH.write_text(wanted, encoding="utf-8")
    print(f"索引已刷新: {INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
