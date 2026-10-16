# fix-timeline-write-amplification 验证报告（收口）

- 日期：2026-10-17（archive 收口轮；实施与合并为 PR #304，2026-10-13）
- 变更：`openspec/changes/fix-timeline-write-amplification`（session-persistence MODIFIED×1；
  incident 039 / issue #265 子项 1——管线时序持久化双重 O(n²) 写放大治理）
- 背景：本 delta 的实施、测试与性能实证均已完成（tasks 14/14 全勾），archive 一直未执行；
  本报告为收口补齐的验证档案。

## §1 变更语义（复述，防漂移）

- 可变累加器（`NodeTimelineAccumulator`）：thinking token O(1) 分片缓冲；`materialize()` 与
  纯函数 fold 逐字节同构（对照测试钉死）。
- 自适应中间写间隔：`clamp(上次序列化字节 ÷ 256KB/s, [0.5s, 30s])`；节点边界事件
  （node_complete/node_timing）与结束 flush **即时全量写**，不受间隔限制。
- 每次写入恒为当前全量结构；持久化 JSON 格式与前端恢复路径零改动。

## §2 单元/回归证据（实施期，tasks §3–§4）

- 洪峰测试：3000 thinking token 假 store 计写次数有界（≪ naive 上界）；结束 flush 写全量内容
  （agent_factory 与 pipeline_runner 双路径同款口径）。
- 累加器等价性：随机交错序列 vs 纯函数 fold 逐字节同构；既有 115+ 时序测试全绿。
- 性能实证：6 万 token 洪峰累加耗时对比（纯函数 fold vs 累加器）——平方项消除。
- `uv run pytest -m "not live"` 0 失败；`openspec validate --strict` 过。

## §3 E2E 门禁（2026-10-17 补跑，时间轴专用 config 8001/5174 隔离）

- 首轮：31 passed / **1 failed** / 2 flaky / 1 skipped——失败 =
  `pipeline-hierarchical-timeline.spec.ts #2`，根因 = **规格陈旧（spec rot）**：报告 DOM 演进后
  分析师节标题为中文名（技术面分析师/宏观分析师/基本面分析师/舆情分析师），spec 仍在断言旧
  `heading{name:'technical'}`（与本 delta 无交集——报告渲染属 report-decision-rendering /
  peer-comparison 线）。
- **rot 修复**（保测试意图）：断言改为「节标题 + 对应 STUB 内容」成对出现（防旧版全部显示
  technical 摘要的错位回归）→ 单跑 **4/4 过**。
- 全量复跑（rot 修复后）：见 §5 读数。
- 2 flaky（pipeline-hierarchical #4 / resume-pipeline #1）= 既有 #312/#328 家族（重试即绿，
  与写放大变更无交集）。

## §4 生产活体证据（变更已在线运行，2026-10-13 起）

- 600519 全管线实跑（2026-10-16 22:09 完成，fast path 真实流量）：`pipeline_timelines`
  **519,111 bytes / 13 节点 / 内容 ≈ 506,208 字符**——终态 flush 全量落库 ✓（含全部节点
  thinking 内容，无截断）。
- cohort 批分析走直连图路径（无 UI 时序持久化）——预先存在行为，与本 delta 无关（本 delta
  只改两条消费路径的持久化节奏）。

## §5 复跑读数

- **全量复跑（rot 修复后，隔离快照配置）：31 passed / 3 flaky / 1 skipped / 0 failed（13.2m）**——
  flaky 为既有 #312/#328 瞬态族（重试即绿）；1 skipped = visual-baseline 采集工具自跳过。
  E2E 门禁：**通过**。
- 附带发现（incident 044）：本套件原配置缺 `SNAPSHOT_DB_PATH` 隔离（本机跑时读生产快照库）；
  已随本 PR 修复（7 个配置补齐）。test 2 的断言同步改为「逐分析师 STUB 摘要内容」（报告两条
  渲染分支下的不变量——标题风格随环境漂移，内容不漂）。

## §6 结论

- 语义零漂移、测试双路径覆盖、性能实证与生产活体证据齐备；E2E 门禁（时间轴专用套件）在
  rot 修复后以本报告读数为准。**可 archive**（sync session-persistence MODIFIED 入主库）。
- 附带修复：pipeline-hierarchical-timeline.spec.ts 规格陈旧（drive-by，随本收口 PR）。
