# Proposal: add-chart-persistent-storage

## Why

issue #239 缺陷 A：会话 `report_markdown` 引用的图表源文件写在容器 `/tmp/finance_charts/<code>/`（未挂载卷）——容器重启后路径消失，对历史会话按需重导出时 `sanitize_missing_images` 静默删除全部图片行，产出无图 PDF/DOCX/PPTX，用户侧零报错。2026-10-06 后端容器重启后 `/tmp/finance_charts` 已不存在（docker exec 实证）。

## What Changes

1. **图表产物落持久路径**：`CHARTS_DIR` 环境变量（默认 `data/charts`，与既有 `REPORTS_DIR` 注入惯例一致）——`data/` 为 compose 卷挂载目录（`./data:/app/data`），图表跨容器重启持久
2. 新会话 `report_markdown` 引用持久路径；存量会话的 `/tmp` 引用本已失效（文件已丢），md 导出既有降级语义（跳过缺失图片行）不变
3. `.gitignore` += `data/charts/`（本地产物不入库）

## Impact

- Affected specs: `chart-data-integrity`（ADDED「图表产物持久化存储」）
- Affected code: `nodes/report.py`（`_charts_dir` 纯函数接缝 + 调用点）、`.gitignore`
- 零回归：默认行为仅路径前缀变化（/tmp/finance_charts → data/charts），导出/嵌入/降级语义全部不动
