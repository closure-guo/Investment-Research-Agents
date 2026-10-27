# incident 052：因子详情接口请求内现算准入面板——护栏被 IC 历史回填绕过，详情页全部挂死（2026-10-27）

> 同族：[add-factor-ic-visibility](https://github.com/closure-guo/Investment-Research-Agents/pull/338)（首次发作的修复）。
> **复发型**：首次根因就写在 `_factor_detail_diagnostics` 注释里（「500 日 × 全市场面板实测需数分钟，会把详情接口拖成超时」），护栏只堵了「IC 点数不足」一个分支——点数涨回去后原病复发。价位校验器三发（#220/#254/斜杠分数）后第二次出现「护栏只护一半」家族。

## 现象（2026-10-27 22:00 前后排查取证）

1. `/factor-research` 因子库页展开任何 active 因子详情 → 「详情加载中…」永不返回（浏览器 ~300s 超时后显示「详情加载失败」+ 重试按钮；重试再堆一个服务端任务）。
2. `GET /api/factors/{id}` **无超时完成记录**：容器 7 小时生命周期内 uvicorn access log 里详情请求 0 条完成（列表 `/api/factors` 正常 200）。
3. 探针实测：单次详情请求 **570s 客户端超时后服务端仍继续算，71 分钟未完成**；后端容器 CPU 持续 180–210%，进程线程池打到 48 线程（默认 executor 上限附近）——多次点击/重试的自放大堆积。
4. 页面判别特征：2 条 retired 因子（IC 0/2 点）详情秒开，10 条 active 因子（窗口 IC 全 60/90 点）全挂。

## 根因

```
_factor_detail_diagnostics（api.py 请求路径）
  └─ IC 点数 < 30 → 短路返回 None            ← 唯一护栏（add-factor-ic-visibility, #338）
  └─ IC 点数 ≥ 30 → _panel_from_snapshot(None)  ← 500 日 × 全市场面板，容器实测 10–20+ 分钟
       └─ DSL 求值 + rank_ic + 单调性 + 5 个 builtin 风格代理求值（无缓存、无复用）
```

- **触发链**：10-22 收割批量入库 7 条因子 + IC 历史回填（#462 族）把库内因子 `factor_ic` 回填到 60–90 点 → 全部 active 因子跨过 `MIN_STYLE_SAMPLES=30` → 护栏对所有 active 因子失效。
- **成本量级（同容器同面板校准）**：`factor_builtin_register` 9.5–18 分钟/日、`factor_harvest_daily` 50–82 分钟/日（内含一次面板构建 + 候选评估）。详情请求 = 面板 + 单因子求值 + 5 builtin + 单调性，量级同源。
- **深层病灶**：`MIN_STYLE_SAMPLES=30` 是「风格相关需要 ≥30 个共同 IC 日」的**数据充分性**要求，被复用成「跳过面板计算」的**成本豁免**——两个语义被一个常量耦合；数据一旦长够，成本豁免静默消失。
- **结构病灶**：诊断读数是收盘后数据的日内不变量，却在 HTTP 请求里现算——违反系统既有「日批算、接口只读」模式（衰减监控/收割/注册全走该模式）。

## 修复（PR #502，add-factor-diagnostics-batch）

- 诊断读数改**日批预计算落库**：新表 `factor_diagnostics`（每因子一行含 `as_of`）+ 新模块 `factors/diagnostics.py`（增量待算：读数缺失或 `as_of` 落后最新快照日才算；按需面板——无待算零面板构建；逐因子失败隔离）。
- 新作业 `factor_diagnostics_daily`（17:15，含周末；收割过晚可能并发，均为进程内 CPU 任务无正确性影响）。
- 详情接口 `_factor_detail_diagnostics` 改**纯读库** + 新增 `diagnostics_as_of`；**无条件零面板构建**——回归测试 `test_detail_never_builds_panel_regardless_of_ic_points` 把面板构建打桩为 raise，任何分支触及即失败（IC 4 点与 40 点双分支都钉）。

## 防复发要点

1. **护栏语义分离**：数据充分性门槛（MIN_STYLE_SAMPLES）只该出现在日批的待算判定里，永远不该作为 HTTP 路径的成本豁免——「点了会慢」的东西不进请求路径。
2. **回归测试形态**：零面板构建是**无条件不变量**，用「打桩为 raise」断言（IC 点数多寡都触发不了慢路径），而不是「快照库为空时自然短路」（后者在测试里恒真、护不住复发）。
3. **监控盲区**：该挂死不产生 5xx/错误日志（请求只是不结束），access log 无完成行是唯一信号——「慢而不死」的接口要有延迟预算类断言/观测（本期未落，挂账 issue）。
