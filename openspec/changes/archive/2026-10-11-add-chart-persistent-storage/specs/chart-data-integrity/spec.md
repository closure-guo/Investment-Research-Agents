# add-chart-persistent-storage delta — chart-data-integrity

## ADDED Requirements

### Requirement: 图表产物持久化存储

管线生成的图表 PNG SHALL 写入持久化目录 `CHARTS_DIR`（环境变量，默认 `data/charts`）下按股票代码划分的子目录，MUST NOT 写入容器临时目录（`/tmp`）——`data/` 为 compose 卷挂载目录，图表 SHALL 跨容器重启存活，使历史会话按需重导出时图片源文件仍可得（#239 缺陷 A：/tmp 路径重启即失，重导出静默降级为无图产物）。`CHARTS_DIR` 覆盖语义与既有 `REPORTS_DIR` 注入惯例一致（E2E/测试隔离）。

存量会话 `report_markdown` 中指向已消失 `/tmp` 路径的图片行，沿用 Markdown 导出「源文件缺失时优雅降级」既有语义（跳过图片行继续导出），本变更 MUST NOT 改写会话存储中的历史引用。

#### Scenario: 图表写入持久目录且跨重启可得

- **GIVEN** 后端容器完成一次深度分析（图表生成于 `data/charts/<code>/`）
- **WHEN** 容器重启后对同一会话按需重导出
- **THEN** 图片源文件 SHALL 仍存在，导出产物 SHALL 含全部图表
- **AND** MUST NOT 出现静默删除图片行的无图产物

#### Scenario: CHARTS_DIR 覆盖隔离

- **GIVEN** E2E 门禁 webServer 注入 `CHARTS_DIR=<tmp>/charts`
- **WHEN** stub 管线生成报告
- **THEN** 图表 SHALL 写入注入目录，MUST NOT 污染检出工作树的 `data/charts`
