# 041 — Langfuse Scores 页 internal server error：ClickHouse 26.6 新分析器在 scores 查询形状上必然崩溃

**日期**: 2026-09-12
**状态**: 已修复（enable_analyzer=0 + 镜像固定；UI 页面待 owner 刷新确认）
**严重度**: 中（观测平台 UI 单页不可用 3 周；公共 API 与采集链路全程未受影响）

## 症状

Langfuse UI Scores 页面报 `Internal server error`（tRPC `scores.all`）；`/api/public/scores` 公共 API、trace 页、数据摄入全部正常——只有该页挂。

## 根因（systematic-debugging 四阶段定位）

1. **复现**：langfuse-web 日志 → ClickHouse `NOT_FOUND_COLUMN_IN_BLOCK (Code 10)`："Not found column and(equals(project_id...),in(data_type...),...) "——CH 把 WHERE 的索引条件复合表达式当成列去找。
2. **最小复现**（26.6.1 实测）：

```sql
SELECT s.id FROM scores AS s FINAL
LEFT JOIN traces AS t ON (s.trace_id = t.id) AND (t.project_id = s.project_id)
WHERE s.project_id = '...' AND s.data_type IN [...]
ORDER BY s.timestamp DESC LIMIT 50;   -- 有 LIMIT 才触发；无 LIMIT / 无 FINAL / 无 JOIN 均正常
```

3. **根因**：ClickHouse **26.6 新查询分析器**在 `FINAL + JOIN + ORDER BY + LIMIT`（恰好是 Langfuse scores 页查询形状）下的回归 bug。旧分析器（`enable_analyzer=0`）下同一查询正常返回。
4. **引入时点**：镜像是浮动 `latest` tag，**2026-08-20 起** CH 26.6.1 上线当日即开始报错（日志证据：log.4.gz 首条 08-20 11:25），中间 8/26–9/5 零报错只是没人打开该页（人工标注走导出 xlsx）。今天 10:45 的 `docker compose up --build` 只是重建了容器，**不是**引入时刻。

## 处置（为什么不用降级）

| 方案 | 评估 |
|---|---|
| ✅ 默认 profile `enable_analyzer=0`（`docker/clickhouse/users.d/incident-041-legacy-analyzer.xml`） | 实测修复；语义回到 Langfuse 支持的 24.x 时代；不动镜像、不动数据、可秒级回滚 |
| ✅ 镜像固定 `clickhouse-server:26.6.1.1193`（docker-compose.yml） | 杜绝下次 float 无预警拉入新版本 |
| ❌ 降级 ClickHouse 镜像 | 数据目录已被 26.6.1 写了 3 周（官方不支持降级，part 格式风险）；且无降级必要——旧分析器路径已完全绕开 bug |

**验证**：query_log 中 1218 字符的原始失败 SQL 逐字重放 → 正常返回；`/api/public/health` 200；scores API 200。

## 遗留与约束

- UI Scores 页面由 owner 刷新浏览器确认（tRPC 需登录态，脚本无法代验）。
- 旧分析器是废弃路径：未来升级 ClickHouse 时（1）先在新版本上重放 scores.all 查询形状回归（本文件的最小复现即可），（2）确认新分析器修复后再删除 users.d 配置。
- 本次暴露的流程缺口：基础设施镜像浮动 tag 无预警升级，本地无 Scores 页冒烟——「依赖镜像一律固定版本」是本次的教训（后端 backend 镜像固定、langfuse langfuse:3 大版本 tag 已是惯例，clickhouse 漏了）。
