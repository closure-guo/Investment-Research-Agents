"""refresh_eval_index 运维脚本：索引新鲜度守卫。

背景（2026-10-17 盘点）：索引由纯函数生成但无复跑入口，导致新报告入库后反复陈旧
（当时缺 1 条 active 行 + 7 份未标注文档）。本测试把「索引必须与刷新器产物一致」
钉成回归守卫——任何新报告入库而忘记刷新，CI 即红。
"""

from __future__ import annotations

from scripts.refresh_eval_index import INDEX_PATH, main, render_index


def test_index_is_fresh():
    """仓库索引 == 刷新器当前产物（陈旧即失败：先跑 uv run python scripts/refresh_eval_index.py）。"""
    assert render_index() == INDEX_PATH.read_text(encoding="utf-8")
    assert main(["--check"]) == 0


def test_render_is_deterministic():
    """纯读扫描 + 纯渲染：两次渲染逐字节一致（无时间戳/无顺序抖动）。"""
    assert render_index() == render_index()
